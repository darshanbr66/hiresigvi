"""The progressive self-service apply flow (app/services/application_draft_service.py):
JD-derived qualification options, the draft's server-side state, the academic
document check and its outcomes, manual review, and the final submission.

The AI is replaced by `FakeEducationAI` — the only stand-in here (tests must
never reach a real model, see conftest.py). Everything else is real: HTTP,
the database (RLS included), storage and the email verification flow.
"""

import re
import zlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import rls_bypass
from app.integrations.ai import (
    AIProviderError,
    AIProviderNotConfiguredError,
    AIProviderTimeoutError,
)
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
    EducationCheckStatus,
    EducationVerificationOutcome,
    EducationVerificationRun,
)
from app.models.candidate import Candidate
from app.models.user import User
from app.services import application_draft_service
from app.services.education import (
    catalog,
    document_check,
    job_qualification_service,
    specializations,
)
from app.services.education.job_qualification_service import (
    QualificationRequirements,
    normalize,
)
from tests.conftest import SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD, login, make_minimal_pdf
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

#: `_save_education` overrides for a different specialization (and category).
VLSI: dict[str, Any] = {
    "specialization_category": "Electronics & Electrical",
    "specialization": "VLSI Design",
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
            {
                "selected": selected.key,
                "selected_branch": selected_branch,
                "content_type": content_type,
                "size": len(content),
            }
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
        jd(
            accepted_qualification_keys=["BE_BTECH"],
            higher_qualifications_accepted=False,
            evidence="Freshers with B.Tech only.",
        ),
        "h",
    )
    assert result.eligible_keys == ["BE_BTECH"]
    # "B.Tech only": nothing the form offers is eligible, so nothing is offered.
    assert result.offered_keys == []
    assert not result.allows("ME_MTECH")
    assert "can't be selected in the online application" in result.summary


@pytest.mark.parametrize(
    "evidence",
    [
        "M.Tech in Computer Science is required.",
        "Minimum qualification: M.Tech / M.E. in ECE.",
        "",
    ],
)
def test_naming_a_masters_degree_keeps_the_phd_eligible(evidence: str) -> None:
    # The model flagged "exact degrees only", but the JD never says so: a
    # Ph.D. is a higher qualification and stays eligible.
    result = normalize(
        jd(
            minimum_level="MASTERS",
            accepted_qualification_keys=["ME_MTECH"],
            higher_qualifications_accepted=False,
            evidence=evidence,
        ),
        "h",
    )
    assert result.restricted
    assert result.offered_keys == ["ME_MTECH", "PHD"]
    assert result.allows("PHD")
    assert "M.Tech, Ph.D." in result.summary


@pytest.mark.parametrize(
    "evidence",
    [
        "M.Tech only.",
        "Strictly M.Tech graduates.",
        "Ph.D. holders need not apply.",
        "M.Tech required; doctorates will not be considered.",
        "No Ph.D. candidates.",
    ],
)
def test_an_explicit_exact_degree_restriction_excludes_the_phd(evidence: str) -> None:
    result = normalize(
        jd(
            minimum_level="MASTERS",
            accepted_qualification_keys=["ME_MTECH"],
            higher_qualifications_accepted=False,
            evidence=evidence,
        ),
        "h",
    )
    assert result.offered_keys == ["ME_MTECH"]
    assert not result.allows("PHD")


