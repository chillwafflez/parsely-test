# FastAPI port — practice-rewrite state

Tracks the **practice rewrite** of the .NET backend into FastAPI + SQLModel + Neon. The original `api/` is the canonical spec; this doc tracks the replica at `api-py/` and what's different.

**Sibling docs**
- `context/PROJECT_CONTEXT.md` — .NET project spec (the source of truth for behavior)
- `memory/practice_architecture.md` — *why* we made each architecture choice
- `memory/practice_phased_plan.md` — phase ordering for the broader stack-adoption practice

---

## Where we are (last updated 2026-05-18)

**Phase 1 backend port is functionally complete.** 4 routers · 18 endpoints · end-to-end verified against real Azure DI + Azure Blob + Neon.

The Next.js frontend (`web/`) is unchanged from the .NET-era code. Cutover is one env var (`NEXT_PUBLIC_API_BASE_URL=http://localhost:5181` in `web/.env.local`). Full parse → correct → save-template → re-upload → auto-match loop confirmed in the browser.

**Not ported (intentional defer):**
- `/api/voice/token` and `/api/voice/fill` — the Voice-Fill button in the UI will 404 against FastAPI. Slated for a "Phase 1.5" if/when wanted; depends on Azure Speech REST + OpenAI Python SDK.

---

## Stack changes vs .NET (parity decisions)

| Concern | .NET | FastAPI port |
|---|---|---|
| Persistence | SQL Server via EF Core | Neon Postgres via SQLModel + SQLAlchemy async + asyncpg |
| Migrations | EF Core migrations | Alembic with async `env.py` |
| Blob storage | `Azure.Storage.Blobs` (sync) | `azure.storage.blob.aio` (needs `aiohttp` transport — not transitive) |
| Document Intelligence | `Azure.AI.DocumentIntelligence` 1.0 | `azure-ai-documentintelligence` 1.0 — uses `value_array` / `value_object` (REST schema names), **not** C#'s `ValueList` / `ValueDictionary` |
| JSON wire format | PascalCase → ASP.NET Core's CamelCase policy | snake_case Python → camelCase JSON via `app/schemas/base.py:CamelModel` (Pydantic `alias_generator=to_camel`) |
| DocumentStatus | C# enum (int) | `IntEnum` stored as `SmallInteger`, wire-serialised as PascalCase via `_STATUS_NAMES` map in `app/routers/documents.py` |
| DI / lifetime | constructor injection via ASP.NET Core DI | lifespan-managed singletons on `app.state`, exposed via `app/deps.py` |
| Request body validation | DataAnnotations attributes | Pydantic `Field(..., min_length=N, ge=N, max_length=N)` |

---

## Gotchas discovered during the port (not derivable from code)

1. **`"Template | None"` in a SQLModel `Relationship()` annotation crashes at first query.** SQLAlchemy parses the whole annotation string as a class name and chokes on the `|`. Use `Optional["Template"]` (with `from typing import Optional`) instead. Single-class string forms also work; the union form does not.

2. **`await session.refresh(obj)` after `await session.commit()` clears in-memory relationship collections.** Subsequent access to `obj.rules` triggers a lazy SQL load → `MissingGreenlet` in async context. Fix: don't refresh after commit. With `expire_on_commit=False` (set in `app/db.py`), in-memory collections you appended pre-commit are still valid — just use them. If you genuinely need a fresh view, re-`SELECT` with `selectinload(...)`.

