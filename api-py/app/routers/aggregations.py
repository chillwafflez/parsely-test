import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.aggregations import compute as aggregation_compute
from app.aggregations.evaluator import evaluate as evaluate_aggregation
from app.aggregations.operations import AggregationOperation
from app.aggregations.parser import parse_words
from app.db import get_session
from app.deps import get_layout
from app.geometry.polygon import words_inside_region
from app.models.document import Document
from app.models.field import ExtractedField
from app.models.template import Template, TemplateAggregationRule
from app.schemas.aggregation import (
    AggregationPreviewRequest,
    AggregationPreviewResponse,
    AggregationTokenResponse,
    CreateAggregationRequest,
)
from app.schemas.field import ExtractedFieldResponse, field_to_response
from app.services.layout_storage import LayoutStorageService

router = APIRouter(
    prefix="/api/documents/{document_id}/aggregations",
    tags=["aggregations"],
)


@router.post("/preview", response_model=AggregationPreviewResponse)
async def preview_aggregation(
    document_id: UUID,
    request: AggregationPreviewRequest,
    session: AsyncSession = Depends(get_session),
    layout: LayoutStorageService = Depends(get_layout),
) -> AggregationPreviewResponse:
    """Filters layout words to the drawn polygon, parses numeric tokens, and
    returns them so the aggregation modal can render a live preview.
    Triggers lazy backfill when a legacy doc has no persisted layout."""
    doc = (
        await session.exec(select(Document).where(Document.id == document_id))
    ).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    pages = await layout.get_or_backfill(document_id, doc.storage_path)
    if pages is None:
        # Original PDF blob is missing — broken document, can't recover.
        raise HTTPException(status_code=404, detail="Document layout is unavailable.")

    page = next((p for p in pages if p.page_number == request.page_number), None)
    if page is None:
        # Page out of range — soft failure (empty token list) rather than 400
        # so the modal can surface "no numbers detected" gracefully.
        return AggregationPreviewResponse(tokens=[])

    matched = words_inside_region(page.words, request.polygon)
    tokens = [
        AggregationTokenResponse(
            text=t.source.content,
            value=float(t.value),
            confidence=t.source.confidence,
            polygon=list(t.source.polygon),
        )
        for t in parse_words(matched)
    ]
    return AggregationPreviewResponse(tokens=tokens)


@router.post("", response_model=ExtractedFieldResponse, status_code=201)
async def create_aggregation(
    document_id: UUID,
    request: CreateAggregationRequest,
    session: AsyncSession = Depends(get_session),
    layout: LayoutStorageService = Depends(get_layout),
) -> ExtractedFieldResponse:
    """Commits an aggregation field on the document and — when the document
    is matched to a template — auto-promotes it to a TemplateAggregationRule
    so future uploads replay it. Recomputes the result server-side from the
    layout (rather than trusting a client-side number) so the persisted
    value is always the canonical answer."""
    operation = AggregationOperation.try_parse(request.operation)
    if operation is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid operation. Use: Sum, Average, Count, Min, Max.",
        )

    doc = (
        await session.exec(
            select(Document)
            .where(Document.id == document_id)
            .options(
                selectinload(Document.template).selectinload(
                    Template.aggregation_rules
                )
            )
        )
    ).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    pages = await layout.get_or_backfill(document_id, doc.storage_path)
    if pages is None:
        raise HTTPException(status_code=404, detail="Document layout is unavailable.")

    evaluated_at = datetime.now(timezone.utc)
    result = evaluate_aggregation(
        operation, request.polygon, request.page_number, pages, evaluated_at
    )

    name = request.name.strip()
    regions_json = json.dumps(
        [{"pageNumber": request.page_number, "polygon": list(request.polygon)}]
    )
    config_json = result.config.model_dump_json(by_alias=True)

    field = ExtractedField(
        id=uuid4(),
        document_id=document_id,
        name=name,
        value=result.value,
        data_type="Number",
        confidence=result.confidence,
        is_required=request.is_required,
        is_corrected=True,
        corrected_at=evaluated_at,
        is_user_added=True,
        bounding_regions_json=regions_json,
        aggregation_config_json=config_json,
    )

    session.add(field)

    # Auto-promote to a template rule so future matching uploads replay the
    # aggregation. Templateless documents keep it as a local field only;
    # promotion happens automatically when the user later saves the document
    # as a new template.
    if doc.template is not None:
        doc.template.aggregation_rules.append(
            TemplateAggregationRule(
                id=uuid4(),
                template_id=doc.template.id,
                name=name,
                operation=operation.value,
                is_required=request.is_required,
                bounding_regions_json=regions_json,
            )
        )

    await session.commit()

    return field_to_response(field)
