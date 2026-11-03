from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, has_request_context
import json
import os

app = Flask(__name__)
app.secret_key = "change-this-in-production"

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_FILE = os.path.join(DATA_DIR, "db.json")

TWOPLACES = Decimal("0.01")
AVATARS = ["🧑", "👩", "👨", "🧑‍💻", "👩‍💻", "🧔", "👱", "🧒", "👩‍🎨", "🧑‍🚀"]
EVENT_TYPES = {
    "dinner": {"icon": "🍽️", "label": "Dinner"},
    "lunch": {"icon": "🥗", "label": "Lunch"},
    "breakfast": {"icon": "🥞", "label": "Breakfast"},
    "drinks": {"icon": "🍻", "label": "Drinks"},
    "resort": {"icon": "🏨", "label": "Resort / Stay"},
    "transport": {"icon": "🚗", "label": "Transport"},
    "activity": {"icon": "🎢", "label": "Activity"},
    "shopping": {"icon": "🛍️", "label": "Shopping"},
    "other": {"icon": "📦", "label": "Other"},
}


# ---------------------------------------------------------------------------
# Disk Persistence Layer (Safe, permanent file storage in data/db.json)
# ---------------------------------------------------------------------------

def load_db():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {
                        "members": data.get("members", []),
                        "trips": data.get("trips", []),
                        "groups": data.get("groups", []),
                    }
        except Exception as e:
            print(f"Error loading DB: {e}")
    return {"members": [], "trips": [], "groups": []}


def save_db(data):
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR, exist_ok=True)
    temp_file = DB_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(temp_file, DB_FILE)


def sync_db():
    """Ensure session is populated from disk db.json (the permanent single source of truth)."""
    db = load_db()
    if not has_request_context():
        return
    session["members"] = db.get("members", [])
    session["trips"] = db.get("trips", [])

    if db.get("trips") or db.get("members"):
        session["members"] = db.get("members", [])
        session["trips"] = db.get("trips", [])


@app.before_request
def before_request_hook():
    init_state()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def money(value):
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def init_state():
    sync_db()
    if has_request_context():
        session.setdefault("members", [])
        session.setdefault("trips", [])


def get_members():
    sync_db()
    db = load_db()
    if db.get("members"):
        return db["members"]
    if has_request_context():
        return session.get("members", [])
    return []


def get_trips():
    sync_db()
    db = load_db()
    if db.get("trips"):
        return db["trips"]
    if has_request_context():
        return session.get("trips", [])
    return []


def save_trips(trips):
    db = load_db()
    db["trips"] = trips
    save_db(db)
    if has_request_context():
        session["trips"] = trips
        session.modified = True


def save_members(members):
    db = load_db()
    db["members"] = members
    save_db(db)
    if has_request_context():
        session["members"] = members
        session.modified = True


def get_used_member_ids(trips=None):
    if trips is None:
        trips = get_trips()
    used_ids = set()
    for trip in trips:
        for ev in trip.get("events", []):
            for exp in ev.get("expenses", []):
                if exp.get("paid_by"):
                    used_ids.add(exp["paid_by"])
                for c in exp.get("consumers", []):
                    used_ids.add(c)
                for it in exp.get("items", []):
                    if it.get("paid_by"):
                        used_ids.add(it["paid_by"])
                    for c in it.get("consumed_by", []):
                        used_ids.add(c)
        for s_key in trip.get("settlements", {}).keys():
            parts = s_key.split("_")
            for p in parts:
                if p:
                    used_ids.add(p)
    return used_ids


def get_unused_members(all_members=None, trips=None):
    if all_members is None:
        all_members = get_members()
    used_ids = get_used_member_ids(trips)
    return [m for m in all_members if m["id"] not in used_ids]


def cleanup_unused_members(trips=None):
    db = load_db()
    all_members = db.get("members", [])
    used_ids = get_used_member_ids(trips if trips is not None else db.get("trips", []))
    remaining = [m for m in all_members if m["id"] in used_ids]
    removed = [m for m in all_members if m["id"] not in used_ids]
    if len(remaining) != len(all_members):
        db["members"] = remaining
        save_db(db)
        if has_request_context():
            session["members"] = remaining
            session.modified = True
    return removed


def get_groups():
    sync_db()
    db = load_db()
    groups = db.get("groups", [])
    if not groups:
        trips = db.get("trips", [])
        seen_combos = set()
        inferred = []
        for t in trips:
            if not t.get("is_general") and t.get("name"):
                m_list = get_trip_members(t, db.get("members", []))
                mids = [m["id"] for m in m_list]
                if mids and len(mids) > 1:
                    combo_key = tuple(sorted(mids))
                    if combo_key not in seen_combos:
                        seen_combos.add(combo_key)
                        inferred.append({
                            "id": f"group_{t['id'][:8]}",
                            "name": f"{t['name']} Gang",
                            "member_ids": mids,
                        })
        if inferred:
            groups = inferred
            db["groups"] = groups
            save_db(db)
    return groups


def save_groups(groups):
    db = load_db()
    db["groups"] = groups
    save_db(db)


def get_trip_members(trip, all_members=None):
    """Return the list of member objects belonging to this trip."""
    if all_members is None:
        all_members = get_members()
    mmap = {m["id"]: m for m in all_members}
    
    t_mids = trip.get("member_ids")
    if t_mids:
        found = [mmap[mid] for mid in t_mids if mid in mmap]
        if found:
            return found

    # Fallback for legacy trips: find any members referenced in events
    used_ids = set()
    for ev in trip.get("events", []):
        for exp in ev.get("expenses", []):
            if exp.get("paid_by"):
                used_ids.add(exp["paid_by"])
            for c in exp.get("consumers", []):
                used_ids.add(c)
            for it in exp.get("items", []):
                if it.get("paid_by"):
                    used_ids.add(it["paid_by"])
                for c in it.get("consumed_by", []):
                    used_ids.add(c)

    if used_ids:
        members_in_trip = [mmap[mid] for mid in used_ids if mid in mmap]
        trip["member_ids"] = [m["id"] for m in members_in_trip]
        update_trip(trip)
        return members_in_trip

    return all_members


def update_trip(trip):
    trips = get_trips()
    for idx, t in enumerate(trips):
        if t["id"] == trip["id"]:
            trips[idx] = trip
            break
    else:
        trips.append(trip)
    save_trips(trips)


def get_trip(trip_id):
    return next((t for t in get_trips() if t["id"] == trip_id), None)


def find_member(member_id):
    return next((m for m in get_members() if m["id"] == member_id), None)


def member_map_for(members):
    return {m["id"]: m for m in members}


def ensure_general_trip():
    """Ensure there's at least one trip if the database is completely empty."""
    trips = get_trips()
    if trips:
        return trips[0]
    general = {
        "id": str(uuid4()),
        "name": "General",
        "date": "",
        "is_general": True,
        "events": [],
        "settlements": {},
    }
    trips.append(general)
    save_trips(trips)
    return general


def get_all_expenses():
    """Flatten all expenses across all trips and events."""
    expenses = []
    for trip in get_trips():
        for event in trip.get("events", []):
            for expense in event.get("expenses", []):
                expenses.append({
                    **expense,
                    "trip_id": trip["id"],
                    "trip_name": trip["name"],
                    "event_id": event["id"],
                    "event_name": event["name"],
                })
    return expenses


# ---------------------------------------------------------------------------
# Restaurant / Dineout Split Calculator
# ---------------------------------------------------------------------------

