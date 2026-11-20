"""Manual email: load a template, preview it, and send it — only ever in
response to an explicit action by an authorized recruiter/admin.

Two entry points share one code path (template resolution, placeholder
rules, HTML rendering, delivery, audit):

* application emails — tied to an application, so the candidate, job and (for
  the assessment invitation) the assigned assessment come from the database;
* general emails — sent to any addresses the sender types, with no application.

Flow (compose / preview / send all go through `_prepare_*`, so a preview is
exactly what a send would deliver):

  compose  template + template fields  -> editable subject/body
  preview  the (possibly edited) draft  -> rendered HTML/text, nothing sent
  send     the same draft              -> delivered via the SMTP provider

Server-authoritative facts (company, recruiter, and for application emails the
candidate/job/assessment link) come from the database and the signed-in user;
the client only supplies the template's own fields, the recipients, and the
text of the draft. Authorization (`application.email.send`) is enforced by the
route, tenant isolation by RLS (application lookup) and by always taking the
organization from the authenticated user.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import NoReturn

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import (
    BadGatewayError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    UnprocessableError,
)
from app.core.security import generate_opaque_token, hash_opaque_token
from app.email_templates import (
    EmailTemplate,
    EmailTemplateKey,
    RenderedEmail,
    TemplateField,
    get_template,
    is_safe_http_url,
    render_email,
    resolve_placeholders,
)
from app.integrations.email import EmailError, EmailNotConfiguredError
from app.models.application import Application
from app.models.assessment import AssessmentInvitation, InvitationStatus
from app.models.organization import Organization
from app.models.user import User
from app.schemas.email import EmailComposeRequest, EmailDraftRequest, GeneralEmailDraftRequest
from app.services import (
    activity_service,
    application_service,
    assessment_service,
    notification_service,
)

_MAX_VALUE_LENGTH = 2000
# Greeting used when a general email is sent without a recipient name.
_FALLBACK_RECIPIENT_NAME = "Sir or Madam"
_MAX_LABEL_LENGTH = 255


@dataclass(frozen=True)
class ComposedEmail:
    template_key: EmailTemplateKey
    subject: str
    body: str
    variables: dict[str, str]
    missing_required: list[str]


@dataclass(frozen=True)
class PreviewedEmail:
    to: str
    reply_to: str
    rendered: RenderedEmail
    has_call_to_action: bool


@dataclass(frozen=True)
class PreviewedGeneralEmail:
    to: list[str]
    cc: list[str]
    bcc: list[str]
    reply_to: str
    rendered: RenderedEmail
    has_call_to_action: bool


@dataclass(frozen=True)
class _Prepared:
    template: EmailTemplate
    fields: tuple[TemplateField, ...]
    organization: Organization
    values: dict[str, str]
    variables: dict[str, str]
    application: Application | None = None
    invitation: AssessmentInvitation | None = None
    # Set only when sending an assessment invitation: the link that will be
    # valid once the new token hash is stored.
    fresh_token: str | None = None

    @property
    def required(self) -> frozenset[str]:
        return frozenset(field.key for field in self.fields if field.required)


# --- input handling ------------------------------------------------------


def _format_date(value: str) -> str:
    parsed = date.fromisoformat(value)
    return f"{parsed:%A}, {parsed.day} {parsed:%B %Y}"


def _format_deadline(moment: datetime) -> str:
    utc = moment.astimezone(UTC)
    return f"{utc.day} {utc:%B %Y}, {utc:%H:%M} UTC"


def _clean_variables(
    template_name: str, fields: Sequence[TemplateField], variables: dict[str, str]
) -> dict[str, str]:
    by_key = {field.key: field for field in fields}
    unknown = sorted(set(variables) - set(by_key))
    if unknown:
        raise UnprocessableError(
            f"Unknown field(s) for the {template_name} template: {', '.join(unknown)}."
        )

    cleaned: dict[str, str] = {}
    for key, raw in variables.items():
        field = by_key[key]
        value = (raw or "").strip()
        if len(value) > _MAX_VALUE_LENGTH:
            raise UnprocessableError(f"{field.label} is too long.")
        if field.input_type != "textarea":
            value = " ".join(value.split())
        if value:
            if field.input_type == "url" and not is_safe_http_url(value):
                raise UnprocessableError(f"{field.label} must be a valid http(s) link.")
            if field.input_type == "select" and value not in field.options:
                raise UnprocessableError(
                    f"{field.label} must be one of: {', '.join(field.options)}."
                )
            if field.input_type == "date":
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise UnprocessableError(f"{field.label} must be a valid date.") from exc
        cleaned[key] = value
    return cleaned


def _display_values(fields: Sequence[TemplateField], variables: dict[str, str]) -> dict[str, str]:
    """Field values as they should read in the email (dates spelled out)."""
    display = dict(variables)
    for field in fields:
        if field.input_type == "date" and display.get(field.key):
            display[field.key] = _format_date(display[field.key])
    return display


# --- context -------------------------------------------------------------


async def _load_application(db: AsyncSession, application_id: uuid.UUID) -> Application:
    """RLS scopes this lookup to the caller's organization, so another
    tenant's application is indistinguishable from a missing one."""
    application = await application_service.get_application(db, application_id)
    if application is None:
        raise NotFoundError("Application not found.")
    if application.deleted_at is not None:
        raise ConflictError("This application has been deleted, so it can't be emailed.")
    return application


