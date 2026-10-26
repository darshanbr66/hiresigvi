"""Template registry, placeholder resolution and the HTML layout — pure
functions, no database."""

import re

import pytest

from app.email_templates import (
    KNOWN_PLACEHOLDERS,
    PLACEHOLDER_PATTERN,
    EmailTemplateKey,
    get_template,
    list_templates,
    render_email,
    resolve_placeholders,
)

_CONTEXT = {
    "candidate_name": "Cara Candidate",
    "job_title": "Backend Engineer",
    "company_name": "Acme Corp",
    "recruiter_name": "Riya Recruiter",
    "recruiter_email": "riya@acme.dev",
}


def test_all_required_templates_exist_with_the_specified_subjects() -> None:
    subjects = {template.key: template.subject for template in list_templates()}
    assert subjects == {
        EmailTemplateKey.APPLICATION_RECEIVED: "Application Received – {{job_title}}",
        EmailTemplateKey.INTERVIEW_INVITATION: "Interview Invitation – {{job_title}}",
        EmailTemplateKey.ASSESSMENT_INVITATION: "Assessment Invitation – {{job_title}}",
        EmailTemplateKey.NEXT_STEPS: "Next Steps – {{job_title}}",
        EmailTemplateKey.REJECTION: "Application Update – {{job_title}}",
        EmailTemplateKey.SCHOLARSHIP_UPDATE: "Scholarship Update – {{job_title}}",
        EmailTemplateKey.TRAINING_ENROLLMENT: "Training Enrollment – {{job_title}}",
        EmailTemplateKey.GENERAL: "A message from {{company_name}}",
    }


def test_every_placeholder_used_by_a_template_is_a_known_placeholder() -> None:
    for template in list_templates():
        used = set(PLACEHOLDER_PATTERN.findall(template.subject + template.body))
        assert used <= KNOWN_PLACEHOLDERS, (template.key, used - KNOWN_PLACEHOLDERS)


def test_default_templates_resolve_completely_without_any_template_fields() -> None:
    """Application received / next steps / rejection / general should be
    sendable as-is: nothing left unresolved, no raw placeholder."""
    for key in (
        EmailTemplateKey.APPLICATION_RECEIVED,
        EmailTemplateKey.NEXT_STEPS,
        EmailTemplateKey.REJECTION,
        EmailTemplateKey.GENERAL,
    ):
        template = get_template(key)
        subject = resolve_placeholders(
            template.subject, _CONTEXT, required=template.required_placeholders
        )
        body = resolve_placeholders(
            template.body, _CONTEXT, required=template.required_placeholders
        )
        assert subject.unresolved == () and body.unresolved == (), key
        assert "{{" not in subject.text + body.text
        assert "Cara Candidate" in body.text and "Acme Corp" in body.text
        assert "Backend Engineer" in subject.text + body.text


def test_placeholders_are_replaced_and_whitespace_inside_braces_is_tolerated() -> None:
    result = resolve_placeholders("Hi {{ candidate_name }}, re {{job_title}}", _CONTEXT)
    assert result.text == "Hi Cara Candidate, re Backend Engineer"
    assert result.unresolved == ()


def test_optional_placeholder_without_a_value_drops_its_whole_line() -> None:
    template = get_template(EmailTemplateKey.INTERVIEW_INVITATION)
    values = {
        **_CONTEXT,
        "interview_date": "Friday, 25 September 2026",
        "interview_time": "14:30",
        "interview_mode": "Phone call",
    }

    body = resolve_placeholders(template.body, values, required=template.required_placeholders)

    assert body.unresolved == ()
    assert "{{" not in body.text
    assert "Date: Friday, 25 September 2026" in body.text
    # Optional details that weren't supplied leave no label behind.
    for label in ("Location:", "Meeting link:", "Round:"):
        assert label not in body.text
    assert "\n\n\n" not in body.text


def test_optional_details_that_are_supplied_are_kept() -> None:
    template = get_template(EmailTemplateKey.INTERVIEW_INVITATION)
    values = {
        **_CONTEXT,
        "interview_round": "Technical round 1",
        "interview_date": "Friday, 25 September 2026",
        "interview_time": "14:30",
        "interview_mode": "Video call",
        "meeting_link": "https://meet.example.com/abc",
        "additional_instructions": "Please keep your ID handy.",
    }

    body = resolve_placeholders(template.body, values, required=template.required_placeholders)

    assert "Round: Technical round 1" in body.text
    assert "Meeting link: https://meet.example.com/abc" in body.text
    assert "Please keep your ID handy." in body.text
    assert "Location:" not in body.text


def test_required_placeholder_without_a_value_stays_visible_and_is_reported() -> None:
    template = get_template(EmailTemplateKey.INTERVIEW_INVITATION)

    body = resolve_placeholders(template.body, _CONTEXT, required=template.required_placeholders)

    assert set(body.unresolved) == {"interview_date", "interview_time", "interview_mode"}
    assert "{{interview_date}}" in body.text


def test_unknown_placeholder_is_reported_not_silently_dropped() -> None:
    result = resolve_placeholders("Hello {{candidate_name}} {{salary_offer}}", _CONTEXT)
    assert result.unresolved == ("salary_offer",)
    assert "{{salary_offer}}" in result.text


def test_a_placeholder_smuggled_in_through_a_value_is_still_caught() -> None:
    values = {**_CONTEXT, "candidate_name": "{{recruiter_email}}"}
    result = resolve_placeholders("Dear {{candidate_name}},", values)
    assert result.unresolved == ("recruiter_email",)


