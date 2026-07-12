"""Pure business logic for El Fermentario.

RULE OF THIS MODULE: no side effects. No files, no input(), no print(),
no logging. Every function maps data to data. That is exactly what makes
this layer reusable from the terminal today and from a web API tomorrow —
an HTTP endpoint can call these functions the same way the CLI does.

All functions here operate on the same two structures the program has
always used:

    catalog   = {"flavors": {name: {"minimum_stock": int}}, "staff": [str]}
    movements = [ {movement_id, date_time, staff_name, flavor_name,
                   lot_number, movement_type, bottle_quantity, note}, ... ]
                (all values are strings, exactly as stored in the CSV)
"""

from datetime import datetime

# ------------------------------------------------------------
# SHARED CONSTANTS (previously at the top of inventory_system.py)
# ------------------------------------------------------------
CSV_FIELDS = ["movement_id", "date_time", "staff_name", "flavor_name",
              "lot_number", "movement_type", "bottle_quantity", "note"]
LEGACY_CSV_FIELDS = CSV_FIELDS[:-1]  # file format before the 'note' column
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
# Accepted when READING dates for reports: rows edited by hand or re-saved
# by Excel may not match the exact format the program writes.
DATE_PARSE_FORMATS = [DATE_FORMAT, "%Y-%m-%d %H:%M",
                      "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"]

# QR label conventions (shared with generate_lot_label.py):
#   - LOT labels encode the lot code alone.
#   - FLAVOR labels encode "FLAVOR:<name>". The prefix is what makes two
#     label types safe: a flavor label scanned at a lot prompt is detected
#     and rejected instead of silently becoming a bogus lot code.
FLAVOR_QR_PREFIX = "FLAVOR:"


def looks_like_flavor_label(text):
    """True when the text carries the flavor-label QR prefix."""
    return text.upper().startswith(FLAVOR_QR_PREFIX)


def strip_flavor_prefix(text):
    """Returns the flavor name encoded in a FLAVOR label payload."""
    return text[len(FLAVOR_QR_PREFIX):].strip()


# ------------------------------------------------------------
# VALIDATION (structure of the catalog and of history rows)
# ------------------------------------------------------------
def validate_catalog(data):
    """Returns a list of structural problems found in the catalog."""
    problems = []

    flavors = data.get("flavors")
    if not isinstance(flavors, dict):
        problems.append("'flavors' must be an object of flavor_name: {minimum_stock}.")
    else:
        for flavor_name, record in flavors.items():
            if not isinstance(record, dict):
                problems.append(f"Flavor '{flavor_name}' must be an object.")
                continue
            minimum_stock = record.get("minimum_stock")
            if not isinstance(minimum_stock, int) or minimum_stock < 0:
                problems.append(f"Flavor '{flavor_name}' needs an integer 'minimum_stock' >= 0.")

    staff = data.get("staff")
    if not isinstance(staff, list) or any(not isinstance(name, str) or not name.strip()
                                          for name in staff):
        problems.append("'staff' must be a list of non-empty names.")

    return problems


def validate_movement_row(row, row_number, seen_ids):
    """Validates one history row. Returns an error description, or None.
    Valid movement_ids are registered in seen_ids to detect duplicates.
    The note column is free text and may be empty."""
    if any(row.get(field) is None for field in CSV_FIELDS) or row.get(None) is not None:
        return f"Row {row_number}: wrong number of columns."

    movement_id = row["movement_id"].strip()
    if not movement_id.isdigit() or int(movement_id) <= 0:
        return f"Row {row_number}: movement_id '{movement_id}' must be a positive integer."
    normalized_id = str(int(movement_id))
    if normalized_id in seen_ids:
        return f"Row {row_number}: duplicated movement_id '{normalized_id}'."
    seen_ids.add(normalized_id)

    if not row["date_time"].strip():
        return f"Row {row_number}: date_time is empty."
    if not row["staff_name"].strip():
        return f"Row {row_number}: staff_name is empty."
    if not row["flavor_name"].strip():
        return f"Row {row_number}: flavor_name is empty."
    if row["movement_type"] not in ("entry", "exit"):
        return (f"Row {row_number}: movement_type '{row['movement_type']}' "
                "must be 'entry' or 'exit'.")

    quantity = row["bottle_quantity"].strip()
    if not quantity.isdigit() or int(quantity) <= 0:
        return (f"Row {row_number}: bottle_quantity '{quantity}' "
                "must be an integer greater than zero.")

    if row["movement_type"] == "entry" and not row["lot_number"].strip():
        return f"Row {row_number}: an entry needs a lot_number."

    if looks_like_flavor_label(row["lot_number"]):
        return (f"Row {row_number}: lot_number '{row['lot_number']}' starts with "
                f"'{FLAVOR_QR_PREFIX}', a prefix reserved for flavor QR labels.")

    return None


