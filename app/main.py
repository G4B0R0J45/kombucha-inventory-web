"""El Fermentario — HTTP API over the shared service layer.

This file is to the web what inventory_cli.py is to the terminal: the ONLY
place that knows about the transport (here, HTTP). It gathers the request,
calls fermentario.InventoryService, and serializes the result. There is no
business rule and no file handling here.

Endpoint map (mirrors the CLI menu):

    GET    /health                     liveness
    GET    /inventory                  current stock per flavor (+ warnings)
    GET    /warnings                   startup-style consistency warnings
    GET    /sales                       sales summary (every exit is a sale)

    GET    /movements                  history (?limit=&flavor=)
    POST   /movements                  register an entry/exit
    GET    /movements/{id}             one movement
    PUT    /movements/{id}             edit one movement
    DELETE /movements/{id}             delete one movement

    GET    /flavors                    catalog + current totals
    POST   /flavors                    add a flavor
    PATCH  /flavors/{name}             change its minimum stock
    POST   /flavors/{name}/rename      rename (updates the whole history)
    DELETE /flavors/{name}             delete a flavor (?confirm=true if it has history)

    GET    /staff                      staff list
    POST   /staff                      add a staff member
    DELETE /staff/{name}               remove a staff member

Run it locally:  uvicorn app.main:app --reload
Interactive docs: http://127.0.0.1:8000/docs
"""

import logging
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from fermentario import core, InventoryDataError, InventoryService
from .deps import get_service, init_service
from .schemas import (FlavorCreate, FlavorRename, MinimumUpdate, MovementCreate,
                      MovementEdit, MovementResponse, OpResponse, StaffCreate)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fermentario.api")

