"""Pure mappers from Azure DI SDK types to our domain types. Shared by
DocumentIntelligenceService (top-level field extraction) and
TableSynthesizer (synth-table cell formatting). Centralised so a future
change to currency rendering, bbox shape, etc. lands in one place."""

from azure.ai.documentintelligence.models import (
    BoundingRegion,
    DocumentField,
    DocumentFieldType,
)

from app.domain import BoundingRegionData


def format_value(field: DocumentField) -> str | None:
    # Strings get their parsed value (or fall back to raw content for the
    # rare case where the model recognised it as a string but didn't extract
    # a normalised value); currency renders as "{symbol}{amount}"; everything
    # else falls through to content, which Azure DI populates on every leaf.
    t = field.type
    if t == DocumentFieldType.STRING:
        return field.value_string or field.content
    if t == DocumentFieldType.CURRENCY and field.value_currency is not None:
        c = field.value_currency
        return f"{c.currency_symbol}{c.amount}"
    return field.content


def to_region_data(
    regions: list[BoundingRegion] | None,
) -> tuple[BoundingRegionData, ...]:
    if regions is None:
        return ()
    return tuple(
        BoundingRegionData(
            page_number=r.page_number,
            polygon=tuple(r.polygon or []),
        )
        for r in regions
    )


def is_array_of_objects(field: DocumentField) -> tuple[bool, list[DocumentField]]:
    """Returns (True, items) when field is a non-empty array of object fields.

    Both DocumentIntelligenceService (Tabular placeholder emission) and
    TableSynthesizer (synth-table generation) gate on this same predicate so
    they always agree on which fields qualify."""
    if (
        field.type == DocumentFieldType.ARRAY
        and field.value_array
        and len(field.value_array) > 0
        and field.value_array[0].type == DocumentFieldType.OBJECT
    ):
        return True, field.value_array
    return False, []
