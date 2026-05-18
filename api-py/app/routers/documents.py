import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.aggregations import compute as agg_compute
from app.aggregations.evaluator import evaluate as evaluate_aggregation
from app.aggregations.operations import AggregationOperation
from app.catalog import document_types
from app.db import get_session
from app.deps import get_blobs, get_intelligence, get_layout
from app.domain import PageExtraction
from app.geometry.polygon import words_inside_region
from app.models.document import Document, DocumentStatus
from app.models.field import ExtractedField
from app.models.table import ExtractedTable
from app.models.template import Template, TemplateFieldRule
from app.schemas.bounding import BoundingRegionResponse
from app.schemas.document import (
    CreateFieldRequest,
    DocumentResponse,
    DocumentSummary,
    TableCellResponse,
    TableResponse,
    UpdateFieldRequest,
    UpdateTableCellRequest,
)
from app.schemas.field import ExtractedFieldResponse, field_to_response
from app.services.blob_storage import BlobStorageService
from app.services.document_intelligence import DocumentIntelligenceService
from app.services.layout_storage import LayoutStorageService

router = APIRouter(prefix="/api/documents", tags=["documents"])
logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
DEFAULT_MODEL_ID = "prebuilt-invoice"


_STATUS_NAMES = {
    DocumentStatus.UPLOADED: "Uploaded",
    DocumentStatus.ANALYZING: "Analyzing",
    DocumentStatus.COMPLETED: "Completed",
    DocumentStatus.FAILED: "Failed",
}


# --- Read endpoints ---------------------------------------------------------


@router.get("", response_model=list[DocumentSummary])
async def list_documents(
    session: AsyncSession = Depends(get_session),
) -> list[DocumentSummary]:
    docs = (
        await session.exec(
            select(Document)
            .order_by(Document.created_at.desc())
            .limit(50)
            .options(
                selectinload(Document.extracted_fields),
                selectinload(Document.template),
            )
        )
    ).all()

    return [
        DocumentSummary(
            id=d.id,
            file_name=d.original_file_name,
            status=_STATUS_NAMES[DocumentStatus(d.status)],
            created_at=d.created_at,
            field_count=len(d.extracted_fields),
            template_name=d.template.name if d.template else None,
        )
        for d in docs
    ]


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> DocumentResponse:
    doc = await _load_full(document_id, session)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return _document_to_response(doc)


@router.get("/{document_id}/file")
async def get_document_file(
    document_id: UUID,
    session: AsyncSession = Depends(get_session),
    blobs: BlobStorageService = Depends(get_blobs),
) -> Response:
    doc = (
        await session.exec(select(Document).where(Document.id == document_id))
    ).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    content = await blobs.try_download_bytes(doc.storage_path)
    if content is None:
        raise HTTPException(status_code=404, detail="Original file not found.")

    return Response(
        content=content,
        media_type=_guess_content_type(doc.original_file_name),
        # inline disposition so the frontend can render in <iframe>/react-pdf
        headers={
            "Content-Disposition": f'inline; filename="{doc.original_file_name}"',
        },
    )


# --- Upload (the big one) ---------------------------------------------------


