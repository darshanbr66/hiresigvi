"""The progressive self-service apply flow (app/services/application_draft_service.py):
JD-derived qualification options, the draft's server-side state, the academic
document check and its outcomes, manual review, and the final submission.

The AI is replaced by `FakeEducationAI` — the only stand-in here (tests must
never reach a real model, see conftest.py). Everything else is real: HTTP,
the database (RLS included), storage and the email verification flow.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import rls_bypass
from app.integrations.ai import AIProviderTimeoutError
from app.integrations.ai.education import (
    AcademicDocumentAnalysis,
    EducationAIProvider,
    JobQualificationAnalysis,
    VocabularyEntry,
)
from app.models.activity import Activity
from app.models.application import Application, ApplicationEducationStatus
from app.models.application_draft import (
    AcademicDocument,
    ApplicationDraft,
    EducationVerificationOutcome,
    EducationVerificationRun,
)
from app.models.candidate import Candidate
from app.models.user import User
from app.services import application_draft_service
from app.services.education import catalog, document_check, job_qualification_service
from app.services.education.job_qualification_service import (
    QualificationRequirements,
    normalize,
)
from tests.conftest import make_minimal_pdf
from tests.org_setup import bootstrap_org, open_job
from tests.public_apply import (
    DEFAULT_EDUCATION,
    academic_document_pdf,
    apply_publicly,
    phone_for,
    verify_email,
)

# --- Fakes / helpers -----------------------------------------------------------


def jd(**overrides: Any) -> JobQualificationAnalysis:
    data: dict[str, Any] = {
        "determinable": True,
        "requirement_strength": "REQUIRED",
        "minimum_level": None,
        "accepted_qualification_keys": [],
        "higher_qualifications_accepted": True,
        "fields_of_study": [],
        "field_mandatory": False,
        "confidence": 0.9,
        "evidence": "",
    }
    data.update(overrides)
    return JobQualificationAnalysis.model_validate(data)


def doc(**overrides: Any) -> AcademicDocumentAnalysis:
    data: dict[str, Any] = {
        "is_academic_document": True,
        "legible": True,
        "document_type": "MARKS_CARD",
        "detected_level": "MASTERS",
        "detected_qualification_key": "ME_MTECH",
        "degree_text": "Master of Technology",
        "branch_text": "Computer Science",
        "institution_text": "Anna University",
        "name_on_document": "Jane Candidate",
        "branch_matches_selected": "YES",
        "confidence": 0.92,
        "notes": "Final-year marks card.",
    }
    data.update(overrides)
    return AcademicDocumentAnalysis.model_validate(data)


#: `doc()` overrides for a B.Tech marks card.
BTECH_RECORD: dict[str, Any] = {
    "detected_level": "BACHELORS",
    "detected_qualification_key": "BE_BTECH",
    "degree_text": "Bachelor of Technology",
}


class FakeEducationAI(EducationAIProvider):
    name = "fake"
    model = "fake-education-1"

    def __init__(self) -> None:
        self.job_result: JobQualificationAnalysis | Exception = jd(
            determinable=False, requirement_strength="NOT_STATED", confidence=0.9
        )
        self.document_results: list[AcademicDocumentAnalysis | Exception] = []
        self.job_calls = 0
        self.document_calls: list[dict[str, Any]] = []

    async def analyze_job_qualifications(
        self, *, job_title: str, job_text: str, vocabulary: Sequence[VocabularyEntry]
    ) -> JobQualificationAnalysis:
        self.job_calls += 1
        if isinstance(self.job_result, Exception):
            raise self.job_result
        return self.job_result

    async def analyze_academic_document(
        self,
        *,
        content: bytes,
        content_type: str,
        extracted_text: str | None,
        selected: VocabularyEntry,
        selected_branch: str | None,
        vocabulary: Sequence[VocabularyEntry],
    ) -> AcademicDocumentAnalysis:
        self.document_calls.append(
            {"selected": selected.key, "content_type": content_type, "size": len(content)}
        )
        result = self.document_results.pop(0) if self.document_results else doc()
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def fake_ai(monkeypatch: pytest.MonkeyPatch) -> FakeEducationAI:
    fake = FakeEducationAI()
    monkeypatch.setattr(job_qualification_service, "get_education_ai_provider", lambda: fake)
    monkeypatch.setattr(application_draft_service, "get_education_ai_provider", lambda: fake)
    return fake


async def _setup(client: AsyncClient, slug: str) -> dict:
    ctx = await bootstrap_org(client, slug)
    job = await open_job(client, ctx, title="Patent Engineer")
    ctx["job"] = job
    ctx["base"] = f"/api/v1/public/organizations/{slug}/jobs/{job['id']}"
    return ctx


_BACKGROUND = {
    "date_of_birth": "1998-04-12",
    "place_of_birth": "Chennai",
    "languages": ["English", "Tamil"],
    "candidate_type": "FRESHER",
    "location": "Chennai",
    "preferred_location": "Bengaluru",
}


async def _start(
    client: AsyncClient, ctx: dict, email: str, *, full_name: str = "Jane Candidate"
) -> dict:
    token = await verify_email(client, ctx["slug"], email)
    auth = {"email": email, "email_verification_token": token}
    started = await client.post(
        f"{ctx['base']}/apply/draft",
        json={**auth, "full_name": full_name, "phone": phone_for(email)},
    )
    assert started.status_code == 200, started.text
    saved = await client.put(
        f"{ctx['base']}/apply/draft/background", json={**auth, "background": _BACKGROUND}
    )
    assert saved.status_code == 200, saved.text
    return auth


async def _save_education(client: AsyncClient, ctx: dict, auth: dict, **overrides: Any):
    return await client.put(
        f"{ctx['base']}/apply/draft/education",
        json={**auth, "education": {**DEFAULT_EDUCATION, **overrides}},
    )


async def _upload(client: AsyncClient, ctx: dict, auth: dict, file=None):
    return await client.post(
        f"{ctx['base']}/apply/draft/education/document",
        data=auth,
        files={"document": file or academic_document_pdf()},
    )


async def _submit(client: AsyncClient, ctx: dict, auth: dict):
    return await client.post(
        f"{ctx['base']}/apply",
        data=auth,
        files={"resume": ("resume.pdf", make_minimal_pdf("Jane. B.Tech CSE."), "application/pdf")},
    )


# --- Unit: JD requirements normalization ----------------------------------------


def test_bachelors_engineering_requirement_adds_related_higher_qualifications() -> None:
    # Example A: an engineering background — B.E./B.Tech and related higher.
    result = normalize(jd(accepted_qualification_keys=["BE_BTECH"]), "h")
    assert result.restricted
    assert result.eligible_keys == ["BE_BTECH", "ME_MTECH", "PHD"]
    # B.E./B.Tech isn't in the offered list, so only the higher ones show.
    assert result.offered_keys == ["ME_MTECH", "PHD"]


def test_masters_mandatory_never_offers_undergraduate_degrees() -> None:
    # Example B: M.Tech mandatory — the AI wrongly listing B.Tech too is
    # dropped by the minimum-level rule.
    result = normalize(
        jd(minimum_level="MASTERS", accepted_qualification_keys=["ME_MTECH", "BE_BTECH"]), "h"
    )
    assert result.restricted
    assert "BE_BTECH" not in result.eligible_keys
    assert result.eligible_keys == ["ME_MTECH", "PHD"]
    assert result.offered_keys == ["ME_MTECH", "PHD"]


def test_doctorate_mandatory_offers_only_the_phd() -> None:
    result = normalize(jd(minimum_level="DOCTORATE", accepted_qualification_keys=["PHD"]), "h")
    assert result.offered_keys == ["PHD"]
    assert not result.allows("ME_MTECH")
    assert result.summary == (
        "Minimum qualification: doctorate. Eligible qualifications for this role: Ph.D."
    )


def test_any_bachelors_degree_restricts_by_level_only() -> None:
    # Example C: "a bachelor's degree" with no particular degree named.
    result = normalize(jd(minimum_level="BACHELORS"), "h")
    assert result.restricted
    assert "DIPLOMA_ENGINEERING" not in result.eligible_keys
    assert {"BE_BTECH", "BSC", "BACHELORS_OTHER", "ME_MTECH", "PHD"} <= set(result.eligible_keys)


def test_mandatory_branch_is_kept() -> None:
    # Example D: a specific branch.
    result = normalize(
        jd(
            accepted_qualification_keys=["BE_BTECH"],
            fields_of_study=["Mechanical Engineering"],
            field_mandatory=True,
        ),
        "h",
    )
    assert result.field_mandatory and result.fields_of_study == ["Mechanical Engineering"]
    assert "Required field: Mechanical Engineering" in result.summary


def test_exact_degree_only_adds_no_higher_qualifications() -> None:
    result = normalize(
        jd(accepted_qualification_keys=["BE_BTECH"], higher_qualifications_accepted=False), "h"
    )
    assert result.eligible_keys == ["BE_BTECH"]
    # "B.Tech only": nothing the form offers is eligible, so nothing is offered.
    assert result.offered_keys == []
    assert not result.allows("ME_MTECH")
    assert "can't be selected in the online application" in result.summary


@pytest.mark.parametrize(
    "analysis",
    [
        jd(requirement_strength="PREFERRED", accepted_qualification_keys=["ME_MTECH"]),
        jd(accepted_qualification_keys=["ME_MTECH"], confidence=0.4),
        jd(determinable=False, requirement_strength="NOT_STATED"),
        jd(accepted_qualification_keys=["NOT_A_REAL_KEY"]),
    ],
)
def test_preferred_ambiguous_or_unknown_never_restricts(analysis: JobQualificationAnalysis) -> None:
    result = normalize(analysis, "h")
    assert not result.restricted
    assert result.eligible_keys == [o.key for o in catalog.CATALOG]
    assert result.offered_keys == ["ME_MTECH", "PHD"]


def test_default_list_is_only_mtech_and_phd() -> None:
    open_job = job_qualification_service.unrestricted("h")
    assert [(o.key, o.label) for o in application_draft_service.offered_options(open_job)] == [
        ("ME_MTECH", "M.Tech"),
        ("PHD", "Ph.D."),
    ]
    for key in ("BE_BTECH", "BSC", "BCA", "MSC", "MCA", "BACHELORS_OTHER", "MASTERS_OTHER"):
        assert not open_job.allows(key)
    assert open_job.summary == (
        "Select your highest qualification. Accepted qualifications: M.Tech, Ph.D."
    )


def test_a_lower_jd_minimum_is_not_stated_above_the_offered_options() -> None:
    # "Bachelor's degree" JD: M.Tech/Ph.D. are offered; saying "minimum:
    # bachelor's" next to a dropdown without one would contradict it.
    result = normalize(jd(minimum_level="BACHELORS"), "h")
    assert result.offered_keys == ["ME_MTECH", "PHD"]
    assert result.summary == "Eligible qualifications for this role: M.Tech, Ph.D."


# --- Unit: document outcome rules ------------------------------------------------

_OPEN = QualificationRequirements(
    source_hash="h", restricted=False, eligible_keys=[o.key for o in catalog.CATALOG]
)


def _evaluate(analysis: AcademicDocumentAnalysis, key: str = "ME_MTECH", **kwargs: Any):
    return document_check.evaluate(
        analysis,
        selected=catalog.BY_KEY[key],
        selected_branch=kwargs.get("branch", "Computer Science"),
        requirements=kwargs.get("requirements", _OPEN),
        candidate_name=kwargs.get("name", "Jane Candidate"),
    )


def test_consistent_document_matches_without_claiming_authenticity() -> None:
    result = _evaluate(doc())
    assert result.outcome == EducationVerificationOutcome.MATCH
    assert "genuine" not in result.message.lower() and "appears consistent" in result.message


def test_btech_record_for_selected_mtech_is_a_clear_mismatch() -> None:
    result = _evaluate(doc(**BTECH_RECORD), key="ME_MTECH")
    assert result.outcome == EducationVerificationOutcome.MISMATCH
    assert result.reason_code == "level_mismatch"
    assert result.message == (
        "The qualification selected does not appear to match the uploaded document. You "
        "selected M.Tech, but the uploaded document appears to be a B.E. / B.Tech "
        "academic record. Please upload the correct document."
    )


def test_school_record_is_a_mismatch() -> None:
    result = _evaluate(doc(detected_level="SECONDARY", detected_qualification_key="NONE"))
    assert result.outcome == EducationVerificationOutcome.MISMATCH
    assert "10th-standard" in result.message


@pytest.mark.parametrize(
    ("analysis", "reason"),
    [
        (doc(legible=False), "illegible"),
        (doc(confidence=0.3), "low_confidence"),
        (doc(detected_level="UNKNOWN"), "low_confidence"),
        (
            doc(is_academic_document=False, document_type="NOT_ACADEMIC", confidence=0.3),
            "low_confidence",
        ),
        (doc(name_on_document="Rahul Verma"), "name_mismatch"),
    ],
)
def test_uncertainty_is_unclear_never_a_rejection(
    analysis: AcademicDocumentAnalysis, reason: str
) -> None:
    result = _evaluate(analysis)
    assert result.outcome == EducationVerificationOutcome.UNCLEAR
    assert result.reason_code == reason


def test_confident_non_academic_upload_is_a_mismatch() -> None:
    result = _evaluate(doc(is_academic_document=False, document_type="NOT_ACADEMIC"))
    assert result.outcome == EducationVerificationOutcome.MISMATCH
    assert result.reason_code == "not_academic"


def test_branch_mismatch_and_ineligible_selection() -> None:
    assert _evaluate(doc(branch_matches_selected="NO")).reason_code == "branch_mismatch"
    restricted = QualificationRequirements(source_hash="h", restricted=True, eligible_keys=["PHD"])
    assert _evaluate(doc(), requirements=restricted).reason_code == "not_eligible"
    # The default list is enforced too, not only a restricting JD.
    btech = _evaluate(doc(**BTECH_RECORD), key="BE_BTECH")
    assert btech.reason_code == "not_eligible"


def test_name_comparison_tolerates_initials_and_order() -> None:
    assert not document_check.names_conflict("CANDIDATE JANE", "Jane Candidate")
    assert not document_check.names_conflict("J. Candidate", "Jane Candidate")
    assert not document_check.names_conflict("", "Jane Candidate")
    assert document_check.names_conflict("Rahul Verma", "Jane Candidate")


# --- API: qualification options ----------------------------------------------------


async def test_options_follow_the_jd_and_are_cached_per_jd_version(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    ctx = await _setup(client, "flow-options")
    fake_ai.job_result = jd(minimum_level="MASTERS", accepted_qualification_keys=["ME_MTECH"])

    first = await client.get(f"{ctx['base']}/qualification-options")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["restricted"] is True
    assert [o["key"] for o in body["options"]] == ["ME_MTECH", "PHD"]
    assert body["max_document_size_mb"] == 5
    await client.get(f"{ctx['base']}/qualification-options")
    assert fake_ai.job_calls == 1  # cached

    # The recruiter edits the JD: recomputed under the new text.
    fake_ai.job_result = jd(accepted_qualification_keys=["BE_BTECH"])
    edited = await client.patch(
        f"/api/v1/recruiter/jobs/{ctx['job']['id']}",
        json={"description": "B.E./B.Tech in any engineering branch."},
        headers=ctx["headers"],
    )
    assert edited.status_code == 200, edited.text
    again = await client.get(f"{ctx['base']}/qualification-options")
    assert fake_ai.job_calls == 2
    assert [o["key"] for o in again.json()["options"]] == ["ME_MTECH", "PHD"]


async def test_ai_outage_offers_the_default_list(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    ctx = await _setup(client, "flow-options-down")
    fake_ai.job_result = AIProviderTimeoutError("timed out")
    body = (await client.get(f"{ctx['base']}/qualification-options")).json()
    assert body["restricted"] is False
    assert [(o["key"], o["label"]) for o in body["options"]] == [
        ("ME_MTECH", "M.Tech"),
        ("PHD", "Ph.D."),
    ]
    assert body["summary"] == (
        "Select your highest qualification. Accepted qualifications: M.Tech, Ph.D."
    )


# --- API: draft access ---------------------------------------------------------------


async def test_draft_requires_a_verified_token(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    ctx = await _setup(client, "flow-auth")
    response = await client.post(
        f"{ctx['base']}/apply/draft",
        json={
            "email": "nobody@example.com",
            "email_verification_token": "forged-token",
            "full_name": "No Body",
            "phone": "+919876543210",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "email_not_verified"


async def test_another_organizations_token_opens_nothing(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    a = await _setup(client, "flow-tenant-a")
    b = await _setup(client, "flow-tenant-b")
    auth = await _start(client, a, "tenant@example.com")
    leaked = await client.post(f"{b['base']}/apply/draft/view", json=auth)
    assert leaked.status_code == 422
    assert leaked.json()["error"]["code"] == "email_not_verified"


async def test_start_is_idempotent_and_returns_saved_progress(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI, db_session: AsyncSession
) -> None:
    ctx = await _setup(client, "flow-idempotent")
    auth = await _start(client, ctx, "same@example.com")
    again = await client.post(
        f"{ctx['base']}/apply/draft",
        json={**auth, "full_name": "Jane Q Candidate", "phone": phone_for("same@example.com")},
    )
    assert again.status_code == 200
    view = again.json()
    assert view["full_name"] == "Jane Q Candidate"
    assert view["background"]["place_of_birth"] == "Chennai"
    async with rls_bypass(db_session):
        drafts = (
            await db_session.scalars(
                select(ApplicationDraft).where(ApplicationDraft.email == "same@example.com")
            )
        ).all()
    assert len(drafts) == 1


async def test_background_is_validated_server_side(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    ctx = await _setup(client, "flow-background")
    auth = await _start(client, ctx, "bg@example.com")
    response = await client.put(
        f"{ctx['base']}/apply/draft/background",
        json={**auth, "background": {**_BACKGROUND, "candidate_type": "EXPERIENCED"}},
    )
    assert response.status_code == 422
    assert "Experienced candidates must provide" in response.text


# --- API: education + document check ------------------------------------------------


async def test_ineligible_qualification_is_refused(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    ctx = await _setup(client, "flow-ineligible")
    fake_ai.job_result = jd(minimum_level="MASTERS", accepted_qualification_keys=["ME_MTECH"])
    auth = await _start(client, ctx, "ineligible@example.com")
    response = await _save_education(client, ctx, auth, qualification_key="BE_BTECH")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "qualification_not_eligible"


async def test_default_list_is_enforced_server_side(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    # A JD with no qualification rule: a forged request for anything outside
    # the default list (M.Tech, Ph.D.) is refused.
    ctx = await _setup(client, "flow-default-list")
    auth = await _start(client, ctx, "defaultlist@example.com")
    for key in ("BE_BTECH", "BSC", "BCA", "MSC", "MCA", "BACHELORS_OTHER", "NOT_A_KEY"):
        response = await _save_education(
            client, ctx, auth, qualification_key=key, degree_name="Anything"
        )
        assert response.status_code == 422, key
        assert response.json()["error"]["code"] == "qualification_not_eligible", key
    for key in ("PHD", "ME_MTECH"):
        assert (await _save_education(client, ctx, auth, qualification_key=key)).status_code == 200


async def test_submit_revalidates_a_tampered_qualification(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI, db_session: AsyncSession
) -> None:
    # Even with the JD unchanged, submit re-checks the stored selection.
    ctx = await _setup(client, "flow-tampered")
    auth = await _start(client, ctx, "tampered@example.com")
    await _save_education(client, ctx, auth)
    await _upload(client, ctx, auth)
    async with rls_bypass(db_session):
        draft = await db_session.scalar(
            select(ApplicationDraft).where(ApplicationDraft.email == "tampered@example.com")
        )
        assert draft is not None and draft.education is not None
        draft.education = {**draft.education, "qualification_key": "BE_BTECH"}
        await db_session.commit()
    response = await _submit(client, ctx, auth)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "education_recheck_required"


async def test_match_then_submit_records_a_verified_application(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI, db_session: AsyncSession
) -> None:
    ctx = await _setup(client, "flow-match")
    auth = await _start(client, ctx, "match@example.com")
    assert (await _save_education(client, ctx, auth)).status_code == 200
    uploaded = await _upload(client, ctx, auth)
    assert uploaded.status_code == 200, uploaded.text
    check = uploaded.json()["education_check"]
    assert check["status"] == "VERIFIED" and check["can_continue"] is True
    assert "appears consistent" in check["message"]
    # Nothing about the model, prompt or extracted contents reaches the client.
    assert "fake" not in uploaded.text and "Anna University. Master" not in uploaded.text
    assert fake_ai.document_calls[0]["content_type"] == "application/pdf"

    submitted = await _submit(client, ctx, auth)
    assert submitted.status_code == 201, submitted.text

    async with rls_bypass(db_session):
        application = await db_session.get(Application, submitted.json()["id"])
        assert application is not None
        assert application.education_verification_status == ApplicationEducationStatus.VERIFIED
        candidate = await db_session.get(Candidate, application.candidate_id)
        assert candidate is not None
        assert candidate.qualification == "M.Tech in Computer Science"
        assert candidate.institution_name == "Anna University"
        assert candidate.qualification_year == 2020
        document = await db_session.scalar(
            select(AcademicDocument).where(AcademicDocument.application_id == application.id)
        )
        assert document is not None and document.candidate_id == candidate.id
        runs = (
            await db_session.scalars(
                select(EducationVerificationRun).where(
                    EducationVerificationRun.application_id == application.id
                )
            )
        ).all()
        assert [run.outcome for run in runs] == [EducationVerificationOutcome.MATCH]

    # Recruiter view of it.
    education = await client.get(
        f"/api/v1/recruiter/applications/{application.id}/education", headers=ctx["headers"]
    )
    assert education.status_code == 200, education.text
    assert education.json()["education_verification_status"] == "VERIFIED"
    assert education.json()["checks"][0]["detected_level"] == "MASTERS"
    download = await client.get(
        f"/api/v1/recruiter/applications/{application.id}/education/document",
        headers=ctx["headers"],
    )
    assert download.status_code == 200
    assert download.content.startswith(b"%PDF")

    # Another organization's staff can't see it.
    other = await bootstrap_org(client, "flow-match-other")
    hidden = await client.get(
        f"/api/v1/recruiter/applications/{application.id}/education", headers=other["headers"]
    )
    assert hidden.status_code == 404


async def test_mismatch_blocks_until_the_right_document_is_uploaded(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    ctx = await _setup(client, "flow-mismatch")
    fake_ai.job_result = jd(minimum_level="MASTERS", accepted_qualification_keys=["ME_MTECH"])
    auth = await _start(client, ctx, "mismatch@example.com")
    assert (
        await _save_education(client, ctx, auth, qualification_key="ME_MTECH", branch="CSE")
    ).status_code == 200

    fake_ai.document_results = [doc(**BTECH_RECORD)]  # a B.Tech marks card
    first = (await _upload(client, ctx, auth)).json()["education_check"]
    assert first["status"] == "MISMATCH"
    assert first["can_continue"] is False and first["can_request_manual_review"] is False
    assert "You selected M.Tech" in first["message"]

    refused = await client.post(f"{ctx['base']}/apply/draft/education/manual-review", json=auth)
    assert refused.status_code == 422
    blocked = await _submit(client, ctx, auth)
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "education_not_verified"

    fake_ai.document_results = [
        doc(detected_level="MASTERS", detected_qualification_key="ME_MTECH")
    ]
    replaced = (await _upload(client, ctx, auth)).json()
    assert replaced["education_check"]["status"] == "VERIFIED"
    assert (await _submit(client, ctx, auth)).status_code == 201


async def test_unclear_document_can_go_to_manual_review(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI, db_session: AsyncSession
) -> None:
    ctx = await _setup(client, "flow-unclear")
    auth = await _start(client, ctx, "unclear@example.com")
    await _save_education(client, ctx, auth)
    fake_ai.document_results = [doc(legible=False)]
    check = (await _upload(client, ctx, auth)).json()["education_check"]
    assert check["status"] == "UNCLEAR" and check["can_request_manual_review"] is True

    reviewed = await client.post(f"{ctx['base']}/apply/draft/education/manual-review", json=auth)
    assert reviewed.json()["education_check"]["status"] == "MANUAL_REVIEW"
    submitted = await _submit(client, ctx, auth)
    assert submitted.status_code == 201, submitted.text
    async with rls_bypass(db_session):
        application = await db_session.get(Application, submitted.json()["id"])
        assert application is not None
        assert application.education_verification_status == ApplicationEducationStatus.MANUAL_REVIEW
        flagged = await db_session.scalar(
            select(Activity).where(
                Activity.action == "EDUCATION_MANUAL_REVIEW_REQUIRED",
                Activity.entity_id == application.id,
            )
        )
        assert flagged is not None


async def test_ai_failure_keeps_the_document_and_allows_retry_then_manual_review(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    ctx = await _setup(client, "flow-failure")
    auth = await _start(client, ctx, "failure@example.com")
    await _save_education(client, ctx, auth)
    fake_ai.document_results = [AIProviderTimeoutError("t"), AIProviderTimeoutError("t"), doc()]

    first = (await _upload(client, ctx, auth)).json()
    assert first["education_check"]["status"] == "FAILED"
    assert first["education_check"]["can_retry"] is True
    assert first["education_check"]["can_request_manual_review"] is False
    assert first["document"]["filename"] == "mtech-marks-card.pdf"  # not lost

    second = (await client.post(f"{ctx['base']}/apply/draft/education/recheck", json=auth)).json()
    assert second["education_check"]["status"] == "FAILED"
    assert second["education_check"]["can_request_manual_review"] is True

    third = (await client.post(f"{ctx['base']}/apply/draft/education/recheck", json=auth)).json()
    assert third["education_check"]["status"] == "VERIFIED"


async def test_changing_the_qualification_resets_the_check(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    ctx = await _setup(client, "flow-reset")
    auth = await _start(client, ctx, "reset@example.com")
    await _save_education(client, ctx, auth)
    assert (await _upload(client, ctx, auth)).json()["education_check"]["status"] == "VERIFIED"

    changed = await _save_education(client, ctx, auth, qualification_key="PHD", branch="Physics")
    assert changed.json()["education_check"]["status"] == "NOT_STARTED"
    assert changed.json()["document"] is not None  # kept, re-checkable
    assert (await _submit(client, ctx, auth)).status_code == 422

    # Same details again (e.g. only the year corrected): no reset.
    await _save_education(client, ctx, auth, qualification_key="PHD", branch="Physics")
    fake_ai.document_results = [
        doc(detected_level="DOCTORATE", detected_qualification_key="PHD", branch_text="Physics")
    ]
    rechecked = await client.post(f"{ctx['base']}/apply/draft/education/recheck", json=auth)
    assert rechecked.json()["education_check"]["status"] == "VERIFIED"
    kept = await _save_education(
        client, ctx, auth, qualification_key="PHD", branch="Physics", completion_year=2021
    )
    assert kept.json()["education_check"]["status"] == "VERIFIED"


async def test_jd_change_that_excludes_the_selection_requires_review(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    ctx = await _setup(client, "flow-jd-change")
    auth = await _start(client, ctx, "jdchange@example.com")
    await _save_education(client, ctx, auth)
    await _upload(client, ctx, auth)

    fake_ai.job_result = jd(minimum_level="DOCTORATE", accepted_qualification_keys=["PHD"])
    await client.patch(
        f"/api/v1/recruiter/jobs/{ctx['job']['id']}",
        json={"description": "Ph.D. mandatory."},
        headers=ctx["headers"],
    )
    response = await _submit(client, ctx, auth)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "education_recheck_required"


async def test_invalid_documents_are_rejected_before_any_ai_call(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    ctx = await _setup(client, "flow-files")
    auth = await _start(client, ctx, "files@example.com")
    await _save_education(client, ctx, auth)
    renamed = await _upload(
        client, ctx, auth, ("card.pdf", b"MZ\x90\x00 not a pdf", "application/pdf")
    )
    assert renamed.status_code == 400
    assert renamed.json()["error"]["code"] == "invalid_file_type"
    wrong_type = await _upload(client, ctx, auth, ("card.docx", b"PK\x03\x04", "application/zip"))
    assert wrong_type.json()["error"]["code"] == "invalid_file_type"
    big = b"%PDF-1.4" + b"0" * (5 * 1024 * 1024 + 10)
    too_large = await _upload(client, ctx, auth, ("card.pdf", big, "application/pdf"))
    assert too_large.json()["error"]["code"] == "file_too_large"
    assert fake_ai.document_calls == []


async def test_check_attempts_are_capped(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI, monkeypatch
) -> None:
    from app.core.config import get_settings

    patched = get_settings().model_copy(update={"max_education_checks_per_draft": 2})
    monkeypatch.setattr(application_draft_service, "get_settings", lambda: patched)
    ctx = await _setup(client, "flow-cap")
    auth = await _start(client, ctx, "cap@example.com")
    await _save_education(client, ctx, auth, qualification_key="ME_MTECH", branch="CSE")
    await _upload(client, ctx, auth)
    await _upload(client, ctx, auth)
    third = await _upload(client, ctx, auth)
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "education_check_limit"


# --- API: submission ------------------------------------------------------------------


async def test_submission_needs_every_step(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    ctx = await _setup(client, "flow-incomplete")
    token = await verify_email(client, ctx["slug"], "incomplete@example.com")
    auth = {"email": "incomplete@example.com", "email_verification_token": token}
    await client.post(
        f"{ctx['base']}/apply/draft",
        json={**auth, "full_name": "In Complete", "phone": phone_for("incomplete@example.com")},
    )
    response = await _submit(client, ctx, auth)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "background_required"


async def test_repeated_submit_creates_one_application(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI, db_session: AsyncSession
) -> None:
    ctx = await _setup(client, "flow-double")
    auth = await _start(client, ctx, "double@example.com")
    await _save_education(client, ctx, auth)
    await _upload(client, ctx, auth)
    assert (await _submit(client, ctx, auth)).status_code == 201
    again = await _submit(client, ctx, auth)
    assert again.status_code in (409, 422)
    async with rls_bypass(db_session):
        count = len(
            (
                await db_session.scalars(
                    select(Application.id)
                    .join(Candidate, Candidate.id == Application.candidate_id)
                    .where(Candidate.email == "double@example.com")
                )
            ).all()
        )
    assert count == 1


async def test_full_flow_through_the_shared_helper_without_any_ai(
    client: AsyncClient, super_admin: User
) -> None:
    """No provider configured at all: the check says "not available" and the
    candidate continues to manual review — the real degraded path."""
    ctx = await _setup(client, "flow-no-ai")
    response = await apply_publicly(client, ctx["slug"], ctx["job"]["id"], email="noai@example.com")
    assert response.status_code == 201, response.text


async def test_expired_drafts_are_purged_with_their_document(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI, db_session: AsyncSession
) -> None:
    ctx = await _setup(client, "flow-purge")
    auth = await _start(client, ctx, "purge@example.com")
    await _save_education(client, ctx, auth)
    await _upload(client, ctx, auth)
    async with rls_bypass(db_session):
        draft = await db_session.scalar(
            select(ApplicationDraft).where(ApplicationDraft.email == "purge@example.com")
        )
        assert draft is not None
        removed = await application_draft_service.purge_expired_drafts(
            db_session,
            now=datetime.now(UTC) + timedelta(days=30),
            organization_id=draft.organization_id,
        )
        assert removed >= 1
        remaining = await db_session.scalar(
            select(AcademicDocument.id)
            .join(ApplicationDraft, ApplicationDraft.id == AcademicDocument.draft_id, isouter=True)
            .where(ApplicationDraft.email == "purge@example.com")
        )
        assert remaining is None


# --- Candidate-facing email branding ------------------------------------------------


async def test_candidate_emails_carry_the_organizations_brand_not_sigvitas(
    client: AsyncClient, super_admin: User, recording_email, fake_ai: FakeEducationAI
) -> None:
    """The verification code and the welcome email speak in the
    organization's own name (the careers organization is "Zodiac Patent")."""
    from app.core.config import get_settings

    ctx = await bootstrap_org(client, "flow-brand", name="Zodiac Patent")
    job = await open_job(client, ctx, title="Patent Engineer")
    ctx.update(job=job, base=f"/api/v1/public/organizations/flow-brand/jobs/{job['id']}")
    requested = await client.post(
        "/api/v1/public/organizations/flow-brand/email-verification/request",
        json={"email": "brand@example.com"},
    )
    assert requested.status_code == 202
    (code_email,) = recording_email.sent
    assert code_email.subject.startswith("Your Zodiac Patent verification code: ")
    assert "Zodiac Patent" in code_email.text and "Zodiac Patent" in code_email.html

    auth = await _start(client, ctx, "brand2@example.com")
    await _save_education(client, ctx, auth)
    await _upload(client, ctx, auth)
    assert (await _submit(client, ctx, auth)).status_code == 201
    welcome = recording_email.sent[-1]
    assert "Zodiac Patent" in welcome.subject
    assert "Recruitment Team\nZodiac Patent" in welcome.text
    for sent in recording_email.sent:
        for part in (sent.subject, sent.text, sent.html):
            assert "sigvitas" not in part.lower()
    # The configured sender display name (the address itself is the
    # deployment's, see docs/deployment.md).
    assert get_settings().smtp_from_name == "Zodiac Patent Recruitment"


