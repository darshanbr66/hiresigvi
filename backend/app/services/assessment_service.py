import uuid
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import generate_opaque_token, hash_opaque_token
from app.integrations.documents.question_import import parse_questions as _parse_questions
from app.models.application import ApplicationStatus
from app.models.assessment import (
    CHOICE_QUESTION_TYPES,
    TEXT_QUESTION_TYPES,
    Assessment,
    AssessmentInvitation,
    AssessmentMonitoringEvent,
    AssessmentStatus,
    InvitationStatus,
    Question,
    QuestionAnswerKey,
    QuestionOption,
)
from app.models.user import User
from app.schemas.assessment import (
    AssessmentCreateRequest,
    AssessmentUpdateRequest,
    ParsedQuestionsResponse,
    QuestionCreate,
    RetestAssessmentChoice,
)
from app.schemas.assessment import QuestionOptionCreate as QuestionOptionCreateSchema
from app.services import activity_service, application_service

_INVITATION_EXPIRY_DAYS = 7


def parse_import_file(*, content: bytes, filename: str) -> ParsedQuestionsResponse:
    """Extracts candidate questions from an uploaded PDF/DOCX/XLSX/CSV for
    the "Import Questions" preview step — never persists anything (see
    app/integrations/documents/question_import.py for the parsing rules).
    """
    result = _parse_questions(content=content, filename=filename)
    questions: list[QuestionCreate] = []
    warnings = list(result.warnings)
    for number, q in enumerate(result.questions, start=1):
        try:
            questions.append(
                QuestionCreate(
                    prompt=q.prompt,
                    type=q.type,
                    points=q.points,
                    options=[
                        QuestionOptionCreateSchema(label=o.label, is_correct=o.is_correct)
                        for o in q.options
                    ],
                    accepted_answers=q.accepted_answers,
                    explanation=q.explanation,
                )
            )
        except ValidationError as exc:
            # Never include a question the shared rules reject (e.g. a
            # single-choice question marked with two correct answers).
            reason = exc.errors()[0]["msg"].removeprefix("Value error, ")
            warnings.append(f"{q.source or f'Question {number}'} was skipped: {reason}")
    return ParsedQuestionsResponse(questions=questions, warnings=warnings)


async def _add_questions(
    db: AsyncSession,
    assessment: Assessment,
    questions: list[QuestionCreate],
    *,
    existing_versions: frozenset[uuid.UUID] = frozenset(),
) -> None:
    """Inserts the question set in order. A bank question is copied from the
    exact bank version (content and answer key) — never from what the
    client sent — and must belong to the assessment's own organization.
    `existing_versions` are the bank versions the assessment held before an
    edit; those may be kept even if the bank item has a newer version."""
    # Local import: question_bank_service imports from the assessment models.
    from app.services import question_bank_service

    for order_index, payload in enumerate(questions):
        if payload.bank_version_id is not None:
            version = await question_bank_service.require_snapshot_source(
                db,
                assessment.organization_id,
                payload.bank_version_id,
                already_in_test=payload.bank_version_id in existing_versions,
            )
            key = version.answer_key
            correct = set(key.correct_option_keys)
            question = Question(
                assessment_id=assessment.id,
                prompt=version.prompt,
                type=version.type,
                points=payload.points or version.points,
                negative_points=min(version.negative_points, payload.points or version.points),
                order_index=order_index,
                source_bank_version_id=version.id,
            )
            options = [(o["label"], o["key"] in correct) for o in version.options]
            accepted, case_sensitive = list(key.accepted_answers), key.case_sensitive
            grading_notes, explanation = key.grading_notes, key.explanation
        else:
            question = Question(
                assessment_id=assessment.id,
                prompt=payload.prompt.strip(),
                type=payload.type,
                points=payload.points or 1,
                negative_points=payload.negative_points,
                order_index=order_index,
            )
            options = (
                [(o.label.strip(), o.is_correct) for o in payload.options]
                if payload.type in CHOICE_QUESTION_TYPES
                else []
            )
            accepted, case_sensitive = payload.accepted_answers, payload.case_sensitive
            grading_notes, explanation = payload.grading_notes, payload.explanation
        db.add(question)
        await db.flush()

        for option_order, (label, is_correct) in enumerate(options):
            db.add(
                QuestionOption(
                    question_id=question.id,
                    label=label,
                    is_correct=is_correct,
                    order_index=option_order,
                )
            )
        if question.type in TEXT_QUESTION_TYPES or grading_notes or explanation:
            db.add(
                QuestionAnswerKey(
                    question_id=question.id,
                    accepted_answers=accepted if question.type in TEXT_QUESTION_TYPES else [],
                    case_sensitive=case_sensitive,
                    grading_notes=grading_notes,
                    explanation=explanation,
                )
            )
    await db.flush()


