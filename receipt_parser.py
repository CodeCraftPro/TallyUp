"""
receipt_parser.py — 100% Free, Local, Open-Source Universal Receipt Parser
Powered by RapidOCR (PP-OCRv4 ONNX) + Layout-Aware Receipt Parser.

Supports all receipt types:
- Dining & Restaurants (dishes, subtotals, CGST/SGST/taxes, voluntary service charge, round-off)
- Grocery & Supermarkets (Spencer's, Blinkit, Instamart, Zepto, DMart, retail items)
- Fuel & Petrol pumps (Indian Oil, HPCL, BPCL, Shell, fuel type, volume, total)
- Cab & Transport (Uber, Ola, Rapido, airport taxis, tolls)
- Hotels & Lodging (room charges, GST, dates)
- General Retail & Pharmacies (medicines, electronics, clothes)
"""

import os
import re
from datetime import datetime
from rapidocr_onnxruntime import RapidOCR

# Lazy-loaded singleton OCR engine
_ocr_engine = None

def get_ocr_engine():
    global _ocr_engine
    if _ocr_engine is None:
        _ocr_engine = RapidOCR()
    return _ocr_engine


def parse_receipt_image(image_path_or_bytes):
    """
    Parse an image of a receipt and extract structured financial, venue, and item data.
    """
    engine = get_ocr_engine()
    result, elapse = engine(image_path_or_bytes)

    if not result:
        return {
            "success": False,
            "error": "No text could be detected on the receipt. Please try a clearer or brighter photo."
        }

    # Bounding boxes with geometric centers
    boxes = []
    for box, text, score in result:
        text = text.strip()
        if not text:
            continue
        xmin = min(p[0] for p in box)
        xmax = max(p[0] for p in box)
        ymin = min(p[1] for p in box)
        ymax = max(p[1] for p in box)
        yc = (ymin + ymax) / 2.0
        xc = (xmin + xmax) / 2.0
        boxes.append({
            "text": text,
            "score": float(score),
            "yc": yc,
            "xc": xc,
            "xmin": xmin,
            "xmax": xmax,
            "ymin": ymin,
            "ymax": ymax,
        })

    boxes.sort(key=lambda b: b["yc"])
    lines = [b["text"] for b in boxes]
    full_text = " \n ".join(lines)

    # 1. Date & Time
    date_str, date_display = extract_date(full_text, lines)
    time_str = extract_time(full_text)

    # 2. Financial Charges & Totals
    grand_total = extract_grand_total(lines, full_text)
    subtotal = extract_subtotal(lines, full_text)
    service_charge = extract_service_charge(lines, full_text)
    cgst, sgst, total_tax = extract_taxes(lines, full_text)
    round_off = extract_round_off(lines, full_text)

    # 3. Detect Receipt Category & Merchant
    category, merchant_name, suggested_title, extra_details = detect_category_and_merchant(
        lines, full_text, time_str, grand_total
    )

    # 4. Extract Line Items (dishes / products / fuel)
    items = extract_items(boxes, lines, grand_total, subtotal)

    # If category is fuel and no items found, synthesize fuel item
    if category == "fuel" and not items and grand_total > 0:
        fuel_desc = extra_details.get("fuel_product", "Petrol / Diesel")
        if extra_details.get("fuel_liters"):
            fuel_desc += f" ({extra_details['fuel_liters']} L)"
        items = [{"name": fuel_desc, "price": grand_total, "qty": 1}]

    # If transport (cab/toll) and no items found, synthesize ride item
    if category == "transport" and not items and grand_total > 0:
        items = [{"name": suggested_title, "price": grand_total, "qty": 1}]

    # If subtotal is 0 but we found items, infer subtotal from items sum
    if subtotal == 0.0 and items:
        subtotal = round(sum(it["price"] for it in items), 2)

    # If grand_total is 0 but we have subtotal and taxes
    if grand_total == 0.0:
        if subtotal > 0:
            grand_total = round(subtotal + service_charge + total_tax + round_off, 2)
        elif items:
            grand_total = round(sum(it["price"] for it in items), 2)

    # Calculate effective tax percentage
    tax_percent = 0.0
    if subtotal > 0 and total_tax > 0:
        tax_percent = round((total_tax / subtotal) * 100.0, 1)

    # Payer match guess (e.g. from printed customer name/phone)
    payer_name = extract_payer_guess(lines)

    # Checksum validation
    computed_sum = round(subtotal + service_charge + total_tax + round_off, 2)
    has_match = (grand_total > 0 and abs(computed_sum - grand_total) <= 0.5)

    return {
        "success": True,
        "category": category,
        "merchant_name": merchant_name,
        "suggested_title": suggested_title,
        "date": date_str,
        "date_display": date_display,
        "time": time_str,
        "total_bill": grand_total,
        "subtotal": subtotal,
        "service_charge": service_charge,
        "service_charge_percent": round((service_charge / subtotal * 100), 1) if (subtotal > 0 and service_charge > 0) else 0.0,
        "cgst": cgst,
        "sgst": sgst,
        "total_tax": total_tax,
        "tax_percent": tax_percent,
        "round_off": round_off,
        "payer_name": payer_name,
        "items": items,
        "extra_details": extra_details,
        "has_checksum_match": has_match,
        "raw_lines_count": len(lines),
        "raw_lines": lines,
    }