def compute_restaurant_split(expense, members):
    """
    Compute per-person balances for a restaurant/dineout expense.
    The total bill entered is inclusive of taxes.
    Taxes are reverse-calculated, discount is applied on the tax-inclusive bill,
    and tax can be split equally or proportionally.

    Returns dict of {member_id: net_amount} where positive = owed money,
    negative = owes money.
    """
    total_bill = money(expense.get("total_bill", "0"))
    consumers = list(expense.get("consumers", []))
    paid_by = expense.get("paid_by")
    num_consumers = len(consumers)
    if num_consumers == 0:
        return {}

    if expense.get("is_treat"):
        # Payer treated everyone. No one owes anything back.
        return {m["id"]: Decimal("0.00") for m in members}

    # Step 1: Calculate pre-tax food bill, service charge, and taxes
    # The total_bill entered is from the paper bill (Grand Total).
    service_charge = money(Decimal(str(expense.get("service_charge", "0") or "0")))
    tax_pct = Decimal(str(expense.get("tax_percent", "0") or "0"))
    tax_amount_input = Decimal(str(expense.get("tax_amount", "0") or "0"))

    if tax_amount_input > 0:
        tax_amount = money(tax_amount_input)
        food_bill = money(max(Decimal("0.00"), total_bill - service_charge - tax_amount))
    elif tax_pct > 0:
        taxable_base = money(max(Decimal("0.00"), total_bill - service_charge))
        food_bill = money(taxable_base / (Decimal("1") + tax_pct / Decimal("100")))
        tax_amount = money(max(Decimal("0.00"), taxable_base - food_bill))
    else:
        tax_amount = Decimal("0.00")
        food_bill = money(max(Decimal("0.00"), total_bill - service_charge))

    # Step 2: Apply discount percentage on the bill inclusive of taxes and add convenience fee
    discount_pct = Decimal(str(expense.get("discount_percent", "0") or "0"))
    discount_amount = money(total_bill * discount_pct / Decimal("100"))
    convenience_fee = money(Decimal(str(expense.get("convenience_fee", "0") or "0")))
    net_bill = money(max(Decimal("0.00"), total_bill - discount_amount + convenience_fee))

    # Net food, net service charge, and net tax after discount
    discount_factor = (Decimal("1") - discount_pct / Decimal("100")) if discount_pct < 100 else Decimal("0")
    net_food = money(food_bill * discount_factor)
    net_service = money(service_charge * discount_factor)
    net_tax = money(max(Decimal("0.00"), net_bill - net_food - net_service))

    # Step 3: Handle cover charge
    cover_total = Decimal(str(expense.get("cover_charge_total", "0") or "0"))
    cover_per_person_input = Decimal(str(expense.get("cover_charge_per_person", "0") or "0"))
    cover_paid_by = expense.get("cover_charge_paid_by") or paid_by

    if cover_per_person_input > 0 and cover_total == 0:
        cover_total = money(cover_per_person_input * Decimal(num_consumers))
    else:
        cover_total = money(cover_total)

    # Step 4: Handle dineout coins
    coins_amount = money(Decimal(str(expense.get("dineout_coins", "0") or "0")))
    coins_by = expense.get("dineout_coins_by") or paid_by
    include_coins = bool(expense.get("include_coins_in_split", False))

    # Step 5: Determine target bill to split among consumers
    if include_coins and coins_amount > 0:
        target_bill = money(max(Decimal("0.00"), net_bill - coins_amount))
        if net_bill > 0:
            target_food = money(net_food * (target_bill / net_bill))
            target_tax = money(target_bill - target_food)
        else:
            target_food = Decimal("0.00")
            target_tax = Decimal("0.00")
    else:
        target_bill = net_bill
        target_food = net_food
        target_tax = net_tax

    # Step 6: Determine per-person share
    items = expense.get("items", [])
    has_items = bool(items) and any(items)
    tax_split_method = expense.get("tax_split_method", "equal")

    if has_items:
        # Separate items into outside items vs bill items
        # An outside item is explicitly marked is_outside=True and has paid_by != paid_by
        outside_items = [it for it in items if it.get("is_outside") and it.get("paid_by") and it.get("paid_by") != paid_by]
        bill_items = [it for it in items if it not in outside_items]

        all_item_consumers = set(consumers)
        for item in items:
            for cid in item.get("consumed_by", []):
                if cid:
                    all_item_consumers.add(cid)
            if item.get("paid_by"):
                all_item_consumers.add(item["paid_by"])
        consumers = [c for c in all_item_consumers if c]
        num_consumers = len(consumers)

        person_item_totals = {c: Decimal("0.00") for c in consumers}
        bill_items_sum = Decimal("0.00")
        bill_item_payments = {c: Decimal("0.00") for c in consumers}
        for item in bill_items:
            item_price = money(item.get("price", "0"))
            item_consumers = [c for c in item.get("consumed_by", []) if c in consumers] or consumers
            per_person_item = money(item_price / Decimal(len(item_consumers)))
            for person_id in item_consumers:
                person_item_totals[person_id] += per_person_item
            bill_items_sum += item_price

            item_payer = item.get("paid_by")
            if item_payer and item_price > 0:
                bill_item_payments[item_payer] = bill_item_payments.get(item_payer, Decimal("0.00")) + item_price

        if bill_items_sum > 0:
            if tax_split_method == "equal" and num_consumers > 0:
                equal_tax_share = money(target_tax / Decimal(num_consumers))
                food_ratio = target_food / bill_items_sum
                per_person_share = {
                    c: money(person_item_totals[c] * food_ratio + equal_tax_share)
                    for c in consumers
                }
            else:
                ratio = target_bill / bill_items_sum
                per_person_share = {
                    c: money(person_item_totals[c] * ratio)
                    for c in consumers
                }
        else:
            equal_share = money(target_bill / Decimal(num_consumers))
            per_person_share = {c: equal_share for c in consumers}

        # Fix rounding drift for bill items
        total_shares = sum(per_person_share.values())
        drift = money(target_bill - total_shares)
        if drift != Decimal("0.00") and consumers:
            per_person_share[consumers[-1]] += drift

        # Now add outside items (bought separately outside the bill, e.g. counter buy)
        outside_item_payments = {c: Decimal("0.00") for c in consumers}
        for item in outside_items:
            item_price = money(item.get("price", "0"))
            item_consumers = [c for c in item.get("consumed_by", []) if c in consumers] or consumers
            share_out = money(item_price / Decimal(len(item_consumers)))
            for cid in item_consumers:
                per_person_share[cid] = per_person_share.get(cid, Decimal("0.00")) + share_out
            out_drift = money(item_price - share_out * Decimal(len(item_consumers)))
            if out_drift != Decimal("0.00") and item_consumers:
                per_person_share[item_consumers[-1]] += out_drift

            item_payer = item.get("paid_by")
            if item_payer and item_price > 0:
                outside_item_payments[item_payer] = outside_item_payments.get(item_payer, Decimal("0.00")) + item_price
    else:
        # Equal split
        equal_share = money(target_bill / Decimal(num_consumers))
        per_person_share = {c: equal_share for c in consumers}
        drift = money(target_bill - sum(per_person_share.values()))
        if drift != Decimal("0.00") and consumers:
            per_person_share[consumers[-1]] += drift
        bill_item_payments = {}
        outside_item_payments = {}

    # Step 7: Build net balances
    all_parties = set(consumers) | {paid_by, cover_paid_by, coins_by} | set(bill_item_payments.keys()) | set(outside_item_payments.keys())
    balances = {p: Decimal("0.00") for p in all_parties if p}

    # Debit each consumer for their share
    for c in consumers:
        balances[c] -= per_person_share[c]

    # Calculate cash paid at restaurant table for the bill
    paid_at_restaurant = money(max(Decimal("0.00"), net_bill - cover_total - coins_amount))

    # Bill items paid by others reduce what table payer paid at table
    others_bill_paid = sum(amt for p, amt in bill_item_payments.items() if p != paid_by)
    table_payer_cash = money(max(Decimal("0.00"), paid_at_restaurant - others_bill_paid))

    # Credit table payer
    if paid_by:
        balances[paid_by] = balances.get(paid_by, Decimal("0.00")) + table_payer_cash

    # Credit non-table payers for bill items they paid
    for p, amt in bill_item_payments.items():
        if p != paid_by and amt > 0:
            balances[p] = balances.get(p, Decimal("0.00")) + amt

    # Credit payers for outside items they paid
    for p, amt in outside_item_payments.items():
        if amt > 0:
            balances[p] = balances.get(p, Decimal("0.00")) + amt

    # Credit cover charge payer (advance booking guarantee)
    if cover_total > 0 and cover_paid_by:
        balances[cover_paid_by] = balances.get(cover_paid_by, Decimal("0.00")) + cover_total

    # Credit coins owner if coins are personal (reimbursed by group)
    if not include_coins and coins_amount > 0 and coins_by:
        balances[coins_by] = balances.get(coins_by, Decimal("0.00")) + coins_amount

    return {k: money(v) for k, v in balances.items()}


