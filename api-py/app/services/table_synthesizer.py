"""Builds TableExtractions from array-of-object structured fields ("Items",
"Accounts", nested "Transactions") — the model's structured interpretation
of repeating data.

Distinct from layout's result.tables (visual table structure on the page).
Both surfaces coexist by design: synth tables are bound to a parent field
via TableExtraction.name, while layout tables use detection-order "Table N"
labelling.

Walking is recursive: a top-level Items array becomes one table, and a
nested Accounts[i].Transactions array becomes its own table at the leaf
name. Repeated leaf names are disambiguated with a [N] suffix."""

from dataclasses import replace

from azure.ai.documentintelligence.models import DocumentField, DocumentFieldType

from app.domain import BoundingRegionData, TableCellData, TableExtraction, TableSources
from app.services.azure_field_mapping import (
    format_value,
    is_array_of_objects,
    to_region_data,
)


def synthesize(fields: dict[str, DocumentField]) -> list[TableExtraction]:
    tables: list[TableExtraction] = []
    name_counts: dict[str, int] = {}
    for name, field in fields.items():
        _walk_field(name, field, tables, name_counts)
    return [replace(t, index=i) for i, t in enumerate(tables)]


def _walk_field(
    name: str,
    field: DocumentField,
    output: list[TableExtraction],
    name_counts: dict[str, int],
) -> None:
    is_array, items = is_array_of_objects(field)
    if is_array:
        output.append(_build_table(_leaf_name(name), items, name_counts))

        # Recurse into each row's children. A nested array-of-object (e.g.
        # Accounts[i].Transactions) becomes its own table — the [N] suffix
        # handles the repeated leaf name.
        for item in items:
            if item.value_object:
                for child_key, child_field in item.value_object.items():
                    _walk_field(child_key, child_field, output, name_counts)
        return

    if field.type == DocumentFieldType.OBJECT and field.value_object:
        for child_key, child_field in field.value_object.items():
            _walk_field(child_key, child_field, output, name_counts)


def _build_table(
    base_name: str,
    items: list[DocumentField],
    name_counts: dict[str, int],
) -> TableExtraction:
    name = _assign_name(base_name, name_counts)

    # Column order = first appearance across rows. Stable across reloads
    # because value_object preserves insertion order.
    column_order: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item.value_object is None:
            continue
        for key in item.value_object.keys():
            if key not in seen:
                seen.add(key)
                column_order.append(key)

    cells: list[TableCellData] = []

    # Row 0: column headers. No bbox — synthesized labels, not text on page.
    for col, col_name in enumerate(column_order):
        cells.append(
            TableCellData(
                row_index=0,
                column_index=col,
                row_span=1,
                column_span=1,
                kind="columnHeader",
                content=col_name,
                bounding_regions=(),
            )
        )

    # Data rows: one per item, one cell per column key. Missing keys emit
    # an empty cell so every (row, col) is addressable for editing.
    for row_idx, item in enumerate(items):
        for col, key in enumerate(column_order):
            cell_field: DocumentField | None = None
            if item.value_object is not None and key in item.value_object:
                cell_field = item.value_object[key]
            cells.append(
                TableCellData(
                    row_index=row_idx + 1,
                    column_index=col,
                    row_span=1,
                    column_span=1,
                    kind="content",
                    content=format_value(cell_field) if cell_field is not None else None,
                    bounding_regions=(
                        to_region_data(cell_field.bounding_regions)
                        if cell_field is not None
                        else ()
                    ),
                )
            )

    # Per-row regions: flatten across items, mirroring DI Studio's visual
    # treatment of structured arrays. Items with no region are skipped
    # silently rather than faked from cell unions.
    row_regions: list[BoundingRegionData] = []
    for item in items:
        row_regions.extend(to_region_data(item.bounding_regions))

    return TableExtraction(
        index=0,  # Re-assigned by synthesize() after full walk.
        page_number=_first_page(items),
        row_count=len(items) + 1,  # +1 for header row.
        column_count=len(column_order),
        source=TableSources.SYNTHESIZED,
        name=name,
        bounding_regions=tuple(row_regions),
        cells=tuple(cells),
    )


def _assign_name(base_name: str, name_counts: dict[str, int]) -> str:
    count = name_counts.get(base_name)
    if count is None:
        name_counts[base_name] = 1
        return base_name
    name_counts[base_name] = count + 1
    return f"{base_name} [{count + 1}]"


def _leaf_name(flat_name: str) -> str:
    # "Accounts.Transactions" → "Transactions". A nested array's table is
    # labelled by its array name only — the user-recognisable concept.
    dot = flat_name.rfind(".")
    return flat_name if dot < 0 else flat_name[dot + 1 :]


def _first_page(items: list[DocumentField]) -> int:
    for item in items:
        if item.bounding_regions and len(item.bounding_regions) > 0:
            return item.bounding_regions[0].page_number
        # Item itself often has no top-level region — peek at its first
        # child field that does.
        if item.value_object is None:
            continue
        for child in item.value_object.values():
            if child.bounding_regions and len(child.bounding_regions) > 0:
                return child.bounding_regions[0].page_number
    return 1
