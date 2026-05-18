from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelModel
from app.schemas.bounding import BoundingRegionResponse
from app.schemas.field import ExtractedFieldResponse


class TableCellResponse(CamelModel):
    """Wire shape AND on-disk JSON shape for a single cell — same pattern as
    BoundingRegionResponse for field regions. is_corrected flips on first
    user edit and stays true."""

    row_index: int
    column_index: int
    row_span: int
    column_span: int
    kind: str
    content: str | None
    is_corrected: bool = False
    bounding_regions: list[BoundingRegionResponse] = Field(default_factory=list)


class TableResponse(CamelModel):
    id: UUID
    index: int
    page_number: int
    row_count: int
    column_count: int
    source: str
    name: str | None
    bounding_regions: list[BoundingRegionResponse]
    cells: list[TableCellResponse]


class DocumentSummary(CamelModel):
    id: UUID
    file_name: str
    status: str
    created_at: datetime
    field_count: int
    template_name: str | None


class DocumentResponse(CamelModel):
    id: UUID
    file_name: str
    model_id: str
    status: str
    created_at: datetime
    completed_at: datetime | None
    error_message: str | None
    template_id: UUID | None
    template_name: str | None
    fields: list[ExtractedFieldResponse]
    tables: list[TableResponse]


# --- Write requests ---------------------------------------------------------


class CreateFieldRequest(CamelModel):
    """User-drawn field. Client supplies the polygon (8 floats min — 4
    corners × 2 coords) in the page's native unit (inches for PDFs)."""

    name: str = Field(min_length=1, max_length=256)
    data_type: str = Field(min_length=1, max_length=64)
    is_required: bool = False
    page_number: int = Field(ge=1)
    polygon: list[float] = Field(min_length=8)


class UpdateFieldRequest(CamelModel):
    """PATCH body — only present keys are applied."""

    value: str | None = None
    data_type: str | None = None
    is_required: bool | None = None


class UpdateTableCellRequest(CamelModel):
    """For merged cells, supply the top-left (row, col) — Azure DI's
    addressing convention. content=null clears the cell."""

    row_index: int
    column_index: int
    content: str | None = None


TemplateApplyMode = Literal["auto", "manual", "none"]