# ---------------------------------------------------------------------------
# Global Balance Computation
# ---------------------------------------------------------------------------

def compute_all_balances(members, trips, apply_settlements=False):
    """Compute balances across all trips and events, optionally factoring in recorded settlements."""
    balances = {m["id"]: Decimal("0") for m in members}

    for trip in trips:
        for event in trip.get("events", []):
            for expense in event.get("expenses", []):
                if expense.get("is_treat"):
                    continue

                if expense.get("mode") == "restaurant":
                    expense_balances = compute_restaurant_split(expense, members)
                    for mid, amt in expense_balances.items():
                        if mid in balances:
                            balances[mid] += amt
                else:
                    # Simple mode
                    amount = money(expense.get("amount", "0"))
                    exp_consumers = expense.get("consumers", [])
                    items = expense.get("items", [])
                    paid_by = expense.get("paid_by")
                    if not exp_consumers and not items:
                        continue

                    if items and any(items):
                        # Simple mode with itemized split
                        all_c = set(exp_consumers)
                        for it in items:
                            for cid in it.get("consumed_by", []):
                                if cid:
                                    all_c.add(cid)
                            if it.get("paid_by"):
                                all_c.add(it["paid_by"])
                        consumers_list = [c for c in all_c if c] or exp_consumers
                        person_item_totals = {c: Decimal("0.00") for c in consumers_list}
                        items_sum = Decimal("0.00")
                        item_payments = {c: Decimal("0.00") for c in consumers_list}
                        for it in items:
                            iprice = money(it.get("price", "0"))
                            iconsumers = [c for c in it.get("consumed_by", []) if c in consumers_list] or consumers_list
                            share_item = money(iprice / Decimal(len(iconsumers)))
                            for cid in iconsumers:
                                person_item_totals[cid] += share_item
                            items_sum += iprice
                            ipayer = it.get("paid_by")
                            if ipayer and iprice > 0:
                                item_payments[ipayer] = item_payments.get(ipayer, Decimal("0.00")) + iprice

                        ratio = (amount / items_sum) if items_sum > 0 else Decimal("1")
                        per_person_shares = {c: money(person_item_totals[c] * ratio) for c in consumers_list}
                        drift = money(amount - sum(per_person_shares.values()))
                        if drift != Decimal("0") and consumers_list:
                            per_person_shares[consumers_list[-1]] += drift

                        others_paid = sum(amt for p, amt in item_payments.items() if p != paid_by)
                        main_payer_credit = money(max(Decimal("0.00"), amount - others_paid))
                        if paid_by in balances:
                            balances[paid_by] += main_payer_credit
                        for p, amt in item_payments.items():
                            if p != paid_by and p in balances and amt > 0:
                                balances[p] += amt
                        for cid in consumers_list:
                            if cid in balances:
                                balances[cid] -= per_person_shares[cid]
                    else:
                        if not exp_consumers:
                            continue
                        share = money(amount / Decimal(len(exp_consumers)))
                        if paid_by in balances:
                            balances[paid_by] += amount
                        for cid in exp_consumers:
                            if cid in balances:
                                balances[cid] -= share
                        drift = money(amount - share * Decimal(len(exp_consumers)))
                        if drift != Decimal("0") and exp_consumers and exp_consumers[-1] in balances:
                            balances[exp_consumers[-1]] -= drift

    if apply_settlements:
        for trip in trips:
            for s_key, s_data in trip.get("settlements", {}).items():
                if s_data.get("settled"):
                    parts = s_key.split("_")
                    if len(parts) == 2:
                        from_id, to_id = parts
                        amt = money(s_data.get("amount", "0"))
                        if from_id in balances:
                            balances[from_id] += amt
                        if to_id in balances:
                            balances[to_id] -= amt

    rows = []
    for m in members:
        bal = money(balances[m["id"]])
        # Clear fractional rounding residue (<= 0.02)
        if abs(bal) <= Decimal("0.02"):
            bal = Decimal("0.00")
        rows.append({
            "id": m["id"],
            "name": m["name"],
            "avatar": m["avatar"],
            "balance": bal,
            "is_fully_settled": (bal == Decimal("0.00")),
        })
    return rows


def compute_trip_balances(members, trip):
    """Compute balances for a single trip."""
    return compute_all_balances(members, [trip])


def simplify_transactions(balance_rows):
    creditors = []
    debtors = []
    for row in balance_rows:
        amt = money(row["balance"])
        if amt > Decimal("0.02"):
            creditors.append({"id": row["id"], "name": row["name"], "avatar": row.get("avatar", "👤"), "amount": amt})
        elif amt < Decimal("-0.02"):
            debtors.append({"id": row["id"], "name": row["name"], "avatar": row.get("avatar", "👤"), "amount": abs(amt)})

    creditors.sort(key=lambda x: x["amount"], reverse=True)
    debtors.sort(key=lambda x: x["amount"], reverse=True)

    transfers = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        pay_amount = money(min(debtors[i]["amount"], creditors[j]["amount"]))
        if pay_amount > 0:
            transfer_key = f"{debtors[i]['id']}_{creditors[j]['id']}"
            transfers.append({
                "from_id": debtors[i]["id"],
                "from": debtors[i]["name"],
                "from_avatar": debtors[i].get("avatar", "👤"),
                "to_id": creditors[j]["id"],
                "to": creditors[j]["name"],
                "to_avatar": creditors[j].get("avatar", "👤"),
                "amount": pay_amount,
                "key": transfer_key,
            })
        debtors[i]["amount"] = money(debtors[i]["amount"] - pay_amount)
        creditors[j]["amount"] = money(creditors[j]["amount"] - pay_amount)
        if debtors[i]["amount"] == 0:
            i += 1
        if creditors[j]["amount"] == 0:
            j += 1
    return transfers


# ---------------------------------------------------------------------------
# Template Filters
# ---------------------------------------------------------------------------

@app.template_filter("currency")
def currency_filter(value):
    value = money(value)
    return f"₹{value:,.2f}"


@app.template_filter("format_date")
def format_date_filter(value):
    """Format any date string to DD-Mon-YYYY (e.g. 02-Nov-2026)."""
    if not value:
        return ""
    val_str = str(value).strip()
    try:
        dt = datetime.strptime(val_str, "%Y-%m-%d")
        return dt.strftime("%d-%b-%Y")
    except Exception:
        try:
            dt = datetime.strptime(val_str, "%d/%m/%Y")
            return dt.strftime("%d-%b-%Y")
        except Exception:
            return val_str


@app.template_filter("tojson_safe")
def tojson_safe_filter(value):
    from markupsafe import Markup

    def default_serializer(o):
        if isinstance(o, Decimal):
            return str(o)
        raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")

    return Markup(json.dumps(value, default=default_serializer))


@app.context_processor
def inject_globals():
    return {"event_types": EVENT_TYPES}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
@app.route("/dashboard")
def dashboard():
    init_state()
    members = get_members()
    if not members:
        return redirect(url_for("setup"))

    trips = get_trips()
    balances = compute_all_balances(members, trips, apply_settlements=True)

    # Add stats to each trip
    trip_stats = []
    for trip in trips:
        total_expenses = Decimal("0")
        expense_count = 0
        event_count = len(trip.get("events", []))
        for event in trip.get("events", []):
            for expense in event.get("expenses", []):
                expense_count += 1
                if expense.get("mode") == "restaurant":
                    total_expenses += money(expense.get("total_bill", "0"))
                else:
                    total_expenses += money(expense.get("amount", "0"))
        trip_stats.append({
            **trip,
            "trip_members": get_trip_members(trip, members),
            "total_expenses": money(total_expenses),
            "expense_count": expense_count,
            "event_count": event_count,
        })

    groups = get_groups()
    unused_members = get_unused_members(members, trips)

    return render_template(
        "dashboard.html",
        members=members,
        trips=trip_stats,
        balances=balances,
        groups=groups,
        unused_members=unused_members,
    )