@router.post("/upload", response_model=DocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    model_id: str | None = Form(default=None, alias="modelId"),
    template_mode: str | None = Form(default=None, alias="templateMode"),
    template_id: UUID | None = Form(default=None, alias="templateId"),
    session: AsyncSession = Depends(get_session),
    blobs: BlobStorageService = Depends(get_blobs),
    intelligence: DocumentIntelligenceService = Depends(get_intelligence),
    layout: LayoutStorageService = Depends(get_layout),
) -> DocumentResponse:
    if file is None or file.filename is None:
        raise HTTPException(status_code=400, detail="No file provided.")

    mode = (template_mode or "auto").strip().lower()
    if mode not in ("auto", "manual", "none"):
        raise HTTPException(
            status_code=400,
            detail="Invalid templateMode. Use: auto, manual, none.",
        )

    # Eagerly load the manual template so the upload fails cheaply before
    # the expensive Azure DI call when the user picked a bad templateId.
    manual_template: Template | None = None
    if mode == "manual":
        if template_id is None:
            raise HTTPException(
                status_code=400,
                detail="templateId is required when templateMode=manual.",
            )
        manual_template = (
            await session.exec(
                select(Template)
                .where(Template.id == template_id)
                .options(
                    selectinload(Template.rules),
                    selectinload(Template.aggregation_rules),
                )
            )
        ).first()
        if manual_template is None:
            raise HTTPException(status_code=404, detail="Template not found.")

    doc_id = uuid4()
    safe_name = Path(file.filename).name
    blob_name = f"{doc_id.hex}-{safe_name}"
    content_type = file.content_type or _guess_content_type(safe_name)

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="No file provided.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large.")

    await blobs.upload(blob_name, content, content_type)

    doc = Document(
        id=doc_id,
        original_file_name=safe_name,
        storage_path=blob_name,
        model_id=model_id.strip() if model_id and model_id.strip() else DEFAULT_MODEL_ID,
        status=DocumentStatus.ANALYZING,
        created_at=datetime.now(timezone.utc),
    )

    try:
        extraction = await intelligence.analyze(content, doc.model_id)

        for f in extraction.fields:
            doc.extracted_fields.append(
                ExtractedField(
                    id=uuid4(),
                    document_id=doc.id,
                    name=f.name,
                    value=f.value,
                    data_type=f.data_type,
                    confidence=f.confidence,
                    bounding_regions_json=(
                        json.dumps(
                            [
                                {"pageNumber": r.page_number, "polygon": list(r.polygon)}
                                for r in f.bounding_regions
                            ]
                        )
                        if f.bounding_regions
                        else None
                    ),
                )
            )

        for t in extraction.tables:
            cells = [
                TableCellResponse(
                    row_index=c.row_index,
                    column_index=c.column_index,
                    row_span=c.row_span,
                    column_span=c.column_span,
                    kind=c.kind,
                    content=c.content,
                    is_corrected=False,
                    bounding_regions=[
                        BoundingRegionResponse(page_number=r.page_number, polygon=list(r.polygon))
                        for r in c.bounding_regions
                    ],
                ).model_dump(by_alias=True)
                for c in t.cells
            ]
            regions = [
                {"pageNumber": r.page_number, "polygon": list(r.polygon)}
                for r in t.bounding_regions
            ]
            doc.extracted_tables.append(
                ExtractedTable(
                    id=uuid4(),
                    document_id=doc.id,
                    index=t.index,
                    page_number=t.page_number,
                    row_count=t.row_count,
                    column_count=t.column_count,
                    source=t.source,
                    name=t.name,
                    bounding_regions_json=json.dumps(regions) if regions else None,
                    cells_json=json.dumps(cells),
                )
            )

        # Persist the page-level layout so spatial features (aggregation
        # preview, template replay) can re-query later without re-running
        # Azure DI. Failure is non-fatal — missing layout backfills lazily
        # on first read.
        try:
            await layout.save(doc.id, list(extraction.pages))
        except Exception as exc:
            logger.warning(
                "Failed to persist layout blob for document %s; continuing. "
                "Layout will be backfilled on first spatial query. (%s)",
                doc.id, exc,
            )

        doc.status = DocumentStatus.COMPLETED
        doc.completed_at = datetime.now(timezone.utc)

        if mode == "auto":
            await _try_match_template(doc, list(extraction.pages), session)
        elif mode == "manual" and manual_template is not None:
            doc.template_id = manual_template.id
            doc.template = manual_template
            _apply_template_rules(doc, manual_template, list(extraction.pages))

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Analysis failed for document %s: %s", doc.id, exc)
        doc.status = DocumentStatus.FAILED
        doc.error_message = str(exc)

    session.add(doc)
    await session.commit()

    reloaded = await _load_full(doc.id, session)
    assert reloaded is not None
    return _document_to_response(reloaded)


# --- Field CRUD -------------------------------------------------------------