async def test_options_and_validation_offer_the_phd_for_an_mtech_jd(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    ctx = await _setup(client, "flow-mtech-phd")
    fake_ai.job_result = jd(
        minimum_level="MASTERS",
        accepted_qualification_keys=["ME_MTECH"],
        higher_qualifications_accepted=False,
        evidence="M.Tech in Computer Science is required.",
    )
    options = (await client.get(f"{ctx['base']}/qualification-options")).json()
    assert [option["key"] for option in options["options"]] == ["ME_MTECH", "PHD"]
    # The server accepts exactly what the API offered.
    auth = await _start(client, ctx, "mtech-phd@example.com")
    for key in ("ME_MTECH", "PHD"):
        saved = await _save_education(client, ctx, auth, qualification_key=key)
        assert saved.status_code == 200, saved.text


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
        assert candidate.qualification == "M.Tech in Computer Science and Engineering"
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


async def test_mismatch_keeps_the_document_and_goes_to_manual_review(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI, db_session: AsyncSession
) -> None:
    """AI MISMATCH: the document is kept, flagged for manual verification,
    the outcome and reason recorded — and the candidate continues."""
    ctx = await _setup(client, "flow-mismatch")
    fake_ai.job_result = jd(minimum_level="MASTERS", accepted_qualification_keys=["ME_MTECH"])
    auth = await _start(client, ctx, "mismatch@example.com")
    assert (
        await _save_education(client, ctx, auth, qualification_key="ME_MTECH")
    ).status_code == 200

    fake_ai.document_results = [doc(**BTECH_RECORD)]  # a B.Tech marks card
    uploaded = await _upload(client, ctx, auth)
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    check = body["education_check"]
    assert check["status"] == "MANUAL_REVIEW"
    assert check["can_continue"] is True
    assert check["message"] == (
        "Your document has been uploaded successfully. Our recruitment team will review it "
        "manually. You can continue with your application."
    )
    assert body["document"]["filename"] == "mtech-marks-card.pdf"  # retained
    # The view is the same when the draft is reopened.
    reopened = await client.post(f"{ctx['base']}/apply/draft/view", json=auth)
    assert reopened.json()["education_check"] == check

    submitted = await _submit(client, ctx, auth)
    assert submitted.status_code == 201, submitted.text
    async with rls_bypass(db_session):
        application = await db_session.get(Application, submitted.json()["id"])
        assert application is not None
        assert application.education_verification_status == ApplicationEducationStatus.MANUAL_REVIEW
        (run,) = (
            await db_session.scalars(
                select(EducationVerificationRun).where(
                    EducationVerificationRun.application_id == application.id
                )
            )
        ).all()
        assert run.outcome == EducationVerificationOutcome.MISMATCH
        assert run.reason_code == "level_mismatch"
        assert run.analysis is not None and run.analysis["detected_level"] == "BACHELORS"

    education = (
        await client.get(
            f"/api/v1/recruiter/applications/{application.id}/education", headers=ctx["headers"]
        )
    ).json()
    assert education["education_verification_status"] == "MANUAL_REVIEW"
    assert education["manual_review_reason"].startswith(
        "AI identified a possible document mismatch"
    )
    assert education["checks"][0]["outcome"] == "MISMATCH"
    documents = (
        await client.get(
            f"/api/v1/recruiter/applications/{application.id}/documents", headers=ctx["headers"]
        )
    ).json()["documents"]
    academic = next(d for d in documents if d["kind"] == "academic-document")
    assert academic["verification"] == "MANUAL_VERIFICATION_REQUIRED"
    assert academic["verification_reason"] == education["manual_review_reason"]
    assert academic["available"] is True


async def test_unclear_document_goes_to_manual_review_without_asking(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI, db_session: AsyncSession
) -> None:
    ctx = await _setup(client, "flow-unclear")
    auth = await _start(client, ctx, "unclear@example.com")
    await _save_education(client, ctx, auth)
    fake_ai.document_results = [doc(legible=False)]
    check = (await _upload(client, ctx, auth)).json()["education_check"]
    assert check["status"] == "MANUAL_REVIEW" and check["can_continue"] is True
    assert "review it manually" in check["message"]
    assert check["can_request_manual_review"] is False  # nothing left to ask for

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
    education = (
        await client.get(
            f"/api/v1/recruiter/applications/{application.id}/education", headers=ctx["headers"]
        )
    ).json()
    assert education["manual_review_reason"] == (
        "AI could not confidently classify the document: the document could not be read clearly."
    )


@pytest.mark.parametrize(
    ("failure", "reason_code", "recruiter_reason"),
    [
        (AIProviderTimeoutError("timed out"), "ai_failed", "AI classification failed."),
        (AIProviderError("provider returned 503"), "ai_failed", "AI classification failed."),
        # An invalid / unparseable answer surfaces as a non-provider error.
        (ValueError("model answered with invalid JSON"), "ai_failed", "AI classification failed."),
        (
            AIProviderNotConfiguredError("no key"),
            "ai_not_configured",
            "AI verification service was unavailable.",
        ),
    ],
    ids=["timeout", "provider-error", "invalid-response", "unavailable"],
)
async def test_ai_failure_keeps_the_document_and_lets_the_candidate_continue(
    client: AsyncClient,
    super_admin: User,
    fake_ai: FakeEducationAI,
    db_session: AsyncSession,
    failure: Exception,
    reason_code: str,
    recruiter_reason: str,
) -> None:
    """A failed *check* is never a failed upload: the stored document goes
    to manual review, the failure is recorded safely, and the candidate
    continues. Nothing about the error reaches the candidate or HR."""
    slug = f"flow-failure-{reason_code}-{type(failure).__name__}".lower().replace("_", "-")[:60]
    ctx = await _setup(client, slug)
    email = f"{slug}@example.com"
    auth = await _start(client, ctx, email)
    await _save_education(client, ctx, auth)
    fake_ai.document_results = [failure]

    uploaded = await _upload(client, ctx, auth)
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    assert body["education_check"]["status"] == "MANUAL_REVIEW"
    assert body["education_check"]["can_continue"] is True
    assert "uploaded successfully" in body["education_check"]["message"]
    assert body["document"]["filename"] == "mtech-marks-card.pdf"  # not lost
    assert str(failure) not in uploaded.text

    submitted = await _submit(client, ctx, auth)
    assert submitted.status_code == 201, submitted.text
    async with rls_bypass(db_session):
        run = await db_session.scalar(
            select(EducationVerificationRun).where(
                EducationVerificationRun.application_id == submitted.json()["id"]
            )
        )
        assert run is not None
        assert run.outcome == EducationVerificationOutcome.FAILED
        assert run.reason_code == reason_code
        assert run.error_message  # kept internally for diagnosis only
    application_id = submitted.json()["id"]
    education = await client.get(
        f"/api/v1/recruiter/applications/{application_id}/education", headers=ctx["headers"]
    )
    assert education.json()["manual_review_reason"] == recruiter_reason
    assert str(failure) not in education.text and "error_message" not in education.text
    documents = await client.get(
        f"/api/v1/recruiter/applications/{application_id}/documents", headers=ctx["headers"]
    )
    assert str(failure) not in documents.text


async def test_a_storage_failure_is_still_a_failed_upload(
    client: AsyncClient,
    super_admin: User,
    fake_ai: FakeEducationAI,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real upload stays blocking: when the file can't be stored, the
    candidate is told so, nothing is recorded as uploaded and no AI call is
    made."""
    from app.integrations.storage import StorageError
    from app.integrations.storage.local import LocalResumeStorage

    ctx = await _setup(client, "flow-storage-failure")
    auth = await _start(client, ctx, "storage-failure@example.com")
    await _save_education(client, ctx, auth)

    async def broken_save(self: object, **kwargs: Any) -> None:
        raise StorageError("disk full")

    monkeypatch.setattr(LocalResumeStorage, "save", broken_save)
    response = await _upload(client, ctx, auth)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "storage_error"
    assert fake_ai.document_calls == []
    view = (await client.post(f"{ctx['base']}/apply/draft/view", json=auth)).json()
    assert view["document"] is None
    assert view["education_check"]["can_continue"] is False
    blocked = await _submit(client, ctx, auth)
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "academic_document_required"


async def test_a_draft_left_blocked_by_the_old_check_can_continue(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI, db_session: AsyncSession
) -> None:
    """Drafts saved while MISMATCH / UNCLEAR / FAILED still blocked (before
    this change) aren't stuck: they're offered manual review."""
    ctx = await _setup(client, "flow-legacy-blocked")
    auth = await _start(client, ctx, "legacy-blocked@example.com")
    await _save_education(client, ctx, auth)
    await _upload(client, ctx, auth)
    async with rls_bypass(db_session):
        draft = await db_session.scalar(
            select(ApplicationDraft).where(ApplicationDraft.email == "legacy-blocked@example.com")
        )
        assert draft is not None
        draft.education_status = EducationCheckStatus.MISMATCH
        await db_session.commit()

    check = (await client.post(f"{ctx['base']}/apply/draft/view", json=auth)).json()[
        "education_check"
    ]
    assert check["can_request_manual_review"] is True and check["can_continue"] is False
    reviewed = await client.post(f"{ctx['base']}/apply/draft/education/manual-review", json=auth)
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["education_check"]["status"] == "MANUAL_REVIEW"
    assert (await _submit(client, ctx, auth)).status_code == 201


async def test_changing_the_qualification_resets_the_check(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    ctx = await _setup(client, "flow-reset")
    auth = await _start(client, ctx, "reset@example.com")
    await _save_education(client, ctx, auth)
    assert (await _upload(client, ctx, auth)).json()["education_check"]["status"] == "VERIFIED"

    changed = await _save_education(client, ctx, auth, qualification_key="PHD", **VLSI)
    assert changed.json()["education_check"]["status"] == "NOT_STARTED"
    assert changed.json()["document"] is not None  # kept, re-checkable
    assert (await _submit(client, ctx, auth)).status_code == 422

    # Same details again (e.g. only the year corrected): no reset.
    await _save_education(client, ctx, auth, qualification_key="PHD", **VLSI)
    fake_ai.document_results = [
        doc(detected_level="DOCTORATE", detected_qualification_key="PHD", branch_text="VLSI Design")
    ]
    rechecked = await client.post(f"{ctx['base']}/apply/draft/education/recheck", json=auth)
    assert rechecked.json()["education_check"]["status"] == "VERIFIED"
    kept = await _save_education(
        client, ctx, auth, qualification_key="PHD", **VLSI, completion_year=2021
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


async def test_check_attempts_are_capped_without_blocking_the_upload(
    client: AsyncClient,
    super_admin: User,
    fake_ai: FakeEducationAI,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The AI budget caps AI calls, not uploads: past it the document is
    stored and goes to manual review without another AI call."""
    from app.core.config import get_settings

    patched = get_settings().model_copy(update={"max_education_checks_per_draft": 2})
    monkeypatch.setattr(application_draft_service, "get_settings", lambda: patched)
    ctx = await _setup(client, "flow-cap")
    auth = await _start(client, ctx, "cap@example.com")
    await _save_education(client, ctx, auth, qualification_key="ME_MTECH")
    fake_ai.document_results = [doc(**BTECH_RECORD), doc(**BTECH_RECORD)]
    await _upload(client, ctx, auth)
    await _upload(client, ctx, auth)
    third = await _upload(client, ctx, auth)
    assert third.status_code == 200, third.text
    assert len(fake_ai.document_calls) == 2
    assert third.json()["education_check"]["status"] == "MANUAL_REVIEW"
    assert third.json()["education_check"]["can_continue"] is True
    submitted = await _submit(client, ctx, auth)
    assert submitted.status_code == 201, submitted.text
    education = (
        await client.get(
            f"/api/v1/recruiter/applications/{submitted.json()['id']}/education",
            headers=ctx["headers"],
        )
    ).json()
    assert "check limit" in education["manual_review_reason"]


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
    result = normalize(jd(minimum_level="MASTERS", accepted_qualification_keys=["ME_MTECH"]), "h")
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


# --- Free-text specialization category + specialization ------------------------


def _error_code(response: Any) -> str:
    return response.json()["error"]["code"]


async def test_options_still_serve_the_catalogue_as_suggestions(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    """The catalogue stays available (the form offers it as typing
    suggestions); it no longer limits what can be saved."""
    ctx = await _setup(client, "flow-spec-options")
    response = await client.get(f"{ctx['base']}/qualification-options")
    assert response.status_code == 200, response.text
    served = response.json()["specialization_categories"]
    assert served == [
        {
            "key": category.key,
            "label": category.label,
            "specializations": [
                {"key": item.key, "label": item.label} for item in category.specializations
            ],
        }
        for category in specializations.CATEGORIES
    ]
    assert all(set(category) == {"key", "label", "specializations"} for category in served)


async def test_any_specialization_text_is_accepted_and_trimmed(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    ctx = await _setup(client, "flow-spec-free")
    auth = await _start(client, ctx, "free-text@example.com")
    for category, specialization in [
        (
            "Computer Science & Information Technology",
            "Artificial Intelligence and Machine Learning",
        ),
        # Values that were never in the catalogue.
        ("Quantum Technologies", "Quantum Information Science"),
        ("Design", "Human-Computer Interaction"),
        # A catalogue specialization under a different category is fine too.
        ("Mechanical & Manufacturing", "Data Science"),
    ]:
        saved = await _save_education(
            client,
            ctx,
            auth,
            specialization_category=f"  {category}\t",
            specialization=f"\n {specialization}  ",
        )
        assert saved.status_code == 200, saved.text
        education = saved.json()["education"]
        assert education["specialization_category"] == category
        assert education["specialization"] == specialization
        assert education["branch"] == specialization


async def test_category_is_required_and_specialization_optional(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    ctx = await _setup(client, "flow-spec-required")
    auth = await _start(client, ctx, "spec-required@example.com")
    category_max = specializations.CATEGORY_MAX_LENGTH
    specialization_max = specializations.SPECIALIZATION_MAX_LENGTH
    for value in (None, "", "   \t "):
        response = await _save_education(client, ctx, auth, specialization_category=value)
        assert response.status_code == 422, (value, response.text)
        assert _error_code(response) == "specialization_category_required", value
    for overrides in (
        {"specialization_category": "C" * (category_max + 1)},
        # An older client may still send a specialization: still length-checked.
        {"specialization": "S" * (specialization_max + 1)},
    ):
        response = await _save_education(client, ctx, auth, **overrides)
        assert response.status_code == 422, (overrides, response.text)
    view = await client.post(f"{ctx['base']}/apply/draft/view", json=auth)
    assert view.json()["education"] is None  # nothing invalid was stored
    # The Education step no longer asks for a specialization: absent, empty
    # or blank is accepted and stored as none.
    for value in (None, "", "  \n "):
        saved = await _save_education(client, ctx, auth, specialization=value)
        assert saved.status_code == 200, (value, saved.text)
        education = saved.json()["education"]
        assert education["specialization"] is None
        assert education["branch"] is None
    # Exactly at the limit (surrounding spaces don't count) is fine.
    at_limit = await _save_education(
        client,
        ctx,
        auth,
        specialization_category=" " + "C" * category_max + " ",
        specialization=None,
    )
    assert at_limit.status_code == 200, at_limit.text


async def test_submission_without_a_specialization(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI, db_session: AsyncSession
) -> None:
    ctx = await _setup(client, "flow-no-spec")
    auth = await _start(client, ctx, "no-spec@example.com")
    saved = await _save_education(
        client,
        ctx,
        auth,
        qualification_key="PHD",
        specialization_category="Robotics",
        specialization=None,
    )
    assert saved.status_code == 200, saved.text
    fake_ai.document_results = [
        doc(detected_level="DOCTORATE", detected_qualification_key="PHD", branch_text="Robotics")
    ]
    uploaded = await _upload(client, ctx, auth)
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["education_check"]["status"] == "VERIFIED"
    # The check still runs against the qualification; there is no branch.
    assert fake_ai.document_calls[-1]["selected"] == "PHD"
    assert fake_ai.document_calls[-1]["selected_branch"] is None

    submitted = await _submit(client, ctx, auth)
    assert submitted.status_code == 201, submitted.text
    async with rls_bypass(db_session):
        application = await db_session.get(Application, submitted.json()["id"])
        assert application is not None
        assert application.education_qualification_key == "PHD"
        assert application.education_specialization_category == "Robotics"
        assert application.education_specialization is None
    education = (
        await client.get(
            f"/api/v1/recruiter/applications/{submitted.json()['id']}/education",
            headers=ctx["headers"],
        )
    ).json()
    assert education["specialization_category"] == "Robotics"
    assert education["specialization"] is None


@pytest.mark.parametrize(
    ("category", "flagged"),
    [("Computer Science & Information Technology", False), ("Mechanical & Manufacturing", True)],
    ids=["category-matches", "category-differs"],
)
async def test_without_a_specialization_the_category_is_matched_to_the_jd_field(
    client: AsyncClient,
    super_admin: User,
    fake_ai: FakeEducationAI,
    category: str,
    flagged: bool,
) -> None:
    ctx = await _setup(client, f"flow-cat-field-{'flag' if flagged else 'ok'}")
    fake_ai.job_result = jd(
        minimum_level="MASTERS",
        accepted_qualification_keys=["ME_MTECH"],
        fields_of_study=["Computer Science"],
        field_mandatory=True,
    )
    auth = await _start(client, ctx, f"cat-field-{'flag' if flagged else 'ok'}@example.com")
    saved = await _save_education(
        client, ctx, auth, specialization_category=category, specialization=None
    )
    assert saved.status_code == 200, saved.text  # never refused on the field
    await _upload(client, ctx, auth)
    submitted = await _submit(client, ctx, auth)
    assert submitted.status_code == 201, submitted.text
    education = (
        await client.get(
            f"/api/v1/recruiter/applications/{submitted.json()['id']}/education",
            headers=ctx["headers"],
        )
    ).json()
    reasons = education["eligibility_review_reasons"]
    if flagged:
        assert education["matched_field_of_study"] is None
        assert reasons == [
            "The job requires Computer Science, and the selected specialization category "
            f"(“{category}”) couldn't be matched to it automatically."
        ]
    else:
        assert education["matched_field_of_study"] == "Computer Science"
        assert reasons == []


async def test_branch_is_always_derived_from_the_specialization(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    ctx = await _setup(client, "flow-spec-branch")
    auth = await _start(client, ctx, "branch@example.com")
    saved = await _save_education(
        client,
        ctx,
        auth,
        specialization_category="Mechanical & Manufacturing",
        specialization="Robotics and Automation",
        branch="Anything the client sends",
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["education"]["branch"] == "Robotics and Automation"
    # A legacy catalogue key (e.g. an older client) still resolves to its
    # label for the document check.
    legacy = await _save_education(
        client,
        ctx,
        auth,
        specialization_category="MECHANICAL_MANUFACTURING",
        specialization="ROBOTICS_AUTOMATION",
    )
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["education"]["branch"] == "Robotics and Automation"


async def test_draft_restores_and_submission_keeps_the_typed_specialization(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI, db_session: AsyncSession
) -> None:
    ctx = await _setup(client, "flow-spec-submit")
    auth = await _start(client, ctx, "robotics@example.com")
    selection = {
        "qualification_key": "PHD",
        "specialization_category": "Robotics & Intelligent Systems",
        "specialization": "Swarm Robotics",
    }
    assert (await _save_education(client, ctx, auth, **selection)).status_code == 200

    # Resumed later: a fresh view, and a fresh start of the same draft.
    resumed = [
        await client.post(f"{ctx['base']}/apply/draft/view", json=auth),
        await client.post(
            f"{ctx['base']}/apply/draft",
            json={**auth, "full_name": "Jane Candidate", "phone": phone_for(auth["email"])},
        ),
    ]
    for response in resumed:
        assert response.status_code == 200, response.text
        education = response.json()["education"]
        assert {key: education[key] for key in selection} == selection

    fake_ai.document_results = [
        doc(
            detected_level="DOCTORATE",
            detected_qualification_key="PHD",
            branch_text="Swarm Robotics",
        )
    ]
    uploaded = await _upload(client, ctx, auth)
    assert uploaded.json()["education_check"]["status"] == "VERIFIED"
    # The Ph.D. check still runs against the qualification, with the typed
    # specialization as the branch.
    assert fake_ai.document_calls[-1]["selected"] == "PHD"
    assert fake_ai.document_calls[-1]["selected_branch"] == "Swarm Robotics"

    submitted = await _submit(client, ctx, auth)
    assert submitted.status_code == 201, submitted.text
    async with rls_bypass(db_session):
        application = await db_session.get(Application, submitted.json()["id"])
        assert application is not None
        assert application.education_qualification_key == "PHD"
        assert application.education_specialization_category == "Robotics & Intelligent Systems"
        assert application.education_specialization == "Swarm Robotics"
        candidate = await db_session.get(Candidate, application.candidate_id)
        assert candidate is not None and candidate.specialization == "Swarm Robotics"
        assert candidate.qualification == "Ph.D. in Swarm Robotics"
        run = await db_session.scalar(
            select(EducationVerificationRun).where(
                EducationVerificationRun.application_id == application.id
            )
        )
        assert run is not None and run.qualification_key == "PHD"
        assert run.specialization_category == "Robotics & Intelligent Systems"
        assert run.specialization == "Swarm Robotics"

    response = await client.get(
        f"/api/v1/recruiter/applications/{application.id}/education", headers=ctx["headers"]
    )
    assert response.status_code == 200, response.text
    education = response.json()
    assert education["highest_qualification"] == "Ph.D."
    assert education["specialization_category"] == "Robotics & Intelligent Systems"
    assert education["specialization"] == "Swarm Robotics"
    assert education["manual_review_reason"] is None  # AI verified
    first_check = education["checks"][0]
    assert first_check["selected_qualification"] == "Ph.D."
    assert first_check["selected_specialization_category"] == "Robotics & Intelligent Systems"
    assert first_check["selected_specialization"] == "Swarm Robotics"
    documents = (
        await client.get(
            f"/api/v1/recruiter/applications/{application.id}/documents", headers=ctx["headers"]
        )
    ).json()["documents"]
    academic = next(d for d in documents if d["kind"] == "academic-document")
    assert academic["verification"] == "AI_VERIFIED"
    assert academic["verification_reason"] is None


async def test_long_specialization_text_is_stored_in_full(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI, db_session: AsyncSession
) -> None:
    """Values longer than the old 50-character key columns are stored and
    shown in full (needs migration 3a4b5c6d7e8f)."""
    ctx = await _setup(client, "flow-spec-long")
    auth = await _start(client, ctx, "spec-long@example.com")
    category = "Electronics, Communication and Instrumentation Engineering (Integrated)"
    specialization = (
        "Electronics and Communication Engineering with a minor in VLSI Design and Embedded Systems"
    )
    assert len(category) > 50 and len(specialization) > 50
    saved = await _save_education(
        client, ctx, auth, specialization_category=category, specialization=specialization
    )
    assert saved.status_code == 200, saved.text
    await _upload(client, ctx, auth)
    submitted = await _submit(client, ctx, auth)
    assert submitted.status_code == 201, submitted.text
    education = (
        await client.get(
            f"/api/v1/recruiter/applications/{submitted.json()['id']}/education",
            headers=ctx["headers"],
        )
    ).json()
    assert education["specialization_category"] == category
    assert education["specialization"] == specialization


async def test_changing_only_the_specialization_resets_the_check(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    ctx = await _setup(client, "flow-spec-reset")
    auth = await _start(client, ctx, "spec-reset@example.com")
    await _save_education(client, ctx, auth)
    assert (await _upload(client, ctx, auth)).json()["education_check"]["status"] == "VERIFIED"
    changed = await _save_education(client, ctx, auth, specialization="Data Science")
    assert changed.status_code == 200, changed.text
    assert changed.json()["education_check"]["status"] == "NOT_STARTED"
    # Only the category (not the branch the document is checked against)
    # changing keeps the check.
    rechecked = await client.post(f"{ctx['base']}/apply/draft/education/recheck", json=auth)
    assert rechecked.json()["education_check"]["status"] == "VERIFIED"
    kept = await _save_education(
        client, ctx, auth, specialization="Data Science", specialization_category="Data & AI"
    )
    assert kept.json()["education_check"]["status"] == "VERIFIED"


async def test_records_without_the_new_fields_still_load(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI, db_session: AsyncSession
) -> None:
    ctx = await _setup(client, "flow-spec-legacy")
    # An application submitted before the fields were recorded...
    auth = await _start(client, ctx, "legacy-app@example.com")
    await _save_education(client, ctx, auth)
    await _upload(client, ctx, auth)
    submitted = await _submit(client, ctx, auth)
    assert submitted.status_code == 201, submitted.text
    # ...and a draft saved before the catalogue existed (free-text branch).
    draft_auth = await _start(client, ctx, "legacy-draft@example.com")
    await _save_education(client, ctx, draft_auth)
    await _upload(client, ctx, draft_auth)
    async with rls_bypass(db_session):
        application = await db_session.get(Application, submitted.json()["id"])
        assert application is not None
        application.education_qualification_key = None
        application.education_specialization_category = None
        application.education_specialization = None
        application.education_eligibility = None
        runs = await db_session.scalars(
            select(EducationVerificationRun).where(
                EducationVerificationRun.application_id == application.id
            )
        )
        for run in runs:
            run.specialization_category = None
            run.specialization = None
        draft = await db_session.scalar(
            select(ApplicationDraft).where(ApplicationDraft.email == "legacy-draft@example.com")
        )
        assert draft is not None and draft.education is not None
        legacy = {
            key: value
            for key, value in draft.education.items()
            if key not in ("specialization_category", "specialization")
        }
        draft.education = {**legacy, "branch": "CSE"}
        await db_session.commit()

    response = await client.get(
        f"/api/v1/recruiter/applications/{application.id}/education", headers=ctx["headers"]
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Falls back to the document's qualification; the new fields are empty.
    assert body["highest_qualification"] == "M.Tech"
    assert body["specialization_category"] is None and body["specialization"] is None
    assert body["job_fields_of_study"] == [] and body["eligibility_review_reasons"] == []
    assert body["checks"][0]["selected_specialization"] is None

    view = await client.post(f"{ctx['base']}/apply/draft/view", json=draft_auth)
    assert view.status_code == 200, view.text
    assert view.json()["education"]["specialization_category"] is None
    assert view.json()["education"]["branch"] == "CSE"
    # It can't be submitted until the candidate enters a specialization.
    blocked = await _submit(client, ctx, draft_auth)
    assert blocked.status_code == 422
    assert _error_code(blocked) == "education_required"


async def test_records_stored_with_catalogue_keys_still_read_and_submit(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI, db_session: AsyncSession
) -> None:
    """Applications and drafts saved while the fields were catalogue keys
    are untouched in the database and read as their labels."""
    ctx = await _setup(client, "flow-spec-keys")
    auth = await _start(client, ctx, "keys-app@example.com")
    await _save_education(client, ctx, auth)
    await _upload(client, ctx, auth)
    submitted = await _submit(client, ctx, auth)
    assert submitted.status_code == 201, submitted.text
    draft_auth = await _start(client, ctx, "keys-draft@example.com")
    await _save_education(client, ctx, draft_auth)
    await _upload(client, ctx, draft_auth)
    async with rls_bypass(db_session):
        application = await db_session.get(Application, submitted.json()["id"])
        assert application is not None
        application.education_specialization_category = "CS_IT"
        application.education_specialization = "AI_ML"
        for run in await db_session.scalars(
            select(EducationVerificationRun).where(
                EducationVerificationRun.application_id == application.id
            )
        ):
            run.specialization_category = "CS_IT"
            run.specialization = "AI_ML"
        draft = await db_session.scalar(
            select(ApplicationDraft).where(ApplicationDraft.email == "keys-draft@example.com")
        )
        assert draft is not None and draft.education is not None
        draft.education = {
            **draft.education,
            "specialization_category": "ELECTRONICS_ELECTRICAL",
            "specialization": "VLSI_DESIGN",
            "branch": "VLSI Design",
        }
        await db_session.commit()

    education = (
        await client.get(
            f"/api/v1/recruiter/applications/{application.id}/education", headers=ctx["headers"]
        )
    ).json()
    assert education["specialization_category"] == "Computer Science & Information Technology"
    assert education["specialization"] == "Artificial Intelligence and Machine Learning"
    assert education["checks"][0]["selected_specialization"] == (
        "Artificial Intelligence and Machine Learning"
    )

    view = (await client.post(f"{ctx['base']}/apply/draft/view", json=draft_auth)).json()
    assert view["education"]["specialization_category"] == "Electronics & Electrical"
    assert view["education"]["specialization"] == "VLSI Design"
    assert view["education_check"]["status"] == "VERIFIED"  # unchanged
    # Submitted as stored: the keys are kept, not rewritten.
    legacy_submit = await _submit(client, ctx, draft_auth)
    assert legacy_submit.status_code == 201, legacy_submit.text
    async with rls_bypass(db_session):
        legacy_application = await db_session.get(Application, legacy_submit.json()["id"])
        assert legacy_application is not None
        assert legacy_application.education_specialization == "VLSI_DESIGN"
        candidate = await db_session.get(Candidate, legacy_application.candidate_id)
        assert candidate is not None and candidate.specialization == "VLSI Design"


@pytest.mark.parametrize(
    ("field_of_study", "category_key", "specialization_key", "flagged"),
    [
        ("Mechanical Engineering", "Mechanical & Manufacturing", "Mechanical Engineering", False),
        # typed text naming a catalogue specialization keeps its equivalents
        ("CSE", "Computer Science", "Computer Science and Engineering", False),
        ("CSE", "Computer Science", "Computer Science & Engineering", False),
        ("Mechanical Engineering", "Mechanical & Manufacturing", "Robotics and Automation", True),
        # free text outside the catalogue matches by its own words
        ("Quantum Computing", "Physics", "Quantum Computing and Information", False),
        ("Quantum Computing", "Physics", "Engineering", True),  # generic words never match
        # legacy catalogue keys behave exactly as before
        ("Mechanical Engineering", "MECHANICAL_MANUFACTURING", "MECHANICAL_ENGINEERING", False),
        ("Mechanical Engineering", "MECHANICAL_MANUFACTURING", "ROBOTICS_AUTOMATION", True),
    ],
)
async def test_a_mandatory_jd_field_flags_an_unmatched_specialization_for_hr(
    client: AsyncClient,
    super_admin: User,
    fake_ai: FakeEducationAI,
    db_session: AsyncSession,
    field_of_study: str,
    category_key: str,
    specialization_key: str,
    flagged: bool,
) -> None:
    slug = re.sub(r"[^a-z0-9]+", "-", f"{specialization_key} {flagged}".lower()).strip("-")
    suffix = f"{slug[:40]}-{zlib.crc32(f'{field_of_study}|{category_key}'.encode()) % 10_000}"
    ctx = await _setup(client, f"flow-field-{suffix}"[:60])
    fake_ai.job_result = jd(
        minimum_level="MASTERS",
        accepted_qualification_keys=["ME_MTECH"],
        fields_of_study=[field_of_study],
        field_mandatory=True,
    )
    auth = await _start(client, ctx, f"field-{suffix}@example.com")
    saved = await _save_education(
        client,
        ctx,
        auth,
        specialization_category=category_key,
        specialization=specialization_key,
    )
    assert saved.status_code == 200, saved.text  # never refused on the field
    await _upload(client, ctx, auth)
    submitted = await _submit(client, ctx, auth)
    assert submitted.status_code == 201, submitted.text
    application_id = submitted.json()["id"]
    detail = await client.get(
        f"/api/v1/recruiter/applications/{application_id}", headers=ctx["headers"]
    )
    assert detail.json()["eligibility_review_required"] is flagged

    # HR sees the job's field, the selections, and why it was flagged.
    education = (
        await client.get(
            f"/api/v1/recruiter/applications/{application_id}/education", headers=ctx["headers"]
        )
    ).json()
    assert education["job_fields_of_study"] == [field_of_study]
    assert education["job_field_mandatory"] is True
    assert education["specialization_category"] == specializations.category_label(category_key)
    assert education["specialization"] == specializations.specialization_label(specialization_key)
    reasons = education["eligibility_review_reasons"]
    if flagged:
        assert education["matched_field_of_study"] is None
        assert len(reasons) == 1
        assert field_of_study in reasons[0]
        assert specializations.specialization_label(specialization_key) in reasons[0]
    else:
        assert education["matched_field_of_study"] == field_of_study
        assert reasons == []
    async with rls_bypass(db_session):
        activities = (
            await db_session.scalars(
                select(Activity).where(
                    Activity.action == "QUALIFICATION_REVIEW_REQUIRED",
                    Activity.entity_id == application_id,
                )
            )
        ).all()
    assert len(activities) == (1 if flagged else 0)


async def test_unclear_requirements_give_hr_the_reason(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    ctx = await _setup(client, "flow-reason-unclear")
    fake_ai.job_result = AIProviderTimeoutError("timed out")
    auth = await _start(client, ctx, "reason-unclear@example.com")
    await _save_education(client, ctx, auth)
    await _upload(client, ctx, auth)
    submitted = await _submit(client, ctx, auth)
    assert submitted.status_code == 201, submitted.text
    education = (
        await client.get(
            f"/api/v1/recruiter/applications/{submitted.json()['id']}/education",
            headers=ctx["headers"],
        )
    ).json()
    assert education["job_fields_of_study"] == []
    assert len(education["eligibility_review_reasons"]) == 1
    assert "couldn't be determined automatically" in education["eligibility_review_reasons"][0]


# --- Recruiter: education documents ---------------------------------------------


async def _submit_with_marks_card(client: AsyncClient, ctx: dict, auth: dict):
    return await client.post(
        f"{ctx['base']}/apply",
        data=auth,
        files={
            "resume": ("resume.pdf", make_minimal_pdf("Jane. M.Tech."), "application/pdf"),
            "tenth_marks_card": (
                "10th-marks.pdf",
                make_minimal_pdf("Secondary School Certificate. Marks."),
                "application/pdf",
            ),
        },
    )


async def test_recruiter_lists_and_downloads_education_documents(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    ctx = await _setup(client, "flow-docs")
    auth = await _start(client, ctx, "docs@example.com")
    await _save_education(client, ctx, auth)
    await _upload(client, ctx, auth)
    submitted = await _submit_with_marks_card(client, ctx, auth)
    assert submitted.status_code == 201, submitted.text
    base = f"/api/v1/recruiter/applications/{submitted.json()['id']}/documents"

    listed = await client.get(base, headers=ctx["headers"])
    assert listed.status_code == 200, listed.text
    documents = {d["kind"]: d for d in listed.json()["documents"]}
    assert set(documents) == {"academic-document", "tenth-marks-card"}
    academic = documents["academic-document"]
    assert academic["uploaded"] is True and academic["available"] is True
    assert academic["filename"] == "mtech-marks-card.pdf"
    assert academic["description"] == "Marks card or certificate for M.Tech"
    assert academic["required"] is True
    marks = documents["tenth-marks-card"]
    assert marks["uploaded"] is True and marks["available"] is True
    assert marks["filename"] == "10th-marks.pdf" and marks["required"] is False
    # Metadata only: no storage location reaches the client.
    assert "storage" not in listed.text and "uploads" not in listed.text

    for kind, filename in (
        ("academic-document", "mtech-marks-card.pdf"),
        ("tenth-marks-card", "10th-marks.pdf"),
    ):
        download = await client.get(f"{base}/{kind}", headers=ctx["headers"])
        assert download.status_code == 200, download.text
        assert download.content.startswith(b"%PDF")
        assert download.headers["content-disposition"] == f'attachment; filename="{filename}"'

    assert (await client.get(f"{base}/resume", headers=ctx["headers"])).status_code == 422


async def test_missing_education_documents_are_reported_not_failed(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI, db_session: AsyncSession
) -> None:
    ctx = await _setup(client, "flow-docs-missing")
    auth = await _start(client, ctx, "docs-missing@example.com")
    await _save_education(client, ctx, auth)
    await _upload(client, ctx, auth)
    submitted = await _submit(client, ctx, auth)  # no 10th marks card
    assert submitted.status_code == 201, submitted.text
    base = f"/api/v1/recruiter/applications/{submitted.json()['id']}/documents"

    listed = (await client.get(base, headers=ctx["headers"])).json()["documents"]
    documents = {d["kind"]: d for d in listed}
    assert documents["tenth-marks-card"]["uploaded"] is False
    assert documents["tenth-marks-card"]["filename"] is None
    missing = await client.get(f"{base}/tenth-marks-card", headers=ctx["headers"])
    assert missing.status_code == 404
    assert "No 10th marks card" in missing.json()["error"]["message"]

    # The record exists but its file is gone from storage.
    async with rls_bypass(db_session):
        document = await db_session.scalar(
            select(AcademicDocument).where(
                AcademicDocument.application_id == submitted.json()["id"]
            )
        )
        assert document is not None
        document.storage_path = "missing/not-there.pdf"
        await db_session.commit()
    listed = (await client.get(base, headers=ctx["headers"])).json()["documents"]
    documents = {d["kind"]: d for d in listed}
    assert documents["academic-document"]["uploaded"] is True
    assert documents["academic-document"]["available"] is False
    gone = await client.get(f"{base}/academic-document", headers=ctx["headers"])
    assert gone.status_code == 404


async def test_education_documents_require_an_authorized_recruiter_of_the_organization(
    client: AsyncClient, super_admin: User, fake_ai: FakeEducationAI
) -> None:
    ctx = await _setup(client, "flow-docs-auth")
    auth = await _start(client, ctx, "docs-auth@example.com")
    await _save_education(client, ctx, auth)
    await _upload(client, ctx, auth)
    submitted = await _submit_with_marks_card(client, ctx, auth)
    base = f"/api/v1/recruiter/applications/{submitted.json()['id']}/documents"
    urls = [base, f"{base}/academic-document", f"{base}/tenth-marks-card"]

    other = await bootstrap_org(client, "flow-docs-auth-other")
    tokens = await login(client, email=SUPER_ADMIN_EMAIL, password=SUPER_ADMIN_PASSWORD)
    platform_admin = {"Authorization": f"Bearer {tokens['access_token']}"}
    for url in urls:
        assert (await client.get(url)).status_code == 401
        assert (await client.get(url, headers=platform_admin)).status_code == 403
        # Another organization's recruiters can't even tell it exists.
        assert (await client.get(url, headers=other["headers"])).status_code == 404
