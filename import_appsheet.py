"""
One-time importer: AppSheet 'Movimientos' export  ->  movements.csv + catalog.

El Fermentario ran first on AppSheet (Google Sheets backend). This script
migrates that history into the format the program expects, following the
same error-handling policy as the main program:

  1. Never overwrite data silently.
  2. Never compute stock from data that failed validation.
  3. Always tell the user exactly what is wrong and how to fix it.

CONSOLIDATION NOTE (why this file is now shorter):
Everything that is NOT specific to AppSheet is imported from the shared
`fermentario` package instead of being re-implemented here:

  - the FIFO replay that finds impossible exits  -> core.compute_stock
    (this used to be a second copy of the algorithm — the riskiest kind of
    duplication, because a fix in one copy could silently miss the other);
  - the canonical row shape and its validator    -> core.build_movement /
    core.validate_movements (the file this writes is now guaranteed to load
    in the main program, because the SAME validator checks it before saving);
  - the reserved FLAVOR: prefix rule             -> core.looks_like_flavor_label;
  - reading/writing the data files atomically    -> FileStorage.

What stays here is only what belongs to the migration itself: parsing the
Spanish AppSheet columns, and the interactive de-duplication of flavor
spellings (a human decision the main program deliberately does not offer).

Expected input (CSV UTF-8, the shape AppSheet/Google Sheets exports):
  ID_Movimiento, Fecha, Tipo de movimiento, Lote, Sabor,
  ID_Producto_Escaneado, Cantidad (botellas), Responsable, Observaciones

Usage:
  python3 import_appsheet.py <appsheet_export.csv>
"""
import logging
import os
import sys
from datetime import datetime

# The whole point of the refactor: the importer speaks the same language as
# the program instead of re-declaring constants and re-coding the rules.
from fermentario import core, FileStorage, InventoryDataError, StorageError

# The shared modules log through the 'fermentario' logger. A one-shot CLI
# tool should stay quiet on stdout, so we swallow those records with a
# NullHandler (any real failure is still reported by our own messages below).
logging.getLogger("fermentario").addHandler(logging.NullHandler())

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# AppSheet-specific input contract (this is what genuinely belongs here):
REQUIRED_COLUMNS = ["Fecha", "Tipo de movimiento", "Lote", "Sabor",
                    "Cantidad (botellas)", "Responsable", "Observaciones"]
TYPE_MAP = {"entrada": "entry", "salida": "exit",
            "entry": "entry", "exit": "exit"}
