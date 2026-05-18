from typing import IO

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import BlobServiceClient


class BlobStorageService:
    def __init__(self, connection_string: str, container_name: str):
        if not container_name:
            raise ValueError("Blob container name must be configured.")
        self._service_client = BlobServiceClient.from_connection_string(connection_string)
        self._container = self._service_client.get_container_client(container_name)

    async def upload(
        self,
        blob_name: str,
        content: bytes | IO[bytes],
        content_type: str,
    ) -> None:
        blob = self._container.get_blob_client(blob_name)
        await blob.upload_blob(
            content,
            content_settings=ContentSettings(content_type=content_type),
            overwrite=True,
        )

    async def try_download_bytes(self, blob_name: str) -> bytes | None:
        # Returns the full blob contents, or None when the blob doesn't
        # exist. We use bytes (not a stream) because every caller fully
        # reads the data anyway — either to feed Azure DI, write a response,
        # or deserialise JSON. Streaming buys nothing at this size.
        blob = self._container.get_blob_client(blob_name)
        try:
            stream = await blob.download_blob()
            return await stream.readall()
        except ResourceNotFoundError:
            return None

    async def close(self) -> None:
        await self._service_client.close()