def require_published(assessment: Assessment) -> None:
    """Only a PUBLISHED, non-archived assessment can be sent to anyone."""
    if assessment.deleted_at is not None:
        raise NotFoundError("Assessment not found.")
    if assessment.status != AssessmentStatus.PUBLISHED:
        raise ConflictError(
            f'"{assessment.title}" is still a draft. Publish it before assigning it.',
            code="assessment_not_published",
        )


async def create_assessment(
    db: AsyncSession, *, organization_id: uuid.UUID, created_by: uuid.UUID, payload: AssessmentCreateRequest,
    actor: User | None = None,
) -> Assessment:
    assessment = Assessment(
        organization_id=organization_id,
        title=payload.title,
        instructions=payload.instructions,
        duration_minutes=payload.duration_minutes,
        pass_score=payload.pass_score,
        status=payload.status,
        created_by=created_by,
    )
    db.add(assessment)
    await db.flush()
    await _add_questions(db, assessment, payload.questions)

    await activity_service.record_activity(
        db,
        organization_id=organization_id,
        actor=actor,
        action="ASSESSMENT_CREATED",
        entity_type="assessment",
        entity_id=assessment.id,
        entity_label=assessment.title,
        description=(
            f"Assessment \"{assessment.title}\" was created with {len(payload.questions)} "
            f"question(s) ({assessment.status.value.lower()})."
        ),
    )

    reloaded = await get_assessment(db, assessment.id)
    assert reloaded is not None
    return reloaded


async def list_assessments(db: AsyncSession, organization_id: uuid.UUID) -> list[Assessment]:
    result = await db.execute(
        select(Assessment)
        .where(Assessment.organization_id == organization_id, Assessment.deleted_at.is_(None))
        .options(selectinload(Assessment.questions))
        .order_by(Assessment.created_at.desc())
    )
    return list(result.unique().scalars().all())


async def delete_assessment(db: AsyncSession, assessment: Assessment, *, actor: User, reason: str) -> Assessment:
    """Soft-deletes/archives: keeps the row (and every AssessmentInvitation/
    AssessmentResult that used it) but removes it from `list_assessments`
    and the invite/retest pickers, and records an Activity."""
    if assessment.deleted_at is not None:
        raise ConflictError("This assessment has already been deleted.")

    # Local import: avoids a cross-domain top-level dependency between the
    # assessment and campus-drive service modules.
    from app.models.campus_drive import CampusDrive

    dependent_drive = await db.scalar(
        select(CampusDrive).where(
            CampusDrive.default_assessment_id == assessment.id,
            CampusDrive.deleted_at.is_(None),
        )
    )
    if dependent_drive is not None:
        raise ConflictError(
            f"This assessment is set as the default for campus drive \"{dependent_drive.name}\". "
            "Change that drive's default assessment before deleting this one."
        )

    from app.models.program import ProgramStatus, RecruitmentProgram

    dependent_program = await db.scalar(
        select(RecruitmentProgram).where(
            RecruitmentProgram.assessment_id == assessment.id,
            RecruitmentProgram.status != ProgramStatus.ARCHIVED,
        )
    )
    if dependent_program is not None:
        raise ConflictError(
            f"This assessment is the selection test of the program \"{dependent_program.title}\". "
            "Change that program's assessment before deleting this one."
        )

    assessment.deleted_at = datetime.now(UTC)
    assessment.deleted_by_user_id = actor.id
    assessment.deletion_reason = reason
    await db.flush()

    await activity_service.record_activity(
        db,
        organization_id=assessment.organization_id,
        actor=actor,
        action="ASSESSMENT_DELETED",
        entity_type="assessment",
        entity_id=assessment.id,
        entity_label=assessment.title,
        description=f"Assessment \"{assessment.title}\" was deleted.",
        reason=reason,
    )
    return assessment


