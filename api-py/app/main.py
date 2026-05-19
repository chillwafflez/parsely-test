from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import get_settings
from app.db import engine
from app.routers import aggregations as aggregations_router
from app.routers import document_types as document_types_router
from app.routers import documents as documents_router
from app.routers import templates as templates_router
from app.security import CurrentUser, CurrentUserDep
from app.services.blob_storage import BlobStorageService
from app.services.document_intelligence import DocumentIntelligenceService
from app.services.layout_storage import LayoutStorageService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Fail-fast on startup if the DB is unreachable — cheaper to surface
    # misconfig here than on the first real request.
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))

    # Azure clients are constructed lazily-network: from_connection_string +
    # the DI client constructor only parse credentials, no network call.
    # First Azure call happens on first upload.
    s = get_settings()
    blobs = BlobStorageService(s.azure_blob_connection_string, s.azure_blob_container)
    intelligence = DocumentIntelligenceService(s.azure_di_endpoint, s.azure_di_key)
    layout = LayoutStorageService(blobs, intelligence)
    app.state.blobs = blobs
    app.state.intelligence = intelligence
    app.state.layout = layout

    yield

    await intelligence.close()
    await blobs.close()
    await engine.dispose()


settings = get_settings()
app = FastAPI(title="Parsely API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(document_types_router.router)
app.include_router(templates_router.router)
app.include_router(aggregations_router.router)
app.include_router(documents_router.router)


@app.get("/health")
async def health() -> dict[str, str]:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/me")
async def me(user: CurrentUserDep) -> CurrentUser:
    """Echoes the authenticated user. Exists so we can verify the auth
    dependency end-to-end before applying it to the real routes in 3.5.
    """
    return user
