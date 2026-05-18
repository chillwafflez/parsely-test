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