@app.route("/setup", methods=["GET", "POST"])
def setup():
    init_state()
    members = get_members()
    # If members already exist, bypass setup entirely and go to Dashboard
    if members and request.method == "GET":
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        names = [n.strip() for n in request.form.getlist("names") if n.strip()]
        if len(names) < 2:
            flash("Please add at least two people.", "error")
            return redirect(url_for("setup"))

        new_members = []
        for idx, name in enumerate(names):
            new_members.append({
                "id": str(uuid4()),
                "name": name,
                "avatar": AVATARS[(len(members) + idx) % len(AVATARS)],
            })
        # NEVER wipe existing trips or data!
        save_members(members + new_members)
        return redirect(url_for("dashboard"))

    return render_template("setup.html")


@app.route("/trip/new", methods=["POST"])
def create_trip():
    init_state()
    name = request.form.get("trip_name", "").strip()
    date = request.form.get("trip_date", "").strip()
    if not name:
        flash("Please enter a trip name.", "error")
        return redirect(url_for("dashboard"))

    all_members = get_members()
    trip_roster_mode = request.form.get("trip_roster_mode", "saved_group")
    group_id = request.form.get("group_id", "").strip()
    member_ids = []

    if trip_roster_mode == "saved_group" and group_id:
        for g in get_groups():
            if g["id"] == group_id:
                member_ids = list(g.get("member_ids", []))
                break
    else:
        member_ids = request.form.getlist("member_ids")

    # Add any newly typed members (comma-separated, e.g. "Rahul, Sneha")
    new_names_raw = request.form.get("new_members", "").strip()
    if new_names_raw:
        new_names = [n.strip() for n in new_names_raw.split(",") if n.strip()]
        for idx, n in enumerate(new_names):
            new_id = str(uuid4())
            new_m = {
                "id": new_id,
                "name": n,
                "avatar": AVATARS[(len(all_members) + idx) % len(AVATARS)],
            }
            all_members.append(new_m)
            member_ids.append(new_id)
        save_members(all_members)

    # Default to all members if none selected
    if not member_ids:
        member_ids = [m["id"] for m in all_members]

    # Optionally save as a named group for future trips
    save_group_name = request.form.get("save_group_name", "").strip()
    if save_group_name:
        groups = get_groups()
        groups.append({
            "id": str(uuid4()),
            "name": save_group_name,
            "member_ids": member_ids,
        })
        save_groups(groups)

    trip = {
        "id": str(uuid4()),
        "name": name,
        "date": date,
        "is_general": False,
        "member_ids": member_ids,
        "group_name": save_group_name or "",
        "events": [],
        "settlements": {},
    }
    trips = get_trips()
    trips.append(trip)
    save_trips(trips)
    flash(f'Trip "{name}" created with {len(member_ids)} members!', "success")
    return redirect(url_for("trip_view", trip_id=trip["id"]))


@app.route("/trip/<trip_id>/members", methods=["POST"])
def manage_trip_members(trip_id):
    init_state()
    trip = get_trip(trip_id)
    if not trip:
        flash("Trip not found.", "error")
        return redirect(url_for("dashboard"))

    all_members = get_members()
    selected_ids = request.form.getlist("member_ids")

    # Add new typed names
    new_names_raw = request.form.get("new_members", "").strip()
    if new_names_raw:
        new_names = [n.strip() for n in new_names_raw.split(",") if n.strip()]
        for idx, n in enumerate(new_names):
            new_id = str(uuid4())
            new_m = {
                "id": new_id,
                "name": n,
                "avatar": AVATARS[(len(all_members) + idx) % len(AVATARS)],
            }
            all_members.append(new_m)
            selected_ids.append(new_id)
        save_members(all_members)

    if not selected_ids:
        flash("Trip must have at least one member.", "error")
        return redirect(url_for("trip_view", trip_id=trip_id))

    trip["member_ids"] = selected_ids
    update_trip(trip)
    flash("Trip members updated!", "success")
    return redirect(url_for("trip_view", trip_id=trip_id))


@app.route("/trip/<trip_id>")
def trip_view(trip_id):
    init_state()
    all_members = get_members()
    if not all_members:
        flash("Start by creating a group.", "error")
        return redirect(url_for("setup"))

    trip = get_trip(trip_id)
    if not trip:
        flash("Trip not found.", "error")
        return redirect(url_for("dashboard"))

    trip_members = get_trip_members(trip, all_members)
    mmap = member_map_for(all_members)
    balances = compute_trip_balances(trip_members, trip)
    transactions = simplify_transactions(balances)
    max_abs = max((abs(r["balance"]) for r in balances), default=Decimal("0"))

    # Enrich transactions with settlements and UPI
    settlements = trip.setdefault("settlements", {})
    for tx in transactions:
        tx_info = settlements.get(tx["key"], {})
        tx["is_settled"] = tx_info.get("settled", False)
        tx["settled_at"] = tx_info.get("settled_at", "")
        creditor = mmap.get(tx["to_id"], {})
        tx["to_upi"] = creditor.get("upi_id", "")

    # Mark fully settled members in balance cards
    for b in balances:
        member_debts = [tx for tx in transactions if tx["from_id"] == b["id"]]
        if member_debts and all(tx["is_settled"] for tx in member_debts):
            b["is_fully_settled"] = True
        else:
            b["is_fully_settled"] = False

    # Enrich events with display data
    events_view = []
    for event in trip.get("events", []):
        expenses_view = []
        for exp in event.get("expenses", []):
            ev = {**exp}
            payer = mmap.get(exp["paid_by"], {})
            ev["paid_by_name"] = payer.get("name", "Unknown")
            ev["consumer_names"] = [mmap.get(c, {}).get("name", "?") for c in exp.get("consumers", [])]

            # Enrich items with member names, avatars, and formatted price
            raw_items = exp.get("items", [])
            enriched_items = []
            for it in raw_items:
                c_ids = it.get("consumed_by", [])
                it_payer_id = it.get("paid_by")
                it_payer = mmap.get(it_payer_id, {}) if it_payer_id else {}
                enriched_items.append({
                    "name": it.get("name") or "Unnamed item",
                    "price": money(it.get("price", "0")),
                    "paid_by": it_payer_id,
                    "paid_by_name": it_payer.get("name", ""),
                    "paid_by_avatar": it_payer.get("avatar", "👤"),
                    "is_outside": it.get("is_outside", False),
                    "consumer_names": [mmap.get(cid, {}).get("name", "?") for cid in c_ids],
                    "consumer_avatars": [mmap.get(cid, {}).get("avatar", "👤") for cid in c_ids],
                })
            ev["enriched_items"] = enriched_items
            ev["has_items"] = bool(enriched_items)

            if exp.get("mode") == "restaurant":
                ev["display_amount"] = money(exp.get("total_bill", "0"))
                cover_payer = mmap.get(exp.get("cover_charge_paid_by", ""), {})
                ev["cover_paid_by_name"] = cover_payer.get("name", "")
                coins_owner = mmap.get(exp.get("dineout_coins_by", ""), {})
                ev["coins_by_name"] = coins_owner.get("name", "")
            else:
                ev["display_amount"] = money(exp.get("amount", "0"))

            expenses_view.append(ev)
        etype = EVENT_TYPES.get(event.get("type", "other"), EVENT_TYPES["other"])
        events_view.append({
            **event,
            "type_icon": etype["icon"],
            "type_label": etype["label"],
            "expenses": expenses_view,
            "total": sum(e["display_amount"] for e in expenses_view),
        })

    return render_template(
        "trip.html",
        trip=trip,
        events=events_view,
        members=trip_members,
        all_members=all_members,
        balances=balances,
        transactions=transactions,
        max_abs=max_abs,
    )


