from datetime import datetime
from enum import IntEnum
from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, SmallInteger
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from app.models.field import ExtractedField
    from app.models.table import ExtractedTable
    from app.models.template import Template


class DocumentStatus(IntEnum):
    UPLOADED = 0
    ANALYZING = 1
    COMPLETED = 2
    FAILED = 3


class Document(SQLModel, table=True):
    __tablename__ = "documents"

    id: UUID = Field(
        default_factory=uuid4,
        sa_column=Column(PgUUID(as_uuid=True), primary_key=True),
    )
    original_file_name: str = Field(max_length=512)
    storage_path: str = Field(max_length=1024)
    model_id: str = Field(default="prebuilt-invoice", max_length=128)
    status: DocumentStatus = Field(
        default=DocumentStatus.UPLOADED,
        sa_column=Column(SmallInteger, nullable=False),
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    error_message: str | None = Field(default=None)

    # Soft link — deleting a template must not cascade-delete its matched
    # documents.
    template_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            PgUUID(as_uuid=True),
            ForeignKey("templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    template: Optional["Template"] = Relationship()
    extracted_fields: list["ExtractedField"] = Relationship(
        back_populates="document",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    extracted_tables: list["ExtractedTable"] = Relationship(
        back_populates="document",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

    __table_args__ = (
        Index("ix_documents_created_at", "created_at"),
        Index("ix_documents_template_id", "template_id"),
    )
