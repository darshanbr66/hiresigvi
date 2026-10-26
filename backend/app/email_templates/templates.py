# ruff: noqa: E501  (template bodies are prose; long lines are intentional)
"""The predefined candidate-email templates.

These are code-defined constants: a recruiter editing an email edits *that
email's* subject/body in the request, never the template itself, so the
defaults are always the same professional starting point.

Body syntax (plain text, so it stays easy to edit in a textarea):

* Blank line -> paragraph break.
* A paragraph whose lines all read ``Label: value`` (optionally preceded by a
  short heading line) is rendered as a details table.
* A paragraph whose lines all start with ``- `` is rendered as a list.
* ``{{placeholder}}`` is replaced from the application context or the
  recruiter's template fields (see ``rendering.py``). A line that contains a
  placeholder with no value is dropped, so optional details never leave a
  dangling label or a raw ``{{...}}`` in the sent email.
"""

from dataclasses import dataclass
from enum import StrEnum


class EmailTemplateKey(StrEnum):
    APPLICATION_RECEIVED = "APPLICATION_RECEIVED"
    INTERVIEW_INVITATION = "INTERVIEW_INVITATION"
    ASSESSMENT_INVITATION = "ASSESSMENT_INVITATION"
    NEXT_STEPS = "NEXT_STEPS"
    REJECTION = "REJECTION"
    # Recruitment programs (docs/programs.md § 6). Sent manually, like every
    # other template; the wording carries no amounts or terms of its own.
    SCHOLARSHIP_UPDATE = "SCHOLARSHIP_UPDATE"
    TRAINING_ENROLLMENT = "TRAINING_ENROLLMENT"
    GENERAL = "GENERAL"


# Every placeholder a template body or an edited email may use. Anything
# else is treated as unresolved and blocks sending.
KNOWN_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "candidate_name",
        "job_title",
        "company_name",
        "recruiter_name",
        "recruiter_email",
        "interview_round",
        "interview_date",
        "interview_time",
        "interview_mode",
        "interview_location",
        "meeting_link",
        "assessment_name",
        "assessment_link",
        "assessment_deadline",
        "assessment_instructions",
        "additional_instructions",
        "scholarship_update",
        "training_start_date",
        "training_duration",
    }
)

# Filled from the application/organization/signed-in user, or issued by the
# server — never accepted from the client.
CONTEXT_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "candidate_name",
        "job_title",
        "company_name",
        "recruiter_name",
        "recruiter_email",
        "assessment_name",
        "assessment_link",
        "assessment_deadline",
    }
)


@dataclass(frozen=True)
class TemplateField:
    """One input the recruiter may fill in for a template."""

    key: str
    label: str
    input_type: str = "text"  # text | textarea | date | time | select | url
    required: bool = False
    options: tuple[str, ...] = ()
    placeholder: str = ""


@dataclass(frozen=True)
class CallToAction:
    """A button rendered under the body; `url_placeholder` names the
    placeholder that supplies its link. No link -> no button."""

    label: str
    url_placeholder: str


@dataclass(frozen=True)
class EmailTemplate:
    key: EmailTemplateKey
    name: str
    description: str
    subject: str
    body: str
    fields: tuple[TemplateField, ...] = ()
    call_to_action: CallToAction | None = None

    @property
    def required_placeholders(self) -> frozenset[str]:
        return frozenset(field.key for field in self.fields if field.required)

    @property
    def available_without_application(self) -> bool:
        """The assessment invitation depends on an assigned assessment (its
        name, deadline and personal link), which only exists on an
        application; every other template can be sent on its own."""
        return self.key != EmailTemplateKey.ASSESSMENT_INVITATION

    @property
    def general_fields(self) -> tuple[TemplateField, ...] | None:
        """The inputs for sending this template WITHOUT an application, where
        the recipient and role can't come from a candidate record: a recipient
        name (falls back to "Sir or Madam") and — only if the template text
        mentions a role — a role/position. None when the template needs an
        application."""
        if not self.available_without_application:
            return None
        extra: list[TemplateField] = [
            TemplateField(
                "candidate_name",
                "Recipient name",
                placeholder='Leave blank to greet them as "Sir or Madam"',
            )
        ]
        if "{{job_title}}" in self.subject + self.body:
            extra.append(
                TemplateField(
                    "job_title",
                    "Role / position",
                    # The General template only mentions the role on an
                    # optional line, so it isn't needed there.
                    required=self.key != EmailTemplateKey.GENERAL,
                    placeholder="e.g. Backend Engineer",
                )
            )
        return (*extra, *self.fields)


