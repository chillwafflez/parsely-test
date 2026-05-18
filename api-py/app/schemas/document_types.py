from app.schemas.base import CamelModel


class DocumentTypeResponse(CamelModel):
    """Wire shape returned by /api/document-types. Mirrors the .NET
    DocumentTypeResponse — internal catalog fields (identifier path,
    flatten flag, layout-fallback flag) are intentionally omitted."""

    model_id: str
    display_name: str
    sample_asset_url: str | None