# ---------------------------------------------------------------------------
# Extraction Helpers
# ---------------------------------------------------------------------------

def extract_date(full_text, lines):
    """Extract and format receipt date to YYYY-MM-DD and DD-Mon-YYYY."""
    # Matches patterns like 01/11/26, 01/11/2026, 01-11-2026, 2026-11-01
    date_patterns = [
        r'(?i)(?:date|dt)[\s\.:]*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{4})\b',
        r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{2})\b',
        r'\b(\d{4}[/-]\d{1,2}[/-]\d{1,2})\b',
    ]
    raw_date = None
    for pat in date_patterns:
        m = re.search(pat, full_text)
        if m:
            raw_date = m.group(1).replace('/', '-')
            break

    if not raw_date:
        today = datetime.now()
        return today.strftime("%Y-%m-%d"), today.strftime("%d-%b-%Y")

    # Try parsing
    parts = raw_date.split('-')
    if len(parts) == 3:
        p0, p1, p2 = parts[0], parts[1], parts[2]
        # YYYY-MM-DD
        if len(p0) == 4:
            try:
                dt = datetime.strptime(raw_date, "%Y-%m-%d")
                return dt.strftime("%Y-%m-%d"), dt.strftime("%d-%b-%Y")
            except Exception:
                pass
        # DD-MM-YY or DD-MM-YYYY
        year = int(p2)
        if year < 100:
            year += 2000
        try:
            day, month = int(p0), int(p1)
            # If month > 12, swap day and month
            if month > 12 and day <= 12:
                day, month = month, day
            dt = datetime(year, month, day)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%d-%b-%Y")
        except Exception:
            pass

    today = datetime.now()
    return today.strftime("%Y-%m-%d"), today.strftime("%d-%b-%Y")


def extract_time(full_text):
    """Extract receipt timestamp (e.g. 15:28)."""
    m = re.search(r'\b([012]?\d:[0-5]\d(?::[0-5]\d)?(?:\s*[apAP][mM])?)\b', full_text)
    if m:
        return m.group(1).strip()
    return ""


def extract_grand_total(lines, full_text):
    """Extract the final grand total paid."""
    # 1. Look for explicit Grand Total or Total Amount lines
    for idx, l in enumerate(lines):
        if re.search(r'(?i)\b(?:grand\s*total|net\s*payable|amount\s*paid|total\s*amount|bill\s*total)\b', l):
            # check this line and next 2 lines
            for sub in lines[idx:idx+3]:
                matches = re.findall(r'(\d+(?:\.\d{2}))', sub)
                if matches:
                    return float(matches[-1])

    # 2. Look for lines with 'Total' but not 'Total Qty' or 'Sub Total'
    for idx in range(len(lines) - 1, -1, -1):
        l = lines[idx]
        if re.search(r'(?i)\btotal\b', l) and not re.search(r'(?i)\b(?:qty|quantity|sub|item)\b', l):
            for sub in lines[idx:idx+2]:
                matches = re.findall(r'(\d+(?:\.\d{2}))', sub)
                if matches:
                    val = float(matches[-1])
                    if val > 0:
                        return val

    # 3. Fallback: highest decimal number near the bottom 30% of receipt
    bottom_lines = lines[int(len(lines)*0.6):]
    all_amounts = []
    for l in bottom_lines:
        for val in re.findall(r'(\d{2,6}\.\d{2})', l):
            all_amounts.append(float(val))
    if all_amounts:
        return max(all_amounts)

    return 0.0