# Dates as AppSheet exports them (a date, usually without time).
INPUT_DATE_FORMATS = ["%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
                      "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]


def parse_date(text):
    """Returns a datetime for any accepted AppSheet date shape, or None."""
    for date_format in INPUT_DATE_FORMATS:
        try:
            return datetime.strptime(text, date_format)
        except ValueError:
            continue
    return None


def read_export(path):
    """Reads and validates the AppSheet export against its OWN (Spanish)
    shape. Returns a list of (line_number, parsed_date, values) tuples ready
    to convert. Every problem is collected and reported at once; the import
    aborts if any exists. This validation is importer-specific and stays
    here; the converted output is checked again later with the shared
    validator, so nothing invalid can reach the file the program reads."""
    import csv
    try:
        with open(path, "r", newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            header = reader.fieldnames or []
            raw_rows = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        sys.exit(f"ERROR: could not read '{path}' ({e}).")

    missing = [column for column in REQUIRED_COLUMNS if column not in header]
    if missing:
        sys.exit("ERROR: the export is missing expected column(s): "
                 + ", ".join(missing)
                 + "\nExport the 'Movimientos' sheet again as CSV without "
                   "renaming its columns.")

    rows = []
    skipped_empty = []
    problems = []
    for line_number, row in enumerate(raw_rows, start=2):  # line 1 = header
        values = {column: (row.get(column) or "").strip()
                  for column in REQUIRED_COLUMNS}
        if not any(values.values()):
            skipped_empty.append(line_number)   # blank export artifact
            continue

        parsed_date = parse_date(values["Fecha"])
        if parsed_date is None:
            problems.append(f"Row {line_number}: unreadable Fecha "
                            f"'{values['Fecha']}' (expected DD/MM/YYYY).")
        if values["Tipo de movimiento"].lower() not in TYPE_MAP:
            problems.append(f"Row {line_number}: Tipo de movimiento "
                            f"'{values['Tipo de movimiento']}' must be "
                            "'Entrada' or 'Salida'.")
        if not values["Sabor"]:
            problems.append(f"Row {line_number}: Sabor is empty.")
        if not values["Lote"]:
            problems.append(f"Row {line_number}: Lote is empty.")
        elif core.looks_like_flavor_label(values["Lote"]):
            problems.append(f"Row {line_number}: Lote '{values['Lote']}' starts "
                            f"with '{core.FLAVOR_QR_PREFIX}', a reserved prefix.")
        if not values["Responsable"]:
            problems.append(f"Row {line_number}: Responsable is empty.")
        quantity = values["Cantidad (botellas)"]
        if not quantity.isdigit() or int(quantity) <= 0:
            problems.append(f"Row {line_number}: Cantidad (botellas) "
                            f"'{quantity}' must be an integer > 0.")

        rows.append((line_number, parsed_date, values))

    if skipped_empty:
        print(f"Skipped {len(skipped_empty)} completely empty row(s) "
              f"(file line(s): {', '.join(map(str, skipped_empty))}).")
    if problems:
        sys.exit("ERROR: the export has invalid rows. Nothing was imported. "
                 "Fix them and run again:\n  - " + "\n  - ".join(problems))
    if not rows:
        sys.exit("ERROR: the export has no data rows.")
    return rows


# ------------------------------------------------------------
# INTERACTIVE FLAVOR DE-DUPLICATION (importer-only by design)
# The main program's rename refuses to merge two existing flavors on
# purpose; the import is the one moment where merging is safe and easy.
# ------------------------------------------------------------
def simplified(name):
    """Key used to detect same-flavor spellings: case- and space-insensitive
    ('Limón con hierba buena' == 'Limón con hierbabuena')."""
    return "".join(name.lower().split())


def flavor_profiles(rows, mapping):
    """Per-flavor counts (rows, bottles in, bottles out) with any already
    accepted mapping applied. The in/out profile is the best clue for
    deciding whether two spellings are the same product."""
    profiles = {}
    for _, _, values in rows:
        name = values["Sabor"]
        name = mapping.get(name, name)
        record = profiles.setdefault(name, {"rows": 0, "in": 0, "out": 0})
        record["rows"] += 1
        quantity = int(values["Cantidad (botellas)"])
        if values["Tipo de movimiento"].lower() in ("entrada", "entry"):
            record["in"] += quantity
        else:
            record["out"] += quantity
    return profiles


def describe(name, profiles):
    record = profiles[name]
    return (f"{name} ({record['rows']} rows, "
            f"in {record['in']} / out {record['out']} bottles)")


def unify_flavor_spellings(rows, mapping):
    """Pass 1 — near-certain duplicates: names that only differ by case or
    spacing. The catalog cannot hold two of them anyway, so unifying is the
    default (Enter); 'k' keeps them separate."""
    profiles = flavor_profiles(rows, mapping)
    groups = {}
    for name in profiles:
        groups.setdefault(simplified(name), []).append(name)
    duplicated = [sorted(names, key=lambda n: -profiles[n]["rows"])
                  for names in groups.values() if len(names) > 1]
    if not duplicated:
        return

    print("\n--- Duplicate flavor spellings found ---")
    for names in duplicated:
        print("These spellings appear to be the same flavor:")
        for position, name in enumerate(names, start=1):
            print(f"  {position}. {describe(name, profiles)}")
        choice = input("Unify every row under which spelling? "
                       "(number, Enter = 1, 'k' = keep separate): ").strip().lower()
        if choice == "k":
            print("Kept separate.\n")
            continue
        index = int(choice) if choice.isdigit() and 1 <= int(choice) <= len(names) else 1
        canonical = names[index - 1]
        for name in names:
            if name != canonical:
                mapping[name] = canonical
        print(f"Unified as '{canonical}'.\n")


def review_related_flavors(rows, mapping):
    """Pass 2 — names that share their first word MAY be the same product
    (e.g. entries registered as 'Piña' and exits as 'Piña con chile'). Only
    a human can tell, so NOTHING is merged unless explicitly asked here.
    This moment matters: once imported, the program's rename (option 5)
    refuses to merge two existing flavors, on purpose."""
    profiles = flavor_profiles(rows, mapping)
    groups = {}
    for name in sorted(profiles):
        groups.setdefault(name.split()[0].lower(), []).append(name)
    candidates = [names for names in groups.values() if len(names) > 1]
    if not candidates:
        return

    print("--- Possibly related flavors (review) ---")
    print("Compare the in/out bottles: entries under one name and exits under")
    print("another usually mean the SAME product was registered two ways.")
    for names in candidates:
        print("\nThese names share their first word:")
        for position, name in enumerate(names, start=1):
            print(f"  {position}. {describe(name, profiles)}")
        while True:
            directive = input("Merge? type FROM->TO numbers (e.g. 2->1), "
                              "Enter = done with this group: ").strip()
            if not directive:
                break
            parts = directive.replace(" ", "").split("->")
            if (len(parts) != 2 or not all(part.isdigit() for part in parts)
                    or not all(1 <= int(part) <= len(names) for part in parts)
                    or parts[0] == parts[1]):
                print("Format: FROM->TO with two different numbers from the list.")
                continue
            source, target = names[int(parts[0]) - 1], names[int(parts[1]) - 1]
            mapping[source] = target
            print(f"'{source}' will be imported as '{target}'.")


def resolve_mapping(mapping):
    """Follows chains (A->B, B->C becomes A->C) with a cycle guard."""
    resolved = {}
    for name in mapping:
        target = name
        visited = {name}
        while target in mapping and mapping[target] not in visited:
            target = mapping[target]
            visited.add(target)
        resolved[name] = target
    return resolved


# ------------------------------------------------------------
# CONVERSION (AppSheet rows -> canonical movement rows)
# ------------------------------------------------------------
def convert(rows, mapping):
    """Sorts chronologically and converts every AppSheet row to the canonical
    movement shape using the SHARED builder, so the result is exactly what
    the rest of the system expects."""
    ordered = sorted(rows, key=lambda item: item[1])  # stable: keeps file order
    movements = []
    for movement_id, (_, parsed_date, values) in enumerate(ordered, start=1):
        flavor_name = mapping.get(values["Sabor"], values["Sabor"])
        movement_type = TYPE_MAP[values["Tipo de movimiento"].lower()]
        note = values["Observaciones"]
        if movement_type == "entry":
            lot_number = values["Lote"]
        else:
            # Exits carry no lot (FIFO decides); the lot AppSheet recorded is
            # preserved as documentation inside the note.
            lot_number = ""
            note = f"{note} [AppSheet lot: {values['Lote']}]".strip()
        movements.append(core.build_movement(
            movement_id,
            parsed_date.strftime(core.DATE_FORMAT),
            values["Responsable"],
            flavor_name,
            lot_number,
            movement_type,
            values["Cantidad (botellas)"],
            note,
        ))
    return movements


def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: python3 import_appsheet.py <appsheet_export.csv>")

    storage = FileStorage(SCRIPT_DIR)

    # Never overwrite an existing history: this is a first-migration tool.
    # Loading through storage also means a CORRUPT existing file is reported
    # precisely (instead of just counted), for free.
    try:
        existing, _ = storage.load_movements()
    except InventoryDataError as e:
        sys.exit(f"ERROR: {storage.movements_file} already exists and could not "
                 f"be validated:\n{e}\nMove or fix it, then run the import again.")
    if existing:
        sys.exit(f"ERROR: {storage.movements_file} already has {len(existing)} "
                 "movement(s). This importer only fills an empty history: "
                 "rename or back up that file first, then run again.")

    print("=== El Fermentario - AppSheet history import ===")
    rows = read_export(sys.argv[1])
    mapping = {}
    unify_flavor_spellings(rows, mapping)
    review_related_flavors(rows, mapping)
    movements = convert(rows, resolve_mapping(mapping))

    # Safety net (new): run the program's OWN validator on the converted rows
    # before writing. If this ever fires it is a bug in the importer, but it
    # guarantees the file we are about to write will load in the main program.
    conversion_problems = core.validate_movements([dict(m) for m in movements])
    if conversion_problems:
        sys.exit("INTERNAL ERROR: the converted rows did not pass validation. "
                 "Nothing was written. Please report this:\n  - "
                 + "\n  - ".join(conversion_problems))

    # Impossible exits: the SAME FIFO engine the main program uses at startup,
    # so the warnings here and there are guaranteed to match, word for word.
    _, inconsistencies = core.compute_stock(movements)
    if inconsistencies:
        print(f"\nWARNING: {len(inconsistencies)} exit(s) exceed the stock "
              "available at their point in the history (entries missing in "
              "AppSheet, or duplicate flavors kept separate). The main "
              "program will show these same warnings at startup:")
        for problem in inconsistencies[:10]:
            print(f"  - {problem}")
        if len(inconsistencies) > 10:
            print(f"  ... and {len(inconsistencies) - 10} more.")
        if input("Import anyway? (y/n): ").strip().lower() != "y":
            sys.exit("Import cancelled. Nothing was written.")

    # Existing catalog (validated by storage) or a fresh one.
    try:
        catalog = storage.load_catalog()
    except InventoryDataError as e:
        sys.exit(f"ERROR: the catalog file exists but could not be used:\n{e}\n"
                 "Move it away and run the import again.")

    flavor_names = sorted({m["flavor_name"] for m in movements})
    new_flavors = [name for name in flavor_names if name not in catalog["flavors"]]
    for name in new_flavors:
        catalog["flavors"][name] = {"minimum_stock": 0}
    staff_names = sorted({m["staff_name"] for m in movements})
    new_staff = [name for name in staff_names if name not in catalog["staff"]]
    catalog["staff"].extend(new_staff)

    entries = sum(1 for m in movements if m["movement_type"] == "entry")
    print(f"\nReady to write {len(movements)} movements "
          f"({entries} entries, {len(movements) - entries} exits), "
          f"add {len(new_flavors)} flavor(s) and {len(new_staff)} staff "
          "name(s) to the catalog.")
    if input("Proceed? (y/n): ").strip().lower() != "y":
        sys.exit("Import cancelled. Nothing was written.")

    # Atomic writes via storage (temp file + os.replace, StorageError on fail).
    # Movements first: the catalog is derived from them, so if only one write
    # can happen the source of truth is the one that lands.
    try:
        storage.save_movements(movements)
        storage.save_catalog(catalog)
    except StorageError as e:
        sys.exit(f"ERROR: could not write the data files ({e}). "
                 "Close Excel if it has them open and run again.")

    print(f"\nDone. {storage.movements_file} and {storage.data_file} were written.")
    print(f"Date range: {movements[0]['date_time'][:10]} to "
          f"{movements[-1]['date_time'][:10]}.")

    # Net stock per flavor for the sanity comparison. This is a RAW sum
    # (in - out), NOT the FIFO on-hand: it matches how a spreadsheet adds the
    # column, so it lines up with the AppSheet dashboard — and if data is
    # missing it can go negative, which is exactly the discrepancy you want
    # to see. The FIFO inconsistencies above already flag the same problem.
    totals = {}
    for movement in movements:
        quantity = int(movement["bottle_quantity"])
        delta = quantity if movement["movement_type"] == "entry" else -quantity
        totals[movement["flavor_name"]] = totals.get(movement["flavor_name"], 0) + delta
    print("\nNet stock per flavor (raw in - out; compare against the AppSheet dashboard):")
    for name in sorted(totals):
        print(f"  {name}: {totals[name]}")

    print("\nNext steps:")
    print("  - Run inventory_cli.py and review the startup warnings.")
    print("  - Option 5: set the real minimum_stock per flavor (all start at 0).")
    print("  - The program's rename (option 5) refuses to merge two existing")
    print("    flavors, on purpose. If you spot another duplicate later, fix")
    print("    its rows one by one with option 4, or move movements.csv away")
    print("    and re-run this import.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nImport cancelled. Nothing was written.")
