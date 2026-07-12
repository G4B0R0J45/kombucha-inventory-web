"""Request and response shapes for the API.

Pydantic validates every incoming body at the edge — quantity > 0, movement
type is 'entry' or 'exit', names are non-empty — so malformed input is
rejected with a clear 422 before it ever reaches the service. The service
still re-validates (defense in depth), but this gives callers immediate,
well-documented errors and powers the automatic /docs page.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ------------------------------------------------------------
# REQUESTS
# ------------------------------------------------------------
class MovementCreate(BaseModel):
    staff_name: str = Field(min_length=1)
    flavor_name: str = Field(min_length=1)
    movement_type: Literal["entry", "exit"]
    bottle_quantity: int = Field(gt=0)
    # Required for entries; ignored for exits (FIFO decides the lot).
    lot_number: str = ""
    note: str = ""
    # When set and the flavor does not exist, it is created — but only if this
    # first movement succeeds (the service guarantees no ghost flavors).
    new_flavor_minimum: Optional[int] = Field(default=None, ge=0)


class MovementEdit(BaseModel):
    """Every field optional: only the ones provided are changed. movement_id
    is taken from the URL, never the body, so a movement cannot rewrite its
    own id. For an exit the lot is always cleared (FIFO owns exit lots)."""
    date_time: Optional[str] = None
    staff_name: Optional[str] = Field(default=None, min_length=1)
    flavor_name: Optional[str] = Field(default=None, min_length=1)
    movement_type: Optional[Literal["entry", "exit"]] = None
    lot_number: Optional[str] = None
    bottle_quantity: Optional[int] = Field(default=None, gt=0)
    note: Optional[str] = None


class FlavorCreate(BaseModel):
    flavor_name: str = Field(min_length=1)
    minimum_stock: int = Field(ge=0)


class FlavorRename(BaseModel):
    new_name: str = Field(min_length=1)


class MinimumUpdate(BaseModel):
    minimum_stock: int = Field(ge=0)


class StaffCreate(BaseModel):
    name: str = Field(min_length=1)


# ------------------------------------------------------------
# RESPONSES (documented shapes; the service returns the data)
# ------------------------------------------------------------
class OpResponse(BaseModel):
    ok: bool
    message: str = ""
    data: dict = {}


class MovementResponse(BaseModel):
    movement_id: str
    transaction_status: str
    stock_status: str
    current_stock: int
    flavor_name: str
    lot_display: str
    movement: dict
