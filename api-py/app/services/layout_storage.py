"""Persists the page-level OCR layout (word polygons + content + confidence)
for a document so spatial features — template-rule replay, aggregation
regions — can re-query it without re-running Azure DI. Stored as a sibling
blob next to the original upload in the same container."""

import json
import logging
from uuid import UUID

from app.domain import PageExtraction, WordData
from app.services.blob_storage import BlobStorageService
from app.services.document_intelligence import DocumentIntelligenceService

CONTENT_TYPE = "application/json"
LAYOUT_MODEL_ID = "prebuilt-layout"

logger = logging.getLogger(__name__)


class LayoutStorageService:
    def __init__(
        self,
        blobs: BlobStorageService,
        intelligence: DocumentIntelligenceService,
    ):
        self._blobs = blobs
        self._intelligence = intelligence

    async def save(self, document_id: UUID, pages: list[PageExtraction]) -> None:
        payload = json.dumps(
            [
                {
                    "pageNumber": p.page_number,
                    "words": [
                        {
                            "content": w.content,
                            "polygon": list(w.polygon),
                            "confidence": w.confidence,
                        }
                        for w in p.words
                    ],
                }
                for p in pages
            ]
        ).encode("utf-8")
        await self._blobs.upload(
            self._blob_name(document_id), payload, CONTENT_TYPE
        )

    async def load(self, document_id: UUID) -> list[PageExtraction] | None:
        data = await self._blobs.try_download_bytes(self._blob_name(document_id))
        if data is None:
            return None
        decoded = json.loads(data.decode("utf-8"))
        return [
            PageExtraction(
                page_number=entry["pageNumber"],
                words=tuple(
                    WordData(
                        content=w["content"],
                        polygon=tuple(w["polygon"]),
                        confidence=w["confidence"],
                    )
                    for w in entry["words"]
                ),
            )
            for entry in decoded
        ]

    async def get_or_backfill(
        self,
        document_id: UUID,
        original_blob_name: str,
    ) -> list[PageExtraction] | None:
        existing = await self.load(document_id)
        if existing is not None:
            return existing

        pdf_bytes = await self._blobs.try_download_bytes(original_blob_name)
        if pdf_bytes is None:
            return None

        logger.info(
            "Backfilling layout for legacy document %s via prebuilt-layout.",
            document_id,
        )

        extraction = await self._intelligence.analyze(pdf_bytes, LAYOUT_MODEL_ID)
        pages_list = list(extraction.pages)
        await self.save(document_id, pages_list)
        return pages_list

    @staticmethod
    def _blob_name(document_id: UUID) -> str:
        # {id-without-dashes}-layout.json — parallels the upload blob naming
        # ({id-without-dashes}-{filename}) so related artifacts cluster
        # together when browsing the container.
        return f"{document_id.hex}-layout.json"
