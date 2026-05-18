from pydantic import Field

from app.schemas.base import CamelModel


class AggregationPreviewRequest(CamelModel):
    """Polygon the user just drew. Backend filters layout words to that
    region and parses numeric tokens for the modal preview."""

    page_number: int = Field(ge=1)
    # Minimum 8 floats — 4 points × 2 coords (axis-aligned rectangle).
    polygon: list[float] = Field(min_length=8)


class AggregationTokenResponse(CamelModel):
    text: str  # Original word content (e.g. "$1,234.56")
    # Parsed numeric value. Serialised as a JSON number — the modal computes
    # Sum/Avg/Count/Min/Max client-side as the user toggles operations.
    value: float
    confidence: float
    polygon: list[float]


class AggregationPreviewResponse(CamelModel):
    tokens: list[AggregationTokenResponse]


class CreateAggregationRequest(CamelModel):
    """Commits an aggregation field on the document. When the doc has a
    matched template, the rule is auto-promoted to a TemplateAggregationRule
    so future uploads replay the aggregation."""

    name: str = Field(min_length=1, max_length=256)
    # Case-insensitive: Sum / Average / Count / Min / Max.
    operation: str = Field(min_length=1, max_length=16)
    is_required: bool = False
    page_number: int = Field(ge=1)
    polygon: list[float] = Field(min_length=8)
