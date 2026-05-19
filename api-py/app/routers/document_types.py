from fastapi import APIRouter, Depends

from app.catalog import document_types
from app.schemas.document_types import DocumentTypeResponse
from app.security import get_current_user

router = APIRouter(
    prefix="/api/document-types",
    tags=["document-types"],
    dependencies=[Depends(get_current_user)],
)


@router.get("", response_model=list[DocumentTypeResponse])
async def list_document_types() -> list[DocumentTypeResponse]:
    return [
        DocumentTypeResponse(
            model_id=e.model_id,
            display_name=e.display_name,
            sample_asset_url=e.sample_asset_url,
        )
        for e in document_types.all_entries()
    ]
