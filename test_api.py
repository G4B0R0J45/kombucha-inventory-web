"""End-to-end API smoke test. Points the service at a temp dir, then drives
the endpoints the way a frontend would, asserting both happy paths and the
error codes. Run: python3 test_api.py"""
import os
import sys
import tempfile

os.environ["FERMENTARIO_DATA_DIR"] = tempfile.mkdtemp()

from fastapi.testclient import TestClient
from app.main import app

with TestClient(app) as client:  # 'with' triggers the lifespan (loads data)
    # health
    assert client.get("/health").json() == {"status": "ok"}

    # empty inventory to start
    assert client.get("/inventory").json()["inventory"] == []

    # register an entry that also creates the flavor (first-movement create)
    r = client.post("/movements", json={
        "staff_name": "Ana", "flavor_name": "Jamaica", "movement_type": "entry",
        "bottle_quantity": 100, "lot_number": "L-001", "note": "primera",
        "new_flavor_minimum": 20,
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["current_stock"] == 100 and body["movement_id"] == "1"

    # second lot, same flavor
    r = client.post("/movements", json={
        "staff_name": "Ana", "flavor_name": "Jamaica", "movement_type": "entry",
        "bottle_quantity": 50, "lot_number": "L-002",
    })
    assert r.status_code == 201 and r.json()["current_stock"] == 150

    # exit of 120 -> FIFO across the two lots, stock 30
    r = client.post("/movements", json={
        "staff_name": "Ana", "flavor_name": "Jamaica", "movement_type": "exit",
        "bottle_quantity": 120,
    })
    assert r.status_code == 201, r.text
    assert r.json()["lot_display"] == "L-001 (100), L-002 (20)", r.json()["lot_display"]
    assert r.json()["current_stock"] == 30

    # over-exit -> 409 Conflict, stock unchanged
    r = client.post("/movements", json={
        "staff_name": "Ana", "flavor_name": "Jamaica", "movement_type": "exit",
        "bottle_quantity": 999,
    })
    assert r.status_code == 409, r.text

    # entry without a lot -> 422 (interface rule)
    r = client.post("/movements", json={
        "staff_name": "Ana", "flavor_name": "Jamaica", "movement_type": "entry",
        "bottle_quantity": 5,
    })
    assert r.status_code == 422, r.text

    # bad quantity (<=0) -> 422 from Pydantic
    r = client.post("/movements", json={
        "staff_name": "Ana", "flavor_name": "Jamaica", "movement_type": "entry",
        "bottle_quantity": 0, "lot_number": "L-9",
    })
    assert r.status_code == 422, r.text

    # inventory now reflects the FIFO result
    inv = client.get("/inventory").json()["inventory"]
    jamaica = next(row for row in inv if row["flavor_name"] == "Jamaica")
    assert jamaica["total"] == 30 and jamaica["lots"] == {"L-002": 30}

    # history list + filter + limit
    assert client.get("/movements").json()["count"] == 3
    assert client.get("/movements", params={"flavor": "jamaica"}).json()["count"] == 3
    assert client.get("/movements", params={"limit": 1}).json()["count"] == 1

    # get one / not found
    assert client.get("/movements/1").json()["bottle_quantity"] == "100"
    assert client.get("/movements/999").status_code == 404

    # edit rejected: shrinking entry #1 below what the exit consumed
    r = client.put("/movements/1", json={"bottle_quantity": 10})
    assert r.status_code == 400, r.text
    assert client.get("/movements/1").json()["bottle_quantity"] == "100"  # untouched

    # edit accepted: change the note only
    r = client.put("/movements/1", json={"note": "corregida"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.get("/movements/1").json()["note"] == "corregida"

    # sales summary (one exit so far)
    sales = client.get("/sales").json()
    assert sales["has_sales"] and sales["summary"]["total_bottles"] == 120

    # --- flavors ---
    r = client.post("/flavors", json={"flavor_name": "Flores", "minimum_stock": 5})
    assert r.status_code == 201, r.text
    # duplicate (case-insensitive) -> 409
    assert client.post("/flavors", json={"flavor_name": "flores", "minimum_stock": 1}).status_code == 409
    # change minimum
    assert client.patch("/flavors/Flores", json={"minimum_stock": 12}).status_code == 200
    # rename propagates (no history yet, but must succeed)
    assert client.post("/flavors/Flores/rename", json={"new_name": "Flores de Jamaica"}).status_code == 200
    assert "Flores de Jamaica" in [f["flavor_name"] for f in client.get("/flavors").json()["flavors"]]
    # delete a flavor WITH history requires confirm=true
    assert client.delete("/flavors/Jamaica").status_code == 409          # blocked
    assert client.delete("/flavors/Jamaica", params={"confirm": "true"}).status_code == 200  # ok
    assert "Jamaica" not in [f["flavor_name"] for f in client.get("/flavors").json()["flavors"]]

    # --- staff ---
    assert "Ana" in client.get("/staff").json()["staff"]      # added on first movement
    assert client.post("/staff", json={"name": "Beto"}).status_code == 201
    assert client.post("/staff", json={"name": "Beto"}).status_code == 409   # duplicate
    assert client.delete("/staff/Beto").status_code == 200
    assert client.delete("/staff/Nadie").status_code == 404

    # persistence: the files on disk are the real source of truth
    data_dir = os.environ["FERMENTARIO_DATA_DIR"]
    assert os.path.exists(os.path.join(data_dir, "movements.csv"))
    assert os.path.exists(os.path.join(data_dir, "inventory_data.json"))

print("ALL API TESTS PASSED")
