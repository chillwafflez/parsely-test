import json
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    pass


class Template(SQLModel, table=True):
    __tablename__ = "templates"

    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PgUUID(as_uuid=True), primary_key=True),
    )
    name: str = Field(max_length=256)
    # Azure Document Intelligence prebuilt model the template applies to.
    # Set on creation and treated as immutable thereafter — matching is
    # scoped by model_id, so changing it would silently change which uploads
    # can pick the template up.
    model_id: str = Field(default="prebuilt-invoice", max_length=128)
    description: str | None = Field(default=None, max_length=2048)
    apply_to: str = Field(default="similar", max_length=32)
    vendor_hint: str | None = Field(default=None, max_length=512)
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    # Free pointer to the document the template was created from. No FK
    # constraint — the source may be deleted later and that's fine; the
    # template stands on its own.
    source_document_id: UUID | None = Field(
        default=None,
        sa_column=Column(PgUUID(as_uuid=True), nullable=True),
    )

    rules: list["TemplateFieldRule"] = Relationship(
        back_populates="template",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    aggregation_rules: list["TemplateAggregationRule"] = Relationship(
        back_populates="template",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

    __table_args__ = (
        Index("ix_templates_created_at", "created_at"),
        Index("ix_templates_vendor_hint", "vendor_hint"),
        Index("ix_templates_model_id", "model_id"),
    )


class TemplateFieldRule(SQLModel, table=True):
    __tablename__ = "template_field_rules"

    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PgUUID(as_uuid=True), primary_key=True),
    )
    template_id: UUID = Field(
        sa_column=Column(
            PgUUID(as_uuid=True),
            ForeignKey("templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )

    name: str = Field(max_length=256)
    data_type: str = Field(default="string", max_length=64)
    is_required: bool = Field(default=False)
    bounding_regions_json: str | None = Field(default=None)

    # Optional free-text description shown to the voice-fill LLM for
    # disambiguation (e.g. "the billing contact's full name").
    hint: str | None = Field(default=None, max_length=200)

    # Optional alternative phrasings the user might say. Stored as a JSON
    # array — never queried by alias, only read when building the voice-fill
    # prompt, so a relational table would be overkill.
    aliases_json: str | None = Field(default=None)

    template: "Template" = Relationship(back_populates="rules")

    __table_args__ = (
        Index("ix_template_field_rules_template_id_name", "template_id", "name"),
    )

    def get_aliases(self) -> list[str]:
        if not self.aliases_json:
            return []
        return json.loads(self.aliases_json)

    def set_aliases(self, aliases: list[str] | None) -> None:
        if not aliases:
            self.aliases_json = None
            return
        cleaned: list[str] = []
        seen: set[str] = set()
        for a in aliases:
            if not a or not a.strip():
                continue
            trimmed = a.strip()
            key = trimmed.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(trimmed)
        self.aliases_json = json.dumps(cleaned) if cleaned else None


class TemplateAggregationRule(SQLModel, table=True):
    __tablename__ = "template_aggregation_rules"

    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PgUUID(as_uuid=True), primary_key=True),
    )
    template_id: UUID = Field(
        sa_column=Column(
            PgUUID(as_uuid=True),
            ForeignKey("templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )

    name: str = Field(max_length=256)
    # Aggregation operation as the enum's name string ("Sum", "Average",
    # "Count", "Min", "Max"). Stored as text for readability and to keep
    # schema changes additive when new operations land.
    operation: str = Field(max_length=16)
    is_required: bool = Field(default=False)
    bounding_regions_json: str | None = Field(default=None)

    template: "Template" = Relationship(back_populates="aggregation_rules")

    __table_args__ = (
        Index(
            "ix_template_aggregation_rules_template_id_name",
            "template_id",
            "name",
        ),
    )