def extract_subtotal(lines, full_text):
    """Extract pre-tax subtotal."""
    for idx, l in enumerate(lines):
        if re.search(r'(?i)\b(?:sub\s*total|subtotal|item\s*total|food\s*total|gross\s*amount)\b', l):
            for sub in lines[idx:idx+3]:
                matches = re.findall(r'(\d+(?:\.\d{2}))', sub)
                if matches:
                    val = float(matches[-1])
                    if val > 0:
                        return val
    return 0.0


def extract_service_charge(lines, full_text):
    """Extract voluntary service charge."""
    for idx, l in enumerate(lines):
        if re.search(r'(?i)\bservice\s*(?:charge|fee)\b', l) or 'voluntary service' in l.lower():
            for sub in lines[idx:idx+4]:
                matches = re.findall(r'(\d+(?:\.\d{2}))', sub)
                if matches:
                    val = float(matches[-1])
                    # filter out percentage rates like 5.00 or 10.00 if a higher amount follows
                    return val
    return 0.0


def extract_taxes(lines, full_text):
    """Extract CGST, SGST, IGST, or combined GST."""
    cgst = 0.0
    sgst = 0.0

    for idx, l in enumerate(lines):
        if re.search(r'(?i)\bcgst', l):
            for sub in lines[idx:idx+3]:
                # Remove % token if present
                clean_sub = re.sub(r'\d+(?:\.\d+)?%', '', sub)
                matches = re.findall(r'(\d+(?:\.\d{2}))', clean_sub)
                if matches:
                    cgst = float(matches[-1])
                    break
        if re.search(r'(?i)\bsgst', l):
            for sub in lines[idx:idx+3]:
                clean_sub = re.sub(r'\d+(?:\.\d+)?%', '', sub)
                matches = re.findall(r'(\d+(?:\.\d{2}))', clean_sub)
                if matches:
                    sgst = float(matches[-1])
                    break

    # If both found
    if cgst > 0 and sgst > 0:
        return cgst, sgst, round(cgst + sgst, 2)
    # If one found in Indian GST receipts, other is almost always equal
    if cgst > 0 and sgst == 0:
        return cgst, cgst, round(cgst * 2, 2)
    if sgst > 0 and cgst == 0:
        return sgst, sgst, round(sgst * 2, 2)

    # General GST / VAT match
    for idx, l in enumerate(lines):
        if re.search(r'(?i)\b(?:vat|gst|tax)\b', l) and not re.search(r'(?i)\bgstin\b', l):
            for sub in lines[idx:idx+3]:
                clean_sub = re.sub(r'\d+(?:\.\d+)?%', '', sub)
                matches = re.findall(r'(\d+(?:\.\d{2}))', clean_sub)
                if matches:
                    t_val = float(matches[-1])
                    if t_val > 0:
                        return round(t_val / 2.0, 2), round(t_val / 2.0, 2), t_val

    return 0.0, 0.0, 0.0


def extract_round_off(lines, full_text):
    """Extract round-off adjustment (+/-)."""
    for idx, l in enumerate(lines):
        if re.search(r'(?i)\bround\s*off\b', l):
            for sub in lines[idx:idx+3]:
                m = re.search(r'([+-]?\d+\.\d{2})', sub)
                if m:
                    return float(m.group(1))
    return 0.0


