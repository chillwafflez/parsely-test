from datetime import datetime

from app.schemas.base import CamelModel


class AggregationFieldConfig(CamelModel):
    """Provenance for an aggregation field — stored as JSON on
    ExtractedField.aggregation_config_json. Presence (not data_type) is what
    the frontend keys off to route the field into the aggregation group."""

    operation: str
    source_token_count: int
    evaluated_at: datetime
