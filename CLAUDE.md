# CLAUDE.md — working notes for this repository

Context for AI assistants (and humans) working on El Fermentario Web.

## What this is

Inventory control for an artisanal kombucha business. A reusable Python core
with two interfaces over it: a terminal program and a FastAPI HTTP API.

## Layering (do not break)

```
interfaces (inventory_cli.py, import_appsheet.py, app/)  ->  service  ->  core / storage
```

- `fermentario/core.py` — **pure business logic. No I/O whatsoever**: no
  `print`, no `input`, no files, no logging. Only data in, data out. This is
  what makes it testable and shared. Keep it that way.
- `fermentario/storage.py` — the only module that touches the data files.
  Raises `InventoryDataError` / `StorageError`; never prints. This is the
  seam a future database backend replaces.
- `fermentario/service.py` — the operations every interface calls. Owns the
  in-memory state and the invariant **memory always matches disk** (save
  first; roll back memory on write failure). Returns `OpResult` /
  `MovementResult`, never prints.
- `app/` (FastAPI) and `inventory_cli.py` — the ONLY places with transport
  concerns (HTTP, `input`/`print`). No business rules here.

When adding a feature, put each part in the lowest layer it belongs to: a
rule goes in `core`, a persisted operation in `service`, a persistence detail
in `storage`, and only the request/response wiring in an interface.

## Non-negotiable invariants

1. **History is the source of truth.** Never store computed stock; derive it
   by replaying `movements.csv`. `inventory_data.json` is catalog-only.
2. **Exits store no lot number.** FIFO decides; a stored lot would be a
   contradictable second copy.
3. **Never serve/compute from unvalidated data.** Validate on load; refuse to
   run/start on corruption with a precise reason.
4. **Atomic, reversible writes.** Temp file + `os.replace`; roll back memory
   if the disk write fails.
5. The `FLAVOR:` prefix is reserved for flavor QR labels; lot codes must not
   start with it.

## Conventions

- Python, standard library first. Descriptive names, comments explain *why*.
- The data files live at the repo root by default (shared by all interfaces);
  override with `FERMENTARIO_DATA_DIR`. They are git-ignored.
- Run the API single-worker while state is in memory (see roadmap step 5).

## Run & test

```bash
uvicorn app.main:app --reload     # API + docs at /docs
python3 inventory_cli.py          # terminal, same data
python3 test_api.py               # end-to-end API checks (uses a temp data dir)
```

## Roadmap (current: step 3 done)

1. ✅ Extract core from the CLI.  2. ✅ Importer shares core.
3. ✅ FastAPI over the service.  4. ⬜ PWA frontend + QR scanning.
5. ⬜ Hosted DB (Supabase/PostgreSQL) + login for concurrent multi-user.
