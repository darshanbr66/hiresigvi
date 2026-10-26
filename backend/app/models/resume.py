import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Resume(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A resume file submitted with one Application. `candidate_id` is
    denormalized (also reachable via `application.candidate_id`) so a
    candidate's resume history can be queried directly, matching the
    denormalization pattern `ApplicationResponse.candidate_full_name`
    already uses for the same reason.

    File bytes live in whichever backend `storage_provider` names — local
    disk under `settings.resume_storage_dir`, or MongoDB GridFS (see
    app/integrations/storage) — this row is metadata only (CLAUDE.md § 2:
    "Resume storage != DB blob"). `storage_path` is opaque and provider-
    specific (a relative disk path for "local", a GridFS ObjectId for
    "mongodb_gridfs") and is never returned to clients; downloads go
    through an authenticated streaming endpoint that resolves it
    server-side using the matching `ResumeStorage` implementation.
    """

    __tablename__ = "resumes"

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="RESTRICT"),
        nullable=False,
        # One resume per application — migration a5afbdf6f1b5 created this
        # index as unique.
        unique=True,
        index=True,
    )
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    # "local" | "mongodb_gridfs" (app/integrations/storage) — which
    # ResumeStorage implementation `storage_path` resolves against. Existing
    # rows written before this column existed are backfilled to "local" by
    # the migration that added it, so they keep resolving correctly after
    # production's default flips to "mongodb_gridfs" (CLAUDE.md task:
    # backward compatibility).
    storage_provider: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="local"
    )
    content_type: Mapped[str] = mapped_column(String(150), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
