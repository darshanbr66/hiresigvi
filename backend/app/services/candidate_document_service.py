"""The candidate's optional 10th marks card from the self-service form.

Candidate-level profile data (it belongs to the person, not to one
application — CLAUDE.md § 2: "Candidate != Application"), so it is stored
on no Application and is not part of the resume step. The bytes go through
the same `ResumeStorage` abstraction as resumes — MongoDB GridFS in
production — never into Postgres; the candidate row keeps only the
reference (`Candidate.tenth_marks_card_*`). The stored path is never
returned to a client.

Orphan handling — the storage write happens before the database commit, so
the caller (app/services/public_application_service.py) must:

* call `discard` for the new file if anything fails before the commit
  (a failed registration leaves nothing behind in storage), and
* call `discard` for the *replaced* file only after the commit succeeded
  (a returning candidate's new card replaces their old one).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.integrations.storage import ResumeStorage, StorageError, get_resume_storage_for_provider
from app.models.candidate import Candidate

logger = get_logger(__name__)

DOCUMENT_KIND = "tenth_marks_card"

#: Accepted marks-card formats: the extension must be one of these *and*
#: the bytes must carry the matching signature — a renamed executable or
#: any other file type is rejected, never stored. The stored content type
#: comes from this table, not from the client.
_FORMATS: dict[str, tuple[tuple[bytes, ...], str]] = {
    ".pdf": ((b"%PDF-",), "application/pdf"),
    ".png": ((b"\x89PNG\r\n\x1a\n",), "image/png"),
    ".jpg": ((b"\xff\xd8\xff",), "image/jpeg"),
    ".jpeg": ((b"\xff\xd8\xff",), "image/jpeg"),
}

ALLOWED_EXTENSIONS = tuple(_FORMATS)


@dataclass(frozen=True)
class MarksCardUpload:
    """A validated marks card, ready to store."""

    filename: str
    content_type: str
    content: bytes


@dataclass(frozen=True)
class StoredFileRef:
    storage_provider: str
    storage_path: str


def validate_marks_card(*, filename: str, content: bytes) -> MarksCardUpload:
    settings = get_settings()
    suffix = Path(filename).suffix.lower()
    fmt = _FORMATS.get(suffix)
    if fmt is None:
        raise AppError(
            "The 10th marks card must be a PDF, PNG or JPG file.", code="invalid_file_type"
        )
    if len(content) == 0:
        raise AppError("The uploaded 10th marks card is empty.", code="invalid_file")
    max_bytes = settings.max_marks_card_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise AppError(
            f"The 10th marks card must be smaller than {settings.max_marks_card_size_mb}MB.",
            code="file_too_large",
        )
    signatures, content_type = fmt
    head = content.lstrip()[:16] if suffix == ".pdf" else content[:16]
    if not any(head.startswith(signature) for signature in signatures):
        raise AppError(
            f"The uploaded 10th marks card is not a valid {suffix.lstrip('.').upper()} file.",
            code="invalid_file_type",
        )
    return MarksCardUpload(filename=filename, content_type=content_type, content=content)


async def store_marks_card(
    storage: ResumeStorage,
    *,
    candidate: Candidate,
    upload: MarksCardUpload,
) -> tuple[StoredFileRef, StoredFileRef | None]:
    """Writes the file and points the candidate at it (flushed with the
    caller's transaction, not committed). Returns (the new file, the file
    it replaced or None) — see the module docstring for what the caller
    must do with each."""
    previous = (
        StoredFileRef(
            candidate.tenth_marks_card_storage_provider,
            candidate.tenth_marks_card_storage_path,
        )
        if candidate.tenth_marks_card_storage_path and candidate.tenth_marks_card_storage_provider
        else None
    )
    try:
        saved = await storage.save(
            organization_id=candidate.organization_id,
            candidate_id=candidate.id,
            application_id=None,
            original_filename=upload.filename,
            content_type=upload.content_type,
            content=upload.content,
            document_kind=DOCUMENT_KIND,
        )
    except StorageError as exc:
        raise AppError(str(exc), code="storage_error") from exc

    candidate.tenth_marks_card_storage_provider = saved.storage_provider
    candidate.tenth_marks_card_storage_path = saved.storage_path
    candidate.tenth_marks_card_filename = upload.filename[:500]
    candidate.tenth_marks_card_content_type = upload.content_type
    candidate.tenth_marks_card_size_bytes = saved.size_bytes
    candidate.tenth_marks_card_uploaded_at = datetime.now(UTC)
    return StoredFileRef(saved.storage_provider, saved.storage_path), previous


async def discard(ref: StoredFileRef) -> None:
    """Best-effort delete of a stored marks card that no row references
    (any longer). Never raises: it runs on error paths and after a
    successful commit, where a storage hiccup must neither mask the
    original error nor fail a completed registration — it is logged so the
    file can be cleaned up."""
    try:
        await get_resume_storage_for_provider(ref.storage_provider).delete(ref.storage_path)
    except Exception:
        logger.exception(
            "Could not delete an unreferenced 10th marks card from storage",
            extra={
                "extra_fields": {
                    "storage_provider": ref.storage_provider,
                    "storage_path": ref.storage_path,
                }
            },
        )