_SIGNATURE = """Kind regards,
{{recruiter_name}}
{{company_name}}"""

INTERVIEW_MODES: tuple[str, ...] = ("Video call", "In person", "Phone call")

_TEMPLATES: tuple[EmailTemplate, ...] = (
    EmailTemplate(
        key=EmailTemplateKey.APPLICATION_RECEIVED,
        name="Application received",
        description="Thank the candidate and confirm their application has been received.",
        subject="Application Received – {{job_title}}",
        body=f"""Dear {{{{candidate_name}}}},

Thank you for applying for the {{{{job_title}}}} position at {{{{company_name}}}}. We have received your application and appreciate the time and effort you put into it.

Our recruitment team will review your profile against the requirements of the role. If your background is a good match, we will contact you about the next steps. Because we receive a high volume of applications, this can take a little while, and we appreciate your patience.

If you have any questions in the meantime, please reply to this email.

{_SIGNATURE}""",
    ),
    EmailTemplate(
        key=EmailTemplateKey.INTERVIEW_INVITATION,
        name="Interview invitation",
        description="Invite the candidate to an interview round with the date, time and joining details.",
        subject="Interview Invitation – {{job_title}}",
        body=f"""Dear {{{{candidate_name}}}},

Thank you for your interest in the {{{{job_title}}}} position at {{{{company_name}}}}. We were impressed by your profile and would like to invite you to the next stage of our selection process.

Interview details
Round: {{{{interview_round}}}}
Date: {{{{interview_date}}}}
Time: {{{{interview_time}}}}
Mode: {{{{interview_mode}}}}
Location: {{{{interview_location}}}}
Meeting link: {{{{meeting_link}}}}

{{{{additional_instructions}}}}

Please confirm your availability by replying to this email. If the proposed schedule does not suit you, let us know and we will do our best to find an alternative.

We look forward to speaking with you.

{_SIGNATURE}""",
        fields=(
            TemplateField(
                "interview_round", "Interview round", placeholder="e.g. Technical interview"
            ),
            TemplateField("interview_date", "Interview date", input_type="date", required=True),
            TemplateField("interview_time", "Interview time", input_type="time", required=True),
            TemplateField(
                "interview_mode",
                "Interview mode",
                input_type="select",
                required=True,
                options=INTERVIEW_MODES,
            ),
            TemplateField(
                "interview_location",
                "Location",
                placeholder="Office address (for in-person interviews)",
            ),
            TemplateField(
                "meeting_link",
                "Meeting link",
                input_type="url",
                placeholder="https://meet.example.com/…",
            ),
            TemplateField(
                "additional_instructions",
                "Additional instructions",
                input_type="textarea",
                placeholder="Documents to bring, who to ask for, dress code…",
            ),
        ),
        call_to_action=CallToAction("Join the interview", "meeting_link"),
    ),
    EmailTemplate(
        key=EmailTemplateKey.ASSESSMENT_INVITATION,
        name="Assessment invitation",
        description="Send the candidate their personal link to the assigned online assessment.",
        subject="Assessment Invitation – {{job_title}}",
        body=f"""Dear {{{{candidate_name}}}},

Thank you for your continued interest in the {{{{job_title}}}} position at {{{{company_name}}}}. As the next step in our selection process, we would like to invite you to complete an online assessment.

Assessment details
Assessment: {{{{assessment_name}}}}
Complete by: {{{{assessment_deadline}}}}

Please use the button below to begin. The link is unique to you, so please do not share it with anyone.

{{{{assessment_instructions}}}}

If you run into any technical difficulty or have questions, please reply to this email and we will be glad to help.

Good luck!

{_SIGNATURE}""",
        fields=(
            TemplateField(
                "assessment_instructions",
                "Instructions",
                input_type="textarea",
                placeholder="Anything the candidate should know before they start…",
            ),
        ),
        call_to_action=CallToAction("Start the assessment", "assessment_link"),
    ),
    EmailTemplate(
        key=EmailTemplateKey.NEXT_STEPS,
        name="Shortlisted / selected – next steps",
        description="Congratulate the candidate on progressing and explain that next steps will follow.",
        subject="Next Steps – {{job_title}}",
        body=f"""Dear {{{{candidate_name}}}},

Congratulations! Following a careful review of your application, we are pleased to let you know that you have progressed in our selection process for the {{{{job_title}}}} position at {{{{company_name}}}}.

Our team will be in touch shortly with the details of the next steps. In the meantime, please keep an eye on your inbox, and feel free to reply to this email if you have any questions.

{{{{additional_instructions}}}}

Thank you again for your interest in joining {{{{company_name}}}}.

{_SIGNATURE}""",
        fields=(
            TemplateField(
                "additional_instructions",
                "Additional notes",
                input_type="textarea",
                placeholder="Optional: anything specific to share about the next stage…",
            ),
        ),
    ),
    EmailTemplate(
        key=EmailTemplateKey.REJECTION,
        name="Application update – not moving forward",
        description="Respectfully let the candidate know their application will not proceed.",
        subject="Application Update – {{job_title}}",
        body=f"""Dear {{{{candidate_name}}}},

Thank you for your interest in the {{{{job_title}}}} position at {{{{company_name}}}}, and for the time you invested in our application process.

After careful consideration, we have decided not to move forward with your application at this time. This decision reflects the number of applicants and the specific requirements of the role, and is not a reflection of your potential.

We encourage you to apply for future openings that match your skills and experience, and we wish you every success in your career.

{_SIGNATURE}""",
    ),
    EmailTemplate(
        key=EmailTemplateKey.SCHOLARSHIP_UPDATE,
        name="Scholarship update (programs)",
        description="Tell a program participant about their scholarship status, in your own approved words.",
        subject="Scholarship Update – {{job_title}}",
        body=f"""Dear {{{{candidate_name}}}},

We are writing with an update about the scholarship associated with the {{{{job_title}}}} at {{{{company_name}}}}.

{{{{scholarship_update}}}}

Any scholarship is subject to the company's approved terms and agreement. Please reply to this email if you have any questions.

{_SIGNATURE}""",
        fields=(
            TemplateField(
                "scholarship_update",
                "Scholarship update",
                input_type="textarea",
                required=True,
                placeholder="Only business-approved details: the decision and the next steps…",
            ),
        ),
    ),
    EmailTemplate(
        key=EmailTemplateKey.TRAINING_ENROLLMENT,
        name="Training enrollment (programs)",
        description="Confirm a participant's training enrollment, start date and duration.",
        subject="Training Enrollment – {{job_title}}",
        body=f"""Dear {{{{candidate_name}}}},

We are pleased to confirm your enrollment in the training for the {{{{job_title}}}} at {{{{company_name}}}}.

Training details
Start date: {{{{training_start_date}}}}
Duration: {{{{training_duration}}}}

{{{{additional_instructions}}}}

Please reply to this email to confirm, or if you have any questions.

{_SIGNATURE}""",
        fields=(
            TemplateField(
                "training_start_date", "Training start date", input_type="date", required=True
            ),
            TemplateField(
                "training_duration",
                "Training duration",
                input_type="select",
                required=True,
                options=("6 months", "12 months"),
            ),
            TemplateField(
                "additional_instructions",
                "Additional instructions",
                input_type="textarea",
                placeholder="Where to report, what to bring, fee-payment reminders…",
            ),
        ),
    ),
    EmailTemplate(
        key=EmailTemplateKey.GENERAL,
        name="General recruitment message",
        description="A clean, professional starting point for any other message.",
        subject="A message from {{company_name}}",
        body=f"""Dear {{{{candidate_name}}}},

I am writing to you on behalf of {{{{company_name}}}}.

This message relates to your application for the {{{{job_title}}}} position.

Please reply to this email if you have any questions.

{_SIGNATURE}""",
    ),
)

_BY_KEY: dict[EmailTemplateKey, EmailTemplate] = {template.key: template for template in _TEMPLATES}


def list_templates() -> tuple[EmailTemplate, ...]:
    return _TEMPLATES


def get_template(key: EmailTemplateKey) -> EmailTemplate:
    return _BY_KEY[key]
