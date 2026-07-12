"""El Fermentario — kombucha inventory, as a reusable package.

Layers (each one only talks to the one below):

    interfaces (inventory_cli.py today, web API tomorrow)
        -> service.InventoryService   operations + memory/disk consistency
            -> core                   pure business rules (no side effects)
            -> storage.FileStorage    files on disk (swappable for a DB)
"""

from . import core
from .service import InventoryService, MovementResult, OpResult
from .storage import FileStorage, InventoryDataError, StorageError

__all__ = ["core", "InventoryService", "MovementResult", "OpResult",
           "FileStorage", "InventoryDataError", "StorageError"]
