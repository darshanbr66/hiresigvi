"""MTech/PhD Scholarship & Paid Training Program (docs/programs.md):
configuration and publication rules, candidate eligibility and terms, the
selection gates on the shared application pipeline, the separation of
scholarship eligibility / approval / disbursement, training, fee payments,
offer/joining, agreement/bond, permissions and tenant isolation."""

from typing import Any

from httpx import AsyncClient

from app.models.user import User
from tests.conftest import make_minimal_pdf
from tests.org_setup import bootstrap_org, link_token, open_job, staff_headers
from tests.public_apply import (
    DEFAULT_RESUME_TEXT,
    _request_code_at,
    _verify_email_at,
    apply_publicly,
    profile_form,
)
from tests.test_assessment_grading import submit

PROGRAMS = "/api/v1/recruiter/programs"


def _public(program_id: str) -> str:
    return f"/api/v1/public/programs/{program_id}"


def complete_fields(**extra: Any) -> dict:
    """Everything publication needs — with the money values still unset and
    covered by approved disclosure wording, exactly as the business has it
    today (fee and scholarship amount not finalized)."""
    return {
        "eligible_education_levels": ["MTECH", "PHD"],
        "eligible_study_years": [2],
        "training_duration_options": [6, 12],
        "fee_disclosure": "The training fee will be confirmed in writing before enrollment.",
        "scholarship_disclosure": "Scholarship amounts will be confirmed in writing to selected candidates.",
        "scholarship_eligibility_rules": "Only candidates selected through the full selection process.",
        "employment_pathway": "Full-time roles are offered subject to the company's approved process.",
        "bond_terms_summary": "A three-year commitment under the company's approved agreement.",
        "candidate_disclosures": "By applying you confirm the information you provide is accurate.",
        "terms_version": "2026-10-v1",
        **extra,
    }