@app.route("/trip/<trip_id>/event/new", methods=["POST"])
def create_event(trip_id):
    init_state()
    trip = get_trip(trip_id)
    if not trip:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": False, "error": "Trip not found."}), 404
        flash("Trip not found.", "error")
        return redirect(url_for("dashboard"))

    data = request.get_json(silent=True) or request.form
    name = (data.get("event_name") or data.get("name") or "").strip()
    etype = data.get("event_type") or data.get("type") or "other"
    if not name:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": False, "error": "Please enter an event name."}), 400
        flash("Please enter an event name.", "error")
        return redirect(url_for("trip_view", trip_id=trip_id))

    event = {
        "id": str(uuid4()),
        "name": name,
        "type": etype if etype in EVENT_TYPES else "other",
        "expenses": [],
    }

    # If requested to save parsed receipt expense directly into event
    receipt_data = data.get("receipt_data")
    if data.get("save_expense_directly") and receipt_data:
        all_members = get_members()
        trip_members = get_trip_members(trip, all_members)
        member_ids = [m["id"] for m in trip_members]
        
        # Determine payer
        paid_by = member_ids[0] if member_ids else ""
        payer_name = receipt_data.get("payer_name")
        if payer_name:
            for m in trip_members:
                if payer_name.lower() in m["name"].lower() or m["name"].lower() in payer_name.lower():
                    paid_by = m["id"]
                    break

        cat = receipt_data.get("category", "retail")
        items = receipt_data.get("items", [])
        is_restaurant = (cat == "dining" or len(items) > 1)
        total_bill = receipt_data.get("total_bill", 0)

        if is_restaurant:
            formatted_items = []
            for it in items:
                formatted_items.append({
                    "name": it.get("name", "Item"),
                    "price": f"{float(it.get('price', 0)):.2f}",
                    "consumed_by": member_ids,
                    "paid_by": "",
                    "is_outside": False
                })
            
            exp = {
                "id": str(uuid4()),
                "mode": "restaurant",
                "description": receipt_data.get("suggested_title") or receipt_data.get("merchant_name") or name,
                "is_treat": False,
                "date": receipt_data.get("date", ""),
                "total_bill": f"{float(total_bill):.2f}",
                "discount_percent": "0",
                "convenience_fee": "0",
                "service_charge": f"{float(receipt_data.get('service_charge', 0)):.2f}",
                "tax_percent": f"{float(receipt_data.get('tax_percent', 0)):.2f}" if not receipt_data.get("total_tax") else "0",
                "tax_amount": f"{float(receipt_data.get('total_tax', 0)):.2f}" if receipt_data.get("total_tax") else "0",
                "tax_split_method": "equal",
                "cover_charge_total": "0",
                "cover_charge_per_person": "0",
                "cover_paid_by": paid_by,
                "dineout_coins": "0",
                "coins_by": paid_by,
                "include_coins_in_split": True,
                "paid_by": paid_by,
                "payer_upi": "",
                "consumers": member_ids,
                "items": formatted_items,
            }
        else:
            exp = {
                "id": str(uuid4()),
                "mode": "simple",
                "description": receipt_data.get("suggested_title") or receipt_data.get("merchant_name") or name,
                "is_treat": False,
                "date": receipt_data.get("date", ""),
                "amount": f"{float(total_bill):.2f}",
                "paid_by": paid_by,
                "payer_upi": "",
                "consumers": member_ids,
                "items": [],
            }
        event["expenses"].append(exp)

    trip.setdefault("events", []).append(event)
    update_trip(trip)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
        return jsonify({"success": True, "event": event})

    flash(f'Event "{name}" added.', "success")
    return redirect(url_for("trip_view", trip_id=trip_id))


@app.route("/trip/<trip_id>/event/<event_id>/edit", methods=["POST"])
def edit_event(trip_id, event_id):
    init_state()
    trip = get_trip(trip_id)
    if not trip:
        flash("Trip not found.", "error")
        return redirect(url_for("dashboard"))

    name = request.form.get("event_name", "").strip()
    etype = request.form.get("event_type", "other")
    if not name:
        flash("Please enter an event name.", "error")
        return redirect(url_for("trip_view", trip_id=trip_id))

    target_event = next((e for e in trip.get("events", []) if e["id"] == event_id), None)
    if target_event:
        target_event["name"] = name
        target_event["type"] = etype if etype in EVENT_TYPES else "other"
        update_trip(trip)
        flash(f'Event updated to "{name}".', "success")
    return redirect(url_for("trip_view", trip_id=trip_id))


@app.route("/trip/<trip_id>/event/<event_id>/delete", methods=["POST"])
def delete_event(trip_id, event_id):
    init_state()
    trip = get_trip(trip_id)
    if trip:
        trip["events"] = [e for e in trip.get("events", []) if e["id"] != event_id]
        update_trip(trip)
        flash("Event deleted.", "success")
    return redirect(url_for("trip_view", trip_id=trip_id))


@app.route("/trip/<trip_id>/settle/toggle", methods=["POST"])
def toggle_settlement(trip_id):
    init_state()
    trip = get_trip(trip_id)
    if not trip:
        flash("Trip not found.", "error")
        return redirect(url_for("dashboard"))

    transfer_key = request.form.get("transfer_key", "").strip()
    from_name = request.form.get("from_name", "").strip()
    to_name = request.form.get("to_name", "").strip()
    amount = request.form.get("amount", "").strip()

    settlements = trip.setdefault("settlements", {})
    curr = settlements.get(transfer_key, {})

    if curr.get("settled"):
        settlements[transfer_key] = {"settled": False}
        flash(f"Payment from {from_name} to {to_name} marked as pending.", "info")
    else:
        settlements[transfer_key] = {
            "settled": True,
            "amount": amount,
            "from_name": from_name,
            "to_name": to_name,
            "settled_at": datetime.now().strftime("%d %b, %I:%M %p"),
        }
        flash(f"Marked {from_name}'s payment of ₹{amount} to {to_name} as settled!", "success")

    update_trip(trip)
    next_url = request.form.get("next") or request.referrer or url_for("trip_view", trip_id=trip_id)
    return redirect(next_url)