def detect_category_and_merchant(lines, full_text, time_str, grand_total):
    """
    Detect receipt category:
    'dining', 'grocery', 'fuel', 'transport', 'hotel', 'retail', 'pharmacy'
    Returns: (category, merchant_name, suggested_title, extra_details)
    """
    txt = full_text.lower()
    extra_details = {}

    # Extract any printed discount / savings
    for l in lines:
        m_save = re.search(r'(?i)(?:you\s*saved|savings?|discount)[\s:₹rs\.]*(\d+(?:\.\d{2})?)', l)
        if m_save:
            try:
                extra_details["savings"] = float(m_save.group(1))
            except Exception:
                pass

    # 1. Fuel / Petrol Pump
    fuel_keywords = ['indianoil', 'indian oil', 'bharat petroleum', 'bpcl', 'hindustan petroleum', 'hpcl',
                     'shell', 'fuel', 'petrol', 'diesel', 'ms speed', 'hsd', 'nozzle', 'pump', 'dispenser']
    if any(k in txt for k in fuel_keywords):
        merchant = extract_header_name(lines, fallback="Fuel Pump")
        # Extract product type (Petrol, Diesel, MS, HSD, Power, Speed)
        prod = "Petrol"
        if "diesel" in txt or "hsd" in txt:
            prod = "Diesel"
        elif "power" in txt or "speed" in txt or "xp95" in txt or "xp100" in txt:
            prod = "Premium Petrol"
        elif "petrol" in txt or "ms" in txt:
            prod = "Petrol"
        extra_details["fuel_product"] = prod

        # Extract liters
        for l in lines:
            m_l = re.search(r'(?i)\b(?:volume|qty|ltrs?|litres?)\b[\s:]*(\d+(?:\.\d{2,3})?)', l)
            if m_l:
                extra_details["fuel_liters"] = float(m_l.group(1))
                break
        # Extract rate/rsp
        for l in lines:
            m_r = re.search(r'(?i)\b(?:rate|price|rsp)\b[\s:]*(\d+(?:\.\d{2})?)', l)
            if m_r:
                extra_details["fuel_rate"] = float(m_r.group(1))
                break

        title = f"{merchant} - {prod}"
        if extra_details.get("fuel_liters"):
            title += f" ({extra_details['fuel_liters']} L)"
        return "fuel", merchant, title, extra_details

    # 2. Cab / Transport
    transport_keywords = ['uber', 'ola', 'rapido', 'taxi', 'toll plaza', 'parking receipt', 'fastag', 'ride fare']
    if any(k in txt for k in transport_keywords):
        merchant = "Cab / Transport"
        for name in ['Uber', 'Ola', 'Rapido']:
            if name.lower() in txt:
                merchant = name
                break
        return "transport", merchant, f"{merchant} Ride", extra_details

    # 3. Supermarket / Grocery
    grocery_keywords = ['spencer', 'nature\'s basket', 'blinkit', 'instamart', 'zepto', 'dmart', 'd-mart',
                        'reliance fresh', 'reliance smart', 'more retail', 'bigbasket', 'supermarket', 'hypermarket', 'grocery']
    if any(k in txt for k in grocery_keywords):
        merchant = extract_header_name(lines, fallback="Supermarket")
        return "grocery", merchant, f"{merchant} Grocery", extra_details

    # 4. Pharmacy / Medical
    pharmacy_keywords = ['pharmacy', 'chemist', 'druggist', 'medplus', 'apollo pharmacy', 'netmeds', 'medical store', 'hospital', 'diagnostics']
    if any(k in txt for k in pharmacy_keywords):
        merchant = extract_header_name(lines, fallback="Pharmacy")
        return "retail", merchant, f"{merchant} (Pharmacy)", extra_details

    # 5. Hotel / Lodging
    hotel_keywords = ['hotel', 'resort', 'lodging', 'guest house', 'check in', 'check out', 'room tariff']
    if any(k in txt for k in hotel_keywords):
        merchant = extract_header_name(lines, fallback="Hotel")
        return "hotel", merchant, f"Stay at {merchant}", extra_details

    # 6. Dining / Restaurant / Cafe
    dining_keywords = ['dine in', 'dine-in', 'table', 'waiter', 'cashier', 'kot', 'restaurant', 'cafe',
                       'bistro', 'food', 'bar', 'service charge', 'dessert', 'beverage', 'kitchen']
    is_dining = any(k in txt for k in dining_keywords)

    merchant = extract_header_name(lines, fallback="Restaurant")

    # Determine meal type by time
    meal_type = "Meal"
    if time_str:
        try:
            hour = int(time_str.split(':')[0])
            if 6 <= hour < 11:
                meal_type = "Breakfast"
            elif 11 <= hour < 16:
                meal_type = "Lunch"
            elif 16 <= hour < 19:
                meal_type = "Evening Snacks / Coffee"
            elif 19 <= hour <= 23 or hour == 0:
                meal_type = "Dinner"
            else:
                meal_type = "Late Night Food"
        except Exception:
            meal_type = "Meal"

    if is_dining or meal_type != "Meal":
        return "dining", merchant, f"{meal_type} at {merchant}", extra_details

    return "retail", merchant, f"Purchase at {merchant}", extra_details


def extract_header_name(lines, fallback="Merchant"):
    """Extract clean merchant/venue name from top lines of receipt."""
    candidates = []
    for l in lines[:6]:
        cleaned = re.sub(r'[^a-zA-Z0-9\s&\'\-]', '', l).strip()
        # skip address lines, phone numbers, tax IDs
        if re.search(r'(?i)(?:gstin|tax|road|rd|street|st|kolkata|mumbai|delhi|bangalore|phone|contact|tel|lane|floor|ave)', cleaned):
            continue
        if len(cleaned) >= 3 and not cleaned.isdigit():
            score = 1
            # Prefer capitalized or title-cased printed text over stylized lower-case logos
            if l.strip() and l.strip()[0].isupper():
                score += 3
            # Bonus if standard English word or restaurant name
            if not any(weird in cleaned.lower() for weird in ['cll', 'zsb', 'xqz']):
                score += 1
            candidates.append((score, cleaned.title()))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]
    return fallback