async def _load_organization(db: AsyncSession, organization_id: uuid.UUID | None) -> Organization:
    organization = await db.get(Organization, organization_id) if organization_id else None
    if organization is None:
        raise NotFoundError("Organization not found.")
    return organization


def _contact_email(organization: Organization, actor: User) -> str:
    """The address an outgoing email names as its contact (footer, and the
    `{{recruiter_email}}` placeholder) and sets as Reply-To: the
    organization's published recruitment contact
    (`organizations.careers_contact_email`) when it has one, so recipients
    reach the recruitment team rather than one staff member's login
    address; otherwise the sender's own address."""
    return organization.careers_contact_email or actor.email


async def _prepare_for_application(
    db: AsyncSession,
    application: Application,
    actor: User,
    template_key: EmailTemplateKey,
    variables: dict[str, str],
    *,
    issue_assessment_link: bool,
) -> _Prepared:
    template = get_template(template_key)
    cleaned = _clean_variables(template.name, template.fields, variables)
    organization = await _load_organization(db, application.organization_id)

    values: dict[str, str] = {
        **_display_values(template.fields, cleaned),
        "candidate_name": application.candidate.full_name,
        "job_title": application.job.title,
        "company_name": organization.name,
        "recruiter_name": actor.full_name,
        "recruiter_email": _contact_email(organization, actor),
    }

    invitation: AssessmentInvitation | None = None
    fresh_token: str | None = None
    if template_key == EmailTemplateKey.ASSESSMENT_INVITATION:
        invitation = await assessment_service.get_invitation_for_application(db, application.id)
        if invitation is None:
            raise ConflictError(
                "No assessment has been assigned to this application yet. "
                "Assign an assessment first, then send the invitation."
            )
        if invitation.status != InvitationStatus.SENT:
            raise ConflictError(
                f"This assessment attempt is already {invitation.status.value.lower()}, "
                "so its invitation can't be emailed."
            )
        if invitation.expires_at <= datetime.now(UTC):
            raise ConflictError(
                "This assessment invitation has expired. Assign the assessment again "
                "before sending the invitation."
            )

        base_url = get_settings().frontend_base_url.rstrip("/")
        if issue_assessment_link:
            # The raw token is never stored (only its hash), so a link that
            # can actually be emailed has to be issued now. It only takes
            # effect if the send succeeds — see `send_email`.
            fresh_token = generate_opaque_token()
            link = f"{base_url}/assessment/{fresh_token}"
        else:
            link = f"{base_url}/assessment/your-personal-link"
        values["assessment_name"] = invitation.assessment.title
        values["assessment_deadline"] = _format_deadline(invitation.expires_at)
        values["assessment_link"] = link

    return _Prepared(
        template=template,
        fields=template.fields,
        organization=organization,
        values=values,
        variables=cleaned,
        application=application,
        invitation=invitation,
        fresh_token=fresh_token,
    )


async def _prepare_general(
    db: AsyncSession, actor: User, template_key: EmailTemplateKey, variables: dict[str, str]
) -> _Prepared:
    template = get_template(template_key)
    fields = template.general_fields
    if fields is None:
        raise UnprocessableError(
            f"The {template.name} template needs an application. Send it from the "
            "application page, or choose another template.",
            code="template_requires_application",
        )
    cleaned = _clean_variables(template.name, fields, variables)
    # The organization always comes from the signed-in user — never the request.
    organization = await _load_organization(db, actor.organization_id)

    display = _display_values(fields, cleaned)
    values: dict[str, str] = {
        **display,
        "candidate_name": display.get("candidate_name") or _FALLBACK_RECIPIENT_NAME,
        "company_name": organization.name,
        "recruiter_name": actor.full_name,
        "recruiter_email": _contact_email(organization, actor),
    }
    return _Prepared(
        template=template,
        fields=fields,
        organization=organization,
        values=values,
        variables=cleaned,
    )