@app.route("/trip/<trip_id>/add", methods=["GET", "POST"])
def add_expense(trip_id):
    init_state()
    all_members = get_members()
    if not all_members:
        flash("Start by creating a group.", "error")
        return redirect(url_for("setup"))

    trip = get_trip(trip_id)
    if not trip:
        flash("Trip not found.", "error")
        return redirect(url_for("dashboard"))

    members = get_trip_members(trip, all_members)

    # Ensure at least one event exists
    if not trip.get("events"):
        default_event = {
            "id": str(uuid4()),
            "name": "General",
            "type": "other",
            "expenses": [],
        }
        trip["events"].append(default_event)
        update_trip(trip)

    if request.method == "POST":
        action = request.form.get("action", "add")

        if action == "delete":
            expense_id = request.form.get("expense_id")
            for event in trip.get("events", []):
                event["expenses"] = [e for e in event["expenses"] if e["id"] != expense_id]
            update_trip(trip)
            flash("Expense removed.", "success")
            return redirect(url_for("trip_view", trip_id=trip_id))

        mode = request.form.get("mode", "simple")
        event_id = request.form.get("event_id", "")
        description = request.form.get("description", "").strip()

        if not description:
            flash("Please enter a description.", "error")
            return redirect(url_for("add_expense", trip_id=trip_id, mode=mode))

        # Find or create event
        target_event = None
        for event in trip.get("events", []):
            if event["id"] == event_id:
                target_event = event
                break
        if not target_event:
            target_event = trip["events"][0]

        if mode == "restaurant":
            # Parse items from JSON first
            items_json = request.form.get("items_data", "[]")
            try:
                items = json.loads(items_json)
            except Exception:
                items = []

            items_sum = sum(Decimal(str(it.get("price", "0") or "0")) for it in items)
            total_bill_raw = request.form.get("total_bill", "").strip()

            if not total_bill_raw or total_bill_raw == "0":
                if items_sum > 0:
                    total_bill = money(items_sum)
                else:
                    flash("Please enter a total bill amount or add items.", "error")
                    return redirect(url_for("add_expense", trip_id=trip_id, mode="restaurant"))
            else:
                try:
                    total_bill = money(Decimal(total_bill_raw))
                except Exception:
                    if items_sum > 0:
                        total_bill = money(items_sum)
                    else:
                        flash("Please enter a valid total bill amount.", "error")
                        return redirect(url_for("add_expense", trip_id=trip_id, mode="restaurant"))

            if total_bill <= 0:
                flash("Bill must be greater than ₹0.", "error")
                return redirect(url_for("add_expense", trip_id=trip_id, mode="restaurant"))

            paid_by = request.form.get("paid_by", "")
            if not find_member(paid_by):
                flash("Please select who paid.", "error")
                return redirect(url_for("add_expense", trip_id=trip_id, mode="restaurant"))

            consumers = [m["id"] for m in members if m["id"] in request.form.getlist("consumers")]
            for it in items:
                for cid in it.get("consumed_by", []):
                    if cid and cid not in consumers and find_member(cid):
                        consumers.append(cid)

            if not consumers:
                flash("Please select at least one person involved.", "error")
                return redirect(url_for("add_expense", trip_id=trip_id, mode="restaurant"))

            discount_pct = request.form.get("discount_percent", "0").strip() or "0"
            service_charge = request.form.get("service_charge", "0").strip() or "0"
            tax_pct = request.form.get("tax_percent", "0").strip() or "0"
            tax_amt = request.form.get("tax_amount", "0").strip() or "0"
            tax_split_method = request.form.get("tax_split_method", "equal").strip() or "equal"

            cover_total = request.form.get("cover_charge_total", "0").strip() or "0"
            cover_per_person = request.form.get("cover_charge_per_person", "0").strip() or "0"
            cover_paid_by = request.form.get("cover_charge_paid_by", paid_by)
            coins = request.form.get("dineout_coins", "0").strip() or "0"
            coins_by = request.form.get("dineout_coins_by", paid_by)
            include_coins = request.form.get("include_coins_in_split") == "on"
            convenience_fee = request.form.get("convenience_fee", "0").strip() or "0"
            payer_upi = request.form.get("payer_upi", "").strip()

            if payer_upi:
                payer_member = find_member(paid_by)
                if payer_member:
                    payer_member["upi_id"] = payer_upi
                    save_members(members)

            is_treat = request.form.get("is_treat") in ["true", "1", "on"]

            expense = {
                "id": str(uuid4()),
                "mode": "restaurant",
                "description": description,
                "is_treat": is_treat,
                "date": request.form.get("date", "").strip(),
                "total_bill": f"{total_bill:.2f}",
                "paid_by": paid_by,
                "payer_upi": payer_upi,
                "consumers": consumers,
                "discount_percent": discount_pct,
                "convenience_fee": convenience_fee,
                "service_charge": service_charge,
                "tax_percent": tax_pct,
                "tax_amount": tax_amt,
                "tax_split_method": tax_split_method,
                "cover_charge_total": cover_total,
                "cover_charge_per_person": cover_per_person,
                "cover_charge_paid_by": cover_paid_by,
                "dineout_coins": coins,
                "dineout_coins_by": coins_by,
                "include_coins_in_split": include_coins,
                "items": items,
            }
            target_event["expenses"].append(expense)
            update_trip(trip)
            flash("Restaurant bill added!", "success")
            return redirect(url_for("trip_view", trip_id=trip_id))

        else:
            # Simple mode
            items_json = request.form.get("items_data", "[]")
            try:
                items = json.loads(items_json)
            except Exception:
                items = []

            items_sum = sum(Decimal(str(it.get("price", "0") or "0")) for it in items)
            amount_raw = request.form.get("amount", "").strip()

            if not amount_raw or amount_raw == "0":
                if items_sum > 0:
                    amount = money(items_sum)
                else:
                    flash("Please enter an amount or add items.", "error")
                    return redirect(url_for("add_expense", trip_id=trip_id, mode="simple"))
            else:
                try:
                    amount = money(Decimal(amount_raw))
                except Exception:
                    if items_sum > 0:
                        amount = money(items_sum)
                    else:
                        flash("Please enter a valid amount.", "error")
                        return redirect(url_for("add_expense", trip_id=trip_id, mode="simple"))

            if amount <= 0:
                flash("Amount must be greater than ₹0.", "error")
                return redirect(url_for("add_expense", trip_id=trip_id, mode="simple"))

            paid_by = request.form.get("paid_by", "")
            if not find_member(paid_by):
                flash("Please select who paid.", "error")
                return redirect(url_for("add_expense", trip_id=trip_id, mode="simple"))

            consumers = [m["id"] for m in members if m["id"] in request.form.getlist("consumers")]
            for it in items:
                for cid in it.get("consumed_by", []):
                    if cid and cid not in consumers and find_member(cid):
                        consumers.append(cid)

            if not consumers:
                flash("Please select at least one consumer.", "error")
                return redirect(url_for("add_expense", trip_id=trip_id, mode="simple"))

            payer_upi = request.form.get("payer_upi", "").strip()
            if payer_upi:
                payer_member = find_member(paid_by)
                if payer_member:
                    payer_member["upi_id"] = payer_upi
                    save_members(members)

            is_treat = request.form.get("is_treat") in ["true", "1", "on"]

            expense = {
                "id": str(uuid4()),
                "mode": "simple",
                "description": description,
                "is_treat": is_treat,
                "date": request.form.get("date", "").strip(),
                "amount": f"{amount:.2f}",
                "paid_by": paid_by,
                "payer_upi": payer_upi,
                "consumers": consumers,
                "items": items,
            }
            target_event["expenses"].append(expense)
            update_trip(trip)
            flash("Expense added!", "success")
            return redirect(url_for("trip_view", trip_id=trip_id))

    # GET
    mode = request.args.get("mode", "simple")
    selected_event_id = request.args.get("event_id", "")
    return render_template(
        "add_expense.html",
        trip=trip,
        members=members,
        events=trip.get("events", []),
        mode=mode,
        selected_event_id=selected_event_id,
        is_edit=False,
    )


