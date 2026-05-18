from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelModel
from app.schemas.bounding import BoundingRegionResponse


# --- Read responses ---------------------------------------------------------


class TemplateSummary(CamelModel):
    id: UUID
    name: str
    model_id: str
    description: str | None
    apply_to: str
    vendor_hint: str | None
    created_at: datetime
    rule_count: int
    runs: int


class TemplateFieldRuleResponse(CamelModel):
    id: UUID
    name: str
    data_type: str
    is_required: bool
    hint: str | None
    aliases: list[str]
    bounding_regions: list[BoundingRegionResponse]


class TemplateResponse(CamelModel):
    id: UUID
    name: str
    model_id: str
    description: str | None
    apply_to: str
    vendor_hint: str | None
    created_at: datetime
    source_document_id: UUID | None
    runs: int
    rules: list[TemplateFieldRuleResponse]


# --- Write requests ---------------------------------------------------------


ApplyTo = Literal["vendor", "similar", "all"]


class RuleOverride(CamelModel):
    """Optional voice-fill overrides supplied per captured rule when saving
    a template. Keyed by rule field name in CreateTemplateRequest.rule_overrides."""

    hint: str | None = Field(default=None, max_length=200)
    aliases: list[str] | None = None


class CreateTemplateRequest(CamelModel):
    name: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=2048)
    apply_to: ApplyTo
    source_document_id: UUID
    rule_overrides: dict[str, RuleOverride] | None = None


class UpdateTemplateRuleRequest(CamelModel):
    id: UUID
    name: str = Field(min_length=1, max_length=256)
    data_type: str = Field(min_length=1, max_length=64)
    is_required: bool
    hint: str | None = Field(default=None, max_length=200)
    aliases: list[str] | None = None


class UpdateTemplateRequest(CamelModel):
    """Full-replace payload. Metadata is applied as-is; rules are reconciled
    by id — existing ids updated in place, omitted ids deleted. Adding new
    rules is out of scope (requires a bbox, which requires the PDF + draw)."""

    name: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=2048)
    vendor_hint: str | None = Field(default=None, max_length=512)
    rules: list[UpdateTemplateRuleRequest]


# --- Export / import --------------------------------------------------------

# V2 includes model_id to scope the template to an Azure DI prebuilt. V1
# files (with `kind` instead) are no longer accepted — re-export from source.
EXPORT_SCHEMA_VERSION = 2


class TemplateExportRule(CamelModel):
    name: str
    data_type: str
    is_required: bool
    hint: str | None
    aliases: list[str]
    bounding_regions: list[BoundingRegionResponse]


class TemplateExportPayload(CamelModel):
    """Portable on-disk shape. Server-generated ids + source-document
    references are intentionally omitted so the file is safe to share."""

    version: int
    name: str
    model_id: str
    description: str | None
    apply_to: str
    vendor_hint: str | None
    rules: list[TemplateExportRule]


class ImportTemplateRuleRequest(CamelModel):
    name: str = Field(min_length=1, max_length=256)
    data_type: str = Field(min_length=1, max_length=64)
    is_required: bool
    hint: str | None = Field(default=None, max_length=200)
    aliases: list[str] | None = None
    bounding_regions: list[BoundingRegionResponse] | None = None


class ImportTemplateRequest(CamelModel):
    version: int = Field(ge=2, le=2)
    name: str = Field(min_length=1, max_length=256)
    model_id: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2048)
    apply_to: ApplyTo
    vendor_hint: str | None = Field(default=None, max_length=512)
    rules: list[ImportTemplateRuleRequest]
