"""Application service for El Fermentario.

This is the layer every interface talks to. The CLI calls these methods
today; a FastAPI endpoint will call the SAME methods tomorrow — that is
the whole point of the refactor.

RULES OF THIS MODULE:
  - No input() and no print(): parameters in, result objects out.
  - It owns the in-memory state (catalog + movements) and the invariant
    that memory always matches disk: every operation saves first (or
    rolls back memory when the save fails).
  - Business rules live in core.py; disk details live in storage.py.

Every mutating method returns an OpResult (or MovementResult) instead of
printing, so each interface can phrase the outcome its own way.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from . import core
from .storage import StorageError

logger = logging.getLogger(__name__)


@dataclass
class OpResult:
    """Outcome of one operation: did it work, and what should the user know."""
    ok: bool
    message: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class MovementResult:
    """Outcome of registering one movement. Carries every value the old
    display_result() printed, plus the FIFO breakdown for exits."""
    ok: bool
    transaction_status: str
    stock_status: str
    current_stock: int
    flavor_name: str
    lot_display: str
    message: str = ""


class InventoryService:
    def __init__(self, storage):
        self.storage = storage
        self.catalog = {"flavors": {}, "staff": []}
        self.movements = []

    # --------------------------------------------------------
    # STARTUP
    # --------------------------------------------------------
    def load(self):
        """Loads catalog + history from storage. Propagates
        InventoryDataError untouched: the program must refuse to run on
        corrupted data, and only the interface knows how to say that.
        Returns the informational notices produced while loading."""
        self.catalog = self.storage.load_catalog()
        self.movements, notices = self.storage.load_movements()
        return notices

    def consistency_warnings(self):
        """Startup report of problems that are legal in the files but deserve
        attention (previously check_consistency). It never blocks."""
        stock, inconsistencies = core.compute_stock(self.movements)
        warnings = [f"WARNING (history): {problem}" for problem in inconsistencies]

        orphans = [flavor_name for flavor_name in sorted(stock)
                   if flavor_name not in self.catalog["flavors"]
                   and (stock[flavor_name]["total"] != 0 or stock[flavor_name]["lots"])]
        for flavor_name in orphans:
            warnings.append(
                f"WARNING: '{flavor_name}' appears in the history but not in the catalog. "
                "Add it in the catalog editor (option 5), or correct the history (option 4).")

        if inconsistencies or orphans:
            logger.warning("Startup check: %d inconsistencies, %d flavors missing from catalog",
                           len(inconsistencies), len(orphans))
        return warnings

    # --------------------------------------------------------
    # READ OPERATIONS (views)
    # --------------------------------------------------------
    def stock(self):
        """Current stock derived from the history: (stock, inconsistencies)."""
        return core.compute_stock(self.movements)

    def inventory_rows(self):
        """Inventory ready to render, plus any history inconsistencies."""
        stock, inconsistencies = core.compute_stock(self.movements)
        return core.inventory_rows(self.catalog, stock), inconsistencies

    def history(self, limit=None, flavor_name=None):
        """Movement rows, optionally the last `limit` or filtered by flavor."""
        rows = self.movements
        if flavor_name is not None:
            rows = [m for m in rows if m["flavor_name"].lower() == flavor_name.lower()]
        if limit is not None:
            rows = rows[-limit:]
        return list(rows)

    def find_movement(self, movement_id):
        """Returns the history row with that id, or None."""
        for movement in self.movements:
            if movement["movement_id"] == movement_id:
                return movement
        return None

    def sales_summary(self):
        """Sales aggregation (every exit counts as a sale), or None."""
        return core.sales_summary(self.movements)

    # --------------------------------------------------------
    # MOVEMENT REGISTRATION
    # --------------------------------------------------------
    def register_movement(self, staff_name, flavor_name, movement_type,
                          bottle_quantity, lot_number="", note="",
                          new_flavor_minimum=None):
        """Runs one movement and appends it to the history.

        When the flavor does not exist and new_flavor_minimum is given, the
        flavor is created — but only persisted if its movement succeeds; on
        any failure it is discarded, so no ghost flavors are created (same
        guarantee as always, now enforced in one place for every interface).
        If the movement cannot be written to disk, it is rolled back from
        memory too."""
        flavor_is_new = flavor_name not in self.catalog["flavors"]
        if flavor_is_new:
            if new_flavor_minimum is None:
                return MovementResult(False, "Error: Unknown flavor", "", 0,
                                      flavor_name, "-",
                                      f"'{flavor_name}' is not in the catalog.")
            self.catalog["flavors"][flavor_name] = {"minimum_stock": new_flavor_minimum}

        minimum_stock = self.catalog["flavors"][flavor_name]["minimum_stock"]

        # PROCESS: previous_stock is loaded from the history before calculating.
        stock_before, _ = core.compute_stock(self.movements)
        previous_stock = core.get_flavor_stock(stock_before, flavor_name)["total"]

        current_stock, transaction_status, stock_status = core.process_movement(
            movement_type, bottle_quantity, previous_stock, minimum_stock
        )

        lot_display = lot_number if lot_number else "-"

        if transaction_status != "Success":
            message = ""
            if flavor_is_new:
                # A failed movement must not leave a brand-new flavor behind.
                del self.catalog["flavors"][flavor_name]
                message = (f"Note: the new flavor '{flavor_name}' was NOT created "
                           "because the movement failed.")
            logger.error("Movement failed: %s %s | Qty: %d | Reason: %s",
                         movement_type, flavor_name, bottle_quantity, transaction_status)
            if stock_status == "LOW STOCK":
                logger.warning("LOW STOCK for '%s': current=%d, minimum=%d",
                               flavor_name, current_stock, minimum_stock)
            return MovementResult(False, transaction_status, stock_status,
                                  current_stock, flavor_name, lot_display, message)

        if flavor_is_new:
            try:
                self.storage.save_catalog(self.catalog)
            except StorageError:
                del self.catalog["flavors"][flavor_name]
                return MovementResult(False, "Error: Catalog not saved", stock_status,
                                      previous_stock, flavor_name, lot_display,
                                      "Movement cancelled: the new flavor could not be saved.")
            logger.warning("New flavor '%s' created (minimum %d)", flavor_name, minimum_stock)

        movement = core.build_movement(
            core.next_movement_id(self.movements),
            datetime.now().strftime(core.DATE_FORMAT),
            staff_name, flavor_name, lot_number, movement_type, bottle_quantity, note,
        )
        self.movements.append(movement)
        try:
            self.storage.append_movement(movement)
        except StorageError as e:
            self.movements.pop()  # roll back: memory must match the file
            if flavor_is_new:
                del self.catalog["flavors"][flavor_name]
                try:  # best effort to undo the flavor on disk
                    self.storage.save_catalog(self.catalog)
                except StorageError:
                    pass
            return MovementResult(False, "Error: Not saved", stock_status,
                                  previous_stock, flavor_name, lot_display,
                                  f"ERROR: {e}, so the movement was NOT registered.")

        if movement_type == "exit":
            # FIFO breakdown: diff lots before vs after, to tell the user
            # which physical lots the bottles must be taken from.
            stock_after, _ = core.compute_stock(self.movements)
            breakdown = core.fifo_breakdown(
                core.get_flavor_stock(stock_before, flavor_name)["lots"],
                core.get_flavor_stock(stock_after, flavor_name)["lots"],
            )
            lot_display = ", ".join(f"{lot} ({taken})" for lot, taken in breakdown)

        logger.info("Movement successful: %s %s | Qty: %d | Stock: %d -> %d | By: %s",
                    movement_type, flavor_name, bottle_quantity,
                    previous_stock, current_stock, staff_name)
        if stock_status == "LOW STOCK":
            logger.warning("LOW STOCK for '%s': current=%d, minimum=%d",
                           flavor_name, current_stock, minimum_stock)

        return MovementResult(True, transaction_status, stock_status,
                              current_stock, flavor_name, lot_display)

    # --------------------------------------------------------
    # HISTORY CORRECTION
    # --------------------------------------------------------
    def _apply_history_change(self, candidate, movement_id, action):
        """Shared tail of edit/delete: validate the corrected history before
        persisting it. Only NEW problems block the change (pre-existing ones
        should not lock the editor). The file is saved BEFORE memory is
        updated, so both always stay in sync."""
        new_problems = core.new_problems_after_change(self.movements, candidate)
        if new_problems:
            logger.error("History correction rejected for movement %s (%d problems)",
                         movement_id, len(new_problems))
            return OpResult(False,
                            "Change rejected — it would make the history inconsistent:",
                            {"problems": new_problems})
        try:
            self.storage.save_movements(candidate)
        except StorageError:
            return OpResult(False,
                            "The correction was NOT applied: the file on disk is unchanged.")
        self.movements[:] = candidate
        logger.warning("History corrected: movement %s (%s)", movement_id, action)
        return OpResult(True, "History updated and stock recalculated.")

    def delete_movement(self, movement_id):
        """Deletes one row from the history (after replay validation)."""
        if self.find_movement(movement_id) is None:
            return OpResult(False, "Error: no movement with that ID.")
        candidate = [m for m in self.movements if m["movement_id"] != movement_id]
        return self._apply_history_change(candidate, movement_id, "deleted")

    def edit_movement(self, movement_id, edited):
        """Replaces one row with `edited` (a complete row dict). The row is
        structurally validated against the rest of the history, then the
        replay check runs. Note: editing the date is documentary only — FIFO
        follows row order (the IDs), never the date."""
        if self.find_movement(movement_id) is None:
            return OpResult(False, "Error: no movement with that ID.")
        # Defense in depth (new with the refactor): the CLI validates each
        # field as it is typed, but a future web form might not — so the
        # final row is re-validated here no matter who built it.
        seen_ids = {m["movement_id"] for m in self.movements
                    if m["movement_id"] != movement_id}
        problem = core.validate_movement_row(dict(edited), "being edited", seen_ids)
        if problem:
            return OpResult(False, f"Error: {problem}")
        candidate = [edited if m["movement_id"] == movement_id else m
                     for m in self.movements]
        return self._apply_history_change(candidate, movement_id, "edited")

    # --------------------------------------------------------
    # CATALOG: FLAVORS
    # --------------------------------------------------------
    def add_flavor(self, flavor_name, minimum_stock):
        """Adds a flavor to the catalog and persists it (option 5 path).
        Reverted in memory if it cannot be saved."""
        if not flavor_name:
            return OpResult(False, "Error: flavor name cannot be empty.")
        existing = core.find_flavor_case_insensitive(self.catalog, flavor_name)
        if existing:
            return OpResult(False, f"Error: a flavor with that name already exists: '{existing}'.")
        if minimum_stock < 0:
            return OpResult(False, "Error: minimum stock cannot be negative.")
        self.catalog["flavors"][flavor_name] = {"minimum_stock": minimum_stock}
        try:
            self.storage.save_catalog(self.catalog)
        except StorageError as e:
            del self.catalog["flavors"][flavor_name]
            return OpResult(False, f"ERROR: {e}. The flavor was NOT created.")
        logger.warning("New flavor '%s' created (minimum %d)", flavor_name, minimum_stock)
        return OpResult(True, f"Flavor '{flavor_name}' created.")

    def rename_flavor(self, old_name, new_name):
        """Renames a flavor and propagates the change to the whole history.
        The history is saved first; the catalog only changes if that succeeds."""
        if not new_name:
            return OpResult(False, "Error: new name cannot be empty.")
        if old_name not in self.catalog["flavors"]:
            return OpResult(False, f"Error: '{old_name}' is not in the catalog.")
        existing = core.find_flavor_case_insensitive(self.catalog, new_name)
        if existing and existing != old_name:
            return OpResult(False, f"Error: that name already exists ('{existing}'); "
                                   "renaming would merge two histories.")

        candidate = []
        renamed = 0
        for movement in self.movements:
            if movement["flavor_name"] == old_name:
                updated = dict(movement)
                updated["flavor_name"] = new_name
                candidate.append(updated)
                renamed += 1
            else:
                candidate.append(movement)

        try:
            self.storage.save_movements(candidate)
        except StorageError as e:
            return OpResult(False, f"ERROR: {e}. Rename cancelled: nothing was changed.")
        self.movements[:] = candidate
        self.catalog["flavors"][new_name] = self.catalog["flavors"].pop(old_name)
        warning = ""
        try:
            self.storage.save_catalog(self.catalog)
        except StorageError:
            warning = ("WARNING: the history was renamed but the catalog file was not. "
                       "It will fix itself with the next successful catalog save; "
                       f"if you exit now, rename '{old_name}' to '{new_name}' in "
                       "inventory_data.json by hand.")
        logger.warning("Flavor renamed: '%s' -> '%s' (%d movements)", old_name, new_name, renamed)
        return OpResult(True, f"Renamed '{old_name}' to '{new_name}' "
                              f"({renamed} history rows updated).",
                        {"renamed": renamed, "warning": warning})

    def set_minimum_stock(self, flavor_name, minimum_stock):
        """Updates a flavor's minimum stock. Reverted in memory if it cannot
        be saved. Returns the resulting total and status for that flavor."""
        if flavor_name not in self.catalog["flavors"]:
            return OpResult(False, f"Error: '{flavor_name}' is not in the catalog.")
        if minimum_stock < 0:
            return OpResult(False, "Error: minimum stock cannot be negative.")
        current_minimum = self.catalog["flavors"][flavor_name]["minimum_stock"]
        self.catalog["flavors"][flavor_name]["minimum_stock"] = minimum_stock
        try:
            self.storage.save_catalog(self.catalog)
        except StorageError as e:
            self.catalog["flavors"][flavor_name]["minimum_stock"] = current_minimum
            return OpResult(False, f"ERROR: {e}. The minimum was NOT changed.")
        stock, _ = core.compute_stock(self.movements)
        total = core.get_flavor_stock(stock, flavor_name)["total"]
        stock_status = "LOW STOCK" if total <= minimum_stock else "OK"
        logger.info("Minimum stock for '%s' set to %d", flavor_name, minimum_stock)
        return OpResult(True, f"Minimum updated. Current total: {total} -> Status: {stock_status}",
                        {"total": total, "stock_status": stock_status})

    def flavor_history_count(self, flavor_name):
        """How many history rows belong to a flavor (the delete warning)."""
        return sum(1 for m in self.movements if m["flavor_name"] == flavor_name)

    def delete_flavor(self, flavor_name):
        """Deletes a flavor and (if any) its history rows. The history is
        saved first; the catalog only changes if that succeeds. The
        interface is responsible for asking the strong confirmation."""
        if flavor_name not in self.catalog["flavors"]:
            return OpResult(False, f"Error: '{flavor_name}' is not in the catalog.")
        related = self.flavor_history_count(flavor_name)
        if related:
            candidate = [m for m in self.movements if m["flavor_name"] != flavor_name]
            try:
                self.storage.save_movements(candidate)
            except StorageError as e:
                return OpResult(False, f"ERROR: {e}. Deletion cancelled: nothing was changed.")
            self.movements[:] = candidate

        del self.catalog["flavors"][flavor_name]
        warning = ""
        try:
            self.storage.save_catalog(self.catalog)
        except StorageError:
            warning = ("WARNING: the flavor's history rows were removed but the catalog file "
                       "was not updated. It will fix itself with the next successful catalog save.")
        logger.warning("Flavor '%s' deleted (%d history rows removed)", flavor_name, related)
        return OpResult(True, f"Flavor '{flavor_name}' deleted.",
                        {"removed_rows": related, "warning": warning})

    # --------------------------------------------------------
    # CATALOG: STAFF
    # --------------------------------------------------------
    def add_staff(self, name, keep_in_memory_on_save_failure=False):
        """Adds a staff member. Normally reverted in memory when the save
        fails; the first-run flow keeps the name for the session instead
        (same behavior as always), which is what the flag is for."""
        if not name:
            return OpResult(False, "Error: name cannot be empty.")
        if name in self.catalog["staff"]:
            return OpResult(False, "That name is already registered.")
        self.catalog["staff"].append(name)
        try:
            self.storage.save_catalog(self.catalog)
        except StorageError as e:
            if keep_in_memory_on_save_failure:
                logger.info("Staff member '%s' kept for this session only", name)
                return OpResult(True, f"WARNING: {e}; "
                                      "the name will be used for this session only.")
            self.catalog["staff"].remove(name)
            return OpResult(False, f"ERROR: {e}. The name was NOT added.")
        logger.info("Staff member '%s' added", name)
        return OpResult(True, f"'{name}' added.")

    def remove_staff(self, index):
        """Removes the staff member at 1-based `index`. Reverted on failure."""
        if not 1 <= index <= len(self.catalog["staff"]):
            return OpResult(False, "Error: choose a valid number.")
        removed = self.catalog["staff"].pop(index - 1)
        try:
            self.storage.save_catalog(self.catalog)
        except StorageError as e:
            self.catalog["staff"].insert(index - 1, removed)
            return OpResult(False, f"ERROR: {e}. The name was NOT removed.")
        logger.info("Staff member '%s' removed", removed)
        return OpResult(True, f"'{removed}' removed (past history rows keep the name).")
