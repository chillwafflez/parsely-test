import json
import logging
import re
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.catalog import document_types
from app.db import get_session
from app.models.document import Document
from app.models.template import Template, TemplateAggregationRule, TemplateFieldRule
from app.schemas.bounding import BoundingRegionResponse
from app.schemas.template import (
    EXPORT_SCHEMA_VERSION,
    CreateTemplateRequest,
    ImportTemplateRequest,
    TemplateExportPayload,
    TemplateExportRule,
    TemplateFieldRuleResponse,
    TemplateResponse,
    TemplateSummary,
    UpdateTemplateRequest,
)

router = APIRouter(prefix="/api/templates", tags=["templates"])
logger = logging.getLogger(__name__)


# --- Read endpoints ---------------------------------------------------------


@router.get("", response_model=list[TemplateSummary])
async def list_templates(
    session: AsyncSession = Depends(get_session),
) -> list[TemplateSummary]:
    templates = (
        await session.exec(
            select(Template)
            .order_by(Template.created_at.desc())
            .options(selectinload(Template.rules))
        )
    ).all()

    # Bulk-load document counts grouped by template_id so we don't make N+1
    # queries on the list page.
    run_counts = (
        await session.exec(
            select(Document.template_id, func.count(Document.id))
            .where(Document.template_id.is_not(None))
            .group_by(Document.template_id)
        )
    ).all()
    runs_map = {tid: count for tid, count in run_counts}

    return [
        TemplateSummary(
            id=t.id,
            name=t.name,
            model_id=t.model_id,
            description=t.description,
            apply_to=t.apply_to,
            vendor_hint=t.vendor_hint,
            created_at=t.created_at,
            rule_count=len(t.rules),
            runs=runs_map.get(t.id, 0),
        )
        for t in templates
    ]


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template(
    template_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> TemplateResponse:
    template = await _load_with_rules(template_id, session)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found.")
    runs = await _count_runs(template_id, session)
    return _template_to_response(template, runs)


# --- Write endpoints --------------------------------------------------------


@router.post("", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    request: CreateTemplateRequest,
    session: AsyncSession = Depends(get_session),
) -> TemplateResponse:
    source_doc = (
        await session.exec(
            select(Document)
            .where(Document.id == request.source_document_id)
            .options(selectinload(Document.extracted_fields))
        )
    ).first()
    if source_doc is None:
        raise HTTPException(status_code=400, detail="Source document not found.")

    # The vendor hint is whatever the model considers the document's
    # identifier — VendorName for invoices, Employer.Name for W-2s, etc.
    # Catalog is the single source of truth so this stays in sync with the
    # upload-time matching logic.
    type_def = document_types.find(source_doc.model_id)
    identifier_field_path = (
        type_def.identifier_field_path if type_def else "VendorName"
    )
    vendor_hint: str | None = None
    for f in source_doc.extracted_fields:
        if f.name.lower() == identifier_field_path.lower() and f.value and f.value.strip():
            vendor_hint = f.value.strip()
            break

    # Overrides are keyed by rule name — normalise to case-insensitive lookup
    # so minor casing drift doesn't silently drop the user's hint/aliases.
    overrides_ci = {
        k.lower(): v for k, v in (request.rule_overrides or {}).items()
    }

    # Aggregation fields (those with aggregation_config_json set) belong in
    # aggregation_rules, not field rules — they aren't text extraction, they're
    # rollup operations over a region. Partition once and route each side.
    source_aggregations = [
        f for f in source_doc.extracted_fields if f.aggregation_config_json
    ]
    source_field_rules = [
        f for f in source_doc.extracted_fields if not f.aggregation_config_json
    ]

    template = Template(
        id=uuid4(),
        name=request.name.strip(),
        model_id=source_doc.model_id,
        description=request.description.strip() if request.description else None,
        apply_to=request.apply_to,
        vendor_hint=vendor_hint,
        source_document_id=source_doc.id,
        created_at=datetime.now(timezone.utc),
    )

    for f in source_field_rules:
        rule = TemplateFieldRule(
            id=uuid4(),
            template_id=template.id,
            name=f.name,
            data_type=f.data_type,
            is_required=f.is_required,
            bounding_regions_json=f.bounding_regions_json,
        )
        override = overrides_ci.get(f.name.lower())
        if override is not None:
            rule.hint = override.hint.strip() if override.hint and override.hint.strip() else None
            rule.set_aliases(override.aliases)
        template.rules.append(rule)

    for f in source_aggregations:
        ag_rule = _build_aggregation_rule_from_field(f, template.id)
        if ag_rule is not None:
            template.aggregation_rules.append(ag_rule)

    session.add(template)

    # Link the source document to its newly-created template so the
    # Inspector header reflects "Template: X" on reload without an extra
    # round-trip.
    source_doc.template_id = template.id

    rule_count = len(template.rules)
    await session.commit()

    logger.info(
        "Created template %s (%s) with %d rules from document %s",
        template.id, template.name, rule_count, source_doc.id,
    )

    return _template_to_response(template, runs=1)


@router.put("/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: UUID,
    request: UpdateTemplateRequest,
    session: AsyncSession = Depends(get_session),
) -> TemplateResponse:
    template = await _load_with_rules(template_id, session)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found.")

    # Reject any incoming id that doesn't belong to this template — prevents
    # a caller from guessing a rule id on another template and mutating it
    # through this endpoint.
    existing_by_id = {r.id: r for r in template.rules}
    for incoming in request.rules:
        if incoming.id not in existing_by_id:
            raise HTTPException(
                status_code=400,
                detail=f"Rule {incoming.id} does not belong to this template.",
            )

    template.name = request.name.strip()
    template.description = (
        request.description.strip() if request.description and request.description.strip() else None
    )
    template.vendor_hint = (
        request.vendor_hint.strip() if request.vendor_hint and request.vendor_hint.strip() else None
    )

    incoming_ids = {r.id for r in request.rules}

    # Build the full mutation graph before committing — splitting deletes and
    # updates across multiple commits is a footgun (we hit DbUpdateConcurrencyException
    # in EF that way; SQLAlchemy is more forgiving but we keep the discipline).
    # Remove from the parent collection (rather than session.delete) so the
    # delete-orphan cascade fires AND the in-memory `template.rules` reflects
    # the change immediately for subsequent reads in this request.
    for rule in list(template.rules):
        if rule.id not in incoming_ids:
            template.rules.remove(rule)

    for incoming in request.rules:
        rule = existing_by_id[incoming.id]
        rule.name = incoming.name.strip()
        rule.data_type = incoming.data_type.strip()
        rule.is_required = incoming.is_required
        rule.hint = incoming.hint.strip() if incoming.hint and incoming.hint.strip() else None
        rule.set_aliases(incoming.aliases)

    await session.commit()

    # Re-fetch so the response reflects the canonical persisted shape (with
    # any deleted rules truly gone from the collection).
    refreshed = await _load_with_rules(template_id, session)
    assert refreshed is not None
    runs = await _count_runs(template_id, session)

    logger.info(
        "Updated template %s (%s) — %d rules after reconcile",
        refreshed.id, refreshed.name, len(refreshed.rules),
    )

    return _template_to_response(refreshed, runs)


@router.post(
    "/{template_id}/duplicate",
    response_model=TemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def duplicate_template(
    template_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> TemplateResponse:
    source = (
        await session.exec(
            select(Template)
            .where(Template.id == template_id)
            .options(
                selectinload(Template.rules),
                selectinload(Template.aggregation_rules),
            )
        )
    ).first()
    if source is None:
        raise HTTPException(status_code=404, detail="Template not found.")

    new_name = await _resolve_duplicate_name(source.name, session)

    copy = Template(
        id=uuid4(),
        name=new_name,
        model_id=source.model_id,
        description=source.description,
        apply_to=source.apply_to,
        vendor_hint=source.vendor_hint,
        source_document_id=source.source_document_id,
        created_at=datetime.now(timezone.utc),
    )
    for r in source.rules:
        copy.rules.append(
            TemplateFieldRule(
                id=uuid4(),
                template_id=copy.id,
                name=r.name,
                data_type=r.data_type,
                is_required=r.is_required,
                bounding_regions_json=r.bounding_regions_json,
                hint=r.hint,
                aliases_json=r.aliases_json,
            )
        )
    for r in source.aggregation_rules:
        copy.aggregation_rules.append(
            TemplateAggregationRule(
                id=uuid4(),
                template_id=copy.id,
                name=r.name,
                operation=r.operation,
                is_required=r.is_required,
                bounding_regions_json=r.bounding_regions_json,
            )
        )

    session.add(copy)
    rule_count = len(copy.rules)
    await session.commit()

    logger.info(
        "Duplicated template %s -> %s (%s) with %d rules",
        source.id, copy.id, copy.name, rule_count,
    )

    return _template_to_response(copy, runs=0)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> Response:
    template = (
        await session.exec(select(Template).where(Template.id == template_id))
    ).first()
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found.")
    await session.delete(template)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Export / import --------------------------------------------------------


@router.get("/{template_id}/export")
async def export_template(
    template_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> Response:
    template = await _load_with_rules(template_id, session)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found.")

    payload = TemplateExportPayload(
        version=EXPORT_SCHEMA_VERSION,
        name=template.name,
        model_id=template.model_id,
        description=template.description,
        apply_to=template.apply_to,
        vendor_hint=template.vendor_hint,
        rules=[
            TemplateExportRule(
                name=r.name,
                data_type=r.data_type,
                is_required=r.is_required,
                hint=r.hint,
                aliases=r.get_aliases(),
                bounding_regions=_deserialize_regions(r.bounding_regions_json),
            )
            for r in sorted(template.rules, key=lambda x: x.name)
        ],
    )
    body = payload.model_dump_json(by_alias=True, indent=2).encode("utf-8")
    filename = f"{_sanitize_filename(template.name)}.parsely.json"
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/import", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED
)
async def import_template(
    request: ImportTemplateRequest,
    session: AsyncSession = Depends(get_session),
) -> TemplateResponse:
    name = await _resolve_imported_name(request.name.strip(), session)

    template = Template(
        id=uuid4(),
        name=name,
        model_id=request.model_id.strip(),
        description=request.description.strip() if request.description and request.description.strip() else None,
        apply_to=request.apply_to.strip(),
        vendor_hint=request.vendor_hint.strip() if request.vendor_hint and request.vendor_hint.strip() else None,
        # Imported templates have no source document — frontend already
        # renders a friendly fallback when this is null.
        source_document_id=None,
        created_at=datetime.now(timezone.utc),
    )

    for r in request.rules:
        rule = TemplateFieldRule(
            id=uuid4(),
            template_id=template.id,
            name=r.name.strip(),
            data_type=r.data_type.strip(),
            is_required=r.is_required,
            hint=r.hint.strip() if r.hint and r.hint.strip() else None,
            bounding_regions_json=(
                json.dumps([
                    {"pageNumber": b.page_number, "polygon": list(b.polygon)}
                    for b in r.bounding_regions
                ])
                if r.bounding_regions
                else None
            ),
        )
        rule.set_aliases(r.aliases)
        template.rules.append(rule)

    session.add(template)
    rule_count = len(template.rules)
    await session.commit()

    logger.info(
        "Imported template %s (%s) with %d rules",
        template.id, template.name, rule_count,
    )

    return _template_to_response(template, runs=0)


# --- Helpers ----------------------------------------------------------------


async def _load_with_rules(
    template_id: UUID, session: AsyncSession
) -> Template | None:
    return (
        await session.exec(
            select(Template)
            .where(Template.id == template_id)
            .options(selectinload(Template.rules))
        )
    ).first()


async def _count_runs(template_id: UUID, session: AsyncSession) -> int:
    result = await session.exec(
        select(func.count(Document.id)).where(Document.template_id == template_id)
    )
    return result.one() or 0


def _template_to_response(template: Template, runs: int) -> TemplateResponse:
    return TemplateResponse(
        id=template.id,
        name=template.name,
        model_id=template.model_id,
        description=template.description,
        apply_to=template.apply_to,
        vendor_hint=template.vendor_hint,
        created_at=template.created_at,
        source_document_id=template.source_document_id,
        runs=runs,
        rules=[
            TemplateFieldRuleResponse(
                id=r.id,
                name=r.name,
                data_type=r.data_type,
                is_required=r.is_required,
                hint=r.hint,
                aliases=r.get_aliases(),
                bounding_regions=_deserialize_regions(r.bounding_regions_json),
            )
            for r in sorted(template.rules, key=lambda x: x.name)
        ],
    )


def _deserialize_regions(regions_json: str | None) -> list[BoundingRegionResponse]:
    if not regions_json:
        return []
    decoded = json.loads(regions_json)
    return [BoundingRegionResponse(**entry) for entry in decoded]


def _build_aggregation_rule_from_field(field, template_id: UUID):
    """Snapshots an aggregation field on the source doc into a
    TemplateAggregationRule. Returns None when the stored config can't be
    deserialised — defensive guard against shape changes leaving an old
    document partially convertible."""
    from app.aggregations.operations import AggregationOperation

    if not field.aggregation_config_json:
        return None
    try:
        config = json.loads(field.aggregation_config_json)
    except (ValueError, TypeError):
        return None
    op = AggregationOperation.try_parse(config.get("operation"))
    if op is None:
        return None
    return TemplateAggregationRule(
        id=uuid4(),
        template_id=template_id,
        name=field.name,
        operation=op.value,
        is_required=field.is_required,
        bounding_regions_json=field.bounding_regions_json,
    )


async def _resolve_duplicate_name(base_name: str, session: AsyncSession) -> str:
    # Matches Finder/Explorer behavior — user never hits a wall if they
    # duplicate repeatedly.
    candidate = f"{base_name} (copy)"
    if not await _name_exists(candidate, session):
        return candidate
    n = 2
    while True:
        candidate = f"{base_name} (copy {n})"
        if not await _name_exists(candidate, session):
            return candidate
        n += 1


async def _resolve_imported_name(base_name: str, session: AsyncSession) -> str:
    # "(imported)" suffix is distinct from "(copy)" so provenance stays
    # readable in the sidebar.
    if not await _name_exists(base_name, session):
        return base_name
    candidate = f"{base_name} (imported)"
    if not await _name_exists(candidate, session):
        return candidate
    n = 2
    while True:
        candidate = f"{base_name} (imported {n})"
        if not await _name_exists(candidate, session):
            return candidate
        n += 1


async def _name_exists(name: str, session: AsyncSession) -> bool:
    result = await session.exec(
        select(func.count(Template.id)).where(Template.name == name)
    )
    return (result.one() or 0) > 0


def _sanitize_filename(name: str) -> str:
    # Strip anything that isn't alphanumeric / dash / underscore, then trim
    # leading/trailing dashes. Fall back to "template" if empty.
    cleaned = re.sub(r"[^a-zA-Z0-9-_]+", "-", name).strip("-")
    return cleaned or "template"
