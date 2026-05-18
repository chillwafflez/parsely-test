from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from app.models.document import Document


class ExtractedField(SQLModel, table=True):
    __tablename__ = "extracted_fields"

    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PgUUID(as_uuid=True), primary_key=True),
    )
    document_id: UUID = Field(
        sa_column=Column(
            PgUUID(as_uuid=True),
            ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )

    name: str = Field(max_length=256)
    value: str | None = Field(default=None)
    data_type: str = Field(default="String", max_length=64)
    confidence: float = Field(default=0.0)

    bounding_regions_json: str | None = Field(default=None)

    is_required: bool = Field(default=False)
    is_corrected: bool = Field(default=False)
    corrected_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    # True if the user drew this field manually (not extracted by Azure DI).
    # Used to route user-added fields into the Inspector's "Custom" group.
    is_user_added: bool = Field(default=False)

    # Serialised aggregation provenance ({operation, sourceTokenCount,
    # evaluatedAt}) for fields produced by the aggregation feature. Null on
    # every other field. Presence — not data_type — is the authoritative
    # "is this an aggregation?" signal.
    aggregation_config_json: str | None = Field(default=None)

    document: "Document" = Relationship(back_populates="extracted_fields")

    __table_args__ = (
        Index("ix_extracted_fields_document_id_name", "document_id", "name"),
    )
