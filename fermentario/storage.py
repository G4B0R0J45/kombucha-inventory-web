"""Disk persistence for El Fermentario (catalog JSON + movements CSV).

RULE OF THIS MODULE: it talks to the disk and to the log, never to the
user. There is no print() and no input() here; problems are reported by
raising exceptions, and it is the interface layer (CLI today, web API
tomorrow) that decides how to show them.

The guarantees are the same as always:
  1. Never overwrite data silently (atomic writes: temp file + os.replace).
  2. Never hand over data that failed validation (raise InventoryDataError).
  3. Always say exactly what is wrong (exception messages carry the detail).

Swapping this class for one backed by SQLite/PostgreSQL later means the
service layer and the interfaces do not have to change at all.
"""

import csv
import json
import logging
import os

from . import core

logger = logging.getLogger(__name__)


class InventoryDataError(Exception):
    """Raised when a data file exists but its content cannot be trusted.
    The program refuses to run on corrupted data instead of guessing."""


class StorageError(Exception):
    """Raised when a write to disk fails. Callers roll back memory so that
    what the user sees always matches what the file contains."""


class FileStorage:
    """Catalog + movement history stored as files next to the program.
    `base_dir` makes the storage relocatable — which is also what lets the
    test suite run against a temporary directory instead of the real data."""

    def __init__(self, base_dir):
        self.data_file = os.path.join(base_dir, "inventory_data.json")
        self.movements_file = os.path.join(base_dir, "movements.csv")

    # --------------------------------------------------------
    # CATALOG (flavors + staff)
    # --------------------------------------------------------
    def load_catalog(self):
        """Loads and validates the catalog.
        Raises InventoryDataError when the file exists but cannot be trusted."""
        if not os.path.exists(self.data_file):
            logger.info("No catalog file found at %s. Starting fresh.", self.data_file)
            return {"flavors": {}, "staff": []}

        try:
            with open(self.data_file, "r", encoding="utf-8-sig") as file:
                data = json.load(file)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse catalog file %s: %s", self.data_file, e)
            raise InventoryDataError(
                f"{self.data_file} is not valid JSON ({e}).\n"
                "Fix the file by hand or restore it from git, then run the program again."
            )
        except (OSError, UnicodeDecodeError) as e:
            logger.error("Could not read catalog file %s: %s", self.data_file, e)
            raise InventoryDataError(f"Could not read {self.data_file}: {e}")

        if isinstance(data, dict) and "flavors" in data:
            data.setdefault("staff", [])
            problems = core.validate_catalog(data)
            if problems:
                logger.error("Catalog validation failed: %s", problems)
                raise InventoryDataError(
                    f"{self.data_file} has structural problems:\n  - " + "\n  - ".join(problems) +
                    "\nFix the file by hand or restore it from git."
                )
            logger.info("Catalog loaded from %s (%d flavors, %d staff)",
                        self.data_file, len(data["flavors"]), len(data["staff"]))
            return data

        if isinstance(data, dict) and any(isinstance(record, dict) and "current_stock" in record
                                          for record in data.values()):
            # The pre-Unit-III format stored the stock inside the JSON. It was
            # only ever used with test data, so it is rejected instead of migrated.
            logger.error("Old pre-history inventory format detected in %s", self.data_file)
            raise InventoryDataError(
                f"{self.data_file} uses the old format (stock stored in the JSON). This version "
                "does not migrate it: delete inventory_data.json and movements.csv to start "
                "fresh, or restore a current pair of files from git."
            )

        raise InventoryDataError(f"{self.data_file} does not look like a catalog.")

    def save_catalog(self, catalog):
        """Atomically persists the catalog: writes a temp file first and then
        replaces the real one, so a crash mid-write cannot corrupt it.
        Raises StorageError on failure so the caller can roll back memory."""
        temp_path = self.data_file + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as file:
                json.dump(catalog, file, indent=4, ensure_ascii=False)
            os.replace(temp_path, self.data_file)
            logger.info("Catalog saved to %s", self.data_file)
        except OSError as e:
            logger.error("Failed to save catalog to %s: %s", self.data_file, e)
            raise StorageError(f"the catalog could not be saved ({e})")

    # --------------------------------------------------------
    # MOVEMENT HISTORY (source of truth)
    # --------------------------------------------------------
    def load_movements(self):
        """Loads and validates the full movement history. Because this file is
        the source of truth, it refuses to load if any row is invalid: it
        reports every problem (row by row) instead of computing wrong stock.
        An empty or missing file is not an error: the header is (re)created.
        A legacy file without the 'note' column is upgraded automatically.
        Returns (movements, notices) — notices are informational messages the
        interface may want to show (e.g. the one-time legacy upgrade)."""
        notices = []
        try:
            if not os.path.exists(self.movements_file) or os.path.getsize(self.movements_file) == 0:
                with open(self.movements_file, "w", newline="", encoding="utf-8-sig") as file:
                    csv.DictWriter(file, fieldnames=core.CSV_FIELDS).writeheader()
                logger.info("Created new movements file at %s", self.movements_file)
                return [], notices
        except OSError as e:
            logger.error("Could not create movements file: %s", e)
            raise InventoryDataError(f"Could not create {self.movements_file}: {e}")

        try:
            with open(self.movements_file, "r", newline="", encoding="utf-8-sig") as file:
                reader = csv.DictReader(file)
                header = reader.fieldnames
                movements = list(reader)
        except csv.Error as e:
            logger.error("Movements file is not readable as CSV: %s", e)
            raise InventoryDataError(f"{self.movements_file} could not be parsed as CSV ({e}).")
        except (OSError, UnicodeDecodeError) as e:
            logger.error("Could not read movements file: %s", e)
            raise InventoryDataError(
                f"Could not read {self.movements_file}: {e}\n"
                "If the file was edited with Excel, save it again as 'CSV UTF-8'."
            )

        if header == core.LEGACY_CSV_FIELDS:
            # File created before the 'note' column existed: upgrade it in place.
            damaged = [str(row_number) for row_number, row in enumerate(movements, start=2)
                       if row.get(None) is not None
                       or any(row.get(field) is None for field in core.LEGACY_CSV_FIELDS)]
            if damaged:
                raise InventoryDataError(
                    f"{self.movements_file} uses the legacy format AND has rows with a wrong "
                    f"number of columns (rows: {', '.join(damaged)}). Fix those rows so the "
                    "automatic upgrade can run."
                )
            for row in movements:
                row["note"] = ""
            try:
                self.save_movements(movements)
            except StorageError:
                raise InventoryDataError(
                    f"{self.movements_file} needs the new 'note' column but could not be rewritten."
                )
            notices.append("movements.csv upgraded: 'note' column added.")
            logger.warning("Movements file upgraded with the 'note' column (%d rows)",
                           len(movements))
            header = core.CSV_FIELDS

        if header != core.CSV_FIELDS:
            logger.error("Movements file has unexpected header: %s", header)
            raise InventoryDataError(
                f"{self.movements_file} has an unexpected header.\n"
                f"Expected: {','.join(core.CSV_FIELDS)}\n"
                f"Found:    {','.join(header) if header else '(none)'}"
            )

        problems = core.validate_movements(movements)
        if problems:
            logger.error("Movement history validation failed (%d problems)", len(problems))
            raise InventoryDataError(
                f"{self.movements_file} has invalid rows. It is the source of truth, so the "
                "program will not run until they are fixed:\n  - " + "\n  - ".join(problems) +
                "\nOpen the file, correct those rows (or restore it from git) and run again."
            )

        logger.info("Movement history loaded (%d movements)", len(movements))
        return movements, notices

    def save_movements(self, movements):
        """Atomically rewrites the whole history file (used after corrections
        or renames). Raises StorageError on failure, so callers can keep
        memory and disk consistent."""
        temp_path = self.movements_file + ".tmp"
        try:
            with open(temp_path, "w", newline="", encoding="utf-8-sig") as file:
                writer = csv.DictWriter(file, fieldnames=core.CSV_FIELDS)
                writer.writeheader()
                writer.writerows(movements)
            os.replace(temp_path, self.movements_file)
            logger.info("Movement history rewritten (%d movements)", len(movements))
        except OSError as e:
            logger.error("Failed to save movements file: %s", e)
            raise StorageError(f"the movement history could not be saved ({e})")

    def append_movement(self, movement):
        """Appends one successful movement to the history file. Plain utf-8
        here: appending with utf-8-sig would inject a BOM in the middle of
        the file. Raises StorageError on failure (the caller rolls back)."""
        try:
            with open(self.movements_file, "a", newline="", encoding="utf-8") as file:
                csv.DictWriter(file, fieldnames=core.CSV_FIELDS).writerow(movement)
            logger.info("Movement %s appended to history", movement["movement_id"])
        except OSError as e:
            logger.error("Failed to append movement: %s", e)
            raise StorageError(f"the movement could not be written to movements.csv ({e})")
