"""Application state for the web layer.

The service holds catalog + history IN MEMORY (exactly as the CLI does), so
this API is designed to run as a SINGLE process with a SINGLE worker. That
is the whole reason a hosted database is the next milestone: the moment you
run several workers, each one would keep its own copy of the history and
they would drift apart. Until then, one worker is correct and simple.

Two things live here:

  1. A single InventoryService instance, created once at startup.
  2. A lock that serializes access to it.

Why the lock, even locally? Our path operations are plain `def` functions,
so FastAPI runs them in a threadpool — two requests really can touch the
service at the same time. compute_stock() reads self.movements while a
register could be appending to it; without a lock that is a race. Holding
one lock per request makes every operation atomic. For a small inventory
app the serialization cost is irrelevant, and the hosted DB (with real
transactions) removes the need for it later.
"""

import os
import threading

from fermentario import FileStorage, InventoryService

# Data files live at the project root by default, the SAME place the CLI and
# the importer read/write them — so a movement registered from the terminal
# is immediately visible through the API, with no configuration. Override
# with FERMENTARIO_DATA_DIR to point somewhere else (e.g. a mounted volume).
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("FERMENTARIO_DATA_DIR", ROOT_DIR)

_service = None
_lock = threading.Lock()


def init_service():
    """Creates and loads the single service instance. Called once at startup.
    Propagates InventoryDataError untouched: if the data on disk is corrupt,
    the API must refuse to start rather than serve wrong numbers — the same
    stance the CLI takes. Returns the informational notices from loading."""
    global _service
    os.makedirs(DATA_DIR, exist_ok=True)
    service = InventoryService(FileStorage(DATA_DIR))
    notices = service.load()
    _service = service
    return notices


def get_service():
    """FastAPI dependency: yields the shared service with the lock held for
    the whole request, so each operation is atomic against the others."""
    if _service is None:  # pragma: no cover - guarded by startup
        raise RuntimeError("Service not initialised; startup did not run.")
    with _lock:
        yield _service