@app.route("/trip/<trip_id>/expense/<expense_id>/edit", methods=["GET", "POST"])
def edit_expense(trip_id, expense_id):
    init_state()
    all_members = get_members()
    if not all_members:
        flash("Start by creating a group.", "error")
        return redirect(url_for("setup"))

    trip = get_trip(trip_id)
    if not trip:
        flash("Trip not found.", "error")
        return redirect(url_for("dashboard"))

    members = get_trip_members(trip, all_members)

    target_event = None
    target_expense = None
    for ev in trip.get("events", []):
        for exp in ev.get("expenses", []):
            if exp["id"] == expense_id:
                target_event = ev
                target_expense = exp
                break
        if target_expense:
            break

    if not target_expense:
        flash("Expense not found.", "error")
        return redirect(url_for("trip_view", trip_id=trip_id))

    if request.method == "POST":
        new_event_id = request.form.get("event_id", target_event["id"])
        new_event = next((e for e in trip.get("events", []) if e["id"] == new_event_id), target_event)

        mode = request.form.get("mode", target_expense.get("mode", "simple"))
        description = request.form.get("description", "").strip()
        if not description:
            flash("Please enter a description.", "error")
            return redirect(url_for("edit_expense", trip_id=trip_id, expense_id=expense_id))

        if mode == "restaurant":
            items_json = request.form.get("items_data", "[]")
            try:
                items = json.loads(items_json)
            except Exception:
                items = []

            items_sum = sum(Decimal(str(it.get("price", "0") or "0")) for it in items)
            total_bill_raw = request.form.get("total_bill", "").strip()

            if not total_bill_raw or total_bill_raw == "0":
                if items_sum > 0:
                    total_bill = money(items_sum)
                else:
                    flash("Please enter a total bill amount or add items.", "error")
                    return redirect(url_for("edit_expense", trip_id=trip_id, expense_id=expense_id))
            else:
                try:
                    total_bill = money(Decimal(total_bill_raw))
                except Exception:
                    if items_sum > 0:
                        total_bill = money(items_sum)
                    else:
                        flash("Please enter a valid total bill amount.", "error")
                        return redirect(url_for("edit_expense", trip_id=trip_id, expense_id=expense_id))

            if total_bill <= 0:
                flash("Bill must be greater than ₹0.", "error")
                return redirect(url_for("edit_expense", trip_id=trip_id, expense_id=expense_id))

            paid_by = request.form.get("paid_by", "")
            if not find_member(paid_by):
                flash("Please select who paid.", "error")
                return redirect(url_for("edit_expense", trip_id=trip_id, expense_id=expense_id))

            consumers = [m["id"] for m in members if m["id"] in request.form.getlist("consumers")]
            for it in items:
                for cid in it.get("consumed_by", []):
                    if cid and cid not in consumers and find_member(cid):
                        consumers.append(cid)

            if not consumers:
                flash("Please select at least one person involved.", "error")
                return redirect(url_for("edit_expense", trip_id=trip_id, expense_id=expense_id))

            discount_pct = request.form.get("discount_percent", "0").strip() or "0"
            service_charge = request.form.get("service_charge", "0").strip() or "0"
            tax_pct = request.form.get("tax_percent", "0").strip() or "0"
            tax_amt = request.form.get("tax_amount", "0").strip() or "0"
            tax_split_method = request.form.get("tax_split_method", "equal").strip() or "equal"

            cover_total = request.form.get("cover_charge_total", "0").strip() or "0"
            cover_per_person = request.form.get("cover_charge_per_person", "0").strip() or "0"
            cover_paid_by = request.form.get("cover_charge_paid_by", paid_by)
            coins = request.form.get("dineout_coins", "0").strip() or "0"
            coins_by = request.form.get("dineout_coins_by", paid_by)
            include_coins = request.form.get("include_coins_in_split") == "on"
            convenience_fee = request.form.get("convenience_fee", "0").strip() or "0"
            payer_upi = request.form.get("payer_upi", "").strip()

            if payer_upi:
                payer_member = find_member(paid_by)
                if payer_member:
                    payer_member["upi_id"] = payer_upi
                    save_members(members)

            is_treat = request.form.get("is_treat") in ["true", "1", "on"]

            target_expense.update({
                "mode": "restaurant",
                "description": description,
                "is_treat": is_treat,
                "date": request.form.get("date", "").strip(),
                "total_bill": f"{total_bill:.2f}",
                "paid_by": paid_by,
                "payer_upi": payer_upi,
                "consumers": consumers,
                "discount_percent": discount_pct,
                "convenience_fee": convenience_fee,
                "service_charge": service_charge,
                "tax_percent": tax_pct,
                "tax_amount": tax_amt,
                "tax_split_method": tax_split_method,
                "cover_charge_total": cover_total,
                "cover_charge_per_person": cover_per_person,
                "cover_charge_paid_by": cover_paid_by,
                "dineout_coins": coins,
                "dineout_coins_by": coins_by,
                "include_coins_in_split": include_coins,
                "items": items,
            })
        else:
            # Simple mode edit
            amount_raw = request.form.get("amount", "").strip()
            items_json = request.form.get("items_data", "[]")
            try:
                items = json.loads(items_json)
            except Exception:
                items = []

            items_sum = sum(Decimal(str(it.get("price", "0") or "0")) for it in items)

            if not amount_raw or amount_raw == "0":
                if items_sum > 0:
                    amount = money(items_sum)
                else:
                    flash("Please enter an amount.", "error")
                    return redirect(url_for("edit_expense", trip_id=trip_id, expense_id=expense_id))
            else:
                try:
                    amount = money(Decimal(amount_raw))
                except Exception:
                    flash("Please enter a valid amount.", "error")
                    return redirect(url_for("edit_expense", trip_id=trip_id, expense_id=expense_id))

            if amount <= 0:
                flash("Amount must be greater than ₹0.", "error")
                return redirect(url_for("edit_expense", trip_id=trip_id, expense_id=expense_id))

            paid_by = request.form.get("paid_by", "")
            if not find_member(paid_by):
                flash("Please select who paid.", "error")
                return redirect(url_for("edit_expense", trip_id=trip_id, expense_id=expense_id))

            consumers = [m["id"] for m in members if m["id"] in request.form.getlist("consumers")]
            if not consumers:
                flash("Please select at least one consumer.", "error")
                return redirect(url_for("edit_expense", trip_id=trip_id, expense_id=expense_id))

            payer_upi = request.form.get("payer_upi", "").strip()
            if payer_upi:
                payer_member = find_member(paid_by)
                if payer_member:
                    payer_member["upi_id"] = payer_upi
                    save_members(members)

            is_treat = request.form.get("is_treat") in ["true", "1", "on"]

            target_expense.update({
                "mode": "simple",
                "description": description,
                "is_treat": is_treat,
                "date": request.form.get("date", "").strip(),
                "amount": f"{amount:.2f}",
                "paid_by": paid_by,
                "payer_upi": payer_upi,
                "consumers": consumers,
                "items": items,
            })

        # Move to new event if changed
        if new_event["id"] != target_event["id"]:
            target_event["expenses"] = [e for e in target_event.get("expenses", []) if e["id"] != expense_id]
            new_event.setdefault("expenses", []).append(target_expense)

        update_trip(trip)
        flash(f'Expense "{description}" updated!', "success")
        return redirect(url_for("trip_view", trip_id=trip_id))

    # GET
    mode = target_expense.get("mode", "simple")
    return render_template(
        "add_expense.html",
        trip=trip,
        members=members,
        events=trip.get("events", []),
        mode=mode,
        expense=target_expense,
        target_event=target_event,
        is_edit=True,
    )


@app.route("/trip/<trip_id>/settle")
def trip_settle(trip_id):
    init_state()
    all_members = get_members()
    if not all_members:
        flash("Start by creating a group.", "error")
        return redirect(url_for("setup"))

    trip = get_trip(trip_id)
    if not trip:
        flash("Trip not found.", "error")
        return redirect(url_for("dashboard"))

    members = get_trip_members(trip, all_members)
    mmap = member_map_for(all_members)
    balances = compute_trip_balances(members, trip)
    transactions = simplify_transactions(balances)
    max_abs = max((abs(r["balance"]) for r in balances), default=Decimal("0"))

    # Enrich transactions with settlements and UPI
    settlements = trip.setdefault("settlements", {})
    for tx in transactions:
        tx_info = settlements.get(tx["key"], {})
        tx["is_settled"] = tx_info.get("settled", False)
        tx["settled_at"] = tx_info.get("settled_at", "")
        creditor = mmap.get(tx["to_id"], {})
        tx["to_upi"] = creditor.get("upi_id", "")

    for b in balances:
        member_debts = [tx for tx in transactions if tx["from_id"] == b["id"]]
        if member_debts and all(tx["is_settled"] for tx in member_debts):
            b["is_fully_settled"] = True
        else:
            b["is_fully_settled"] = False

    return render_template(
        "settlement.html",
        trip=trip,
        members=members,
        balances=balances,
        transactions=transactions,
        max_abs=max_abs,
        scope="trip",
    )