def _compose(prepared: _Prepared) -> ComposedEmail:
    template = prepared.template
    subject = resolve_placeholders(template.subject, prepared.values, required=prepared.required)
    body = resolve_placeholders(template.body, prepared.values, required=prepared.required)
    missing = [
        field.key
        for field in prepared.fields
        if field.required and not prepared.values.get(field.key)
    ]
    return ComposedEmail(
        template_key=template.key,
        subject=subject.text,
        body=body.text,
        variables=prepared.variables,
        missing_required=missing,
    )


def _render_draft(prepared: _Prepared, subject: str, body: str) -> tuple[RenderedEmail, bool]:
    template = prepared.template
    resolved_subject = resolve_placeholders(subject, prepared.values, required=prepared.required)
    resolved_body = resolve_placeholders(body, prepared.values, required=prepared.required)

    unresolved = list(dict.fromkeys([*resolved_subject.unresolved, *resolved_body.unresolved]))
    if unresolved:
        names = ", ".join("{{" + name + "}}" for name in unresolved)
        raise UnprocessableError(
            f"The email still contains unfilled placeholders ({names}). "
            "Fill in the template details or replace them in the text before continuing.",
            code="unresolved_placeholders",
        )

    final_subject = " ".join(resolved_subject.text.split())
    if not final_subject or not resolved_body.text:
        raise UnprocessableError("The email needs a subject and a message.")

    cta = template.call_to_action
    cta_url = prepared.values.get(cta.url_placeholder, "").strip() if cta else ""
    has_cta = bool(cta and cta_url and is_safe_http_url(cta_url))

    rendered = render_email(
        subject=final_subject,
        body=resolved_body.text,
        company_name=prepared.organization.name,
        recruiter_name=prepared.values["recruiter_name"],
        recruiter_email=prepared.values["recruiter_email"],
        # Only application emails are "about an application" (footer wording).
        job_title=prepared.values.get("job_title", "") if prepared.application else "",
        cta_label=cta.label if cta and has_cta else None,
        cta_url=cta_url if has_cta else None,
    )
    return rendered, has_cta


def _raise_delivery_error(exc: EmailError) -> NoReturn:
    if isinstance(exc, EmailNotConfiguredError):
        raise ServiceUnavailableError(str(exc), code="email_not_configured") from exc
    raise BadGatewayError(str(exc), code="email_delivery_failed") from exc


# --- application emails ---------------------------------------------------


async def compose_email(
    db: AsyncSession, *, application_id: uuid.UUID, actor: User, request: EmailComposeRequest
) -> ComposedEmail:
    application = await _load_application(db, application_id)
    prepared = await _prepare_for_application(
        db, application, actor, request.template_key, request.variables, issue_assessment_link=False
    )
    return _compose(prepared)


async def preview_email(
    db: AsyncSession, *, application_id: uuid.UUID, actor: User, draft: EmailDraftRequest
) -> PreviewedEmail:
    application = await _load_application(db, application_id)
    prepared = await _prepare_for_application(
        db, application, actor, draft.template_key, draft.variables, issue_assessment_link=False
    )
    rendered, has_cta = _render_draft(prepared, draft.subject, draft.body)
    return PreviewedEmail(
        to=application.candidate.email,
        reply_to=prepared.values["recruiter_email"],
        rendered=rendered,
        has_call_to_action=has_cta,
    )