@router.post(
    "/{document_id}/fields",
    response_model=ExtractedFieldResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_field(
    document_id: UUID,
    request: CreateFieldRequest,
    session: AsyncSession = Depends(get_session),
) -> ExtractedFieldResponse:
    doc = (
        await session.exec(select(Document.id).where(Document.id == document_id))
    ).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    regions_json = json.dumps(
        [{"pageNumber": request.page_number, "polygon": list(request.polygon)}]
    )

    field = ExtractedField(
        id=uuid4(),
        document_id=document_id,
        name=request.name.strip(),
        value=None,
        data_type=request.data_type,
        confidence=1.0,
        is_required=request.is_required,
        is_corrected=True,
        corrected_at=datetime.now(timezone.utc),
        is_user_added=True,
        bounding_regions_json=regions_json,
    )

    session.add(field)
    await session.commit()
    return field_to_response(field)


@router.delete(
    "/{document_id}/fields/{field_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_field(
    document_id: UUID,
    field_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> Response:
    field = (
        await session.exec(
            select(ExtractedField).where(
                ExtractedField.id == field_id,
                ExtractedField.document_id == document_id,
            )
        )
    ).first()
    if field is None:
        raise HTTPException(status_code=404, detail="Field not found.")

    await session.delete(field)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/{document_id}/fields/{field_id}",
    response_model=ExtractedFieldResponse,
)
async def update_field(
    document_id: UUID,
    field_id: UUID,
    update: UpdateFieldRequest,
    session: AsyncSession = Depends(get_session),
) -> ExtractedFieldResponse:
    field = (
        await session.exec(
            select(ExtractedField).where(
                ExtractedField.id == field_id,
                ExtractedField.document_id == document_id,
            )
        )
    ).first()
    if field is None:
        raise HTTPException(status_code=404, detail="Field not found.")

    changed = False
    if update.value is not None and update.value != field.value:
        field.value = update.value
        changed = True
    if update.data_type and update.data_type != field.data_type:
        field.data_type = update.data_type
        changed = True
    if update.is_required is not None and update.is_required != field.is_required:
        field.is_required = update.is_required
        changed = True

    if changed:
        field.is_corrected = True
        field.corrected_at = datetime.now(timezone.utc)
        await session.commit()

    return field_to_response(field)


@router.patch(
    "/{document_id}/tables/{table_id}/cells",
    response_model=TableCellResponse,
)
async def update_table_cell(
    document_id: UUID,
    table_id: UUID,
    request: UpdateTableCellRequest,
    session: AsyncSession = Depends(get_session),
) -> TableCellResponse:
    table = (
        await session.exec(
            select(ExtractedTable).where(
                ExtractedTable.id == table_id,
                ExtractedTable.document_id == document_id,
            )
        )
    ).first()
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found.")

    if not (0 <= request.row_index < table.row_count) or not (
        0 <= request.column_index < table.column_count
    ):
        raise HTTPException(status_code=400, detail="Cell coordinates out of range.")

    cells_raw = json.loads(table.cells_json) if table.cells_json else []
    cells = [TableCellResponse(**c) for c in cells_raw]

    # Cells are addressed by (row, col) — for merged cells, that's the
    # top-left position (Azure's convention; the frontend resolves clicks
    # anywhere in the merged region back to top-left before sending).
    target_idx = next(
        (
            i
            for i, c in enumerate(cells)
            if c.row_index == request.row_index and c.column_index == request.column_index
        ),
        None,
    )
    if target_idx is None:
        raise HTTPException(status_code=404, detail="Cell not found at the given coordinates.")

    existing = cells[target_idx]
    if existing.content == request.content:
        # No-op — preserves is_corrected so a re-save of the original value
        # doesn't visually flag a clean cell.
        return existing

    updated = existing.model_copy(update={"content": request.content, "is_corrected": True})
    cells[target_idx] = updated
    table.cells_json = json.dumps([c.model_dump(by_alias=True) for c in cells])

    await session.commit()
    return updated


# --- Template matching + rule replay ---------------------------------------


async def _try_match_template(
    doc: Document, pages: list[PageExtraction], session: AsyncSession
) -> None:
    """VendorHint heuristic: pulls the model's identifier field
    (VendorName for invoices, Employer.Name for W-2s, etc., resolved via
    DocumentTypeCatalog) and finds the most recent template for the same
    model whose vendor_hint matches case-insensitively."""
    type_def = document_types.find(doc.model_id)
    if type_def is None:
        return

    identifier_value = next(
        (
            f.value.strip()
            for f in doc.extracted_fields
            if f.name.lower() == type_def.identifier_field_path.lower()
            and f.value
            and f.value.strip()
        ),
        None,
    )
    if not identifier_value:
        return

    normalised = identifier_value.lower()

    # Scope by model_id so a W-2 upload never picks up an invoice template
    # even if their identifier strings happen to collide.
    match = (
        await session.exec(
            select(Template)
            .where(
                Template.model_id == doc.model_id,
                Template.vendor_hint.is_not(None),
            )
            .order_by(Template.created_at.desc())
            .options(
                selectinload(Template.rules),
                selectinload(Template.aggregation_rules),
            )
        )
    ).all()

    matched = next(
        (t for t in match if t.vendor_hint and t.vendor_hint.lower() == normalised),
        None,
    )

    if matched is not None:
        doc.template_id = matched.id
        doc.template = matched
        _apply_template_rules(doc, matched, pages)


def _apply_template_rules(
    doc: Document, template: Template, pages: list[PageExtraction]
) -> None:
    """For fields Azure DI already extracted (matched by name,
    case-insensitive), override data_type + is_required; for rules not
    extracted, run a region-based word pickup and inject a field with the
    extracted value. Aggregation rules recompute against the layout and
    inject the result as a new field."""
    for rule in template.rules:
        existing = next(
            (f for f in doc.extracted_fields if f.name.lower() == rule.name.lower()),
            None,
        )

        if existing is not None:
            existing.data_type = rule.data_type
            existing.is_required = rule.is_required
            continue

        value, confidence = _extract_text_from_rule(rule, pages)

        doc.extracted_fields.append(
            ExtractedField(
                id=uuid4(),
                document_id=doc.id,
                name=rule.name,
                value=value,
                data_type=rule.data_type,
                confidence=confidence,
                is_required=rule.is_required,
                is_corrected=False,
                is_user_added=True,
                bounding_regions_json=rule.bounding_regions_json,
            )
        )

    _apply_aggregation_rules(doc, template, pages)


def _apply_aggregation_rules(
    doc: Document, template: Template, pages: list[PageExtraction]
) -> None:
    evaluated_at = datetime.now(timezone.utc)

    for rule in template.aggregation_rules:
        # Skip if name collides with an existing field — user's values are
        # never silently overwritten.
        if any(f.name.lower() == rule.name.lower() for f in doc.extracted_fields):
            continue

        op = AggregationOperation.try_parse(rule.operation)
        if op is None:
            continue

        if not rule.bounding_regions_json:
            continue
        regions = json.loads(rule.bounding_regions_json)
        if not regions:
            continue

        region = regions[0]
        result = evaluate_aggregation(
            op, region["polygon"], region["pageNumber"], pages, evaluated_at
        )

        doc.extracted_fields.append(
            ExtractedField(
                id=uuid4(),
                document_id=doc.id,
                name=rule.name,
                value=result.value,
                data_type="Number",
                confidence=result.confidence,
                is_required=rule.is_required,
                is_corrected=False,
                is_user_added=True,
                bounding_regions_json=rule.bounding_regions_json,
                aggregation_config_json=result.config.model_dump_json(by_alias=True),
            )
        )


def _extract_text_from_rule(
    rule: TemplateFieldRule, pages: list[PageExtraction]
) -> tuple[str | None, float]:
    """Picks layout words whose center falls inside the rule's region,
    concatenates them, and averages confidence. Returns (None, 0.0) when
    the region is missing or no words fell inside."""
    if not rule.bounding_regions_json:
        return None, 0.0
    regions = json.loads(rule.bounding_regions_json)
    if not regions:
        return None, 0.0
    region = regions[0]
    page = next((p for p in pages if p.page_number == region["pageNumber"]), None)
    if page is None:
        return None, 0.0
    matched = words_inside_region(page.words, region["polygon"])
    if not matched:
        return None, 0.0
    value = " ".join(w.content for w in matched).strip()
    confidence = sum(w.confidence for w in matched) / len(matched)
    return (value if value else None), confidence


# --- Helpers ----------------------------------------------------------------


async def _load_full(document_id: UUID, session: AsyncSession) -> Document | None:
    return (
        await session.exec(
            select(Document)
            .where(Document.id == document_id)
            .options(
                selectinload(Document.extracted_fields),
                selectinload(Document.extracted_tables),
                selectinload(Document.template),
            )
        )
    ).first()


def _document_to_response(doc: Document) -> DocumentResponse:
    return DocumentResponse(
        id=doc.id,
        file_name=doc.original_file_name,
        model_id=doc.model_id,
        status=_STATUS_NAMES[DocumentStatus(doc.status)],
        created_at=doc.created_at,
        completed_at=doc.completed_at,
        error_message=doc.error_message,
        template_id=doc.template_id,
        template_name=doc.template.name if doc.template else None,
        fields=[
            field_to_response(f)
            for f in sorted(doc.extracted_fields, key=lambda x: x.name)
        ],
        tables=[
            _table_to_response(t)
            for t in sorted(doc.extracted_tables, key=lambda x: x.index)
        ],
    )


def _table_to_response(t: ExtractedTable) -> TableResponse:
    regions = (
        [BoundingRegionResponse(**r) for r in json.loads(t.bounding_regions_json)]
        if t.bounding_regions_json
        else []
    )
    cells = [TableCellResponse(**c) for c in json.loads(t.cells_json)] if t.cells_json else []
    return TableResponse(
        id=t.id,
        index=t.index,
        page_number=t.page_number,
        row_count=t.row_count,
        column_count=t.column_count,
        source=t.source,
        name=t.name,
        bounding_regions=regions,
        cells=cells,
    )


def _guess_content_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }.get(ext, "application/octet-stream")
