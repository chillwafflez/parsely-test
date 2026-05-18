from dataclasses import dataclass


@dataclass(frozen=True)
class WordData:
    content: str
    polygon: tuple[float, ...]
    confidence: float


@dataclass(frozen=True)
class PageExtraction:
    page_number: int
    words: tuple[WordData, ...]


@dataclass(frozen=True)
class BoundingRegionData:
    page_number: int
    polygon: tuple[float, ...]


@dataclass(frozen=True)
class ExtractedFieldData:
    name: str
    value: str | None
    data_type: str
    confidence: float
    bounding_regions: tuple[BoundingRegionData, ...]


@dataclass(frozen=True)
class TableCellData:
    row_index: int
    column_index: int
    row_span: int
    column_span: int
    kind: str
    content: str | None
    bounding_regions: tuple[BoundingRegionData, ...]


@dataclass(frozen=True)
class TableExtraction:
    index: int
    page_number: int
    row_count: int
    column_count: int
    # "Layout" for tables from result.tables, "Synthesized" for synth tables
    # built from array-of-object structured fields. The two surfaces render in
    # different parts of the Inspector.
    source: str
    name: str | None
    bounding_regions: tuple[BoundingRegionData, ...]
    cells: tuple[TableCellData, ...]


class TableSources:
    LAYOUT = "Layout"
    SYNTHESIZED = "Synthesized"


@dataclass(frozen=True)
class DocumentExtractionResult:
    fields: tuple[ExtractedFieldData, ...]
    pages: tuple[PageExtraction, ...]
    tables: tuple[TableExtraction, ...]
