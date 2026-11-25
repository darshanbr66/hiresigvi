"""The education documents a recruiter can open from an application: the
academic document uploaded in the Education step (per application,
`academic_documents`) and the optional 10th marks card (on the shared
Candidate profile, `candidates.tenth_marks_card_*`).

Both live in the same storage abstraction as resumes but in their own rows,
and every read resolves the storage backend from the row's own
`storage_provider` (never the current default), exactly like a resume
download. Clients only ever get metadata and a streamed copy through an
authorized endpoint — never a storage path or a public URL. Tenant scoping
is the caller's application lookup (and RLS).
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.integrations.storage import StorageError, get_resume_storage_for_provider
from app.models.application import Application
from app.models.application_draft import AcademicDocument
from app.services.education import catalog

logger = get_logger(__name__)


class EducationDocumentKind(StrEnum):
    ACADEMIC_DOCUMENT = "academic-document"
    TENTH_MARKS_CARD = "tenth-marks-card"


@dataclass(frozen=True)
class _StoredDocument:
    storage_provider: str
    storage_path: str
    filename: str
    content_type: str
    size_bytes: int | None
    uploaded_at: datetime | None


@dataclass(frozen=True)
class EducationDocument:
    """One row of the recruiter's Education documents list. `uploaded`:
    the application has a record of this document. `available`: its file
    can actually be read from storage right now."""

    kind: EducationDocumentKind
    label: str
    description: str
    required: bool
    uploaded: bool
    available: bool
    filename: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    uploaded_at: datetime | None = None


@dataclass(frozen=True)
class OpenedDocument:
    chunks: AsyncIterator[bytes]
    filename: str
    content_type: str
    size_bytes: int | None


async def _academic_document(
    db: AsyncSession, application: Application
) -> tuple[_StoredDocument | None, str | None]:
    document = await db.scalar(
        select(AcademicDocument).where(AcademicDocument.application_id == application.id)
    )
    if document is None:
        return None, None
    option = catalog.get_option(document.qualification_key)
    stored = _StoredDocument(
        storage_provider=document.storage_provider,
        storage_path=document.storage_path,
        filename=document.original_filename,
        content_type=document.content_type,
        size_bytes=document.size_bytes,
        uploaded_at=document.created_at,
    )
    return stored, option.label if option else document.qualification_key


def _tenth_marks_card(application: Application) -> _StoredDocument | None:
    candidate = application.candidate
    if not (
        candidate.tenth_marks_card_storage_provider and candidate.tenth_marks_card_storage_path
    ):
        return None
    return _StoredDocument(
        storage_provider=candidate.tenth_marks_card_storage_provider,
        storage_path=candidate.tenth_marks_card_storage_path,
        filename=candidate.tenth_marks_card_filename or "10th-marks-card",
        content_type=candidate.tenth_marks_card_content_type or "application/octet-stream",
        size_bytes=candidate.tenth_marks_card_size_bytes,
        uploaded_at=candidate.tenth_marks_card_uploaded_at,
    )


async def _is_available(stored: _StoredDocument) -> bool:
    try:
        return await get_resume_storage_for_provider(stored.storage_provider).exists(
            stored.storage_path
        )
    except Exception:
        # Unknown provider, storage unreachable: show it as unavailable
        # rather than failing the whole application page.
        logger.warning(
            "Could not check an education document in storage",
            extra={"extra_fields": {"storage_provider": stored.storage_provider}},
            exc_info=True,
        )
        return False


async def list_documents(db: AsyncSession, application: Application) -> list[EducationDocument]:
    """The academic document (always listed for an application that went
    through the Education step, uploaded or not) and the 10th marks card
    (always listed; optional)."""
    academic, qualification = await _academic_document(db, application)
    marks_card = _tenth_marks_card(application)
    documents: list[EducationDocument] = []
    if academic is not None or application.education_verification_status is not None:
        documents.append(
            _describe(
                EducationDocumentKind.ACADEMIC_DOCUMENT,
                "Academic document",
                (
                    f"Marks card or certificate for {qualification}"
                    if qualification
                    else "Marks card or certificate for the highest qualification"
                ),
                required=True,
                stored=academic,
                available=await _is_available(academic) if academic else False,
            )
        )
    documents.append(
        _describe(
            EducationDocumentKind.TENTH_MARKS_CARD,
            "10th marks card",
            "Optional",
            required=False,
            stored=marks_card,
            available=await _is_available(marks_card) if marks_card else False,
        )
    )
    return documents


def _describe(
    kind: EducationDocumentKind,
    label: str,
    description: str,
    *,
    required: bool,
    stored: _StoredDocument | None,
    available: bool,
) -> EducationDocument:
    if stored is None:
        return EducationDocument(
            kind=kind,
            label=label,
            description=description,
            required=required,
            uploaded=False,
            available=False,
        )
    return EducationDocument(
        kind=kind,
        label=label,
        description=description,
        required=required,
        uploaded=True,
        available=available,
        filename=stored.filename,
        content_type=stored.content_type,
        size_bytes=stored.size_bytes,
        uploaded_at=stored.uploaded_at,
    )


async def open_document(
    db: AsyncSession, application: Application, kind: EducationDocumentKind
) -> OpenedDocument:
    """A streamed copy of one document. NotFoundError when the application
    has none, or its file is missing from storage."""
    if kind == EducationDocumentKind.ACADEMIC_DOCUMENT:
        stored, _ = await _academic_document(db, application)
        missing = "No academic document found for this application."
    else:
        stored = _tenth_marks_card(application)
        missing = "No 10th marks card was uploaded for this candidate."
    if stored is None:
        raise NotFoundError(missing)
    try:
        storage = get_resume_storage_for_provider(stored.storage_provider)
        chunks = await storage.open_stream(stored.storage_path)
    except StorageError as exc:
        raise NotFoundError("The document file could not be found in storage.") from exc
    return OpenedDocument(
        chunks=chunks,
        filename=stored.filename,
        content_type=stored.content_type,
        size_bytes=stored.size_bytes,
    )


__all__ = [
    "EducationDocument",
    "EducationDocumentKind",
    "OpenedDocument",
    "list_documents",
    "open_document",
]
