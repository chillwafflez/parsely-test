from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Column, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from app.models.document import Document


class ExtractedTable(SQLModel, table=True):
    __tablename__ = "extracted_tables"

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

    # "Layout" for tables from Azure DI's result.tables; "Synthesized" for
    # rows built by TableSynthesizer from array-of-object structured fields.
    source: str = Field(default="Layout", max_length=32)

    # Always set for synthesized tables (matches the originating field path,
    # with a [N] suffix on repeats). Null for layout tables — the UI labels
    # those by detection order ("Table N").
    name: str | None = Field(default=None, max_length=512)

    # 0-based position within the document — preserves Azure DI's detection
    # order so "Table 1", "Table 2", … stay consistent across reloads.
    index: int = Field(default=0)

    page_number: int = Field(default=1)
    row_count: int = Field(default=0)
    column_count: int = Field(default=0)

    bounding_regions_json: str | None = Field(default=None)

    # Denormalised because cells are always loaded together (the table
    # renders as a single grid) and never queried individually. Edits
    # read-modify-write the whole blob; fine at prototype scale.
    cells_json: str = Field(default="[]")

    document: "Document" = Relationship(back_populates="extracted_tables")

    __table_args__ = (
        Index("ix_extracted_tables_document_id_index", "document_id", "index"),
    )