async def assessment_has_invitations(db: AsyncSession, assessment_id: uuid.UUID) -> bool:
    """True once any candidate could have been invited to this assessment —
    the point past which question-structure edits are locked (see
    `update_assessment`), regardless of whether that invitation was ever
    started or submitted."""
    count = await db.scalar(
        select(func.count(AssessmentInvitation.id)).where(
            AssessmentInvitation.assessment_id == assessment_id
        )
    )
    return bool(count)


async def update_assessment(
    db: AsyncSession, assessment: Assessment, payload: AssessmentUpdateRequest, *, actor: User
) -> Assessment:
    """Title/instructions/duration can always change. The pass mark and the
    question set (replaced wholesale) can only change while no invitation exists
    yet for this assessment (CLAUDE.md § 23: "Completed assessment attempts
    must not be modified accidentally when editing an assessment") — once
    one does, a 409 tells the caller to create a new assessment instead."""
    updates = payload.model_dump(exclude_unset=True, exclude={"questions"})
    replacing_questions = payload.questions is not None

    sent = (
        replacing_questions or updates.get("pass_score", assessment.pass_score) != assessment.pass_score
    ) and await assessment_has_invitations(db, assessment.id)
    if sent and replacing_questions:
        raise ConflictError(
            "This assessment's questions can't be changed once it has been sent to a "
            "candidate. Create a new assessment instead."
        )
    if sent:
        # Candidates were (or are being) tested against the current pass
        # mark; changing it would silently change who passes.
        raise ConflictError(
            "This assessment's pass mark can't be changed once it has been sent to a "
            "candidate. Create a new assessment instead.",
            code="pass_score_locked",
        )

    changed_fields = list(updates) + (["questions"] if replacing_questions else [])
    if not changed_fields:
        return assessment

    for field, value in updates.items():
        setattr(assessment, field, value)

    if replacing_questions:
        assert payload.questions is not None
        existing_questions = list(
            (
                await db.execute(select(Question).where(Question.assessment_id == assessment.id))
            ).scalars().all()
        )
        existing_versions = frozenset(
            q.source_bank_version_id for q in existing_questions if q.source_bank_version_id
        )
        for question in existing_questions:
            await db.delete(question)
        await db.flush()
        await _add_questions(
            db, assessment, payload.questions, existing_versions=existing_versions
        )

        # `assessment.questions` (viewonly, selectin-loaded) was already
        # populated by the caller's earlier `get_assessment` — an eager
        # loader never refreshes an attribute that's already present on an
        # identity-mapped object, so without this the reload below would
        # silently keep serving the just-deleted question set.
        db.expire(assessment, ["questions"])

    await db.flush()

    await activity_service.record_activity(
        db,
        organization_id=assessment.organization_id,
        actor=actor,
        action="ASSESSMENT_UPDATED",
        entity_type="assessment",
        entity_id=assessment.id,
        entity_label=assessment.title,
        description=f"Assessment \"{assessment.title}\" was updated ({', '.join(sorted(changed_fields))}).",
    )

    reloaded = await get_assessment(db, assessment.id)
    assert reloaded is not None
    return reloaded


async def get_assessment(db: AsyncSession, assessment_id: uuid.UUID) -> Assessment | None:
    result = await db.execute(
        select(Assessment)
        .where(Assessment.id == assessment_id)
        .options(
            selectinload(Assessment.questions).selectinload(Question.options),
            selectinload(Assessment.questions).selectinload(Question.answer_key),
        )
    )
    return result.unique().scalar_one_or_none()


async def change_status(
    db: AsyncSession, assessment: Assessment, status: AssessmentStatus, *, actor: User
) -> Assessment:
    """DRAFT -> PUBLISHED makes it assignable. PUBLISHED -> DRAFT is allowed
    only while nobody has been invited — afterwards it would strand the
    invitations that already point at it."""
    if assessment.deleted_at is not None:
        raise NotFoundError("Assessment not found.")
    if status == assessment.status:
        return assessment
    if status == AssessmentStatus.PUBLISHED and not assessment.questions:
        raise ConflictError("Add at least one question before publishing.")
    if status == AssessmentStatus.DRAFT and await assessment_has_invitations(db, assessment.id):
        raise ConflictError(
            "This assessment has already been sent to candidates, so it can't go back to draft.",
            code="assessment_in_use",
        )
    assessment.status = status
    await db.flush()
    await activity_service.record_activity(
        db,
        organization_id=assessment.organization_id,
        actor=actor,
        action="ASSESSMENT_PUBLISHED" if status == AssessmentStatus.PUBLISHED else "ASSESSMENT_UNPUBLISHED",
        entity_type="assessment",
        entity_id=assessment.id,
        entity_label=assessment.title,
        description=f'Assessment "{assessment.title}" is now {status.value.lower()}.',
    )
    reloaded = await get_assessment(db, assessment.id)
    assert reloaded is not None
    return reloaded