def _render(body: str, **overrides: str | None) -> str:
    return render_email(
        subject="Interview Invitation – Backend Engineer",
        body=body,
        company_name="Acme Corp",
        recruiter_name="Riya Recruiter",
        recruiter_email="riya@acme.dev",
        job_title="Backend Engineer",
        **overrides,  # type: ignore[arg-type]
    ).html


def test_html_email_has_a_professional_email_client_friendly_structure() -> None:
    template = get_template(EmailTemplateKey.APPLICATION_RECEIVED)
    body = resolve_placeholders(template.body, _CONTEXT).text

    html = _render(body)

    assert html.startswith("<!DOCTYPE html>")
    assert 'role="presentation"' in html  # table-based layout
    assert "Acme Corp" in html  # header/branding
    assert "Dear Cara Candidate," in html
    assert "riya@acme.dev" in html  # footer contact
    assert 'href="mailto:riya@acme.dev"' in html
    # Inline styles only; nothing that can fail to load or execute.
    for forbidden in ("<script", "<link", "<style", "<img", "@import", "javascript:", "onclick"):
        assert forbidden not in html.lower()
    assert "{{" not in html


def test_details_block_renders_as_a_table_and_a_heading() -> None:
    html = _render(
        "Interview details\nDate: Friday, 25 September 2026\nTime: 14:30\n\nSee you then."
    )
    assert "Interview details" in html
    assert ">Date<" in html and "Friday, 25 September 2026" in html
    assert "<table" in html.split("Interview details", 1)[1]


def test_call_to_action_button_is_rendered_only_with_a_safe_link() -> None:
    with_button = _render(
        "Hello.",
        cta_label="Start the assessment",
        cta_url="https://app.example.com/assessment/t0k3n",
    )
    assert 'href="https://app.example.com/assessment/t0k3n"' in with_button
    assert "Start the assessment" in with_button

    for bad_url in ("javascript:alert(1)", "ftp://example.com/x", "not a url", ""):
        without = _render("Hello.", cta_label="Click", cta_url=bad_url)
        assert ">Click<" not in without, bad_url
        assert "javascript:" not in without


def test_html_escapes_user_supplied_text() -> None:
    html = _render('Dear <script>alert("x")</script> & <b>friend</b>,')
    assert "<script" not in html
    assert "&lt;script&gt;" in html
    assert "&amp;" in html
    assert "<b>friend</b>" not in html


def test_bare_urls_in_the_body_become_links_without_swallowing_punctuation() -> None:
    html = _render("Join here: https://meet.example.com/room?a=1&b=2.")
    assert 'href="https://meet.example.com/room?a=1&amp;b=2"' in html
    assert re.search(r"</a>\.", html)


@pytest.mark.parametrize("key", list(EmailTemplateKey))
def test_plain_text_alternative_is_generated_for_every_template(key: EmailTemplateKey) -> None:
    template = get_template(key)
    values = {
        **_CONTEXT,
        "interview_date": "Friday",
        "interview_time": "14:30",
        "interview_mode": "Phone call",
        "assessment_name": "Python Basics",
        "assessment_deadline": "1 October 2026, 12:00 UTC",
    }
    body = resolve_placeholders(template.body, values, required=template.required_placeholders).text
    email = render_email(
        subject="S",
        body=body,
        company_name="Acme Corp",
        recruiter_name="Riya Recruiter",
        recruiter_email="riya@acme.dev",
        job_title="Backend Engineer",
    )
    assert "Cara Candidate" in email.text
    assert "<" not in email.text
    assert "riya@acme.dev" in email.text


def test_general_fields_ask_for_the_recipient_and_role_a_candidate_record_would_supply() -> None:
    application_received = get_template(EmailTemplateKey.APPLICATION_RECEIVED)
    fields = {f.key: f for f in application_received.general_fields or ()}
    assert fields["candidate_name"].required is False  # falls back to "Sir or Madam"
    assert fields["job_title"].required is True  # the text is about a role

    interview = get_template(EmailTemplateKey.INTERVIEW_INVITATION)
    keys = [f.key for f in interview.general_fields or ()]
    # Recipient/role first, then the template's own interview fields, unchanged.
    assert keys[:2] == ["candidate_name", "job_title"]
    assert keys[2:] == [f.key for f in interview.fields]

    general = get_template(EmailTemplateKey.GENERAL)
    assert {f.key: f.required for f in general.general_fields or ()}["job_title"] is False


def test_only_the_assessment_invitation_needs_an_application() -> None:
    for template in list_templates():
        needs_application = template.key == EmailTemplateKey.ASSESSMENT_INVITATION
        assert (template.general_fields is None) is needs_application, template.key
        assert template.available_without_application is not needs_application


def test_footer_wording_depends_on_whether_the_email_is_about_an_application() -> None:
    with_job = render_email(
        subject="S", body="Hello.", company_name="Acme Corp", recruiter_name="Riya",
        recruiter_email="riya@acme.dev", job_title="Backend Engineer",
    )
    without_job = render_email(
        subject="S", body="Hello.", company_name="Acme Corp", recruiter_name="Riya",
        recruiter_email="riya@acme.dev",
    )
    assert "regarding your application for Backend Engineer at Acme Corp" in with_job.text
    assert "regarding your application" not in without_job.text
    assert "You are receiving this message from Acme Corp." in without_job.html

