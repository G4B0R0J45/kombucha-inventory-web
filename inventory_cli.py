"""El Fermentario — terminal interface.

RULE OF THIS FILE: it is the ONLY place where input() and print() exist.
It gathers what the user types, hands it to fermentario.InventoryService,
and phrases the results on screen. There is no business rule and no file
handling here — replacing this file with a web page gives the same system
a different face, which is exactly the plan.
"""

import logging
import os
from datetime import datetime

from fermentario import (core, FileStorage, InventoryDataError, InventoryService)

# ------------------------------------------------------------
# LOGGING SETUP (app-level: the modules only ask for loggers)
# ------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
REPORTS_DIR = os.path.join(SCRIPT_DIR, "reports")
HISTORY_PAGE_SIZE = 15

try:
    os.makedirs(LOG_DIR, exist_ok=True)
    log_filename = os.path.join(LOG_DIR, f"{datetime.now().strftime('%Y-%m-%d')}.log")
    logging.basicConfig(
        filename=log_filename,
        level=logging.INFO,
        format="%(asctime)s — [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        filemode="a"
    )
except OSError as e:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s — [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    print(f"WARNING: could not open the log file ({e}). Logging to console instead.\n")

logger = logging.getLogger(__name__)


# ------------------------------------------------------------
# PICKERS (kill the typos: choose by number instead of typing)
# ------------------------------------------------------------
def pick_movement_type():
    """Asks for the movement type and re-asks until the answer is valid, so
    an invalid type is caught HERE — the user never gets to type a quantity
    or a note for a movement that was already doomed."""
    while True:
        movement_type = core.normalize_movement_type(
            input("Movement type (e = entry / x = exit): "))
        if movement_type is not None:
            return movement_type
        print("Error: type 'e' (entry) or 'x' (exit).")


def prompt_new_flavor(service):
    """Asks for a new flavor's name and minimum stock and returns
    (flavor_name, minimum_stock), or (None, None) on any invalid input.
    NOTE: unlike the old create_flavor(persist=False), nothing is added to
    the catalog here — the service creates the flavor only if its first
    movement succeeds, so ghost flavors are impossible by construction."""
    flavor_name = input("New flavor name: ").strip()
    if not flavor_name:
        print("Error: flavor name cannot be empty.\n")
        return None, None
    existing = core.find_flavor_case_insensitive(service.catalog, flavor_name)
    if existing:
        print(f"Error: a flavor with that name already exists: '{existing}'.\n")
        return None, None
    try:
        minimum_stock = int(input("Set its minimum stock level: ").strip())
    except ValueError:
        print("Error: minimum stock must be a whole number.\n")
        logger.error("Invalid minimum stock input for new flavor '%s'", flavor_name)
        return None, None
    if minimum_stock < 0:
        print("Error: minimum stock cannot be negative.\n")
        return None, None
    return flavor_name, minimum_stock


def pick_flavor(service):
    """Selects the flavor by number (with 5-20 flavors this is faster and
    kills typos). A scanned FLAVOR label or a typed name is accepted too.
    Returns (flavor_name, new_flavor_minimum): the minimum is None for an
    existing flavor, and the chosen minimum when the flavor is brand new.
    Returns (None, None) if the input was invalid."""
    flavors = sorted(service.catalog["flavors"])
    if not flavors:
        print("No flavors yet — let's create the first one.")
        return prompt_new_flavor(service)

    print("Flavors:")
    for position, name in enumerate(flavors, start=1):
        print(f"{position}. {name}")
    choice_raw = input("Choose a flavor number, 'n' for a new flavor, or scan a code: ").strip()
    if choice_raw.lower() == "n":
        return prompt_new_flavor(service)
    if core.looks_like_flavor_label(choice_raw):
        # Scanned flavor label: the QR payload is FLAVOR:<name>.
        scanned_name = core.strip_flavor_prefix(choice_raw)
        existing = core.find_flavor_case_insensitive(service.catalog, scanned_name)
        if existing:
            return existing, None
        print(f"Error: the scanned flavor '{scanned_name}' is not in the catalog. "
              "Create it first ('n' here, or option 5).\n")
        return None, None
    try:
        index = int(choice_raw)
    except ValueError:
        # Not a number and not a flavor label: maybe a name typed by hand.
        existing = core.find_flavor_case_insensitive(service.catalog, choice_raw)
        if existing:
            return existing, None
        print("Error: choose a number from the list, 'n', or scan a FLAVOR label "
              "(a lot label does not identify the flavor).\n")
        return None, None
    if not 1 <= index <= len(flavors):
        print("Error: choose a valid number or 'n'.\n")
        return None, None
    return flavors[index - 1], None


def pick_existing_flavor(service, prompt="Choose a flavor number (Enter to cancel): "):
    """Numbered pick over the catalog for the editor actions (same idea as
    the registration flow: no retyping long names). A typed name or a
    scanned FLAVOR label is accepted too. Returns the name, or None."""
    flavors = sorted(service.catalog["flavors"])
    if not flavors:
        print("No flavors in the catalog yet.\n")
        return None
    print("Flavors:")
    for position, name in enumerate(flavors, start=1):
        print(f"{position}. {name}")
    choice_raw = input(prompt).strip()
    if not choice_raw:
        print("Cancelled.\n")
        return None
    try:
        index = int(choice_raw)
    except ValueError:
        if core.looks_like_flavor_label(choice_raw):
            choice_raw = core.strip_flavor_prefix(choice_raw)
        existing = core.find_flavor_case_insensitive(service.catalog, choice_raw)
        if existing:
            return existing
        print("Error: choose a valid number from the list.\n")
        return None
    if not 1 <= index <= len(flavors):
        print("Error: choose a valid number from the list.\n")
        return None
    return flavors[index - 1]


def pick_lot(flavor_name, stock):
    """For entries: lists the flavor's lots that still have stock, so a batch
    can be topped up without retyping long, similar codes. 'n' asks for a new
    code. A full code typed or scanned with a QR reader is also accepted:
    if it exists it is selected, otherwise it can be used as the new code.
    Returns the lot number, or None if the input was invalid."""
    lots = core.get_flavor_stock(stock, flavor_name)["lots"]
    if not lots:
        lot_number = input("Lot number: ").strip()
        if not lot_number:
            print("Error: lot number cannot be empty for an entry.\n")
            return None
        if core.looks_like_flavor_label(lot_number):
            print("Error: that is a FLAVOR label; a lot code is needed here.\n")
            return None
        return lot_number

    print(f"Existing lots for '{flavor_name}':")
    for position, (lot_number, quantity) in enumerate(lots.items(), start=1):
        print(f"{position}. {lot_number} ({quantity} bottles)")
    choice_raw = input("Choose a lot number, 'n' for a new lot, or scan a code: ").strip()
    if not choice_raw:
        print("Error: choose a valid number, 'n', or a lot code.\n")
        return None
    if core.looks_like_flavor_label(choice_raw):
        print("Error: that is a FLAVOR label; scan the LOT label instead.\n")
        return None
    if choice_raw.lower() == "n":
        lot_number = input("New lot code: ").strip()
        if not lot_number:
            print("Error: lot number cannot be empty for an entry.\n")
            return None
        if core.looks_like_flavor_label(lot_number):
            print("Error: that is a FLAVOR label; a lot code is needed here.\n")
            return None
        return lot_number
    try:
        index = int(choice_raw)
    except ValueError:
        # Not a number: a lot code typed by hand or scanned with a QR reader.
        if choice_raw in lots:
            return choice_raw  # existing lot -> top it up directly
        confirm = input(f"Use '{choice_raw}' as a NEW lot code? (y/n): ").strip().lower()
        if confirm == "y":
            return choice_raw
        print("Movement cancelled.\n")
        return None
    if not 1 <= index <= len(lots):
        print("Error: choose a valid number, 'n', or a lot code.\n")
        return None
    return list(lots)[index - 1]


def pick_staff(service):
    """Selects who is registering the movement. With 2-3 people, a numbered
    pick avoids typos in the history. Returns the name, or None if invalid."""
    staff = service.catalog["staff"]
    if not staff:
        name = input("No staff registered yet. Your name: ").strip()
        if not name:
            print("Error: staff name cannot be empty.\n")
            return None
        result = service.add_staff(name, keep_in_memory_on_save_failure=True)
        if result.message.startswith("WARNING"):
            print(result.message)
        logger.info("Staff member '%s' added (first run)", name)
        return name

    print("Who is registering this movement? (manage names in option 5)")
    for position, name in enumerate(staff, start=1):
        print(f"{position}. {name}")
    choice = input("Choose a number: ").strip()
    try:
        index = int(choice)
    except ValueError:
        print("Error: choose a valid number.\n")
        return None
    if not 1 <= index <= len(staff):
        print("Error: choose a valid number.\n")
        return None
    return staff[index - 1]


# ------------------------------------------------------------
# MOVEMENT REGISTRATION
# ------------------------------------------------------------
def display_result(result):
    """Prints the transaction result exactly as defined in the IPO OUTPUT block."""
    print(f"Flavor: {result.flavor_name}")
    print(f"Lot Number: {result.lot_display}")
    print(f"Updated Stock: {result.current_stock}")
    print(f"Transaction: {result.transaction_status}")
    print(f"Inventory Status: {result.stock_status}")


def register_movement(service):
    """Collects the INPUT values and hands them to the service, which runs
    the movement with all its guarantees (ghost-flavor prevention, disk
    rollback). This function only asks questions and prints answers."""
    staff_name = pick_staff(service)
    if staff_name is None:
        return

    flavor_name, new_flavor_minimum = pick_flavor(service)
    if flavor_name is None:
        return

    movement_type = pick_movement_type()

    try:
        bottle_quantity = int(input("Bottle quantity: ").strip())
    except ValueError:
        print("Error: bottle quantity must be a whole number.\n")
        logger.error("Invalid bottle quantity input for flavor '%s'", flavor_name)
        return
    if bottle_quantity <= 0:
        print("Error: bottle quantity must be greater than zero.\n")
        logger.error("Bottle quantity must be > 0, got %d for '%s'", bottle_quantity, flavor_name)
        return

    if movement_type == "entry":
        stock_before, _ = service.stock()
        lot_number = pick_lot(flavor_name, stock_before)
        if lot_number is None:
            return
    else:
        lot_number = ""  # exits do not ask for a lot: FIFO decides

    note = input("Note (optional, Enter to skip): ").strip()

    result = service.register_movement(
        staff_name, flavor_name, movement_type, bottle_quantity,
        lot_number=lot_number, note=note, new_flavor_minimum=new_flavor_minimum,
    )

    if result.message:
        print(result.message)
    print()
    display_result(result)
    print()


# ------------------------------------------------------------
# VIEWS
# ------------------------------------------------------------
def view_inventory(service):
    """Displays every flavor with its total stock, minimum and lot breakdown.
    Flavors found in the history but missing from the catalog are shown too,
    flagged, instead of being silently hidden."""
    rows, inconsistencies = service.inventory_rows()

    if not rows:
        print("Inventory is empty.\n")
        logger.info("Inventory view: empty")
        return

    for problem in inconsistencies:
        print(f"WARNING (history inconsistency): {problem}")
    if inconsistencies:
        print()
        logger.warning("Inventory view found %d history inconsistencies", len(inconsistencies))

    # The flavor column stretches to the longest name, so real names like
    # 'Naranja con albahaca y clavo' never break the alignment.
    name_width = max([19] + [len(row["flavor_name"]) for row in rows]) + 1
    print(f"{'Flavor':<{name_width}}{'Total':<10}{'Minimum':<10}{'Status'}")
    print("-" * (name_width + 30))
    for row in rows:
        minimum_display = row["minimum_stock"] if row["in_catalog"] else "-"
        print(f"{row['flavor_name']:<{name_width}}{row['total']:<10}"
              f"{minimum_display:<10}{row['stock_status']}")
        for lot_number, quantity in row["lots"].items():
            print(f"    lot {lot_number}: {quantity}")

    print()
    logger.info("Inventory view displayed (%d flavors)", len(service.catalog["flavors"]))


def shorten(text, width):
    """Truncates long text so the history columns stay aligned."""
    return text if len(text) <= width else text[:width - 1] + "…"


def print_history_rows(rows):
    """Prints one aligned table of movement rows (notes truncated). The lot
    column stretches to the longest code being shown (capped at 24), so real
    codes like 'LOT202606-K114' are never cut off."""
    lot_width = min(24, max([10] + [len(m["lot_number"]) for m in rows]) + 2)
    print(f"{'ID':<5}{'Date / Time':<21}{'Staff':<10}{'Flavor':<14}"
          f"{'Lot':<{lot_width}}{'Type':<7}{'Qty':<5}{'Note'}")
    print("-" * (86 + lot_width))
    for movement in rows:
        lot_display = movement["lot_number"] if movement["lot_number"] else "-"
        note_display = movement["note"] if movement["note"] else "-"
        print(f"{movement['movement_id']:<5}{movement['date_time']:<21}"
              f"{shorten(movement['staff_name'], 9):<10}"
              f"{shorten(movement['flavor_name'], 13):<14}"
              f"{shorten(lot_display, lot_width - 1):<{lot_width}}"
              f"{movement['movement_type']:<7}"
              f"{movement['bottle_quantity']:<5}"
              f"{shorten(note_display, 24)}")


def view_history(service, limit=HISTORY_PAGE_SIZE, interactive=True):
    """Displays the movement history. Only the last `limit` rows by default,
    so hundreds of movements do not flood the screen; the user can then show
    everything or filter by flavor."""
    total = len(service.movements)
    if not total:
        print("No movements registered yet.\n")
        logger.info("History view: empty")
        return

    recent = service.history(limit=limit)
    if total > limit:
        print(f"Showing the last {len(recent)} of {total} movements.")
    print_history_rows(recent)
    print()
    logger.info("History view displayed (%d of %d movements)", len(recent), total)

    if not interactive or total <= limit:
        return
    choice = input("Enter = back | 'a' = show all | flavor name = filter: ").strip()
    print()
    if not choice:
        return
    if choice.lower() == "a":
        print_history_rows(service.movements)
        print()
        logger.info("Full history displayed (%d movements)", total)
        return
    filtered = service.history(flavor_name=choice)
    if filtered:
        print_history_rows(filtered)
        print()
        logger.info("History filtered by '%s' (%d movements)", choice, len(filtered))
    else:
        print(f"No movements for '{choice}'.\n")


# ------------------------------------------------------------
# SALES REPORT
# ------------------------------------------------------------
def save_sales_charts(bottles_by_flavor, bottles_by_month):
    """Renders the sales report as one PNG (two bar charts) in reports/.
    matplotlib is an optional dependency, like qrcode for the labels: when
    it is missing, the text report above is all there is. Returns the path
    of the saved file, or None."""
    try:
        import matplotlib
        matplotlib.use("Agg")  # render straight to file: WSL has no display
        import matplotlib.pyplot as plt
    except ImportError:
        print("Charts need matplotlib. Install it with:")
        print("  sudo apt install python3-matplotlib   (or: pip install matplotlib)")
        return None

    flavors = sorted(bottles_by_flavor.items(), key=lambda item: item[1])
    months = sorted(month for month in bottles_by_month if month != "unknown")
    if "unknown" in bottles_by_month:
        months.append("unknown")

    figure, (flavor_axis, month_axis) = plt.subplots(
        2, 1, figsize=(9, 4 + 0.45 * len(flavors)),
        gridspec_kw={"height_ratios": [max(len(flavors), 2), 5]})

    flavor_axis.barh([name for name, _ in flavors],
                     [bottles for _, bottles in flavors], color="#3a7ca5")
    flavor_axis.set_title("Bottles out by flavor")
    for position, (_, bottles) in enumerate(flavors):
        flavor_axis.text(bottles, position, f" {bottles}", va="center")

    month_axis.bar(months, [bottles_by_month[month] for month in months],
                   color="#81a684")
    month_axis.set_title("Bottles out by month")
    for position, month in enumerate(months):
        month_axis.text(position, bottles_by_month[month],
                        str(bottles_by_month[month]), ha="center", va="bottom")

    figure.suptitle("El Fermentario - Sales report (every exit counts as a sale)")
    figure.tight_layout()

    path = os.path.join(
        REPORTS_DIR, f"sales_report_{datetime.now().strftime('%Y-%m-%d_%H%M')}.png")
    try:
        os.makedirs(REPORTS_DIR, exist_ok=True)
        figure.savefig(path, dpi=150)
    except OSError as e:
        print(f"ERROR: the chart could not be saved ({e}).")
        return None
    finally:
        plt.close(figure)
    return path


def sales_report(service):
    """Prints the sales summary the service derives from the history."""
    summary = service.sales_summary()
    if summary is None:
        print("No exits registered yet, so there are no sales to report.\n")
        logger.info("Sales report: no exits")
        return

    bottles_by_flavor = summary["bottles_by_flavor"]
    bottles_by_month = summary["bottles_by_month"]

    print("SALES REPORT (every exit counts as a sale)")
    print()
    name_width = max([19] + [len(name) for name in bottles_by_flavor]) + 1
    print(f"{'By flavor':<{name_width}}{'Bottles'}")
    print("-" * (name_width + 10))
    for flavor_name, bottles in sorted(bottles_by_flavor.items(), key=lambda item: -item[1]):
        print(f"{flavor_name:<{name_width}}{bottles}")
    print()
    print(f"{'By month':<20}{'Bottles'}")
    print("-" * 30)
    months = sorted(month for month in bottles_by_month if month != "unknown")
    if "unknown" in bottles_by_month:
        months.append("unknown")
    for month in months:
        print(f"{month:<20}{bottles_by_month[month]}")
    print()
    print(f"Total: {summary['total_bottles']} bottles across "
          f"{summary['exit_count']} exit movements.")
    print()
    logger.info("Sales report displayed (%d exits, %d bottles)",
                summary["exit_count"], summary["total_bottles"])

    choice = input("Save this report as PNG charts? (y/n): ").strip().lower()
    print()
    if choice == "y":
        path = save_sales_charts(bottles_by_flavor, bottles_by_month)
        if path:
            print(f"Charts saved: {path}\n")
            logger.info("Sales charts saved to %s", path)


# ------------------------------------------------------------
# HISTORY CORRECTION
# ------------------------------------------------------------
def edit_movement(service):
    """Asks which movement to correct or delete, validates each field as it
    is typed (immediate feedback, like always), and hands the final change
    to the service — which re-validates and replays the whole history
    before persisting anything."""
    if not service.movements:
        print("No movements to correct.\n")
        return

    view_history(service, interactive=False)
    raw_id = input("Movement ID to correct (any ID; Enter to cancel): ").strip()
    if not raw_id:
        print()
        return

    target = service.find_movement(raw_id)
    if target is None:
        print("Error: no movement with that ID.\n")
        return

    action = input("Edit (e) or delete (x) this movement?: ").strip().lower()

    if action == "x":
        confirm = input("Delete it permanently from the history? (y/n): ").strip().lower()
        if confirm != "y":
            print("Correction cancelled.\n")
            return
        result = service.delete_movement(raw_id)
    elif action == "e":
        print("Press Enter to keep the current value.")
        edited = dict(target)

        date_raw = input(f"Date/time [{target['date_time']}] (YYYY-MM-DD HH:MM:SS): ").strip()
        if date_raw:
            try:
                parsed_date = datetime.strptime(date_raw, core.DATE_FORMAT)
            except ValueError:
                print("Error: the date must have the format YYYY-MM-DD HH:MM:SS.\n")
                return
            edited["date_time"] = parsed_date.strftime(core.DATE_FORMAT)

        staff_name = input(f"Staff name [{target['staff_name']}]: ").strip()
        if staff_name:
            edited["staff_name"] = staff_name

        flavor_name = input(f"Flavor name [{target['flavor_name']}]: ").strip()
        if flavor_name:
            canonical = core.find_flavor_case_insensitive(service.catalog, flavor_name)
            if canonical is None:
                print("Error: that flavor does not exist in the catalog. "
                      "Create or rename it first (option 5).\n")
                return
            edited["flavor_name"] = canonical

        movement_raw = input(f"Movement type (e = entry / x = exit) "
                             f"[{target['movement_type']}]: ").strip()
        if movement_raw:
            movement_type = core.normalize_movement_type(movement_raw)
            if movement_type is None:
                print("Error: movement type must be 'e' (entry) or 'x' (exit).\n")
                return
            edited["movement_type"] = movement_type

        if edited["movement_type"] == "entry":
            current_lot = target["lot_number"] if target["lot_number"] else "(none)"
            lot_number = input(f"Lot number [{current_lot}]: ").strip()
            if lot_number:
                if core.looks_like_flavor_label(lot_number):
                    print("Error: that is a FLAVOR label, not a lot code.\n")
                    return
                edited["lot_number"] = lot_number
            elif not target["lot_number"]:
                print("Error: an entry needs a lot number.\n")
                return
        else:
            edited["lot_number"] = ""  # exits carry no lot: FIFO decides

        quantity_raw = input(f"Bottle quantity [{target['bottle_quantity']}]: ").strip()
        if quantity_raw:
            try:
                bottle_quantity = int(quantity_raw)
            except ValueError:
                print("Error: bottle quantity must be a whole number.\n")
                return
            if bottle_quantity <= 0:
                print("Error: bottle quantity must be greater than zero.\n")
                return
            edited["bottle_quantity"] = str(bottle_quantity)

        current_note = target["note"] if target["note"] else "(none)"
        note_raw = input(f"Note [{current_note}] ('-' to clear): ").strip()
        if note_raw == "-":
            edited["note"] = ""
        elif note_raw:
            edited["note"] = note_raw

        result = service.edit_movement(raw_id, edited)
    else:
        print("Error: choose 'e' or 'x'.\n")
        return

    print(result.message)
    for problem in result.data.get("problems", []):
        print(f"  - {problem}")
    print()


# ------------------------------------------------------------
# CATALOG EDITOR
# ------------------------------------------------------------
def create_flavor(service):
    """Option 5 path: adds a flavor directly (no movement needed)."""
    flavor_name = input("New flavor name: ").strip()
    if not flavor_name:
        print("Error: flavor name cannot be empty.\n")
        return
    try:
        minimum_stock = int(input("Set its minimum stock level: ").strip())
    except ValueError:
        print("Error: minimum stock must be a whole number.\n")
        logger.error("Invalid minimum stock input for new flavor '%s'", flavor_name)
        return
    result = service.add_flavor(flavor_name, minimum_stock)
    print(result.message + "\n")


def rename_flavor(service):
    """Renames a flavor and propagates the change to the whole history."""
    old_name = pick_existing_flavor(
        service, "Flavor to rename (number, Enter to cancel): ")
    if old_name is None:
        return
    new_name = input("New name: ").strip()
    result = service.rename_flavor(old_name, new_name)
    if result.ok and result.data.get("warning"):
        print(result.data["warning"])
    print(result.message + "\n")


def change_minimum_stock(service):
    """Updates a flavor's minimum stock and shows the resulting status."""
    flavor_name = pick_existing_flavor(service)
    if flavor_name is None:
        return
    current_minimum = service.catalog["flavors"][flavor_name]["minimum_stock"]
    try:
        minimum_stock = int(input(f"New minimum stock [{current_minimum}]: ").strip())
    except ValueError:
        print("Error: minimum stock must be a whole number.\n")
        return
    result = service.set_minimum_stock(flavor_name, minimum_stock)
    print(result.message + "\n")


def delete_flavor(service):
    """Deletes a flavor. If it has history rows, asks for strong confirmation."""
    flavor_name = pick_existing_flavor(
        service, "Flavor to delete (number, Enter to cancel): ")
    if flavor_name is None:
        return

    related = service.flavor_history_count(flavor_name)
    if related:
        print(f"WARNING: '{flavor_name}' has {related} movements in the history.")
        print("Deleting it will also erase those rows permanently.")
        confirm = input("Type the flavor name to confirm: ").strip()
        if confirm != flavor_name:
            print("Deletion cancelled.\n")
            return
    else:
        confirm = input(f"Delete '{flavor_name}'? (y/n): ").strip().lower()
        if confirm != "y":
            print("Deletion cancelled.\n")
            return

    result = service.delete_flavor(flavor_name)
    if result.ok and result.data.get("warning"):
        print(result.data["warning"])
    print(result.message + "\n")


def manage_staff(service):
    """Adds or removes the people allowed to register movements."""
    while True:
        if service.catalog["staff"]:
            print("Registered staff:")
            for position, name in enumerate(service.catalog["staff"], start=1):
                print(f"{position}. {name}")
        else:
            print("No staff registered.")
        print("a. Add   r. Remove   b. Back")
        choice = input("Choose an option: ").strip().lower()
        print()

        if choice == "a":
            name = input("Name to add: ").strip()
            print(service.add_staff(name).message + "\n")
        elif choice == "r":
            raw = input("Number to remove: ").strip()
            try:
                index = int(raw)
            except ValueError:
                print("Error: choose a valid number.\n")
                continue
            print(service.remove_staff(index).message + "\n")
        elif choice == "b":
            return
        else:
            print("Invalid option.\n")


def edit_catalog(service):
    """Submenu to manage the catalog: add flavors directly (no movement
    needed), fix names and minimums, delete flavors, manage staff."""
    while True:
        print("--- Catalog editor ---")
        print("1. Add a new flavor")
        print("2. Rename a flavor (updates the whole history)")
        print("3. Change a flavor's minimum stock")
        print("4. Delete a flavor")
        print("5. Manage staff")
        print("6. Back to main menu")
        choice = input("Choose an option: ").strip()
        print()

        if choice == "1":
            create_flavor(service)
        elif choice == "2":
            rename_flavor(service)
        elif choice == "3":
            change_minimum_stock(service)
        elif choice == "4":
            delete_flavor(service)
        elif choice == "5":
            manage_staff(service)
        elif choice == "6":
            return
        else:
            print("Invalid option. Please choose 1-6.\n")


# ------------------------------------------------------------
# MAIN MENU
# ------------------------------------------------------------
def main():
    logger.info("=== El Fermentario Inventory System Started ===")
    service = InventoryService(FileStorage(SCRIPT_DIR))
    try:
        notices = service.load()
    except InventoryDataError as e:
        print(f"DATA ERROR:\n{e}\n")
        print("The program stops here so no data gets overwritten or miscalculated.")
        logger.error("Startup aborted: %s", e)
        return

    for notice in notices:
        print(notice + "\n")
    warnings = service.consistency_warnings()
    for warning in warnings:
        print(warning)
    if warnings:
        print()

    while True:
        print("=== El Fermentario - Kombucha Inventory Control ===")
        print("1. Register a movement (entry / exit)")
        print("2. View current inventory")
        print("3. View movement history")
        print("4. Correct a movement in the history")
        print("5. Edit catalog (flavors / minimums / staff)")
        print("6. Sales report (exits by flavor and month)")
        print("7. Exit")
        try:
            choice = input("Choose an option: ").strip()
        except (KeyboardInterrupt, EOFError):
            # Ctrl+C or end of input at the menu = clean exit, never a traceback
            print("\nClosing the system. See you soon!")
            logger.info("Session ended with Ctrl+C or end of input")
            break
        print()

        try:
            if choice == "1":
                register_movement(service)
            elif choice == "2":
                logger.info("User requested inventory view")
                view_inventory(service)
            elif choice == "3":
                logger.info("User requested history view")
                view_history(service)
            elif choice == "4":
                edit_movement(service)
            elif choice == "5":
                edit_catalog(service)
            elif choice == "6":
                logger.info("User requested sales report")
                sales_report(service)
            elif choice == "7":
                logger.info("User exited the system")
                print("Closing the system. See you soon!")
                break
            else:
                logger.warning("Invalid menu option selected: '%s'", choice)
                print("Invalid option. Please choose 1, 2, 3, 4, 5, 6 or 7.\n")
        except (KeyboardInterrupt, EOFError):
            # Ctrl+C inside an operation cancels it and returns to the menu
            print("\nOperation cancelled. Back to the menu.\n")
            logger.warning("Operation cancelled by the user (Ctrl+C)")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Last safety net: the user never sees a raw traceback; the log does.
        logger.exception("Unexpected fatal error")
        print("\nUnexpected error. Technical details were saved to the log file.")
