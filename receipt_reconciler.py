"""
receipt_reconciler.py — Receipt Validation & Reconciliation Engine for TallyUp.

Strict Pipeline:
1. Data Sanitization: Convert all monetary fields to floats. Strip currency symbols.
2. Item Phase: Loop through `items`. Assert Qty * Price == Total. Auto-fix Qty if Qty is null but Price and Total are valid. Return an item-level confidence score.
3. Subtotal Phase: Sum the corrected items. Compare to the OCR `subtotal`. If there is a delta, check if the delta matches exactly one of the item totals (indicating a duplicate/hallucinated item or a skipped item).
4. Total Phase: Assert Subtotal + Service_Charge + Taxes + Fees - Discounts == Grand_Total (allow a rounding tolerance of 1.0).
5. Heuristic Phase: Verify CGST == SGST. Verify that (Tax / Subtotal) equals a standard rate (2.5%, 5%, 18%).
6. Output: Return the repaired JSON payload, an overall `is_valid` boolean, and an array of `reconciliation_notes` explaining any mathematical assumptions or fixes applied.
"""

import re
from typing import Dict, Any, List, Tuple, Optional

STANDARD_GST_RATES = [2.5, 5.0, 12.0, 18.0, 28.0]


def sanitize_money(val: Any, default: float = 0.0) -> float:
    """Strip currency symbols, commas, and formatting, converting to float rounded to 2 decimals."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return round(float(val), 2)
    s = str(val).strip()
    # Remove currency glyphs and trailing /-
    s = re.sub(r'(?i)[₹$€£]|rs\.?|inr|/\-', '', s)
    s = s.replace(',', '').strip()
    match = re.search(r'-?\d+(?:\.\d+)?', s)
    if match:
        try:
            return round(float(match.group(0)), 2)
        except ValueError:
            return default
    return default


def reconcile_receipt(raw_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executes the 6-phase strict reconciliation pipeline.
    Returns:
    {
        "is_valid": bool,
        "confidence_score": float (0.0 to 1.0),
        "repaired_payload": dict,
        "reconciliation_notes": list[str]
    }
    """
    notes: List[str] = []
    confidence_penalties = 0.0

    # ---------------------------------------------------------------------------
    # PHASE 1: Data Sanitization
    # ---------------------------------------------------------------------------
    subtotal = sanitize_money(raw_data.get("subtotal"))
    grand_total = sanitize_money(raw_data.get("total_bill") or raw_data.get("grand_total") or raw_data.get("total"))
    service_charge = sanitize_money(raw_data.get("service_charge"))
    cgst = sanitize_money(raw_data.get("cgst"))
    sgst = sanitize_money(raw_data.get("sgst"))
    total_tax = sanitize_money(raw_data.get("total_tax") or raw_data.get("tax_amount") or raw_data.get("taxes") or raw_data.get("tax"))
    fees = sanitize_money(raw_data.get("convenience_fee") or raw_data.get("fees"))
    discount = sanitize_money(raw_data.get("discount") or raw_data.get("discount_amount"))
    round_off = sanitize_money(raw_data.get("round_off"))

    raw_items = raw_data.get("items") or []
    sanitized_items: List[Dict[str, Any]] = []

    for it in raw_items:
        name = str(it.get("name") or "Item").strip()
        # Clean currency characters and leading numbers from name
        name = re.sub(r'^[0-9]+[\.\-\)]\s*', '', name).strip()
        qty_raw = it.get("qty") if it.get("qty") is not None else it.get("quantity")
        try:
            qty = float(qty_raw) if qty_raw is not None and str(qty_raw).strip() != "" else None
            if qty is not None and qty <= 0:
                qty = None
        except (ValueError, TypeError):
            qty = None

        price = sanitize_money(it.get("price") or it.get("unit_price") or it.get("rate"), default=None)
        total = sanitize_money(it.get("total") or it.get("amount") or it.get("total_price"), default=None)

        # If total is missing but price was passed as item amount, assign
        if total is None and price is not None and (qty is None or qty == 1):
            total = price

        sanitized_items.append({
            "name": name,
            "qty": qty,
            "price": price,
            "total": total,
            "is_outside": bool(it.get("is_outside", False)),
            "paid_by": it.get("paid_by", ""),
            "consumed_by": it.get("consumed_by") or [],
        })

    notes.append("Phase 1 (Sanitization): Normalized monetary figures and stripped currency formatting.")

    # ---------------------------------------------------------------------------
    # PHASE 2: Item Phase (Qty * Price == Total validation & repair)
    # ---------------------------------------------------------------------------
    repaired_items: List[Dict[str, Any]] = []
    item_confidences: List[float] = []

    for it in sanitized_items:
        qty = it["qty"]
        price = it["price"]
        total = it["total"]
        name = it["name"]
        item_conf = 1.0

        # Scenario 1: Qty is null/missing, but Price and Total are valid
        if (qty is None or qty == 0) and price is not None and total is not None:
            if price > 0 and total > 0:
                ratio = total / price
                nearest_qty = round(ratio)
                if abs(ratio - nearest_qty) <= 0.05 and nearest_qty >= 1:
                    qty = float(nearest_qty)
                    item_conf = 0.95
                    notes.append(f"Item '{name}': Auto-fixed Qty = {int(qty)} (Total ₹{total:.2f} / Unit Price ₹{price:.2f}).")
                else:
                    qty = 1.0
                    item_conf = 0.80
                    notes.append(f"Item '{name}': Assumed Qty = 1.0 for Price ₹{price:.2f}, Total ₹{total:.2f}.")
            else:
                qty = 1.0
                item_conf = 0.70

        # Scenario 2: Qty and Price valid, Total is null/missing
        elif qty is not None and price is not None and (total is None or total == 0):
            total = round(qty * price, 2)
            item_conf = 0.95
            notes.append(f"Item '{name}': Computed Line Total = ₹{total:.2f} ({int(qty) if qty.is_integer() else qty} x ₹{price:.2f}).")

        # Scenario 3: Qty and Total valid, Price (unit price) is null/missing
        elif qty is not None and total is not None and (price is None or price == 0):
            price = round(total / qty, 2) if qty > 0 else total
            item_conf = 0.95
            notes.append(f"Item '{name}': Inferred Unit Price = ₹{price:.2f} from Total ₹{total:.2f} / Qty {qty}.")

        # Scenario 4: All three present — Validate Qty * Price == Total
        elif qty is not None and price is not None and total is not None:
            calc_total = round(qty * price, 2)
            if abs(calc_total - total) <= 0.10:
                item_conf = 1.0
            else:
                # Discrepancy: check if price was accidentally line total
                if price == total and qty > 1:
                    price = round(total / qty, 2)
                    item_conf = 0.90
                    notes.append(f"Item '{name}': Corrected unit price to ₹{price:.2f} (Total was ₹{total:.2f} for Qty {qty}).")
                else:
                    # Adjust line total to qty * price
                    item_conf = 0.75
                    confidence_penalties += 0.05
                    notes.append(f"Item '{name}': Fixed math discrepancy. Qty ({qty}) * Price (₹{price:.2f}) = ₹{calc_total:.2f} (overrode Total ₹{total:.2f}).")
                    total = calc_total

        # Scenario 5: Single value fallback
        else:
            fallback_val = total if total is not None else (price if price is not None else 0.0)
            qty = 1.0
            price = fallback_val
            total = fallback_val
            item_conf = 0.70

        # Generate individual portions for multi-quantity dishes
        int_qty = int(round(qty)) if qty and qty >= 1 else 1
        portions: List[Dict[str, Any]] = []
        if int_qty > 1:
            for p_idx in range(int_qty):
                portions.append({
                    "name": f"{name} ({p_idx + 1}/{int_qty})",
                    "price": price,
                    "qty": 1,
                    "portion_index": p_idx + 1,
                    "total_portions": int_qty,
                })

        repaired_items.append({
            "name": name,
            "qty": int_qty if qty.is_integer() else qty,
            "price": price,
            "total": total,
            "confidence": round(item_conf, 2),
            "portions": portions,
            "is_outside": it["is_outside"],
            "paid_by": it["paid_by"],
            "consumed_by": it["consumed_by"],
        })
        item_confidences.append(item_conf)

    notes.append(f"Phase 2 (Items): Validated {len(repaired_items)} item(s) with average confidence {round(sum(item_confidences)/max(len(item_confidences), 1)*100):.0f}%.")

    # ---------------------------------------------------------------------------
    # PHASE 3: Subtotal Phase
    # ---------------------------------------------------------------------------
    computed_items_subtotal = round(sum(it["total"] for it in repaired_items), 2)
    subtotal_valid = True

    if subtotal == 0.0:
        if computed_items_subtotal > 0:
            subtotal = computed_items_subtotal
            notes.append(f"Phase 3 (Subtotal): OCR Subtotal was empty; populated as ₹{subtotal:.2f} from items sum.")
    else:
        subtotal_delta = round(computed_items_subtotal - subtotal, 2)
        if abs(subtotal_delta) <= 0.50:
            notes.append(f"Phase 3 (Subtotal): Items sum (₹{computed_items_subtotal:.2f}) matches OCR Subtotal (₹{subtotal:.2f}) perfectly.")
        else:
            # Check if delta matches an item exactly (hallucinated/duplicate line or skipped line)
            matched_idx = None
            for idx, it in enumerate(repaired_items):
                if abs(it["total"] - abs(subtotal_delta)) <= 0.50:
                    matched_idx = idx
                    break

            if subtotal_delta > 0 and matched_idx is not None:
                dup = repaired_items.pop(matched_idx)
                computed_items_subtotal = round(sum(it["total"] for it in repaired_items), 2)
                notes.append(f"Phase 3 (Subtotal): Discrepancy of +₹{subtotal_delta:.2f} resolved. Excluded duplicate/header item '{dup['name']}' (₹{dup['total']:.2f}).")
            elif subtotal_delta < 0 and matched_idx is not None:
                notes.append(f"Phase 3 (Subtotal): Detected potential skipped/merged item of ~₹{abs(subtotal_delta):.2f}. OCR Subtotal: ₹{subtotal:.2f}, Detected Items: ₹{computed_items_subtotal:.2f}.")
                subtotal_valid = False
                confidence_penalties += 0.15
            else:
                notes.append(f"Phase 3 (Subtotal): Delta of ₹{subtotal_delta:+.2f} between items sum (₹{computed_items_subtotal:.2f}) and Subtotal (₹{subtotal:.2f}).")
                # If subtotal is significantly greater and items are missing, preserve items sum but flag
                subtotal_valid = False
                confidence_penalties += 0.10

    # ---------------------------------------------------------------------------
    # PHASE 4: Total Phase (Subtotal + Service + Taxes + Fees - Discounts == Grand)
    # ---------------------------------------------------------------------------
    # Heuristic pre-check: ensure CGST/SGST symmetry before calculating total
    if cgst > 0 and sgst == 0:
        sgst = cgst
        notes.append(f"Phase 4 (Tax Pre-check): Infilled SGST (₹{sgst:.2f}) to match CGST (₹{cgst:.2f}).")
    elif sgst > 0 and cgst == 0:
        cgst = sgst
        notes.append(f"Phase 4 (Tax Pre-check): Infilled CGST (₹{cgst:.2f}) to match SGST (₹{sgst:.2f}).")

    if total_tax == 0.0 and (cgst > 0 or sgst > 0):
        total_tax = round(cgst + sgst, 2)

    computed_grand_total = round(subtotal + service_charge + total_tax + fees - discount, 2)
    total_valid = True

    if grand_total == 0.0:
        if computed_grand_total > 0:
            grand_total = computed_grand_total
            notes.append(f"Phase 4 (Grand Total): Synthesized Grand Total as ₹{grand_total:.2f} from Subtotal + Charges.")
    else:
        grand_delta = round(computed_grand_total - grand_total, 2)
        if abs(grand_delta) <= 1.0:
            # Within 1.0 rounding tolerance
            if abs(grand_delta) > 0.01:
                round_off = round(grand_total - computed_grand_total, 2)
                notes.append(f"Phase 4 (Grand Total): Verified with ₹{round_off:+.2f} round-off adjustment (Computed: ₹{computed_grand_total:.2f} -> Grand Total: ₹{grand_total:.2f}).")
            else:
                notes.append(f"Phase 4 (Grand Total): Exact match. Subtotal (₹{subtotal:.2f}) + Tax/Fees (₹{total_tax + service_charge + fees:.2f}) - Discount (₹{discount:.2f}) == ₹{grand_total:.2f}.")
        else:
            # Check if discrepancy matches discount (e.g. 10% offer discount printed at bottom)
            if discount == 0.0 and grand_delta > 1.0:
                potential_disc = grand_delta
                # Check if potential discount corresponds to a common round percentage like 10%, 15%, 20%
                for pct in [5, 10, 15, 20, 25]:
                    if abs(round(subtotal * (pct / 100.0), 2) - potential_disc) <= 1.0:
                        discount = potential_disc
                        computed_grand_total = round(subtotal + service_charge + total_tax + fees - discount, 2)
                        notes.append(f"Phase 4 (Grand Total): Auto-reconciled missing {pct}% offer discount of -₹{discount:.2f} to match Grand Total ₹{grand_total:.2f}.")
                        total_valid = True
                        break
            if not total_valid or abs(computed_grand_total - grand_total) > 1.0:
                notes.append(f"Phase 4 (Grand Total): Discrepancy of ₹{grand_delta:+.2f}. Expected ₹{computed_grand_total:.2f}, got ₹{grand_total:.2f}.")
                total_valid = False
                confidence_penalties += 0.15

    # ---------------------------------------------------------------------------
    # PHASE 5: Heuristic Phase (CGST == SGST & Standard Tax Slabs)
    # ---------------------------------------------------------------------------
    # 1. CGST == SGST symmetry
    if cgst > 0 and sgst > 0:
        if abs(cgst - sgst) <= 0.05:
            notes.append(f"Phase 5 (Heuristics): CGST (₹{cgst:.2f}) == SGST (₹{sgst:.2f}) symmetry confirmed.")
        else:
            notes.append(f"Phase 5 (Heuristics): Asymmetric taxes detected — CGST: ₹{cgst:.2f}, SGST: ₹{sgst:.2f}.")
            confidence_penalties += 0.05
    elif cgst > 0 and sgst == 0:
        sgst = cgst
        total_tax = round(cgst + sgst, 2)
        notes.append(f"Phase 5 (Heuristics): Infilled SGST (₹{sgst:.2f}) to match CGST (₹{cgst:.2f}). Total Tax = ₹{total_tax:.2f}.")
    elif sgst > 0 and cgst == 0:
        cgst = sgst
        total_tax = round(cgst + sgst, 2)
        notes.append(f"Phase 5 (Heuristics): Infilled CGST (₹{cgst:.2f}) to match SGST (₹{sgst:.2f}). Total Tax = ₹{total_tax:.2f}.")

    # 2. Tax / Subtotal rate check
    effective_tax_pct = 0.0
    verified_tax_rate = None
    if subtotal > 0 and total_tax > 0:
        effective_tax_pct = round((total_tax / subtotal) * 100.0, 2)
        closest_std = min(STANDARD_GST_RATES, key=lambda r: abs(r - effective_tax_pct))
        if abs(effective_tax_pct - closest_std) <= 0.35:
            verified_tax_rate = closest_std
            notes.append(f"Phase 5 (Heuristics): Verified tax rate {effective_tax_pct:.2f}% aligns with standard {verified_tax_rate}% GST slab.")
        else:
            verified_tax_rate = effective_tax_pct
            notes.append(f"Phase 5 (Heuristics): Non-standard effective tax rate: {effective_tax_pct:.2f}% (nearest standard slab: {closest_std}%).")

    # ---------------------------------------------------------------------------
    # PHASE 6: Output Formulation
    # ---------------------------------------------------------------------------
    # Calculate overall confidence
    avg_item_conf = sum(item_confidences) / max(len(item_confidences), 1)
    overall_confidence = max(0.1, min(1.0, round(avg_item_conf - confidence_penalties, 2)))

    # Overall validity boolean:
    # Requires subtotal reconciliation, grand total reconciliation within tolerance, and reasonable item confidence.
    is_valid = subtotal_valid and total_valid and (overall_confidence >= 0.70)

    repaired_payload = {
        **raw_data,
        "subtotal": subtotal,
        "total_bill": grand_total,
        "grand_total": grand_total,
        "service_charge": service_charge,
        "cgst": cgst,
        "sgst": sgst,
        "total_tax": total_tax,
        "tax_percent": verified_tax_rate if verified_tax_rate is not None else effective_tax_pct,
        "convenience_fee": fees,
        "fees": fees,
        "discount": discount,
        "discount_percent": round((discount / subtotal) * 100.0, 1) if (subtotal > 0 and discount > 0) else 0.0,
        "round_off": round_off,
        "items": repaired_items,
        "reconciliation": {
            "is_valid": is_valid,
            "confidence_score": overall_confidence,
            "subtotal_matches": subtotal_valid,
            "total_matches": total_valid,
            "notes": notes,
        }
    }

    return {
        "is_valid": is_valid,
        "confidence_score": overall_confidence,
        "repaired_payload": repaired_payload,
        "reconciliation_notes": notes,
    }