def validate_movements(movements):
    """Validates (and normalizes in place) a full list of history rows.
    Returns the list of problems found; an empty list means all rows are
    trustworthy. Extracted from the old load_movements() so the same check
    can run on data coming from a file, an import, or (later) an API."""
    problems = []
    seen_ids = set()
    for row_number, row in enumerate(movements, start=2):  # line 1 is the header
        for field in CSV_FIELDS:
            if row.get(field) is not None:
                row[field] = row[field].strip()
        problem = validate_movement_row(row, row_number, seen_ids)
        if problem:
            problems.append(problem)
        else:
            row["movement_id"] = str(int(row["movement_id"]))
    return problems


# ------------------------------------------------------------
# STOCK (derived by replaying the history — the source of truth)
# ------------------------------------------------------------
def compute_stock(movements):
    """
    Derives the current stock by replaying the whole history. Entries add
    bottles to their lot; exits consume bottles from the OLDEST lots first
    (FIFO — Python dicts keep insertion order, so lot order = arrival order).
    Rows must already be validated, so values can be trusted.
    Returns (stock, inconsistencies) where:
        stock = { flavor_name: {"lots": {lot_number: quantity}, "total": int} }
    An inconsistency means some exit could not be fully covered by the stock
    available at that point of the history (only possible after bad edits).
    """
    stock = {}
    inconsistencies = []

    for movement in movements:
        flavor_name = movement["flavor_name"]
        record = stock.setdefault(flavor_name, {"lots": {}, "total": 0})
        quantity = int(movement["bottle_quantity"])

        if movement["movement_type"] == "entry":
            lot_number = movement["lot_number"]
            record["lots"][lot_number] = record["lots"].get(lot_number, 0) + quantity
            record["total"] += quantity
        else:  # exit -> FIFO over this flavor's lots
            remaining = quantity
            for lot_number in list(record["lots"]):
                if remaining == 0:
                    break
                taken = min(record["lots"][lot_number], remaining)
                record["lots"][lot_number] -= taken
                remaining -= taken
                if record["lots"][lot_number] == 0:
                    del record["lots"][lot_number]
            record["total"] -= quantity - remaining
            if remaining > 0:
                inconsistencies.append(
                    f"Movement {movement['movement_id']}: exit of {quantity} "
                    f"'{flavor_name}' exceeds the stock available at that point by {remaining}."
                )
    return stock, inconsistencies


def get_flavor_stock(stock, flavor_name):
    """Safe accessor: returns the {'lots', 'total'} record for a flavor."""
    return stock.get(flavor_name, {"lots": {}, "total": 0})


def process_movement(movement_type, bottle_quantity, previous_stock, minimum_stock):
    """
    Core business logic. Mirrors the approved IPO PROCESS block exactly.
    previous_stock and minimum_stock are per FLAVOR (sum of all its lots).
    Returns a tuple: (current_stock, transaction_status, stock_status).
    """
    # Step 1 - Validate movement type
    if movement_type == "entry":
        current_stock = previous_stock + bottle_quantity
        transaction_status = "Success"
    elif movement_type == "exit":
        if bottle_quantity <= previous_stock:
            current_stock = previous_stock - bottle_quantity
            transaction_status = "Success"
        else:
            current_stock = previous_stock
            transaction_status = "Error: Insufficient stock"
    else:
        # Hardening beyond the IPO: guards against a typo'd movement_type.
        current_stock = previous_stock
        transaction_status = "Error: Invalid movement type"

    # Step 2 - Evaluate stock alert
    if current_stock <= minimum_stock:
        stock_status = "LOW STOCK"
    else:
        stock_status = "OK"

    return current_stock, transaction_status, stock_status


def fifo_breakdown(lots_before, lots_after):
    """Diffs a flavor's lots before vs after an exit and returns the list
    of (lot_number, bottles_taken) pairs, in FIFO order. This is what tells
    staff which physical lots to pull the bottles from."""
    breakdown = []
    for lot_number, quantity_before in lots_before.items():
        taken = quantity_before - lots_after.get(lot_number, 0)
        if taken > 0:
            breakdown.append((lot_number, taken))
    return breakdown


def new_problems_after_change(movements, candidate):
    """Compares history inconsistencies before vs after a proposed change.
    Only NEW problems should block a correction (pre-existing ones must not
    lock the editor). Returns the list of problems the change would add."""
    _, problems_before = compute_stock(movements)
    _, problems_after = compute_stock(candidate)
    return [p for p in problems_after if p not in problems_before]


