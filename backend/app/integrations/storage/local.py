import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import anyio

from app.core.config import get_settings
from app.integrations.storage.base import (
    ResumeFileMetadata,
    ResumeStorage,
    SavedFile,
    StorageError,
)

_STREAM_CHUNK_BYTES = 256 * 1024


class LocalResumeStorage(ResumeStorage):
    """Local-disk implementation for development (CLAUDE.md § 3: "local disk
    now, S3-compatible later"). Files live under
    `settings.resume_storage_dir/<organization_id>/<uuid><ext>` — the stored
    filename is always server-generated, never derived from the client's
    original filename, so there is no path-traversal surface and no
    filename-collision risk.
    """

    provider_name = "local"

    def __init__(self, *, root: Path | None = None) -> None:
        settings = get_settings()
        self._root = root or Path(settings.resume_storage_dir)

    async def save(
        self,
        *,
        organization_id: uuid.UUID,
        candidate_id: uuid.UUID,
        application_id: uuid.UUID | None,
        original_filename: str,
        content_type: str,
        content: bytes,
        uploaded_by_user_id: uuid.UUID | None = None,
        document_kind: str = "resume",
    ) -> SavedFile:
        suffix = Path(original_filename).suffix.lower()
        stored_filename = f"{uuid.uuid4()}{suffix}"
        org_dir = self._root / str(organization_id)

        try:
            await anyio.Path(org_dir).mkdir(parents=True, exist_ok=True)
            target = org_dir / stored_filename
            await anyio.Path(target).write_bytes(content)
        except OSError as exc:
            raise StorageError("Could not save the uploaded file.") from exc

        storage_path = f"{organization_id}/{stored_filename}"
        return SavedFile(
            stored_filename=stored_filename,
            storage_path=storage_path,
            size_bytes=len(content),
            storage_provider=self.provider_name,
        )

    async def read(self, storage_path: str) -> bytes:
        target = self._resolve(storage_path)
        try:
            return await anyio.Path(target).read_bytes()
        except OSError as exc:
            raise StorageError("Could not read the stored file.") from exc

    async def open_stream(self, storage_path: str) -> AsyncIterator[bytes]:
        target = self._resolve(storage_path)
        try:
            file = await anyio.open_file(target, "rb")
        except OSError as exc:
            raise StorageError("Could not read the stored file.") from exc

        async def _chunks() -> AsyncIterator[bytes]:
            async with file:
                while True:
                    chunk = await file.read(_STREAM_CHUNK_BYTES)
                    if not chunk:
                        break
                    yield chunk

        return _chunks()

    async def delete(self, storage_path: str) -> None:
        target = self._resolve(storage_path)
        try:
            await anyio.Path(target).unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError("Could not delete the stored file.") from exc

    async def exists(self, storage_path: str) -> bool:
        target = self._resolve(storage_path)
        return await anyio.Path(target).exists()

    async def get_metadata(self, storage_path: str) -> ResumeFileMetadata:
        target = self._resolve(storage_path)
        try:
            stat = await anyio.Path(target).stat()
        except OSError as exc:
            raise StorageError("Could not read stored file metadata.") from exc
        return ResumeFileMetadata(
            storage_path=storage_path,
            filename=target.name,
            content_type=None,
            size_bytes=stat.st_size,
            checksum_sha256=None,
        )

    def _resolve(self, storage_path: str) -> Path:
        """Resolves a stored `organization_id/uuid.ext` path against the
        storage root and refuses to serve anything that would escape it —
        defense in depth even though every `storage_path` we ever write is
        server-generated, never client-controlled."""
        candidate = (self._root / storage_path).resolve()
        root = self._root.resolve()
        if root not in candidate.parents:
            raise StorageError("Invalid storage path.")
        return candidate
