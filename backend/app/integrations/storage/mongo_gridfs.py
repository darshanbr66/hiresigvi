import hashlib
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from bson import ObjectId
from bson.errors import InvalidId
from gridfs import AsyncGridFSBucket
from gridfs.asynchronous.grid_file import AsyncGridOut
from gridfs.errors import NoFile
from pymongo.errors import PyMongoError

from app.db.mongo import get_mongo_database
from app.integrations.storage.base import (
    ResumeFileMetadata,
    ResumeStorage,
    SavedFile,
    StorageError,
)

#: GridFS bucket name — creates `resumes.files` / `resumes.chunks`
#: collections in the configured database (§ MONGODB_DATABASE).
BUCKET_NAME = "resumes"

#: Bumped only if the metadata document shape below changes in a
#: non-backward-compatible way.
STORAGE_VERSION = 1


class MongoGridFSResumeStorage(ResumeStorage):
    """Production resume-file storage: bytes live in MongoDB GridFS, never
    in Postgres (CLAUDE.md § 2). `storage_path` (the opaque identifier the
    caller persists onto `Resume.storage_path`) is the GridFS file's
    stringified ObjectId.

    Authorization is never decided from anything stored here — every
    caller reaches this class only after Postgres-side tenant/application
    checks have already passed (see app/api/v1/recruiter/applications.py).
    """

    provider_name = "mongodb_gridfs"

    def __init__(self) -> None:
        self._bucket = AsyncGridFSBucket(get_mongo_database(), bucket_name=BUCKET_NAME)

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
        metadata = {
            "candidate_id": str(candidate_id),
            "application_id": str(application_id) if application_id else None,
            "document_kind": document_kind,
            "organization_id": str(organization_id),
            "original_filename": original_filename,
            "content_type": content_type,
            "size_bytes": len(content),
            "checksum_sha256": hashlib.sha256(content).hexdigest(),
            "uploaded_by_user_id": str(uploaded_by_user_id) if uploaded_by_user_id else None,
            "storage_version": STORAGE_VERSION,
        }

        try:
            file_id = await self._bucket.upload_from_stream(
                stored_filename, content, metadata=metadata
            )
        except PyMongoError as exc:
            raise StorageError("Could not save the uploaded file.") from exc

        return SavedFile(
            stored_filename=stored_filename,
            storage_path=str(file_id),
            size_bytes=len(content),
            storage_provider=self.provider_name,
        )

    async def read(self, storage_path: str) -> bytes:
        grid_out = await self._open(storage_path)
        try:
            return await grid_out.read()
        finally:
            await grid_out.close()

    async def open_stream(self, storage_path: str) -> AsyncIterator[bytes]:
        grid_out = await self._open(storage_path)

        async def _chunks() -> AsyncIterator[bytes]:
            try:
                async for chunk in grid_out:
                    yield chunk
            finally:
                await grid_out.close()

        return _chunks()

    async def delete(self, storage_path: str) -> None:
        try:
            await self._bucket.delete(self._to_object_id(storage_path))
        except NoFile:
            # Idempotent: a Postgres cleanup pass that races a prior/partial
            # delete must not crash (CLAUDE.md task § 8: "handle missing
            # GridFS files gracefully").
            pass
        except PyMongoError as exc:
            raise StorageError("Could not delete the stored file.") from exc

    async def exists(self, storage_path: str) -> bool:
        try:
            grid_out = await self._open(storage_path)
        except StorageError:
            return False
        await grid_out.close()
        return True

    async def get_metadata(self, storage_path: str) -> ResumeFileMetadata:
        grid_out = await self._open(storage_path)
        try:
            meta = grid_out.metadata or {}
            return ResumeFileMetadata(
                storage_path=storage_path,
                filename=grid_out.filename,
                content_type=meta.get("content_type"),
                size_bytes=grid_out.length,
                checksum_sha256=meta.get("checksum_sha256"),
            )
        finally:
            await grid_out.close()

    async def _open(self, storage_path: str) -> AsyncGridOut:
        try:
            return await self._bucket.open_download_stream(self._to_object_id(storage_path))
        except NoFile as exc:
            raise StorageError("Could not read the stored file.") from exc
        except PyMongoError as exc:
            raise StorageError("Could not read the stored file.") from exc

    @staticmethod
    def _to_object_id(storage_path: str) -> ObjectId:
        try:
            return ObjectId(storage_path)
        except (InvalidId, TypeError) as exc:
            raise StorageError("Invalid storage path.") from exc