async def invite_candidate(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    assessment_id: uuid.UUID,
    application_id: uuid.UUID,
    invited_by_user_id: uuid.UUID,
) -> tuple[AssessmentInvitation, str]:
    assessment = await get_assessment(db, assessment_id)
    if assessment is None or assessment.deleted_at is not None:
        raise NotFoundError("Assessment not found.")
    require_published(assessment)

    application = await application_service.get_application(db, application_id)
    if application is None:
        raise NotFoundError("Application not found.")

    existing = await db.scalar(
        select(AssessmentInvitation)
        .where(AssessmentInvitation.application_id == application_id)
        .order_by(AssessmentInvitation.attempt_number.desc())
        .limit(1)
    )

    raw_token = generate_opaque_token()
    expires_at = datetime.now(UTC) + timedelta(days=_INVITATION_EXPIRY_DAYS)

    if existing is not None:
        # Resend: reuse the row, regenerate the token (docs/campus-hiring.md § 3).
        # Only legal pre-submission — a submitted attempt goes through
        # `create_retest` instead, which adds a new row rather than
        # mutating this one (the application_workflow gate on this
        # function's own SCREENING-only precondition already prevents
        # invite_candidate from running again post-submission).
        existing.assessment_id = assessment_id
        existing.token_hash = hash_opaque_token(raw_token)
        existing.status = InvitationStatus.SENT
        existing.expires_at = expires_at
        existing.started_at = None
        existing.submitted_at = None
        existing.invited_by_user_id = invited_by_user_id
        invitation = existing
    else:
        invitation = AssessmentInvitation(
            organization_id=organization_id,
            assessment_id=assessment_id,
            application_id=application_id,
            invited_by_user_id=invited_by_user_id,
            token_hash=hash_opaque_token(raw_token),
            status=InvitationStatus.SENT,
            expires_at=expires_at,
            attempt_number=1,
        )
        db.add(invitation)

    try:
        await db.flush()
    except IntegrityError as exc:
        raise ConflictError("This application already has an assessment invitation.") from exc

    await application_service.change_status(
        db, application, to_status=ApplicationStatus.ASSESSMENT_INVITED, actor_user_id=invited_by_user_id
    )

    # Assigning an assessment only *prepares* the invitation. Emailing it is a
    # separate, explicit recruiter action ("Send Assessment Invitation" ->
    # app/services/email_composer.py), so nothing is sent from here.
    invited_by = await db.get(User, invited_by_user_id)
    await activity_service.record_activity(
        db,
        organization_id=organization_id,
        actor=invited_by,
        action="ASSESSMENT_INVITATION_CREATED",
        entity_type="application",
        entity_id=application_id,
        entity_label=f"{application.candidate.full_name} — {assessment.title}",
        description=f"{application.candidate.full_name} was invited to take \"{assessment.title}\".",
    )

    reloaded = await get_invitation_for_application(db, application_id)
    assert reloaded is not None
    return reloaded, raw_token


async def get_invitation_for_application(
    db: AsyncSession, application_id: uuid.UUID
) -> AssessmentInvitation | None:
    """The most recent attempt (highest `attempt_number`) — the one
    candidates currently interact with and the one shown by default. Use
    `list_attempts_for_application` for full retest history."""
    result = await db.execute(
        select(AssessmentInvitation)
        .where(AssessmentInvitation.application_id == application_id)
        .options(joinedload(AssessmentInvitation.assessment), joinedload(AssessmentInvitation.result))
        .order_by(AssessmentInvitation.attempt_number.desc())
        .limit(1)
    )
    return result.unique().scalar_one_or_none()


