"""FastAPI dependency providers for lifespan-managed services.

Each service is initialised once in app.main.lifespan and stored on
app.state. Routers consume them via Depends(get_*)."""

from fastapi import Request

from app.services.blob_storage import BlobStorageService
from app.services.document_intelligence import DocumentIntelligenceService
from app.services.layout_storage import LayoutStorageService


def get_blobs(request: Request) -> BlobStorageService:
    return request.app.state.blobs


def get_intelligence(request: Request) -> DocumentIntelligenceService:
    return request.app.state.intelligence


def get_layout(request: Request) -> LayoutStorageService:
    return request.app.state.layout