async def send_email(
    db: AsyncSession, *, application_id: uuid.UUID, actor: User, draft: EmailDraftRequest
) -> PreviewedEmail:
    application = await _load_application(db, application_id)
    prepared = await _prepare_for_application(
        db, application, actor, draft.template_key, draft.variables, issue_assessment_link=True
    )
    rendered, has_cta = _render_draft(prepared, draft.subject, draft.body)

    candidate = application.candidate
    label = f"{candidate.full_name} — {application.job.title}"
    invitation = prepared.invitation

    previous_token_hash = invitation.token_hash if invitation else None
    if invitation is not None and prepared.fresh_token is not None:
        invitation.token_hash = hash_opaque_token(prepared.fresh_token)
        await db.flush()

    try:
        await notification_service.send_email(
            to=[candidate.email],
            subject=rendered.subject,
            html=rendered.html,
            text=rendered.text,
            reply_to=prepared.values["recruiter_email"],
        )
    except EmailError as exc:
        if invitation is not None and previous_token_hash is not None:
            # Nothing was delivered, so the link the candidate may already
            # hold must keep working.
            invitation.token_hash = previous_token_hash
            await db.flush()
        # get_db rolls the request back on an exception, which would erase
        # this audit row — commit the failed attempt first (same pattern as
        # auth_service.authenticate's LOGIN_FAILED).
        await activity_service.record_activity(
            db,
            organization_id=application.organization_id,
            actor=actor,
            action="CANDIDATE_EMAIL_FAILED",
            entity_type="application",
            entity_id=application.id,
            entity_label=label,
            description=(
                f"{prepared.template.name} email to {candidate.full_name} "
                f'("{rendered.subject}") could not be sent.'
            ),
        )
        await db.commit()
        _raise_delivery_error(exc)

    if invitation is not None:
        invitation.emailed_at = datetime.now(UTC)
        await db.flush()

    await activity_service.record_activity(
        db,
        organization_id=application.organization_id,
        actor=actor,
        action="CANDIDATE_EMAIL_SENT",
        entity_type="application",
        entity_id=application.id,
        entity_label=label,
        description=(
            f"{prepared.template.name} email sent to {candidate.full_name} "
            f'("{rendered.subject}").'
        ),
    )
    return PreviewedEmail(
        to=candidate.email,
        reply_to=prepared.values["recruiter_email"],
        rendered=rendered,
        has_call_to_action=has_cta,
    )


# --- general emails (no application) --------------------------------------


def _summarize_recipients(to: Sequence[str], cc: Sequence[str], bcc: Sequence[str]) -> str:
    parts = [", ".join(to)]
    if cc:
        parts.append(f"Cc: {', '.join(cc)}")
    if bcc:
        parts.append(f"Bcc: {', '.join(bcc)}")
    return "; ".join(parts)


def _recipient_label(to: Sequence[str], total: int) -> str:
    label = to[0] if total == 1 else f"{to[0]} (+{total - 1} more)"
    return label[:_MAX_LABEL_LENGTH]


async def compose_general_email(
    db: AsyncSession, *, actor: User, request: EmailComposeRequest
) -> ComposedEmail:
    prepared = await _prepare_general(db, actor, request.template_key, request.variables)
    return _compose(prepared)


async def preview_general_email(
    db: AsyncSession, *, actor: User, draft: GeneralEmailDraftRequest
) -> PreviewedGeneralEmail:
    prepared = await _prepare_general(db, actor, draft.template_key, draft.variables)
    rendered, has_cta = _render_draft(prepared, draft.subject, draft.body)
    return PreviewedGeneralEmail(
        to=list(draft.to),
        cc=list(draft.cc),
        bcc=list(draft.bcc),
        reply_to=prepared.values["recruiter_email"],
        rendered=rendered,
        has_call_to_action=has_cta,
    )


async def send_general_email(
    db: AsyncSession, *, actor: User, draft: GeneralEmailDraftRequest
) -> PreviewedGeneralEmail:
    """Sends to the addresses the sender typed. The audit entry records who
    sent what kind of email to which addresses and whether it worked — the
    subject, but never the body (which can hold personal detail) and never
    any SMTP configuration."""
    prepared = await _prepare_general(db, actor, draft.template_key, draft.variables)
    rendered, has_cta = _render_draft(prepared, draft.subject, draft.body)

    to, cc, bcc = list(draft.to), list(draft.cc), list(draft.bcc)
    summary = _summarize_recipients(to, cc, bcc)
    label = _recipient_label(to, len(to) + len(cc) + len(bcc))
    organization_id = prepared.organization.id

    try:
        await notification_service.send_email(
            to=to,
            cc=cc,
            bcc=bcc,
            subject=rendered.subject,
            html=rendered.html,
            text=rendered.text,
            reply_to=prepared.values["recruiter_email"],
        )
    except EmailError as exc:
        await activity_service.record_activity(
            db,
            organization_id=organization_id,
            actor=actor,
            action="GENERAL_EMAIL_FAILED",
            entity_type="email",
            entity_label=label,
            description=(
                f'{prepared.template.name} email "{rendered.subject}" to {summary} '
                "could not be sent."
            ),
        )
        await db.commit()  # see send_email: the exception below rolls the request back
        _raise_delivery_error(exc)

    await activity_service.record_activity(
        db,
        organization_id=organization_id,
        actor=actor,
        action="GENERAL_EMAIL_SENT",
        entity_type="email",
        entity_label=label,
        description=f'{prepared.template.name} email "{rendered.subject}" sent to {summary}.',
    )
    return PreviewedGeneralEmail(
        to=to,
        cc=cc,
        bcc=bcc,
        reply_to=prepared.values["recruiter_email"],
        rendered=rendered,
        has_call_to_action=has_cta,
    )