async def list_monitoring_events(
    db: AsyncSession, invitation_id: uuid.UUID
) -> list[AssessmentMonitoringEvent]:
    """Recruiter/admin read of one attempt's observed monitoring events, in
    chronological order — permission-gated by `assessment.read` at the
    route layer (SIGVITAS platform overhaul § 7)."""
    result = await db.execute(
        select(AssessmentMonitoringEvent)
        .where(AssessmentMonitoringEvent.invitation_id == invitation_id)
        .order_by(AssessmentMonitoringEvent.occurred_at.asc())
    )
    return list(result.scalars().all())


async def list_attempts_for_application(
    db: AsyncSession, application_id: uuid.UUID
) -> list[AssessmentInvitation]:
    result = await db.execute(
        select(AssessmentInvitation)
        .where(AssessmentInvitation.application_id == application_id)
        .options(joinedload(AssessmentInvitation.assessment), joinedload(AssessmentInvitation.result))
        .order_by(AssessmentInvitation.attempt_number.asc())
    )
    return list(result.unique().scalars().all())


async def create_retest(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    application_id: uuid.UUID,
    authorized_by: User,
    reason: str,
    assessment_choice: RetestAssessmentChoice = RetestAssessmentChoice.SAME,
    assessment_id: uuid.UUID | None = None,
    new_assessment: AssessmentCreateRequest | None = None,
) -> tuple[AssessmentInvitation, str]:
    """Gives a candidate another attempt — at the *same* assessment
    (default), an *existing* one, or a brand-new one created inline (QA § 6)
    — the previous attempt's row (and its linked AssessmentResult/
    CandidateAnswers) is never touched, only a new row is added (CLAUDE.md
    § 3: never silently erase assessment history)."""
    latest = await get_invitation_for_application(db, application_id)
    if latest is None:
        raise NotFoundError("This application has no assessment attempt to retest.")
    if latest.status != InvitationStatus.SUBMITTED:
        raise ConflictError(
            "A retest can only be given after the current attempt has been submitted."
        )

    application = await application_service.get_application(db, application_id)
    if application is None:
        raise NotFoundError("Application not found.")

    if assessment_choice == RetestAssessmentChoice.EXISTING:
        assert assessment_id is not None
        chosen_assessment = await get_assessment(db, assessment_id)
        if chosen_assessment is None or chosen_assessment.deleted_at is not None:
            raise NotFoundError("Assessment not found.")
        require_published(chosen_assessment)
    elif assessment_choice == RetestAssessmentChoice.NEW:
        assert new_assessment is not None
        chosen_assessment = await create_assessment(
            db,
            organization_id=organization_id,
            created_by=authorized_by.id,
            payload=new_assessment,
            actor=authorized_by,
        )
    else:
        chosen_assessment = latest.assessment
    if assessment_choice == RetestAssessmentChoice.NEW:
        require_published(chosen_assessment)

    raw_token = generate_opaque_token()
    invitation = AssessmentInvitation(
        organization_id=organization_id,
        assessment_id=chosen_assessment.id,
        application_id=application_id,
        invited_by_user_id=authorized_by.id,
        token_hash=hash_opaque_token(raw_token),
        status=InvitationStatus.SENT,
        expires_at=datetime.now(UTC) + timedelta(days=_INVITATION_EXPIRY_DAYS),
        attempt_number=latest.attempt_number + 1,
        retest_reason=reason,
    )
    db.add(invitation)
    await db.flush()

    await application_service.change_status(
        db, application, to_status=ApplicationStatus.ASSESSMENT_INVITED, actor_user_id=authorized_by.id
    )

    choice_label = (
        "the same" if assessment_choice == RetestAssessmentChoice.SAME
        else assessment_choice.value.lower()
    )
    await activity_service.record_activity(
        db,
        organization_id=organization_id,
        actor=authorized_by,
        action="ASSESSMENT_RETEST_CREATED",
        entity_type="application",
        entity_id=application_id,
        entity_label=(
            f"{application.candidate.full_name} — {chosen_assessment.title} "
            f"(attempt {invitation.attempt_number})"
        ),
        description=(
            f"Retest #{invitation.attempt_number - 1} authorized for "
            f"{application.candidate.full_name} using {choice_label} assessment "
            f"(\"{chosen_assessment.title}\")."
        ),
        reason=reason,
    )

    # Like a first assignment, a retest is only prepared here; the recruiter
    # emails it explicitly.
    reloaded = await get_invitation_for_application(db, application_id)
    assert reloaded is not None
    return reloaded, raw_token