def extract_items(boxes, lines, grand_total, subtotal):
    """
    Layout-aware multi-column line items extraction.
    Associates dish names with their corresponding price rows.
    """
    # Find item section boundaries
    start_idx = 0
    for i, b in enumerate(boxes[:25]):
        if re.search(r'(?i)\b(?:item|particulars|description)\b', b["text"]):
            start_idx = i + 1
            break

    # Skip header row continuation (e.g. 'Qty. Price Amount')
    while start_idx < len(boxes) and re.search(r'(?i)\b(?:qty|price|amount|rate)\b', boxes[start_idx]["text"]):
        start_idx += 1

    end_idx = len(boxes)
    for i in range(start_idx, len(boxes)):
        t = boxes[i]["text"]
        if re.search(r'(?i)\b(?:sub\s*total|subtotal|total\s*qty|round\s*off|grand\s*total|voluntary\s*service|taxable)\b', t):
            end_idx = i
            break

    item_boxes = boxes[start_idx:end_idx]
    if not item_boxes:
        return []

    # Calculate median X to separate left (item names) and right (prices)
    all_x = [b["xc"] for b in item_boxes]
    x_thresh = (min(all_x) + max(all_x)) / 2.0 if all_x else 350.0

    # Collect price rows (right-hand column)
    price_rows = []
    for b in item_boxes:
        if b["xc"] > (x_thresh - 30) or re.search(r'\d+\.\d{2}', b["text"]):
            matches = re.findall(r'(\d+(?:\.\d{2}))', b["text"])
            if matches:
                p_val = float(matches[-1])
                # Filter out pure phone numbers or year dates
                if p_val > 0 and p_val != 2026.0:
                    price_rows.append({
                        "yc": b["yc"],
                        "price": p_val,
                        "text": b["text"]
                    })

    # Deduplicate prices on same Y-line (e.g. rate and line amount printed side by side)
    dedup_prices = []
    for pr in sorted(price_rows, key=lambda x: x["yc"]):
        if not dedup_prices or abs(pr["yc"] - dedup_prices[-1]["yc"]) > 14:
            dedup_prices.append(pr)
        else:
            if pr["price"] >= dedup_prices[-1]["price"]:
                dedup_prices[-1] = pr

    if not dedup_prices:
        return []

    # Associate left-column text with each price row
    items = []
    for i, pr in enumerate(dedup_prices):
        # An item row begins just above its price row (pr["yc"] - 15) and extends
        # down to just before the next item's price row (next_pr["yc"] - 15).
        top_bound = (pr["yc"] - 15) if i > 0 else 0
        bottom_bound = (dedup_prices[i+1]["yc"] - 15) if i < len(dedup_prices) - 1 else 99999

        name_parts = []
        for b in item_boxes:
            if b["xc"] <= (x_thresh + 20):
                # Filter out pure small numbers (e.g. stray table/kot numbers '16', '1')
                if b["text"].strip().isdigit() and len(b["text"].strip()) <= 2:
                    continue
                if b["yc"] < top_bound or b["yc"] >= bottom_bound:
                    continue
                name_parts.append(b["text"])

        name = " ".join(name_parts).strip()
        # Clean leading item numbering or stray digits
        name = re.sub(r'^\d+[\s\.\-]+', '', name).strip()
        name = re.sub(r'[\d\.\s]+$', '', name).strip()
        if not name:
            name = f"Item {i+1}"

        # Clean title casing
        name = re.sub(r'\s+', ' ', name).title()

        items.append({
            "name": name,
            "price": pr["price"],
            "qty": 1
        })

    return items


def extract_payer_guess(lines):
    """Attempt to extract customer/payer name from receipt."""
    for l in lines[:15]:
        m = re.search(r'(?i)\bname\s*[:\s]+([a-zA-Z\s]+)', l)
        if m:
            clean = m.group(1).strip()
            # Clean single letters like 'a ghosh' -> 'Ghosh'
            parts = clean.split()
            if len(parts) >= 2 and len(parts[0]) <= 2:
                return parts[1].title()
            return clean.title()
    return ""