@app.route("/settle")
def settle():
    init_state()
    members = get_members()
    if not members:
        flash("Start by creating a group.", "error")
        return redirect(url_for("setup"))

    mmap = member_map_for(members)
    trips = get_trips()
    balances = compute_all_balances(members, trips, apply_settlements=True)
    transactions = simplify_transactions(balances)
    max_abs = max((abs(r["balance"]) for r in balances), default=Decimal("0"))

    for tx in transactions:
        creditor = mmap.get(tx["to_id"], {})
        tx["to_upi"] = creditor.get("upi_id", "")
        # Find which trip this debt originates from
        for trip in trips:
            for ev in trip.get("events", []):
                for exp in ev.get("expenses", []):
                    if exp.get("paid_by") == tx["to_id"] and tx["from_id"] in exp.get("consumers", []):
                        tx["trip_id"] = trip["id"]
                        tx["trip_name"] = trip["name"]
                        break

    # Collect completed settlements across all trips
    completed_settlements = []
    for trip in trips:
        for s_key, s_data in trip.get("settlements", {}).items():
            if s_data.get("settled"):
                parts = s_key.split("_")
                from_id = parts[0] if len(parts) == 2 else ""
                to_id = parts[1] if len(parts) == 2 else ""
                from_mem = mmap.get(from_id, {})
                to_mem = mmap.get(to_id, {})
                completed_settlements.append({
                    "key": s_key,
                    "trip_id": trip["id"],
                    "trip_name": trip["name"],
                    "from_id": from_id,
                    "to_id": to_id,
                    "from_name": s_data.get("from_name") or from_mem.get("name", "Unknown"),
                    "from_avatar": from_mem.get("avatar", "👤"),
                    "to_name": s_data.get("to_name") or to_mem.get("name", "Unknown"),
                    "to_avatar": to_mem.get("avatar", "👤"),
                    "amount": money(s_data.get("amount", "0")),
                    "settled_at": s_data.get("settled_at", ""),
                })

    return render_template(
        "settlement.html",
        trip=None,
        members=members,
        balances=balances,
        transactions=transactions,
        completed_settlements=completed_settlements,
        max_abs=max_abs,
        scope="global",
    )


@app.route("/settle/toggle", methods=["POST"])
def toggle_global_settlement():
    init_state()
    trips = get_trips()
    transfer_key = request.form.get("transfer_key", "").strip()
    trip_id = request.form.get("trip_id", "").strip()
    from_name = request.form.get("from_name", "").strip()
    to_name = request.form.get("to_name", "").strip()
    amount = request.form.get("amount", "").strip()
    action = request.form.get("action", "toggle")

    target_trip = None
    if trip_id:
        target_trip = next((t for t in trips if t["id"] == trip_id), None)
    if not target_trip:
        for t in trips:
            if transfer_key in t.get("settlements", {}):
                target_trip = t
                break
        if not target_trip and trips:
            target_trip = next((t for t in trips if not t.get("is_general")), trips[0])

    if target_trip:
        settlements = target_trip.setdefault("settlements", {})
        curr = settlements.get(transfer_key, {})
        if curr.get("settled") and action != "mark_settled":
            settlements[transfer_key] = {"settled": False}
            flash(f"Payment from {from_name} to {to_name} marked as pending.", "info")
        else:
            settlements[transfer_key] = {
                "settled": True,
                "amount": amount,
                "from_name": from_name,
                "to_name": to_name,
                "settled_at": datetime.now().strftime("%d %b, %I:%M %p"),
            }
            flash(f"Marked {from_name}'s payment of ₹{amount} to {to_name} as settled!", "success")
        update_trip(target_trip)
    else:
        flash("Could not find matching trip for this settlement.", "error")

    next_url = request.form.get("next") or request.referrer or url_for("settle")
    return redirect(next_url)


@app.route("/trip/<trip_id>/delete", methods=["POST"])
def delete_trip(trip_id):
    init_state()
    trips = get_trips()
    trip = next((t for t in trips if t["id"] == trip_id), None)
    if trip and trip.get("is_general"):
        flash("Cannot delete the General trip.", "error")
    else:
        trips = [t for t in trips if t["id"] != trip_id]
        save_trips(trips)
        # Auto-remove unused members who have 0 expenses across remaining trips
        removed = cleanup_unused_members(trips)
        if removed:
            rem_names = ", ".join(m["name"] for m in removed)
            flash(f'Trip deleted. Automatically removed {len(removed)} unused person(s) ({rem_names}) who had no expenses.', "info")
        else:
            flash("Trip deleted.", "success")
    return redirect(url_for("dashboard"))


@app.route("/members/<member_id>/delete", methods=["POST"])
def delete_member(member_id):
    init_state()
    all_members = get_members()
    trips = get_trips()
    used_ids = get_used_member_ids(trips)
    target = next((m for m in all_members if m["id"] == member_id), None)
    if not target:
        flash("Member not found.", "error")
        return redirect(url_for("dashboard"))
    if member_id in used_ids:
        flash(f"Cannot remove {target['name']} because they have recorded expenses in past trips.", "error")
        return redirect(url_for("dashboard"))

    all_members = [m for m in all_members if m["id"] != member_id]
    save_members(all_members)
    groups = get_groups()
    for g in groups:
        g["member_ids"] = [mid for mid in g.get("member_ids", []) if mid != member_id]
    save_groups(groups)
    flash(f"Removed {target['name']} from members list.", "success")
    return redirect(url_for("dashboard"))


@app.route("/members/cleanup", methods=["POST"])
def cleanup_members():
    init_state()
    trips = get_trips()
    removed = cleanup_unused_members(trips)
    if removed:
        names = ", ".join(m["name"] for m in removed)
        flash(f"Cleaned up {len(removed)} unused person(s): {names}", "success")
    else:
        flash("All members have recorded expenses. Nothing to clean up.", "info")
    return redirect(url_for("dashboard"))


@app.post("/reset")
def reset():
    session.clear()
    save_db({"members": [], "trips": []})
    flash("Fresh slate. Ready for a new split.", "success")
    return redirect(url_for("setup"))


@app.route("/pay")
def pay():
    pa = request.args.get("pa", "").strip()
    pn = request.args.get("pn", "").strip() or "Friend"
    am = request.args.get("am", "").strip()
    tn = request.args.get("tn", "").strip() or "TallyUp Settlement"

    import urllib.parse
    params = {"pa": pa, "pn": pn, "cu": "INR"}
    if am:
        try:
            params["am"] = f"{float(am):.2f}"
        except Exception:
            params["am"] = am
    if tn:
        params["tn"] = tn

    upi_uri = "upi://pay?" + urllib.parse.urlencode(params)
    return render_template(
        "pay.html",
        pa=pa,
        pn=pn,
        am=params.get("am", ""),
        tn=tn,
        upi_uri=upi_uri,
    )


# ---------------------------------------------------------------------------
# Receipt Scanner API (100% Free, Local RapidOCR PP-OCRv4)
# ---------------------------------------------------------------------------

@app.route("/api/scan_receipt", methods=["POST"])
def scan_receipt():
    """
    Scans an uploaded receipt image using local RapidOCR.
    Extracts venue/merchant, meal/category, date, items, subtotal, taxes, service charges, and grand total.
    """
    file = request.files.get("receipt") or request.files.get("file") or request.files.get("image")
    if not file or file.filename == "":
        return jsonify({"success": False, "error": "No receipt image provided. Please select or capture a photo."}), 400

    import tempfile
    from receipt_parser import parse_receipt_image

    suffix = os.path.splitext(file.filename)[1].lower() or ".jpg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        temp_path = tmp.name
        file.save(temp_path)

    try:
        parsed = parse_receipt_image(temp_path)
        return jsonify({"success": True, "data": parsed})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": f"Failed to parse receipt: {str(e)}"}), 500
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def find_available_port(preferred_port=5001):
    """Find an available port starting from preferred_port, avoiding port 5000 (used by macOS AirPlay)."""
    import socket
    for candidate in range(preferred_port, preferred_port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", candidate))
                return candidate
            except OSError:
                continue
    return preferred_port


if __name__ == "__main__":
    import os

    configured_port = os.environ.get("PORT")
    if configured_port:
        port = int(configured_port)
    else:
        port = find_available_port(5001)
        os.environ["PORT"] = str(port)

    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        print(f"\n{'='*56}")
        print(f" 🚀 TallyUp is running at: http://127.0.0.1:{port}")
        print(f"{'='*56}\n")

    app.run(host="127.0.0.1", port=port, debug=True)