# --- Ambiguous requirements: general list + eligibility review flag ----------------


@pytest.mark.parametrize(
    ("analysis", "flagged"),
    [
        (jd(accepted_qualification_keys=["ME_MTECH"], confidence=0.4), True),
        (jd(accepted_qualification_keys=["NOT_A_REAL_KEY"]), True),
        (jd(requirement_strength="PREFERRED", accepted_qualification_keys=["ME_MTECH"]), False),
        (jd(determinable=False, requirement_strength="NOT_STATED"), False),
        (jd(accepted_qualification_keys=["BE_BTECH"]), False),
    ],
)
def test_only_ambiguous_requirements_are_flagged(
    analysis: JobQualificationAnalysis, flagged: bool
) -> None:
    assert normalize(analysis, "h").needs_review is flagged


def test_summary_states_the_minimum_qualification() -> None:
    result = normalize(
        jd(minimum_level="MASTERS", accepted_qualification_keys=["ME_MTECH"]), "h"
    )
    assert result.summary == (
        "Minimum qualification: master's degree. "
        "Eligible qualifications for this role: M.Tech, Ph.D."
    )


async def test_ai_outage_on_the_jd_flags_the_application_for_eligibility_review(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI, db_session: AsyncSession
) -> None:
    ctx = await _setup(client, "flow-ambiguous")
    fake_ai.job_result = AIProviderTimeoutError("timed out")
    auth = await _start(client, ctx, "ambiguous@example.com")
    assert (await _save_education(client, ctx, auth)).status_code == 200
    await _upload(client, ctx, auth)
    submitted = await _submit(client, ctx, auth)
    assert submitted.status_code == 201, submitted.text
    detail = await client.get(
        f"/api/v1/recruiter/applications/{submitted.json()['id']}", headers=ctx["headers"]
    )
    assert detail.json()["eligibility_review_required"] is True
    async with rls_bypass(db_session):
        flagged = await db_session.scalar(
            select(Activity).where(
                Activity.action == "QUALIFICATION_REVIEW_REQUIRED",
                Activity.entity_id == submitted.json()["id"],
            )
        )
    assert flagged is not None


async def test_clear_requirements_do_not_flag_the_application(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    ctx = await _setup(client, "flow-clear")
    fake_ai.job_result = jd(accepted_qualification_keys=["BE_BTECH"])
    auth = await _start(client, ctx, "clear@example.com")
    await _save_education(client, ctx, auth)
    await _upload(client, ctx, auth)
    submitted = await _submit(client, ctx, auth)
    detail = await client.get(
        f"/api/v1/recruiter/applications/{submitted.json()['id']}", headers=ctx["headers"]
    )
    assert detail.json()["eligibility_review_required"] is False
