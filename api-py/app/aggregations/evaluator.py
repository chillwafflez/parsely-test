"""Single-source evaluation pipeline used by both the aggregation save
endpoint (computing the result for a freshly-drawn region) and the
template-replay path (re-running an existing rule on a future upload).
Pure function: takes layout pages + a polygon + an operation, returns the
formatted value and provenance metadata."""

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from app.aggregations.compute import compute, format_value
from app.aggregations.config import AggregationFieldConfig
from app.aggregations.operations import AggregationOperation
from app.aggregations.parser import parse_words
from app.domain import PageExtraction
from app.geometry.polygon import words_inside_region


@dataclass(frozen=True)
class AggregationResult:
    value: str | None
    confidence: float
    config: AggregationFieldConfig


def evaluate(
    operation: AggregationOperation,
    polygon: Sequence[float],
    page_number: int,
    pages: Sequence[PageExtraction],
    evaluated_at: datetime,
) -> AggregationResult:
    page = next((p for p in pages if p.page_number == page_number), None)
    matched = [] if page is None else words_inside_region(page.words, polygon)

    parsed = list(parse_words(matched))
    values = [t.value for t in parsed]
    result = compute(operation, values)
    formatted = format_value(operation, result)
    average_confidence = (
        sum(t.source.confidence for t in parsed) / len(parsed) if parsed else 0.0
    )

    return AggregationResult(
        value=formatted if formatted else None,
        confidence=average_confidence,
        config=AggregationFieldConfig(
            operation=operation.value,
            source_token_count=len(parsed),
            evaluated_at=evaluated_at,
        ),
    )