async def _program(client: AsyncClient, ctx: dict, **fields: Any) -> dict:
    response = await client.post(
        PROGRAMS,
        json={"title": "MTech/PhD Scholarship & Paid Training", "description": "Train with us.", **fields},
        headers=ctx["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _published(client: AsyncClient, ctx: dict, **fields: Any) -> dict:
    program = await _program(client, ctx, **complete_fields(**fields))
    published = await client.post(
        f"{PROGRAMS}/{program['id']}/status", json={"status": "PUBLISHED"}, headers=ctx["headers"]
    )
    assert published.status_code == 200, published.text
    return published.json()


def declaration(**overrides: Any) -> dict:
    data = {
        "education_level": "MTECH",
        "study_year": "2",
        "specialization": "Robotics",
        "institution_name": "IIT Madras",
        "terms_version": "2026-10-v1",
        "terms_accepted": "true",
        **overrides,
    }
    return {k: v for k, v in data.items() if v is not None}


async def apply_to_program(
    client: AsyncClient, program_id: str, *, email: str, token: str | None = None, **overrides: Any
):
    base = _public(program_id)
    if token is None:
        token = await _verify_email_at(client, base, email)
    data = {**profile_form(email), **declaration(**overrides), "email_verification_token": token}
    files = {"resume": ("resume.pdf", make_minimal_pdf(DEFAULT_RESUME_TEXT), "application/pdf")}
    return await client.post(f"{base}/apply", data=data, files=files)


async def _enrollment(client: AsyncClient, ctx: dict, program_id: str) -> dict:
    enrollments = (await client.get(f"{PROGRAMS}/{program_id}/enrollments", headers=ctx["headers"])).json()
    assert len(enrollments) == 1
    return enrollments[0]


async def _act(client: AsyncClient, ctx: dict, program_id: str, enrollment_id: str, action: str,
               payload: dict | None = None, headers: dict | None = None):
    return await client.post(
        f"{PROGRAMS}/{program_id}/enrollments/{enrollment_id}/{action}",
        json=payload or {},
        headers=headers or ctx["headers"],
    )


async def _status(client: AsyncClient, ctx: dict, application_id: str, to_status: str):
    return await client.post(
        f"/api/v1/recruiter/applications/{application_id}/status",
        json={"to_status": to_status},
        headers=ctx["headers"],
    )


async def test_drafts_may_be_incomplete_but_publication_is_validated(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "prog-publish")
    draft = await _program(client, ctx)
    assert draft["status"] == "DRAFT"
    assert draft["training_fee_amount"] is None and draft["scholarship_amount_min"] is None
    assert draft["bond_duration_months"] == 36
    assert draft["publication_problems"]

    refused = await client.post(f"{PROGRAMS}/{draft['id']}/status", json={"status": "PUBLISHED"}, headers=ctx["headers"])
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "program_incomplete"
    assert refused.json()["error"]["data"]["problems"]

    bad_duration = await client.patch(
        f"{PROGRAMS}/{draft['id']}", json={"training_duration_options": [9]}, headers=ctx["headers"]
    )
    assert bad_duration.status_code == 422

    await client.patch(f"{PROGRAMS}/{draft['id']}", json=complete_fields(), headers=ctx["headers"])
    published = await client.post(f"{PROGRAMS}/{draft['id']}/status", json={"status": "PUBLISHED"}, headers=ctx["headers"])
    assert published.status_code == 200, published.text
    body = published.json()
    # Nothing was invented for the unfinalized values.
    assert body["training_fee_amount"] is None
    assert body["scholarship_amount_min"] is None
    assert body["published_at"] is not None

    # A published program can't be edited into an unpublishable state.
    broken = await client.patch(
        f"{PROGRAMS}/{draft['id']}", json={"fee_disclosure": None}, headers=ctx["headers"]
    )
    assert broken.status_code == 422


async def test_public_view_shows_only_published_programs_and_approved_values(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "prog-public")
    draft = await _program(client, ctx)
    assert (await client.get(_public(draft["id"]))).status_code == 404

    program = await _published(client, ctx, training_fee_amount="150000.00", training_fee_currency="inr")
    view = (await client.get(_public(program["id"]))).json()
    assert view["kind"] == "program"
    assert view["training_fee_amount"] == "150000.00" and view["training_fee_currency"] == "INR"
    assert view["scholarship_amount_min"] is None
    assert "Scholarship amounts will be confirmed" in view["disclosure_text"]
    assert "does not by itself guarantee employment" in view["disclosure_text"]
    listing = (await client.get(f"/api/v1/public/organizations/{ctx['slug']}/programs")).json()
    assert [p["id"] for p in listing] == [program["id"]]

    await client.post(f"{PROGRAMS}/{program['id']}/status", json={"status": "CLOSED"}, headers=ctx["headers"])
    assert (await client.get(_public(program["id"]))).json()["kind"] == "unavailable"


async def test_program_job_is_never_open_on_the_careers_site(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "prog-careers")
    program = await _published(client, ctx)
    # Even if someone opens the carrier job by hand, the careers site
    # neither lists it nor accepts applications for it.
    await client.patch(f"/api/v1/recruiter/jobs/{program['job_id']}", json={"status": "OPEN"}, headers=ctx["headers"])
    jobs = (await client.get(f"/api/v1/public/organizations/{ctx['slug']}/jobs")).json()
    assert program["job_id"] not in [j["id"] for j in jobs]
    assert (await client.get(f"/api/v1/public/organizations/{ctx['slug']}/jobs/{program['job_id']}")).status_code == 404
    response = await apply_publicly(client, ctx["slug"], program["job_id"], email="bypass@example.com")
    assert response.status_code == 404


async def test_eligibility_and_terms_are_enforced_before_anything_is_stored(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "prog-eligibility")
    program = await _published(client, ctx)
    base = _public(program["id"])
    token = await _verify_email_at(client, base, "student@example.com")

    bachelors = await apply_to_program(
        client, program["id"], email="student@example.com", token=token, education_level="BACHELORS"
    )
    assert bachelors.status_code == 422
    assert bachelors.json()["error"]["code"] == "program_ineligible"
    first_year = await apply_to_program(client, program["id"], email="student@example.com", token=token, study_year="1")
    assert first_year.status_code == 422
    stale_terms = await apply_to_program(
        client, program["id"], email="student@example.com", token=token, terms_version="old"
    )
    assert stale_terms.status_code == 409
    assert stale_terms.json()["error"]["code"] == "terms_outdated"
    not_accepted = await apply_to_program(
        client, program["id"], email="student@example.com", token=token, terms_accepted="false"
    )
    assert not_accepted.status_code == 422

    # None of the refusals spent the verification token or created data.
    accepted = await apply_to_program(client, program["id"], email="student@example.com", token=token)
    assert accepted.status_code == 201, accepted.text
    enrollment = await _enrollment(client, ctx, program["id"])
    assert enrollment["education_level"] == "MTECH" and enrollment["study_year"] == 2
    assert enrollment["eligibility_status"] == "PENDING"
    assert enrollment["terms_version_accepted"] == "2026-10-v1"
    candidate = (await client.get(f"/api/v1/recruiter/candidates/{enrollment['candidate_id']}", headers=ctx["headers"])).json()
    assert candidate["education_level"] == "MTECH" and candidate["specialization"] == "Robotics"

    # The application is an ordinary application, filterable by program.
    applications = (await client.get("/api/v1/recruiter/applications", params={"program_id": program["id"]},
                                     headers=ctx["headers"])).json()
    assert [a["id"] for a in applications] == [enrollment["application_id"]]


async def test_restricted_institutions_must_be_chosen_from_the_list(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "prog-institutions")
    other = await bootstrap_org(client, "prog-institutions-other")
    iitm = (await client.post("/api/v1/recruiter/institutions", json={"name": "IIT Madras"}, headers=ctx["headers"])).json()
    foreign = (await client.post("/api/v1/recruiter/institutions", json={"name": "IIT Madras"},
                                 headers=other["headers"])).json()
    # Another organization's institution can't be configured on a program.
    rejected = await client.post(
        PROGRAMS, json={"title": "P", "description": "D", "eligible_institution_ids": [foreign["id"]]},
        headers=ctx["headers"],
    )
    assert rejected.status_code == 404

    program = await _published(client, ctx, eligible_institution_ids=[iitm["id"]])
    view = (await client.get(_public(program["id"]))).json()
    assert [i["name"] for i in view["eligible_institutions"]] == ["IIT Madras"]
    assert "placement_contact_email" not in view["eligible_institutions"][0]

    token = await _verify_email_at(client, _public(program["id"]), "inst@example.com")
    typed = await apply_to_program(client, program["id"], email="inst@example.com", token=token)
    assert typed.status_code == 422
    other_org = await apply_to_program(client, program["id"], email="inst@example.com", token=token,
                                       institution_id=foreign["id"])
    assert other_org.status_code == 422
    chosen = await apply_to_program(client, program["id"], email="inst@example.com", token=token,
                                    institution_id=iitm["id"], institution_name=None)
    assert chosen.status_code == 201, chosen.text
    assert (await _enrollment(client, ctx, program["id"]))["institution_name"] == "IIT Madras"


async def test_existing_reapply_rules_apply_to_program_applications(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "prog-reapply")
    job = await open_job(client, ctx)
    program = await _published(client, ctx)
    assert (await apply_publicly(client, ctx["slug"], job["id"], email="again@example.com")).status_code == 201
    response, _ = await _request_code_at(client, _public(program["id"]), "again@example.com")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_applied"
    candidates = (await client.get("/api/v1/recruiter/candidates", headers=ctx["headers"])).json()
    assert len(candidates) == 1


async def test_full_selection_workflow_and_scholarship_states(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "prog-workflow")
    assessment = (await client.post("/api/v1/recruiter/assessments", json={
        "title": "Program test", "instructions": "Answer.", "pass_score": 50, "questions": [
            {"type": "MCQ_SINGLE", "prompt": "1 + 1?", "points": 1, "options": [
                {"label": "2", "is_correct": True}, {"label": "3"}]}]}, headers=ctx["headers"])).json()
    program = await _published(
        client, ctx, assessment_id=assessment["id"], scholarship_amount_min="10000",
        scholarship_amount_max="50000", scholarship_currency="INR",
    )
    pid = program["id"]
    assert (await apply_to_program(client, pid, email="flow@example.com")).status_code == 201
    enrollment = await _enrollment(client, ctx, pid)
    eid, application_id = enrollment["id"], enrollment["application_id"]

    # Nothing moves past review until HR marks the candidate eligible.
    assert (await _status(client, ctx, application_id, "UNDER_REVIEW")).status_code == 200
    blocked = await _status(client, ctx, application_id, "SCREENING")
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "program_eligibility_required"
    assert (await _act(client, ctx, pid, eid, "eligibility", {"status": "NOT_ELIGIBLE"})).status_code == 422
    assert (await _act(client, ctx, pid, eid, "eligibility", {"status": "ELIGIBLE"})).status_code == 200

    invited = await _act(client, ctx, pid, eid, "invite-assessment")
    assert invited.status_code == 201, invited.text
    token = link_token(invited.json()["invitation_link"])
    view = (await client.post(f"/api/v1/public/assessment/{token}/start")).json()
    wrong = [o["id"] for o in assessment["questions"][0]["options"] if not o["is_correct"]]
    await submit(client, token, [{"question_id": view["questions"][0]["id"], "selected_option_ids": wrong}])

    # A failed test blocks shortlisting/interview.
    failed = await _status(client, ctx, application_id, "SHORTLISTED")
    assert failed.status_code == 409 and failed.json()["error"]["code"] == "program_test_pass_required"

    retest = await client.post("/api/v1/recruiter/assessments/retest", json={
        "application_id": application_id, "reason": "Second chance per policy."}, headers=ctx["headers"])
    assert retest.status_code == 201
    token = link_token(retest.json()["invitation_link"])
    view = (await client.post(f"/api/v1/public/assessment/{token}/start")).json()
    right = [o["id"] for o in assessment["questions"][0]["options"] if o["is_correct"]]
    await submit(client, token, [{"question_id": view["questions"][0]["id"], "selected_option_ids": right}])
    assert (await _status(client, ctx, application_id, "SHORTLISTED")).status_code == 200

    # Scholarship eligibility only after selection; selection only after the interview.
    early = await _act(client, ctx, pid, eid, "scholarship-eligibility", {"decision": "ELIGIBLE"})
    assert early.status_code == 409
    assert (await _status(client, ctx, application_id, "INTERVIEW")).status_code == 200
    no_interview = await _status(client, ctx, application_id, "SELECTED")
    assert no_interview.status_code == 409 and no_interview.json()["error"]["code"] == "program_interview_required"
    assert (await _act(client, ctx, pid, eid, "interview-completed")).status_code == 200
    assert (await _status(client, ctx, application_id, "SELECTED")).status_code == 200

    eligible = (await _act(client, ctx, pid, eid, "scholarship-eligibility", {"decision": "ELIGIBLE"})).json()
    # Eligible is not approved, and approved is not paid.
    assert eligible["scholarship_eligibility"] == "ELIGIBLE"
    assert eligible["scholarship_status"] == "PENDING"
    assert eligible["approved_scholarship_amount"] is None

    recruiter = await staff_headers(client, ctx, "RECRUITER")
    forbidden = await _act(client, ctx, pid, eid, "scholarship-decision",
                           {"decision": "APPROVED", "approved_amount": "20000", "currency": "INR"}, headers=recruiter)
    assert forbidden.status_code == 403
    no_amount = await _act(client, ctx, pid, eid, "scholarship-decision", {"decision": "APPROVED"})
    assert no_amount.status_code == 422
    out_of_range = await _act(client, ctx, pid, eid, "scholarship-decision",
                              {"decision": "APPROVED", "approved_amount": "90000", "currency": "INR"})
    assert out_of_range.status_code == 422
    approved = (await _act(client, ctx, pid, eid, "scholarship-decision",
                           {"decision": "APPROVED", "approved_amount": "20000", "currency": "INR"})).json()
    assert approved["scholarship_status"] == "APPROVED"
    assert approved["disbursed_total"] == "0"
    assert approved["agreement_status"] == "PENDING"
    assert approved["bond_duration_months"] == 36

    over = await _act(client, ctx, pid, eid, "disbursements", {"amount": "25000"})
    assert over.status_code == 422
    paid = (await _act(client, ctx, pid, eid, "disbursements",
                       {"amount": "10000", "status": "PAID", "paid_on": "2027-01-15", "reference": "VCH-001"})).json()
    assert paid["disbursed_total"] == "10000.00"

    # Training: only approved durations.
    assert (await _act(client, ctx, pid, eid, "training", {"status": "ENROLLED", "duration_months": 9})).status_code == 422
    enrolled = await _act(client, ctx, pid, eid, "training", {"status": "ENROLLED", "duration_months": 12,
                                                              "start_date": "2027-02-01"})
    assert enrolled.status_code == 200, enrolled.text

    # Fee payments are append-only: a mistake is voided, never deleted.
    payment = (await _act(client, ctx, pid, eid, "fee-payments", {
        "amount": "5000", "currency": "INR", "paid_on": "2027-01-20", "method": "UPI", "reference": "TXN-9"})).json()
    payment_id = payment["fee_payments"][0]["id"]
    voided = (await _act(client, ctx, pid, eid, f"fee-payments/{payment_id}/void", {"reason": "Duplicate entry"})).json()
    assert voided["fee_payments"][0]["status"] == "VOIDED"
    assert voided["fee_paid_total"] == "0"

    # Agreement before bond; signing needs a reference; joining needs an accepted offer.
    assert (await _act(client, ctx, pid, eid, "bond", {"start_date": "2027-02-01"})).status_code == 409
    await _act(client, ctx, pid, eid, "agreement", {"status": "SENT"})
    unsigned = await _act(client, ctx, pid, eid, "agreement", {"status": "SIGNED", "signed_on": "2027-01-25"})
    assert unsigned.status_code == 422
    signed = await _act(client, ctx, pid, eid, "agreement",
                        {"status": "SIGNED", "signed_on": "2027-01-25", "reference": "AGR-2027-001"})
    assert signed.status_code == 200
    bond = (await _act(client, ctx, pid, eid, "bond", {"start_date": "2027-02-01"})).json()
    assert bond["bond_end_date"] == "2030-02-01"

    assert (await _act(client, ctx, pid, eid, "joining", {"joined_on": "2028-02-01"})).status_code == 409
    assert (await _status(client, ctx, application_id, "HIRED")).status_code == 409
    await _act(client, ctx, pid, eid, "offer", {"status": "OFFERED"})
    await _act(client, ctx, pid, eid, "offer", {"status": "ACCEPTED"})
    joined = (await _act(client, ctx, pid, eid, "joining", {"joined_on": "2028-02-01"})).json()
    assert joined["application_status"] == "HIRED"
    assert joined["stage"] == "Joined full-time"

    report = (await client.get(f"{PROGRAMS}/{pid}/report", headers=ctx["headers"])).json()
    assert report["applications"] == 1
    assert report["assessment_passed"] == 1
    assert report["scholarship_approved"] == 1
    assert report["joined"] == 1


async def test_finance_details_are_limited_to_finance_permission(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "prog-finance-visibility")
    program = await _published(client, ctx)
    await apply_to_program(client, program["id"], email="fin@example.com")
    enrollment = await _enrollment(client, ctx, program["id"])
    manager = await staff_headers(client, ctx, "HIRING_MANAGER")
    url = f"{PROGRAMS}/{program['id']}/enrollments/{enrollment['id']}"
    assert (await client.get(url, headers=manager)).status_code == 200
    assert (await client.post(f"{url}/eligibility", json={"status": "ELIGIBLE"}, headers=manager)).status_code == 403
    assert (await client.post(PROGRAMS, json={"title": "x", "description": "y"}, headers=manager)).status_code == 403
    interviewer = await staff_headers(client, ctx, "INTERVIEWER")
    assert (await client.get(PROGRAMS, headers=interviewer)).status_code == 403


async def test_programs_are_tenant_isolated(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "prog-iso-a")
    intruder = await bootstrap_org(client, "prog-iso-b")
    program = await _published(client, ctx)
    await apply_to_program(client, program["id"], email="iso@example.com")
    enrollment = await _enrollment(client, ctx, program["id"])

    assert (await client.get(f"{PROGRAMS}/{program['id']}", headers=intruder["headers"])).status_code == 404
    assert (await client.get(f"{PROGRAMS}/{program['id']}/enrollments", headers=intruder["headers"])).status_code == 404
    assert (
        await client.post(f"{PROGRAMS}/{program['id']}/enrollments/{enrollment['id']}/eligibility",
                          json={"status": "ELIGIBLE"}, headers=intruder["headers"])
    ).status_code == 404
    assert (await client.get(PROGRAMS, headers=intruder["headers"])).json() == []
    # The intruder's own program can't reach this enrollment by id either.
    own = await _program(client, intruder)
    assert (
        await client.get(f"{PROGRAMS}/{own['id']}/enrollments/{enrollment['id']}", headers=intruder["headers"])
    ).status_code == 404


async def test_terms_changes_after_applications_need_a_new_version(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "prog-terms")
    program = await _published(client, ctx)
    await apply_to_program(client, program["id"], email="terms@example.com")
    same_version = await client.patch(
        f"{PROGRAMS}/{program['id']}", json={"bond_terms_summary": "Changed wording."}, headers=ctx["headers"]
    )
    assert same_version.status_code == 409
    assert same_version.json()["error"]["code"] == "terms_version_required"
    new_version = await client.patch(
        f"{PROGRAMS}/{program['id']}",
        json={"bond_terms_summary": "Changed wording.", "terms_version": "2026-11-v2"},
        headers=ctx["headers"],
    )
    assert new_version.status_code == 200
    # The earlier applicant's acceptance is unchanged.
    assert (await _enrollment(client, ctx, program["id"]))["terms_version_accepted"] == "2026-10-v1"
