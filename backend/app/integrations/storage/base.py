import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


class StorageError(Exception):
    """Raised for validation failures (bad extension, oversized file) and
    storage-layer I/O failures alike — callers treat both as "could not
    store this upload" (see app/api/v1/public/applications.py)."""


@dataclass(frozen=True)
class SavedFile:
    stored_filename: str
    storage_path: str
    size_bytes: int
    storage_provider: str


@dataclass(frozen=True)
class ResumeFileMetadata:
    """Metadata lookup result — never authorization data. Tenant/candidate/
    application authorization for a resume is always decided from Postgres
    (CLAUDE.md § 2: "Tenant isolation != frontend filtering" — the same
    principle applies to any out-of-Postgres store), never from this."""

    storage_path: str
    filename: str
    content_type: str | None
    size_bytes: int
    checksum_sha256: str | None


class ResumeStorage(ABC):
    """One file per save; the caller owns the metadata row
    (app/models/resume.py for a resume; the `tenth_marks_card_*` columns of
    app/models/candidate.py for a 10th marks card). `document_kind` says
    which ("resume" | "tenth_marks_card"); a candidate-level document has no
    `application_id`. `storage_path` is opaque to callers — only the
    implementation that produced it knows how to resolve or read it back
    (a local relative path for `LocalResumeStorage`, a GridFS file id for
    `MongoGridFSResumeStorage` — see app/integrations/storage/__init__.py).
    """

    #: Value persisted onto `Resume.storage_provider` for every file this
    #: implementation saves — lets old rows written by a since-swapped
    #: implementation keep resolving correctly (CLAUDE.md § "backward
    #: compatibility": local-storage records must not break once production
    #: defaults to mongodb_gridfs).
    provider_name: str

    @abstractmethod
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
    ) -> SavedFile: ...

    @abstractmethod
    async def read(self, storage_path: str) -> bytes:
        """Whole-file read for server-side consumers that need the full
        content in memory regardless (resume text extraction for AI
        screening — app/services/screening_service.py). Prefer
        `open_stream` for anything proxying bytes to an HTTP client."""

    @abstractmethod
    async def open_stream(self, storage_path: str) -> AsyncIterator[bytes]:
        """Chunked read for streaming a download response without loading
        the whole file into memory. Implementations open/validate the file
        eagerly (raising `StorageError` immediately if it's missing) and
        only return the chunk iterator once that's succeeded — so a caller
        building an HTTP response can still turn a missing file into a 404
        before it has written any response headers, instead of the failure
        surfacing mid-stream."""

    @abstractmethod
    async def delete(self, storage_path: str) -> None:
        """Idempotent: deleting an already-missing file is not an error
        (so a metadata-cleanup pass never crashes on drift)."""

    @abstractmethod
    async def exists(self, storage_path: str) -> bool: ...

    @abstractmethod
    async def get_metadata(self, storage_path: str) -> ResumeFileMetadata: ...