# Transaction states from core that deserve a non-400 HTTP code.
_CONFLICT_STATES = {"Error: Insufficient stock"}
_SERVER_STATES = {"Error: Not saved", "Error: Catalog not saved"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Loads the data once, at startup. If the files are corrupt the API
    refuses to start (the same 'never serve unvalidated data' stance the
    CLI takes), printing the precise reason."""
    try:
        notices = init_service()
    except InventoryDataError as e:
        # Fail loudly and clearly instead of starting on bad data.
        print(f"\nDATA ERROR:\n{e}\n"
              "The API will not start until the data files are fixed.\n")
        raise
    for notice in notices:
        logger.info("Startup notice: %s", notice)
    yield


app = FastAPI(title="El Fermentario API", version="0.1.0", lifespan=lifespan)

# Allow the future PWA frontend (served from another origin in dev) to call
# the API. Tighten allow_origins to the real frontend URL in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------
# HELPERS: turn a service result into a response or an HTTP error
# ------------------------------------------------------------
def _op_or_error(result):
    """OpResult -> its serialized body, or a 400 with the message. Not-found
    and conflict cases are handled by the callers before reaching here, so a
    remaining failure is a business-rule rejection."""
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.message)
    return asdict(result)


# ------------------------------------------------------------
# HEALTH / OVERVIEW
# ------------------------------------------------------------
@app.get("/health", tags=["system"])
def health():
    return {"status": "ok"}


@app.get("/inventory", tags=["overview"])
def get_inventory(service: InventoryService = Depends(get_service)):
    rows, inconsistencies = service.inventory_rows()
    return {"inventory": rows, "inconsistencies": inconsistencies}


@app.get("/warnings", tags=["overview"])
def get_warnings(service: InventoryService = Depends(get_service)):
    return {"warnings": service.consistency_warnings()}


@app.get("/sales", tags=["overview"])
def get_sales(service: InventoryService = Depends(get_service)):
    summary = service.sales_summary()
    return {"has_sales": summary is not None, "summary": summary}


# ------------------------------------------------------------
# MOVEMENTS
# ------------------------------------------------------------
@app.get("/movements", tags=["movements"])
def list_movements(
    service: InventoryService = Depends(get_service),
    limit: int = Query(default=None, ge=1, description="Return only the last N movements."),
    flavor: str = Query(default=None, description="Filter by flavor name (case-insensitive)."),
):
    rows = service.history(limit=limit, flavor_name=flavor)
    return {"count": len(rows), "movements": rows}


@app.get("/movements/{movement_id}", tags=["movements"])
def get_movement(movement_id: str, service: InventoryService = Depends(get_service)):
    movement = service.find_movement(movement_id)
    if movement is None:
        raise HTTPException(status_code=404, detail="No movement with that ID.")
    return movement


@app.post("/movements", status_code=201, response_model=MovementResponse, tags=["movements"])
def create_movement(body: MovementCreate, service: InventoryService = Depends(get_service)):
    # Interface-level rule (mirrors the CLI): an entry needs a lot number.
    if body.movement_type == "entry" and not body.lot_number.strip():
        raise HTTPException(status_code=422, detail="An entry needs a lot_number.")
    if core.looks_like_flavor_label(body.lot_number):
        raise HTTPException(status_code=422,
                            detail=f"lot_number must not start with '{core.FLAVOR_QR_PREFIX}'.")

    # Staff invariant, matching the CLI: on an empty roster the first name is
    # registered (first-run bootstrap); afterwards a movement must reference a
    # known staff member — the frontend picks one from GET /staff. (Identity
    # will come from login once auth lands, making this moot.)
    staff_name = body.staff_name.strip()
    if staff_name not in service.catalog["staff"]:
        if service.catalog["staff"]:
            raise HTTPException(status_code=422,
                                detail=f"Unknown staff '{staff_name}'. Add them via POST /staff first.")
        bootstrap = service.add_staff(staff_name)
        if not bootstrap.ok:
            raise HTTPException(status_code=500, detail=bootstrap.message)

    result = service.register_movement(
        staff_name, body.flavor_name.strip(), body.movement_type,
        body.bottle_quantity,
        lot_number=body.lot_number.strip(),
        note=body.note.strip(),
        new_flavor_minimum=body.new_flavor_minimum,
    )

    if not result.ok:
        status_code = (409 if result.transaction_status in _CONFLICT_STATES
                       else 500 if result.transaction_status in _SERVER_STATES
                       else 400)
        raise HTTPException(status_code=status_code,
                            detail={"transaction_status": result.transaction_status,
                                    "message": result.message,
                                    "current_stock": result.current_stock})

    # On success the new row is the last one in the history (safe to read:
    # the request lock means nothing else appended in between).
    created = service.movements[-1]
    return MovementResponse(
        movement_id=created["movement_id"],
        transaction_status=result.transaction_status,
        stock_status=result.stock_status,
        current_stock=result.current_stock,
        flavor_name=result.flavor_name,
        lot_display=result.lot_display,
        movement=created,
    )


@app.put("/movements/{movement_id}", response_model=OpResponse, tags=["movements"])
def edit_movement(movement_id: str, body: MovementEdit,
                  service: InventoryService = Depends(get_service)):
    target = service.find_movement(movement_id)
    if target is None:
        raise HTTPException(status_code=404, detail="No movement with that ID.")

    # Start from the existing row and overlay only the provided fields — the
    # same "Enter keeps the current value" idea the CLI uses.
    edited = dict(target)
    updates = body.model_dump(exclude_unset=True)

    if "flavor_name" in updates:
        canonical = core.find_flavor_case_insensitive(service.catalog, updates["flavor_name"])
        if canonical is None:
            raise HTTPException(status_code=422,
                                detail="That flavor is not in the catalog; create or rename it first.")
        updates["flavor_name"] = canonical
    if "date_time" in updates:
        try:
            core.datetime.strptime(updates["date_time"], core.DATE_FORMAT)
        except ValueError:
            raise HTTPException(status_code=422,
                                detail=f"date_time must match {core.DATE_FORMAT}.")
    if "bottle_quantity" in updates:
        updates["bottle_quantity"] = str(updates["bottle_quantity"])

    edited.update(updates)
    # Exits never carry a lot; an entry must keep one.
    if edited["movement_type"] == "exit":
        edited["lot_number"] = ""
    elif not edited["lot_number"].strip():
        raise HTTPException(status_code=422, detail="An entry needs a lot_number.")
    if core.looks_like_flavor_label(edited["lot_number"]):
        raise HTTPException(status_code=422,
                            detail=f"lot_number must not start with '{core.FLAVOR_QR_PREFIX}'.")

    result = service.edit_movement(movement_id, edited)
    return _op_or_error(result)


@app.delete("/movements/{movement_id}", response_model=OpResponse, tags=["movements"])
def delete_movement(movement_id: str, service: InventoryService = Depends(get_service)):
    if service.find_movement(movement_id) is None:
        raise HTTPException(status_code=404, detail="No movement with that ID.")
    return _op_or_error(service.delete_movement(movement_id))


# ------------------------------------------------------------
# FLAVORS
# ------------------------------------------------------------
@app.get("/flavors", tags=["flavors"])
def list_flavors(service: InventoryService = Depends(get_service)):
    rows, _ = service.inventory_rows()
    flavors = [
        {"flavor_name": row["flavor_name"], "minimum_stock": row["minimum_stock"],
         "total": row["total"], "stock_status": row["stock_status"]}
        for row in rows if row["in_catalog"]
    ]
    return {"count": len(flavors), "flavors": flavors}


@app.post("/flavors", status_code=201, response_model=OpResponse, tags=["flavors"])
def add_flavor(body: FlavorCreate, service: InventoryService = Depends(get_service)):
    existing = core.find_flavor_case_insensitive(service.catalog, body.flavor_name.strip())
    if existing:
        raise HTTPException(status_code=409,
                            detail=f"A flavor with that name already exists: '{existing}'.")
    return _op_or_error(service.add_flavor(body.flavor_name.strip(), body.minimum_stock))


@app.patch("/flavors/{name}", response_model=OpResponse, tags=["flavors"])
def update_minimum(name: str, body: MinimumUpdate,
                   service: InventoryService = Depends(get_service)):
    if name not in service.catalog["flavors"]:
        raise HTTPException(status_code=404, detail=f"'{name}' is not in the catalog.")
    return _op_or_error(service.set_minimum_stock(name, body.minimum_stock))


@app.post("/flavors/{name}/rename", response_model=OpResponse, tags=["flavors"])
def rename_flavor(name: str, body: FlavorRename,
                  service: InventoryService = Depends(get_service)):
    if name not in service.catalog["flavors"]:
        raise HTTPException(status_code=404, detail=f"'{name}' is not in the catalog.")
    existing = core.find_flavor_case_insensitive(service.catalog, body.new_name.strip())
    if existing and existing != name:
        raise HTTPException(status_code=409,
                            detail=f"That name already exists ('{existing}'); "
                                   "renaming would merge two histories.")
    return _op_or_error(service.rename_flavor(name, body.new_name.strip()))


@app.delete("/flavors/{name}", response_model=OpResponse, tags=["flavors"])
def delete_flavor(name: str, service: InventoryService = Depends(get_service),
                  confirm: bool = Query(default=False,
                                        description="Required (true) when the flavor has history rows.")):
    if name not in service.catalog["flavors"]:
        raise HTTPException(status_code=404, detail=f"'{name}' is not in the catalog.")
    related = service.flavor_history_count(name)
    if related and not confirm:
        # Guard the destructive case the CLI protects with a typed confirmation.
        raise HTTPException(
            status_code=409,
            detail=f"'{name}' has {related} movement(s) in the history. "
                   "Deleting it will erase those rows. Re-send with ?confirm=true to proceed.")
    return _op_or_error(service.delete_flavor(name))


# ------------------------------------------------------------
# STAFF
# ------------------------------------------------------------
@app.get("/staff", tags=["staff"])
def list_staff(service: InventoryService = Depends(get_service)):
    return {"staff": service.catalog["staff"]}


@app.post("/staff", status_code=201, response_model=OpResponse, tags=["staff"])
def add_staff(body: StaffCreate, service: InventoryService = Depends(get_service)):
    result = service.add_staff(body.name.strip())
    if not result.ok:
        # Duplicate name is the only expected failure here.
        raise HTTPException(status_code=409, detail=result.message)
    return asdict(result)


@app.delete("/staff/{name}", response_model=OpResponse, tags=["staff"])
def remove_staff(name: str, service: InventoryService = Depends(get_service)):
    staff = service.catalog["staff"]
    if name not in staff:
        raise HTTPException(status_code=404, detail=f"'{name}' is not a registered staff member.")
    # The service removes by 1-based index; translate the name to its position.
    return _op_or_error(service.remove_staff(staff.index(name) + 1))