3. **`await session.delete(rule)` deletes the row but leaves the entity in `parent.rules`.** The response then reflects stale collection size. Use `parent.rules.remove(rule)` instead — fires the `delete-orphan` cascade AND keeps the in-memory collection synchronised. (Templates router's `update` endpoint relies on this.)

4. **`aiohttp` is not pulled in transitively by the async Azure SDKs.** Both `azure-storage-blob.aio.BlobServiceClient` and `azure-ai-documentintelligence.aio.DocumentIntelligenceClient` need it for their async transport, but the SDKs support multiple backends and let the consumer pick. Pin `aiohttp>=3.10` explicitly in `pyproject.toml`.

5. **`HEAD` against a FastAPI `GET` route returns 405** by default. Don't smoke-test `/file` with `curl -I`; use `urllib.request` or `curl -X GET -o /dev/null`. If you ever genuinely need HEAD support, you'd have to add a separate route — currently not worth it.

6. **uvicorn `--reload` doesn't always notice newly-added module files** (vs. modifications to existing ones). After landing a new router file, fully restart the server (Ctrl+C and re-launch) rather than relying on the reloader. Symptom: routes from new files appear as 404.

7. **Sample vendor names matter for auto-match smoke tests.**
   - `sample-invoice.pdf` and `sample-invoice-updated.pdf` → vendor `CONTOSO LTD.` (auto-match fires)
   - `sample-invoice-2.pdf` → different vendor (will not auto-match a CONTOSO template)
   - `sample-invoice-multi-page.pdf` → multi-page invoice variant
   - `bank-statement-sample.pdf` / `-2.pdf` → use `prebuilt-bankStatement.us` model
   - `w2-single.pdf` → `prebuilt-tax.us.w2` model

8. **`requires-python = ">=3.12"` is the pin in `pyproject.toml`.** The user runs Python 3.14 from a Conda env named `gurt` at `C:\Users\jnguyen\AppData\Local\miniconda3\envs\gurt\python.exe`. `uv sync --python <that-path>` is how the project venv is created. `uv` itself is installed inside that Conda env, not on system PATH — invoke as `<gurt>/Scripts/uv.exe` from POSIX bash.

9. **Azure Blob container is not auto-created.** `AZURE_BLOB_CONTAINER` env var must point at a container that already exists in the storage account. The .NET-era container name should be reused (look up via `dotnet user-secrets list` in `api/`) so both backends see the same uploads during cutover. The Python service uploads into it but won't create it.

10. **`expire_on_commit=False` is load-bearing in `app/db.py`.** Without it, every commit expires all loaded attributes and the next access against any relationship triggers a sync SQL load → MissingGreenlet in async. Don't "clean this up" without understanding the cascade.

---

## What's next

Per `memory/practice_phased_plan.md`:

1. ✅ Phase 1 — FastAPI + Neon backend
2. (optional) **Phase 1.5 — voice router port.** Two endpoints. Decision deferred — main loop works without it.
3. **Phase 2 — containerization + local K8s.** Dockerfile for the FastAPI app, Helm chart, run on `kind` or `k3d`, push images to AWS ECR. **Don't use EKS** (control-plane cost — see `memory/company_tech_stack.md`).
4. Phase 3 — Keycloak + JWT auth.
5. Phase 4 — OpenTofu to codify Neon project + ECR repo + Keycloak realm.
6. Phase 5 — Flutter mobile companion (separate small app, see `memory/practice_architecture.md`).

---

## Where things live in `api-py/`

```
pyproject.toml          uv + deps (FastAPI, SQLModel, Alembic, asyncpg, azure SDKs, aiohttp)
alembic.ini
alembic/env.py          async migrations — imports app.models so SQLModel.metadata is populated
alembic/versions/       854dd171a78a_initial_schema.py (initial 6 tables)
app/main.py             lifespan inits engine + 3 Azure clients on app.state
app/config.py           Pydantic Settings — DATABASE_URL, AZURE_*, CORS_*
app/db.py               async engine + SQLModel AsyncSession + to_asyncpg_url() URL adapter
app/deps.py             FastAPI dependency providers (request.app.state → service)
app/domain.py           internal frozen dataclasses (WordData, PageExtraction, etc.)
app/catalog/            DocumentTypeCatalog (5 prebuilt models)
app/geometry/           PolygonGeometry — axis-aligned bounds, words-inside-region
app/aggregations/       operations enum, compute, parser, evaluator, config
app/services/           Azure SDK wrappers (blob_storage, document_intelligence, layout_storage,
                        azure_field_mapping, table_synthesizer)
app/schemas/            Pydantic wire models — CamelModel base, bounding, document_types,
                        template, field, aggregation, document
app/models/             SQLModel entities — document, field, table, template (6 tables)
app/routers/            4 routers, 18 endpoints — document_types, templates, aggregations,
                        documents
```

---

## Quick commands

```powershell
# Start FastAPI (kill any stale instance first — see gotcha #6)
taskkill /F /IM uvicorn.exe 2>$null
cd api-py
.venv\Scripts\uvicorn.exe app.main:app --reload --port 5181

# Apply pending migrations to Neon
cd api-py
.venv\Scripts\alembic.exe upgrade head

# Generate a new migration after a model change
cd api-py
.venv\Scripts\alembic.exe revision --autogenerate -m "what changed"

# Confirm routes are what you expect (queries /openapi.json)
.venv\Scripts\python.exe -c "import urllib.request, json; spec = json.loads(urllib.request.urlopen('http://localhost:5181/openapi.json').read()); [print(m.upper(), p) for p, methods in spec['paths'].items() for m in methods]"
```