# ------------------------------------------------------------
# SMALL HELPERS SHARED BY EVERY INTERFACE
# ------------------------------------------------------------
def next_movement_id(movements):
    """Sequential id for a new movement: highest existing id + 1."""
    if not movements:
        return 1
    return max(int(movement["movement_id"]) for movement in movements) + 1


def normalize_movement_type(raw):
    """Maps user input to a canonical movement type. Single-letter shortcuts
    are accepted so 'entry'/'exit' never have to be typed in full.
    Returns 'entry', 'exit', or None when the input is not recognized."""
    text = raw.strip().lower()
    if text in ("e", "entry"):
        return "entry"
    if text in ("x", "exit"):
        return "exit"
    return None


def find_flavor_case_insensitive(catalog, flavor_name):
    """Returns the existing flavor whose name matches ignoring case, or None.
    Prevents accidental near-duplicates like 'Jamaica' vs 'jamaica'."""
    lowered = flavor_name.lower()
    for existing in catalog["flavors"]:
        if existing.lower() == lowered:
            return existing
    return None


def build_movement(movement_id, date_time, staff_name, flavor_name,
                   lot_number, movement_type, bottle_quantity, note):
    """Builds one history row in the canonical (all-strings) shape."""
    return {
        "movement_id": str(movement_id),
        "date_time": date_time,
        "staff_name": staff_name,
        "flavor_name": flavor_name,
        "lot_number": lot_number,
        "movement_type": movement_type,
        "bottle_quantity": str(bottle_quantity),
        "note": note,
    }


# ------------------------------------------------------------
# VIEW DATA (structures ready to be rendered by ANY interface)
# ------------------------------------------------------------
def inventory_rows(catalog, stock):
    """Returns the inventory as a list of plain dicts, one per flavor:
        {"flavor_name", "total", "minimum_stock", "stock_status",
         "lots": {lot: qty}, "in_catalog": bool}
    Catalog flavors come first (sorted); flavors found only in the history
    are appended, flagged, instead of being silently hidden. The CLI turns
    this into an aligned table; a web UI can turn it into HTML rows."""
    rows = []
    for flavor_name in sorted(catalog["flavors"]):
        record = get_flavor_stock(stock, flavor_name)
        minimum_stock = catalog["flavors"][flavor_name]["minimum_stock"]
        rows.append({
            "flavor_name": flavor_name,
            "total": record["total"],
            "minimum_stock": minimum_stock,
            "stock_status": "LOW STOCK" if record["total"] <= minimum_stock else "OK",
            "lots": dict(record["lots"]),
            "in_catalog": True,
        })
    for flavor_name in sorted(stock):
        if flavor_name in catalog["flavors"]:
            continue
        record = stock[flavor_name]
        if record["total"] == 0 and not record["lots"]:
            continue
        rows.append({
            "flavor_name": flavor_name,
            "total": record["total"],
            "minimum_stock": None,
            "stock_status": "NOT IN CATALOG",
            "lots": dict(record["lots"]),
            "in_catalog": False,
        })
    return rows


def movement_month(date_text):
    """Best-effort 'YYYY-MM' bucket for one movement. The program always
    writes DATE_FORMAT, but rows edited by hand or with Excel may come in
    other shapes; anything unreadable lands in the 'unknown' bucket instead
    of crashing the report."""
    for date_format in DATE_PARSE_FORMATS:
        try:
            return datetime.strptime(date_text, date_format).strftime("%Y-%m")
        except ValueError:
            continue
    return "unknown"


def sales_summary(movements):
    """Sales control derived from the source of truth: every EXIT counts as
    a sale (the note field flags exceptions such as breakage or samples).
    Returns None when there are no exits; otherwise a dict:
        {"bottles_by_flavor": {..}, "bottles_by_month": {..},
         "total_bottles": int, "exit_count": int}"""
    exits = [m for m in movements if m["movement_type"] == "exit"]
    if not exits:
        return None

    bottles_by_flavor = {}
    bottles_by_month = {}
    for movement in exits:
        quantity = int(movement["bottle_quantity"])
        flavor_name = movement["flavor_name"]
        month = movement_month(movement["date_time"])
        bottles_by_flavor[flavor_name] = bottles_by_flavor.get(flavor_name, 0) + quantity
        bottles_by_month[month] = bottles_by_month.get(month, 0) + quantity

    return {
        "bottles_by_flavor": bottles_by_flavor,
        "bottles_by_month": bottles_by_month,
        "total_bottles": sum(bottles_by_flavor.values()),
        "exit_count": len(exits),
    }
