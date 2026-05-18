from app.schemas.base import CamelModel


class BoundingRegionResponse(CamelModel):
    """Per-page bbox region — flat polygon array in the page's native unit
    (inches for PDFs, pixels for images). Shared by template rules,
    extracted fields, tables, and aggregations."""

    page_number: int
    polygon: list[float]
