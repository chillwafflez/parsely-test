import json
from uuid import UUID

from app.aggregations.config import AggregationFieldConfig
from app.models.field import ExtractedField
from app.schemas.base import CamelModel
from app.schemas.bounding import BoundingRegionResponse


class ExtractedFieldResponse(CamelModel):
    id: UUID
    name: str
    value: str | None
    data_type: str
    confidence: float
    is_required: bool
    is_corrected: bool
    is_user_added: bool
    bounding_regions: list[BoundingRegionResponse]
    # Presence (not data_type) is what the frontend keys off to route the
    # field into the aggregation group — users can still override the
    # displayed type via the type popover.
    aggregation_config: AggregationFieldConfig | None


def field_to_response(field: ExtractedField) -> ExtractedFieldResponse:
    regions = (
        [BoundingRegionResponse(**entry) for entry in json.loads(field.bounding_regions_json)]
        if field.bounding_regions_json
        else []
    )
    aggregation = (
        AggregationFieldConfig.model_validate_json(field.aggregation_config_json)
        if field.aggregation_config_json
        else None
    )
    return ExtractedFieldResponse(
        id=field.id,
        name=field.name,
        value=field.value,
        data_type=field.data_type,
        confidence=field.confidence,
        is_required=field.is_required,
        is_corrected=field.is_corrected,
        is_user_added=field.is_user_added,
        bounding_regions=regions,
        aggregation_config=aggregation,
    )
