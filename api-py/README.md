# parsely-api (Python port)

FastAPI rewrite of the .NET document parsing backend. Practice port for new company tech stack (FastAPI + Pydantic + Neon + Docker + K8s).

## Stack

- **FastAPI** + **SQLModel** (SQLAlchemy 2.x async + Pydantic in one)
- **asyncpg** → **Neon** Postgres
- **Alembic** migrations
- **Azure Document Intelligence** + **Azure Blob Storage** (kept from .NET stack)

## First-time setup

From `api-py/`:

```powershell
# 1. Create your .env from the template, then fill in real values.
copy .env.example .env
#    Paste Azure values from `dotnet user-secrets list` (run in ../api).
#    Paste Neon connection string from the neon.tech console.

# 2. Create the venv and install deps with uv.
uv sync --python "C:\Users\jnguyen\AppData\Local\miniconda3\envs\gurt\python.exe"

# 3. (Stage 1a only — no models yet, skip migration.)
#    Once stage 1b adds models, run:
# .venv\Scripts\alembic.exe upgrade head

# 4. Run the API on :5181 (the .NET API stays on :5180 during cutover).
.venv\Scripts\uvicorn.exe app.main:app --reload --port 5181
```

## Smoke test

```powershell
curl http://localhost:5181/health
# Expect: {"status":"ok"}
```

A 200 response means: env loaded, Neon reachable, FastAPI is up.

## Pointing the Next.js frontend at this backend

The frontend defaults to `http://localhost:5180` (.NET). To cut it over to FastAPI:

```powershell
# in web/
copy .env.local.example .env.local
# Edit .env.local — uncomment the NEXT_PUBLIC_API_BASE_URL line
```

```
NEXT_PUBLIC_API_BASE_URL=http://localhost:5181
```

Restart `pnpm dev`. The frontend now talks to FastAPI for everything *except* the voice-fill feature — `/api/voice/token` and `/api/voice/fill` haven't been ported yet (Phase 1.5). The voice button will 404 against FastAPI until then; the rest of the parse → correct → template loop works end-to-end.

## End-to-end parity smoke test

With FastAPI running on `:5181` and frontend pointing at it:

1. Open `http://localhost:3000`
2. Upload `samples/sample-invoice.pdf` (or any sample under `samples/`)
3. Verify: review stage renders, bounding boxes align with PDF, fields list is populated
4. Edit one field's value inline
5. Save as template (name it anything, e.g. "Smoke")
6. Upload `samples/sample-invoice-updated.pdf` (same vendor)
7. Verify: Inspector header shows "Template: Smoke" — auto-match fired

## Layout (final shape)

```
api-py/
  pyproject.toml
  alembic.ini
  alembic/                # migrations
    env.py
    versions/
  app/
    main.py               # FastAPI app, CORS, lifespan, health
    config.py             # Pydantic Settings (env vars)
    db.py                 # async engine, sessionmaker, connection adapter
    models/               # SQLModel — added in stage 1b
    schemas/              # Pydantic request/response — added in stage 1b
    routers/              # FastAPI routers, one per .NET controller — stage 1d
    services/             # Azure DI, Blob, LayoutStorage wrappers — stage 1c
    aggregations/         # mirror of api/Aggregations — stage 1b
    geometry/             # mirror of api/Services/PolygonGeometry — stage 1b
    catalog/              # mirror of api/Catalog — stage 1b
```
