# El Fermentario — Web

Inventory control for an artisanal kombucha business, now as an HTTP API
over a reusable Python core. Tracks bottle entries and exits by flavor and
lot, derives current stock (FIFO) from a movement history that is the single
source of truth, raises low-stock alerts, and produces a sales summary.

This repository is the **application** evolution of the original course
project. It keeps the terminal program working and adds a web API around the
exact same business logic, as the first step toward a multi-user web app.

> Separate from the course deliverable on purpose: this goes beyond the scope
> of the Programming / Data Engineering unit it grew out of.

## Architecture

Each layer only talks to the one below it. Business rules live in one place
and are reached identically from the terminal and from HTTP.

```
  interfaces
    ├── inventory_cli.py     terminal  (input()/print())
    ├── import_appsheet.py   one-time AppSheet migration
    ├── app/                 HTTP API   (FastAPI)
    └── frontend/            web PWA    (vanilla JS; talks only to app/)
          ↓
  fermentario/
    service.py   operations + the invariant "memory always matches disk"
    core.py      pure business rules — no I/O at all (FIFO, validation, stock)
    storage.py   the data files on disk (atomic writes; swappable for a DB)
```

The point of this split: `core.py` has no side effects, so it is trivially
testable and reused everywhere; `service.py` is transport-agnostic, so the
API and the CLI are just two faces over the same operations; `storage.py`
isolates persistence, so migrating from CSV/JSON to a hosted database later
touches only that file.

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**Run the API (local):**

```bash
uvicorn app.main:app --reload
```

- Interactive docs (try every endpoint): http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

**Run the terminal program (same data):**

```bash
python3 inventory_cli.py
```

**Seed from the old AppSheet export (once, into an empty history):**

```bash
python3 import_appsheet.py Movimientos.csv
```

The API, the CLI and the importer all read/write the same `movements.csv`
and `inventory_data.json` at the project root, so a movement registered in
one is visible in the others. Point them elsewhere with the
`FERMENTARIO_DATA_DIR` environment variable.

> **Single worker for now.** The service keeps the history in memory, so run
> one worker (the default). Several workers would each hold their own copy and
> drift apart — which is exactly what the hosted-database milestone fixes.

## API reference

| Method & path | Purpose |
|---|---|
| `GET /health` | Liveness check. |
| `GET /inventory` | Current stock per flavor, with lot breakdown and low-stock status. |
| `GET /warnings` | History-consistency warnings (same ones the CLI shows at startup). |
| `GET /sales` | Sales summary — every exit counts as a sale. |
| `GET /movements` | Movement history. Query: `?limit=N`, `?flavor=name`. |
| `POST /movements` | Register an entry or exit (see below). |
| `GET /movements/{id}` | One movement. |
| `PUT /movements/{id}` | Edit a movement (partial; re-validated by replaying the whole history). |
| `DELETE /movements/{id}` | Delete a movement (rejected if it would make a past exit impossible). |
| `GET /flavors` | Catalog flavors with current totals and status. |
| `POST /flavors` | Add a flavor. |
| `PATCH /flavors/{name}` | Change a flavor's minimum stock. |
| `POST /flavors/{name}/rename` | Rename a flavor (updates the whole history). |
| `DELETE /flavors/{name}` | Delete a flavor. Needs `?confirm=true` if it has history. |
| `GET /staff` | Staff list. |
| `POST /staff` | Add a staff member. |
| `DELETE /staff/{name}` | Remove a staff member. |

**Register a movement** — `POST /movements`:

```json
{
  "staff_name": "Ana",
  "flavor_name": "Jamaica",
  "movement_type": "entry",
  "bottle_quantity": 40,
  "lot_number": "L-001",
  "note": "",
  "new_flavor_minimum": 10
}
```

- `movement_type` is `entry` or `exit`.
- An **entry** needs `lot_number`; an **exit** ignores it (FIFO assigns the
  oldest lots and the response shows the breakdown, e.g. `L-001 (15)`).
- `new_flavor_minimum` (optional) creates the flavor if it does not exist —
  but only if this first movement succeeds, so no empty flavors are left
  behind on failure.

Design notes (stock as replayed history, why exits store no lot, the
`FLAVOR:` QR prefix, the error-handling policy) are documented in the code
and carried over from the original project.

## Frontend (PWA)

FastAPI serves `frontend/` at the root: with the API running, open
http://localhost:8000/ — no Node, no build step. The static mount lives at
the **end** of `app/main.py` on purpose (routes are matched in registration
order, so every API path wins and the mount catches the rest), which also
puts page and API on the same origin: CORS disappears by design.

Five screens over the existing endpoints, in strict presentation-only
discipline (no business rule lives in JavaScript):

- **Registrar** — the main flow. One QR button; the payload routes itself:
  `FLAVOR:<name>` fills the flavor (validated against the catalog), anything
  else fills the lot (entries only — exits carry no lot, FIFO decides). Exit
  responses show the FIFO breakdown so staff know which physical lots to pull.
- **Inventario** — stock per flavor with lot breakdown, low-stock badges and
  history-consistency warnings.
- **Historial** — newest first, filter by flavor, delete with the server's
  own guard (an entry that a past exit depends on is refused with the reason).
- **Ventas** — totals, per-flavor bars, per-month table.
- **Catálogo** — flavors (add / minimum / rename / two-step delete mirroring
  the API's `?confirm=true`) and staff.

Installable as a PWA (manifest + service worker). The worker caches the app
shell only and **never** the API: stock must always come from the server —
the web equivalent of "memory always matches disk". Bump `CACHE_VERSION`
in `frontend/service-worker.js` when shell files change.

**Camera note:** browsers allow camera access only in secure contexts
(`localhost` or HTTPS). To scan from a phone on the LAN, generate a local
cert with `mkcert` and run
`uvicorn app.main:app --host 0.0.0.0 --ssl-keyfile key.pem --ssl-certfile cert.pem`.

## Design decisions (unchanged from the core project)

- **The history is the source of truth.** Stock is never stored; it is
  derived by replaying `movements.csv`. `inventory_data.json` is catalog-only.
- **Exits record no lot on purpose.** FIFO is deterministic, so the consumed
  lots are reconstructible; storing them would be a second copy that could
  contradict the first after a correction.
- **Never serve unvalidated data.** Both files are validated on load; on
  corruption the API refuses to start and the CLI refuses to run, each with a
  precise, row-by-row reason.
- **Writes are atomic and reversible.** Temp file + `os.replace`; memory rolls
  back whenever the disk write fails, so what you see always matches the file.

## Roadmap

1. ✅ Separate business logic from the terminal interface (`fermentario` package).
2. ✅ Consolidate the AppSheet importer onto the same core.
3. ✅ **Wrap the service layer in a local FastAPI app (this milestone).**
4. ✅ **Responsive web frontend (PWA) with QR scanning via the phone camera
   (this milestone).**
5. ⬜ Migrate storage from CSV/JSON to a hosted database (Supabase / PostgreSQL)
   for safe concurrent multi-user access, with login (which also signs each
   movement automatically).
