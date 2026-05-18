import asyncio
import logging
from dataclasses import replace

from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeResult, DocumentField, DocumentFieldType
from azure.core.credentials import AzureKeyCredential

from app.catalog import document_types as catalog
from app.domain import (
    DocumentExtractionResult,
    ExtractedFieldData,
    PageExtraction,
    TableCellData,
    TableExtraction,
    TableSources,
    WordData,
)
from app.services import table_synthesizer
from app.services.azure_field_mapping import (
    format_value,
    is_array_of_objects,
    to_region_data,
)

LAYOUT_MODEL_ID = "prebuilt-layout"

logger = logging.getLogger(__name__)


class DocumentIntelligenceService:
    def __init__(self, endpoint: str, key: str):
        if not endpoint or not key:
            raise ValueError(
                "Document Intelligence endpoint and key must both be configured."
            )
        self._client = DocumentIntelligenceClient(
            endpoint=endpoint, credential=AzureKeyCredential(key)
        )

    async def analyze(self, content: bytes, model_id: str) -> DocumentExtractionResult:
        type_def = catalog.find(model_id)
        flatten_maps = type_def.flatten_maps if type_def else False
        # Skip the fallback when the caller is already asking for layout —
        # avoids infinite-loop semantics + double-cost for the same data.
        needs_layout_fallback = (
            (type_def.needs_layout_fallback if type_def else False)
            and model_id.lower() != LAYOUT_MODEL_ID
        )

        logger.info(
            "Analyzing document with model %s (flattenMaps=%s, layoutFallback=%s)",
            model_id,
            flatten_maps,
            needs_layout_fallback,
        )

        main_result: AnalyzeResult
        layout_result: AnalyzeResult | None = None

        if needs_layout_fallback:
            # Concurrent calls — perceived latency is max(chosen, layout)
            # rather than chosen + layout. Both run unconditionally when the
            # catalog flags fallback; gating on chosen-model table count
            # would force a serial path and defeat the parallel optimisation.
            main_poller, layout_poller = await asyncio.gather(
                self._client.begin_analyze_document(
                    model_id, body=content, content_type="application/octet-stream"
                ),
                self._client.begin_analyze_document(
                    LAYOUT_MODEL_ID,
                    body=content,
                    content_type="application/octet-stream",
                ),
            )
            main_result, layout_result = await asyncio.gather(
                main_poller.result(), layout_poller.result()
            )
        else:
            poller = await self._client.begin_analyze_document(
                model_id, body=content, content_type="application/octet-stream"
            )
            main_result = await poller.result()

        fields = self._extract_fields(main_result, flatten_maps)
        pages = self._extract_pages(main_result)

        # Visual tables: prefer the layout call's set when it ran (since the
        # chosen model's result.tables is the very thing we expected to be
        # empty). Fall back to the main result so the path stays correct if
        # a future fallback-flagged model starts surfacing tables natively.
        layout_tables = self._extract_layout_tables(
            layout_result if layout_result is not None else main_result
        )

        # Synthesised tables come from the chosen-model fields — Items,
        # Accounts, nested Transactions, etc. Independent of which call
        # produced result.tables.
        first_doc_fields: dict[str, DocumentField] = {}
        if main_result.documents and len(main_result.documents) > 0:
            first_doc_fields = main_result.documents[0].fields or {}
        synth_tables = table_synthesizer.synthesize(first_doc_fields)

        # Combined, with synth indexes offset after layout so (DocumentId,
        # Index) addresses every table on the document.
        all_tables: list[TableExtraction] = list(layout_tables)
        for i, t in enumerate(synth_tables):
            all_tables.append(replace(t, index=len(layout_tables) + i))

        return DocumentExtractionResult(
            fields=tuple(fields),
            pages=tuple(pages),
            tables=tuple(all_tables),
        )

    def _extract_fields(
        self, result: AnalyzeResult, flatten_maps: bool
    ) -> list[ExtractedFieldData]:
        fields: list[ExtractedFieldData] = []
        if not result.documents or len(result.documents) == 0:
            return fields
        for name, field in (result.documents[0].fields or {}).items():
            self._emit_fields(name, field, flatten_maps, fields)
        return fields

    def _emit_fields(
        self,
        name: str,
        field: DocumentField,
        flatten_maps: bool,
        output: list[ExtractedFieldData],
    ) -> None:
        is_array, items = is_array_of_objects(field)
        if is_array:
            output.append(self._to_tabular_placeholder(name, len(items)))
            return

        if (
            flatten_maps
            and field.type == DocumentFieldType.OBJECT
            and field.value_object
        ):
            for child_key, child_field in field.value_object.items():
                self._emit_fields(
                    f"{name}.{child_key}", child_field, flatten_maps, output
                )
            return

        output.append(self._to_field_data(name, field))

    @staticmethod
    def _to_field_data(name: str, field: DocumentField) -> ExtractedFieldData:
        # field.type is the DocumentFieldType enum; render the wire string
        # ("string", "currency", ...) so the data_type column stays stable
        # across SDK upgrades.
        data_type = (
            field.type.value
            if hasattr(field.type, "value")
            else str(field.type)
        )
        return ExtractedFieldData(
            name=name,
            value=format_value(field),
            data_type=data_type,
            confidence=field.confidence or 0.0,
            bounding_regions=to_region_data(field.bounding_regions),
        )

    @staticmethod
    def _to_tabular_placeholder(name: str, record_count: int) -> ExtractedFieldData:
        # Synthetic field row representing an array-of-object structured
        # field. Frontend keys off data_type == "Tabular". Bbox empty (union
        # of scattered children would mislead). Confidence pinned to 1.0 so
        # the placeholder doesn't pollute "low confidence" Inspector stats.
        suffix = "" if record_count == 1 else "s"
        return ExtractedFieldData(
            name=name,
            value=f"{record_count} record{suffix}",
            data_type="Tabular",
            confidence=1.0,
            bounding_regions=(),
        )

    @staticmethod
    def _extract_pages(result: AnalyzeResult) -> list[PageExtraction]:
        pages: list[PageExtraction] = []
        for p in result.pages or []:
            words = tuple(
                WordData(
                    content=w.content,
                    polygon=tuple(w.polygon or []),
                    confidence=w.confidence,
                )
                for w in (p.words or [])
            )
            pages.append(PageExtraction(page_number=p.page_number, words=words))
        return pages

    @staticmethod
    def _extract_layout_tables(result: AnalyzeResult) -> list[TableExtraction]:
        tables: list[TableExtraction] = []
        for index, t in enumerate(result.tables or []):
            page_number = (
                t.bounding_regions[0].page_number
                if t.bounding_regions and len(t.bounding_regions) > 0
                else 1
            )
            cells: list[TableCellData] = []
            for c in t.cells or []:
                kind_value = (
                    c.kind.value
                    if c.kind is not None and hasattr(c.kind, "value")
                    else (c.kind or "content")
                )
                cells.append(
                    TableCellData(
                        row_index=c.row_index,
                        column_index=c.column_index,
                        # Azure omits span for non-merged cells; normalise to
                        # 1 so the frontend never has to special-case None.
                        row_span=c.row_span or 1,
                        column_span=c.column_span or 1,
                        kind=str(kind_value),
                        content=c.content,
                        bounding_regions=to_region_data(c.bounding_regions),
                    )
                )
            tables.append(
                TableExtraction(
                    index=index,
                    page_number=page_number,
                    row_count=t.row_count,
                    column_count=t.column_count,
                    source=TableSources.LAYOUT,
                    # Layout-source tables are labelled by detection order
                    # ("Table N") in the UI; name stays None here.
                    name=None,
                    bounding_regions=to_region_data(t.bounding_regions),
                    cells=tuple(cells),
                )
            )
        return tables

    async def close(self) -> None:
        await self._client.close()
