"""Company funding, fee obligations, interviews, training progress, agreement
terms, employment progression and campus verification for recruitment
programs (docs/programs.md § 3a, § 4a, § 5a–5d) — the money arithmetic, the
approval and separation-of-duties rules, confidentiality and tenant
isolation."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.program import FundingType
from app.db.rls import rls_bypass
from app.models.user import User
from app.services import program_finance
from app.services.program_finance import FundingError, PaymentStatus
from tests.conftest import login
from tests.org_setup import bootstrap_org, staff_headers
from tests.test_programs import PROGRAMS, _act, _enrollment, _published, _status, apply_to_program

# --- Pure arithmetic ---


def test_percentage_and_fixed_funding_split_the_fee_exactly() -> None:
    split = program_finance.split_fee(Decimal("100000"), FundingType.PERCENTAGE, Decimal("40"))
    assert (split.fee, split.company_contribution, split.candidate_payable) == (
        Decimal("100000.00"), Decimal("40000.00"), Decimal("60000.00"),
    )
    fixed = program_finance.split_fee(Decimal("75000.50"), FundingType.FIXED_AMOUNT, Decimal("25000"))
    assert fixed.candidate_payable == Decimal("50000.50")
    none = program_finance.split_fee(Decimal("1000"), None, None)
    assert none.company_contribution == Decimal("0.00") and none.candidate_payable == Decimal("1000.00")


def test_rounding_is_half_up_and_parts_always_add_up() -> None:
    split = program_finance.split_fee(Decimal("1000.01"), FundingType.PERCENTAGE, Decimal("33.33"))
    # 1000.01 * 33.33% = 333.303333 -> 333.30
    assert split.company_contribution == Decimal("333.30")
    assert split.company_contribution + split.candidate_payable == split.fee
    half = program_finance.split_fee(Decimal("0.05"), FundingType.PERCENTAGE, Decimal("50"))
    assert half.company_contribution == Decimal("0.03")  # 0.025 rounds half up
    assert half.candidate_payable == Decimal("0.02")


@pytest.mark.parametrize(
    ("fee", "funding_type", "value"),
    [
        ("1000", FundingType.PERCENTAGE, "100.01"),
        ("1000", FundingType.PERCENTAGE, "0"),
        ("1000", FundingType.FIXED_AMOUNT, "1000.01"),
        ("1000", FundingType.FIXED_AMOUNT, "-5"),
        ("-1", None, None),
    ],
)
def test_invalid_fee_and_funding_values_are_refused(
    fee: str, funding_type: FundingType | None, value: str | None
) -> None:
    with pytest.raises(FundingError):
        program_finance.split_fee(Decimal(fee), funding_type, Decimal(value) if value else None)


def test_contribution_range_and_payment_status() -> None:
    with pytest.raises(FundingError):
        program_finance.check_contribution_range(Decimal("60000"), None, Decimal("50000"))
    with pytest.raises(FundingError):
        program_finance.check_contribution_range(Decimal("5000"), Decimal("10000"), None)
    today = date(2027, 6, 1)
    status = program_finance.payment_status
    assert status(None, Decimal("0"), None, today) == PaymentStatus.NOT_APPLICABLE
    assert status(Decimal("0"), Decimal("0"), None, today) == PaymentStatus.FULLY_FUNDED
    assert status(Decimal("100"), Decimal("0"), None, today) == PaymentStatus.UNPAID
    assert status(Decimal("100"), Decimal("40"), today, today) == PaymentStatus.PARTIALLY_PAID
    assert status(Decimal("100"), Decimal("40"), today - timedelta(days=1), today) == PaymentStatus.OVERDUE
    assert status(Decimal("100"), Decimal("100"), today - timedelta(days=1), today) == PaymentStatus.PAID


# --- Helpers ---


async def _selected(client: AsyncClient, ctx: dict, email: str = "sel@example.com", **program_fields) -> tuple:
    """A published program (no test, no verification) and one participant
    taken through eligibility, interview and selection."""
    fields = {"affiliation_verification_method": "NONE", "training_fee_amount": "100000",
              "training_fee_currency": "INR", **program_fields}
    program = await _published(client, ctx, **fields)
    pid = program["id"]
    assert (await apply_to_program(client, pid, email=email)).status_code == 201
    enrollment = await _enrollment(client, ctx, pid)
    eid, app_id = enrollment["id"], enrollment["application_id"]
    assert (await _act(client, ctx, pid, eid, "eligibility", {"status": "ELIGIBLE"})).status_code == 200
    for to in ("SCREENING", "SHORTLISTED"):
        assert (await _status(client, ctx, app_id, to)).status_code == 200
    interviews = f"/api/v1/recruiter/applications/{app_id}/interviews"
    scheduled = (await client.post(interviews, json={"stage": "Panel", "scheduled_at": "2027-01-10T10:00:00Z"},
                                   headers=ctx["headers"])).json()
    await client.post(f"{interviews}/{scheduled['id']}/outcome", json={"outcome": "PASS"}, headers=ctx["headers"])
    assert (await _act(client, ctx, pid, eid, "interview-completed")).status_code == 200
    assert (await _status(client, ctx, app_id, "SELECTED")).status_code == 200
    assert (await _act(client, ctx, pid, eid, "scholarship-eligibility", {"decision": "ELIGIBLE"})).status_code == 200
    return program, eid, app_id


async def _propose(client: AsyncClient, ctx: dict, pid: str, eid: str, headers: dict, **payload) -> dict:
    response = await _act(client, ctx, pid, eid, "funding-proposals",
                          {"funding_type": "PERCENTAGE", "value": "40", **payload}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["funding_revisions"][-1]


# --- Program configuration ---


async def test_funding_configuration_is_validated(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "fund-config")
    headers = ctx["headers"]
    base = {"title": "P", "description": "D"}
    too_much = await client.post(PROGRAMS, json={**base, "company_funding_type": "PERCENTAGE",
                                                 "company_funding_value": "120"}, headers=headers)
    assert too_much.status_code == 422
    half = await client.post(PROGRAMS, json={**base, "company_funding_type": "PERCENTAGE"}, headers=headers)
    assert half.status_code == 422 and half.json()["error"]["code"] == "invalid_funding_config"
    over_fee = await client.post(PROGRAMS, json={
        **base, "training_fee_amount": "1000", "training_fee_currency": "INR",
        "company_funding_type": "FIXED_AMOUNT", "company_funding_value": "1500"}, headers=headers)
    assert over_fee.status_code == 422
    roster = await client.post(PROGRAMS, json={**base, "affiliation_verification_method": "STUDENT_LIST"},
                               headers=headers)
    assert roster.status_code == 422
    ok = await client.post(PROGRAMS, json={**base, "company_funding_type": "PERCENTAGE",
                                           "company_funding_value": "40"}, headers=headers)
    assert ok.status_code == 201
    # No funding value is ever defaulted by the platform.
    plain = (await client.post(PROGRAMS, json=base, headers=headers)).json()
    assert plain["company_funding_type"] is None and plain["company_funding_value"] is None
    assert plain["funding_requires_independent_approval"] is True


# --- Funding approval ---


async def test_passing_and_selection_never_approve_funding_and_approval_needs_a_second_person(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "fund-approval")
    program, eid, _ = await _selected(client, ctx)
    pid = program["id"]
    detail = (await client.get(f"{PROGRAMS}/{pid}/enrollments/{eid}", headers=ctx["headers"])).json()
    assert detail["scholarship_status"] == "PENDING"
    assert detail["company_contribution_amount"] is None and detail["bond_applies_reason"] is None

    # The admin proposes, so the admin can't also approve.
    revision = await _propose(client, ctx, pid, eid, ctx["headers"])
    own = await _act(client, ctx, pid, eid, f"funding-proposals/{revision['id']}/decision", {"decision": "APPROVED"})
    assert own.status_code == 403
    # Rejection needs a reason and is recorded as the decision.
    finance = await staff_headers(client, ctx, "ORG_ADMIN")
    no_reason = await _act(client, ctx, pid, eid, f"funding-proposals/{revision['id']}/decision",
                           {"decision": "REJECTED"}, headers=finance)
    assert no_reason.status_code == 422
    rejected = (await _act(client, ctx, pid, eid, f"funding-proposals/{revision['id']}/decision",
                           {"decision": "REJECTED", "note": "Budget not available."}, headers=finance)).json()
    assert rejected["scholarship_status"] == "DECLINED"
    assert rejected["funding_revisions"][0]["status"] == "REJECTED"
    assert rejected["bond_applies_reason"] is None
    # Without approved funding there is no agreement/bond.
    assert (await _act(client, ctx, pid, eid, "agreement", {"status": "PENDING"})).status_code == 200
    assert (await _act(client, ctx, pid, eid, "agreement", {"status": "SENT", "version": "v1"})).status_code == 409

    activity = (await client.get("/api/v1/recruiter/activities", headers=ctx["headers"])).json()
    actions = {a["action"] for a in (activity["items"] if isinstance(activity, dict) else activity)}
    assert {"PROGRAM_FUNDING_PROPOSED", "PROGRAM_FUNDING_REJECTED"} <= actions


async def test_single_approver_programs_and_fully_funded_candidates(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "fund-single")
    program, eid, _ = await _selected(client, ctx, funding_requires_independent_approval=False)
    pid = program["id"]
    revision = await _propose(client, ctx, pid, eid, ctx["headers"], value="100")
    approved = (await _act(client, ctx, pid, eid, f"funding-proposals/{revision['id']}/decision",
                           {"decision": "APPROVED"})).json()
    assert approved["candidate_payable_amount"] == "0.00"
    assert approved["payment_status"] == "FULLY_FUNDED"
    payment = await _act(client, ctx, pid, eid, "fee-payments", {
        "amount": "1", "currency": "INR", "paid_on": "2027-01-01", "method": "UPI", "reference": "X"})
    assert payment.status_code == 422


async def test_revisions_before_acceptance_and_amendments_after(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "fund-amend")
    program, eid, _ = await _selected(client, ctx)
    pid = program["id"]
    recruiter = await staff_headers(client, ctx, "RECRUITER")

    first = await _propose(client, ctx, pid, eid, recruiter)
    await _act(client, ctx, pid, eid, f"funding-proposals/{first['id']}/decision", {"decision": "APPROVED"})
    # Changed before acceptance: a REVISION, the approved terms stay until
    # the new ones are approved.
    revised = await _propose(client, ctx, pid, eid, recruiter, funding_type="FIXED_AMOUNT", value="30000")
    assert revised["kind"] == "REVISION"
    pending = (await client.get(f"{PROGRAMS}/{pid}/enrollments/{eid}", headers=ctx["headers"])).json()
    assert pending["scholarship_status"] == "APPROVED"
    assert pending["candidate_payable_amount"] == "60000.00"
    done = (await _act(client, ctx, pid, eid, f"funding-proposals/{revised['id']}/decision",
                       {"decision": "APPROVED"})).json()
    assert done["candidate_payable_amount"] == "70000.00"
    assert [r["status"] for r in done["funding_revisions"]] == ["SUPERSEDED", "APPROVED"]

    # The agreement is sent for revision 2 and signed: acceptance recorded.
    await _act(client, ctx, pid, eid, "agreement", {"status": "SENT", "version": "v1", "reference": "AGR-1"})
    signed = (await _act(client, ctx, pid, eid, "agreement", {"status": "SIGNED", "signed_on": "2027-01-05"})).json()
    assert signed["funding_terms_accepted_revision"] == 2

    # After acceptance a change is an AMENDMENT and needs a reason.
    no_reason = await _act(client, ctx, pid, eid, "funding-proposals",
                           {"funding_type": "FIXED_AMOUNT", "value": "35000"}, headers=recruiter)
    assert no_reason.status_code == 422
    amendment = await _propose(client, ctx, pid, eid, recruiter, funding_type="FIXED_AMOUNT", value="35000",
                               reason="Approved by the committee on appeal.")
    assert amendment["kind"] == "AMENDMENT"
    amended = (await _act(client, ctx, pid, eid, f"funding-proposals/{amendment['id']}/decision",
                          {"decision": "APPROVED"})).json()
    assert amended["candidate_payable_amount"] == "65000.00"
    # The signed agreement still shows what was agreed, flagged as outdated,
    # and renewed acceptance is required.
    assert amended["agreement_terms_snapshot"]["company_contribution"] == "30000.00"
    assert amended["agreement_terms_current"] is False
    assert amended["funding_terms_accepted_revision"] is None


async def test_withdrawn_proposal_returns_to_pending(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "fund-withdraw")
    program, eid, _ = await _selected(client, ctx)
    pid = program["id"]
    revision = await _propose(client, ctx, pid, eid, ctx["headers"])
    withdrawn = (await _act(client, ctx, pid, eid, f"funding-proposals/{revision['id']}/withdraw", {})).json()
    assert withdrawn["scholarship_status"] == "PENDING"
    again = await _act(client, ctx, pid, eid, f"funding-proposals/{revision['id']}/decision",
                       {"decision": "APPROVED"}, headers=await staff_headers(client, ctx, "ORG_ADMIN"))
    assert again.status_code == 409


async def test_fee_obligation_is_a_snapshot_and_finance_is_hidden(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "fund-snapshot")
    program, eid, _ = await _selected(client, ctx)
    pid = program["id"]
    due = (date.today() - timedelta(days=1)).isoformat()
    fixed = (await _act(client, ctx, pid, eid, "fee-obligation", {"due_date": due})).json()
    assert fixed["fee_amount"] == "100000.00" and fixed["payment_status"] == "OVERDUE"
    # A later program fee change (new terms version) doesn't rewrite it.
    await client.patch(f"{PROGRAMS}/{pid}", json={"training_fee_amount": "120000", "terms_version": "v2"},
                       headers=ctx["headers"])
    after = (await client.get(f"{PROGRAMS}/{pid}/enrollments/{eid}", headers=ctx["headers"])).json()
    assert after["fee_amount"] == "100000.00"
    # Without finance permission, no money fields are returned.
    manager = await staff_headers(client, ctx, "HIRING_MANAGER")
    hidden = (await client.get(f"{PROGRAMS}/{pid}/enrollments/{eid}", headers=manager)).json()
    assert hidden["finance_visible"] is False
    assert hidden["fee_amount"] is None and hidden["payment_status"] is None and hidden["fee_payments"] == []
    listed = (await client.get(f"{PROGRAMS}/{pid}/enrollments", headers=manager)).json()
    assert listed[0]["candidate_payable_amount"] is None
    recruiter = await staff_headers(client, ctx, "RECRUITER")
    assert (await _act(client, ctx, pid, eid, "fee-payments", {
        "amount": "10", "currency": "INR", "paid_on": "2027-01-01", "method": "UPI", "reference": "R"},
        headers=recruiter)).status_code == 403
    wrong_currency = await _act(client, ctx, pid, eid, "fee-payments", {
        "amount": "10", "currency": "USD", "paid_on": "2027-01-01", "method": "UPI", "reference": "R"})
    assert wrong_currency.status_code == 422


# --- Training ---


async def test_training_lifecycle_milestones_and_early_exit(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "train-life")
    program, eid, _ = await _selected(client, ctx, training_duration_options=[6])
    pid = program["id"]
    assert (await _act(client, ctx, pid, eid, "milestones", {"title": "Module 1"})).status_code == 409
    six = (await _act(client, ctx, pid, eid, "training", {
        "status": "ENROLLED", "duration_months": 6, "start_date": "2027-03-01"})).json()
    assert six["training_end_date"] == "2027-09-01"
    assert six["training_enrolled_at"] is not None
    # Enrolling fixed the fee obligation (the program tracks fees).
    assert six["fee_amount"] == "100000.00"
    assert (await _act(client, ctx, pid, eid, "training", {"status": "ENROLLED", "duration_months": 12})).status_code == 422

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    added = (await _act(client, ctx, pid, eid, "milestones", {"title": "Module 1", "due_on": yesterday})).json()
    milestone = added["milestones"][0]
    assert milestone["overdue"] is True and added["milestones_overdue"] == 1
    no_date = await _act(client, ctx, pid, eid, f"milestones/{milestone['id']}", {"status": "COMPLETED"})
    assert no_date.status_code == 422
    done = (await _act(client, ctx, pid, eid, f"milestones/{milestone['id']}",
                       {"status": "COMPLETED", "completed_on": yesterday})).json()
    assert done["milestones"][0]["status"] == "COMPLETED" and done["milestones_overdue"] == 0

    assert (await _act(client, ctx, pid, eid, "training", {"status": "WITHDRAWN"})).status_code == 422
    withdrawn = (await _act(client, ctx, pid, eid, "training", {
        "status": "WITHDRAWN", "note": "Candidate left for higher studies."})).json()
    assert withdrawn["training_status"] == "WITHDRAWN"
    assert (await _act(client, ctx, pid, eid, "training", {"status": "IN_PROGRESS"})).status_code == 409
    assert (await _act(client, ctx, pid, eid, "employment-review", {"status": "IN_REVIEW"})).status_code == 409
    report = (await client.get(f"{PROGRAMS}/{pid}/report", headers=ctx["headers"])).json()
    assert report["training_withdrawn"] == 1


# --- Interviews ---


async def test_interview_feedback_is_restricted_to_its_interviewer_and_managers(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "interview-privacy")
    program = await _published(client, ctx, affiliation_verification_method="NONE")
    pid = program["id"]
    await apply_to_program(client, pid, email="iv@example.com")
    enrollment = await _enrollment(client, ctx, pid)
    eid, app_id = enrollment["id"], enrollment["application_id"]
    interviews = f"/api/v1/recruiter/applications/{app_id}/interviews"
    early = await client.post(interviews, json={"stage": "HR", "scheduled_at": "2027-01-10T10:00:00Z"},
                              headers=ctx["headers"])
    assert early.status_code == 409  # not shortlisted yet
    await _act(client, ctx, pid, eid, "eligibility", {"status": "ELIGIBLE"})
    for to in ("SCREENING", "SHORTLISTED"):
        await _status(client, ctx, app_id, to)

    interviewer = await staff_headers(client, ctx, "INTERVIEWER")
    users = (await client.get("/api/v1/recruiter/users", headers=ctx["headers"])).json()
    users = users["items"] if isinstance(users, dict) else users
    interviewer_id = next(u["id"] for u in users if u["email"].startswith("interviewer@"))
    other = await client.post("/api/v1/recruiter/users", json={
        "email": "second.interviewer@interview-privacy.dev", "full_name": "Second", "password": "StaffUserPass1",
        "role": "INTERVIEWER"}, headers=ctx["headers"])
    assert other.status_code == 201
    other_headers = {"Authorization": "Bearer " + (await login(
        client, email="second.interviewer@interview-privacy.dev", password="StaffUserPass1"))["access_token"]}

    assert (await client.post(interviews, json={"stage": "Tech", "scheduled_at": "2027-01-10T10:00:00Z"},
                              headers=interviewer)).status_code == 403
    interview = (await client.post(interviews, json={
        "stage": "Tech", "scheduled_at": "2027-01-10T10:00:00Z", "interviewer_user_id": interviewer_id},
        headers=ctx["headers"])).json()
    url = f"{interviews}/{interview['id']}/outcome"
    assert (await client.post(url, json={"outcome": "PASS"}, headers=other_headers)).status_code == 403
    assert (await client.post(url, json={"outcome": "FAIL"}, headers=interviewer)).status_code == 422
    recorded = await client.post(url, json={"outcome": "FAIL", "feedback": "Weak on algorithms."},
                                 headers=interviewer)
    assert recorded.status_code == 200 and recorded.json()["feedback"] == "Weak on algorithms."
    assert (await client.post(url, json={"outcome": "PASS"}, headers=ctx["headers"])).status_code == 409

    as_other = (await client.get(interviews, headers=other_headers)).json()
    assert as_other["items"][0]["feedback"] is None and as_other["items"][0]["feedback_restricted"] is True
    assert as_other["viewer_permissions"] == ["interview.read"]
    as_admin = (await client.get(interviews, headers=ctx["headers"])).json()
    assert as_admin["items"][0]["feedback"] == "Weak on algorithms."
    assert "interview.manage" in as_admin["viewer_permissions"]

    # A FAIL outcome doesn't decide anything by itself; another tenant
    # can't see the interviews at all.
    assert (await _enrollment(client, ctx, pid))["application_status"] == "INTERVIEW"
    intruder = await bootstrap_org(client, "interview-privacy-other")
    assert (await client.get(interviews, headers=intruder["headers"])).status_code == 404
    assert (await client.get(interviews)).status_code == 401


# --- Campus verification ---


async def test_program_affiliation_email_domain_and_no_self_verification(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "affil-prog")
    iit = (await client.post("/api/v1/recruiter/institutions",
                             json={"name": "IIT Delhi", "email_domains": ["iitd.ac.in"]}, headers=ctx["headers"])).json()
    program = await _published(client, ctx, affiliation_verification_method="EMAIL_DOMAIN",
                               eligible_institution_ids=[iit["id"]])
    pid = program["id"]
    assert (await apply_to_program(client, pid, email="ravi@cse.iitd.ac.in", institution_id=iit["id"],
                                   institution_name=None)).status_code == 201
    auto = await _enrollment(client, ctx, pid)
    assert auto["affiliation"]["status"] == "VERIFIED" and auto["affiliation"]["method"] == "EMAIL_DOMAIN"

    # A staff member who is also the applicant can't verify themselves.
    manual = await _published(client, ctx, affiliation_verification_method="MANUAL")
    recruiter = await staff_headers(client, ctx, "RECRUITER")
    assert (await apply_to_program(client, manual["id"], email="recruiter@affil-prog.dev")).status_code == 201
    mine = await _enrollment(client, ctx, manual["id"])
    self_review = await _act(client, ctx, manual["id"], mine["id"], "affiliation",
                             {"status": "VERIFIED", "method": "MANUAL"}, headers=recruiter)
    assert self_review.status_code == 403
    no_note = await _act(client, ctx, manual["id"], mine["id"], "affiliation", {"status": "REJECTED"})
    assert no_note.status_code == 409
    rejected = await _act(client, ctx, manual["id"], mine["id"], "affiliation",
                          {"status": "REJECTED", "note": "Not on the college list."})
    assert rejected.json()["affiliation"]["status"] == "REJECTED"
    # Resubmission: HR reopens it once new evidence arrives.
    reopened = await _act(client, ctx, manual["id"], mine["id"], "affiliation", {"status": "PENDING"})
    assert reopened.json()["affiliation"]["status"] == "PENDING"


# --- Assessment pass mark ---


async def test_pass_mark_is_locked_once_a_test_is_sent(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "pass-lock")
    assessment = (await client.post("/api/v1/recruiter/assessments", json={
        "title": "T", "instructions": "I", "pass_score": 60, "questions": [
            {"type": "MCQ_SINGLE", "prompt": "Q", "points": 1, "options": [
                {"label": "A", "is_correct": True}, {"label": "B"}]}]}, headers=ctx["headers"])).json()
    url = f"/api/v1/recruiter/assessments/{assessment['id']}"
    assert (await client.patch(url, json={"pass_score": 70}, headers=ctx["headers"])).status_code == 200
    program = await _published(client, ctx, affiliation_verification_method="NONE", assessment_id=assessment["id"])
    await apply_to_program(client, program["id"], email="lock@example.com")
    enrollment = await _enrollment(client, ctx, program["id"])
    await _act(client, ctx, program["id"], enrollment["id"], "eligibility", {"status": "ELIGIBLE"})
    assert (await _act(client, ctx, program["id"], enrollment["id"], "invite-assessment")).status_code == 201
    locked = await client.patch(url, json={"pass_score": 40}, headers=ctx["headers"])
    assert locked.status_code == 409 and locked.json()["error"]["code"] == "pass_score_locked"
    assert (await client.patch(url, json={"title": "Renamed"}, headers=ctx["headers"])).status_code == 200


# --- Tenant isolation of the new endpoints ---


async def test_new_program_endpoints_are_tenant_isolated(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "fund-iso-a")
    intruder = await bootstrap_org(client, "fund-iso-b")
    program, eid, _ = await _selected(client, ctx)
    pid = program["id"]
    revision = await _propose(client, ctx, pid, eid, ctx["headers"])
    for action, payload in (
        ("funding-proposals", {"funding_type": "PERCENTAGE", "value": "10"}),
        (f"funding-proposals/{revision['id']}/decision", {"decision": "APPROVED"}),
        ("fee-obligation", {}),
        ("milestones", {"title": "x"}),
        ("employment-review", {"status": "IN_REVIEW"}),
        ("affiliation", {"status": "VERIFIED"}),
    ):
        response = await _act(client, ctx, pid, eid, action, payload, headers=intruder["headers"])
        assert response.status_code == 404, (action, response.text)


# --- Review fixes: legacy rows, unfinalized fee, drives, notifications ---


async def _legacy_approve(db_session: AsyncSession, eid: str, amount: str = "20000") -> None:
    """What the pre-revision one-step endpoint left behind: an approved
    amount with no `program_funding_revisions` row."""
    async with rls_bypass(db_session):
        await db_session.execute(
            text(
                "UPDATE program_enrollments SET scholarship_status = 'APPROVED', "
                "approved_scholarship_amount = :amount, approved_scholarship_currency = 'INR', "
                "agreement_status = 'PENDING' WHERE id = :id"
            ),
            {"amount": amount, "id": eid},
        )
    db_session.expire_all()


async def test_legacy_approved_funding_can_still_be_agreed_and_signed(
    client: AsyncClient, super_admin: User, db_session: AsyncSession
) -> None:
    ctx = await bootstrap_org(client, "legacy-agree")
    program, eid, _ = await _selected(client, ctx)
    pid = program["id"]
    await _legacy_approve(db_session, eid)
    sent = await _act(client, ctx, pid, eid, "agreement", {"status": "SENT", "version": "v1", "reference": "A-1"})
    assert sent.status_code == 200, sent.text
    assert sent.json()["agreement_terms_snapshot"]["funding_revision"] is None
    assert sent.json()["agreement_terms_snapshot"]["company_contribution"] == "20000.00"
    signed = await _act(client, ctx, pid, eid, "agreement", {"status": "SIGNED", "signed_on": "2027-01-05"})
    assert signed.status_code == 200, signed.text
    assert signed.json()["funding_terms_accepted_at"] is not None


async def test_legacy_funding_applied_is_respected_by_a_new_revision(
    client: AsyncClient, super_admin: User, db_session: AsyncSession
) -> None:
    ctx = await bootstrap_org(client, "legacy-applied")
    program, eid, _ = await _selected(client, ctx, funding_requires_independent_approval=False)
    pid = program["id"]
    await _legacy_approve(db_session, eid)
    applied = await _act(client, ctx, pid, eid, "disbursements",
                         {"amount": "20000", "status": "PAID", "paid_on": "2027-01-01"})
    assert applied.status_code == 200, applied.text
    lower = await _propose(client, ctx, pid, eid, ctx["headers"], funding_type="FIXED_AMOUNT", value="10000")
    refused = await _act(client, ctx, pid, eid, f"funding-proposals/{lower['id']}/decision", {"decision": "APPROVED"})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "disbursement_exceeds_approved"


async def test_training_enrollment_works_before_the_fee_is_finalized(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "fee-undecided")
    # Fee only as approved wording (the business's current state).
    program, eid, _ = await _selected(client, ctx, training_fee_amount=None, training_fee_currency=None)
    pid = program["id"]
    enrolled = await _act(client, ctx, pid, eid, "training", {"status": "ENROLLED", "duration_months": 12})
    assert enrolled.status_code == 200, enrolled.text
    assert enrolled.json()["fee_amount"] is None and enrolled.json()["payment_status"] == "NOT_APPLICABLE"
    # Fixing the obligation explicitly still needs the finalized fee.
    fixed = await _act(client, ctx, pid, eid, "fee-obligation", {})
    assert fixed.status_code == 409 and fixed.json()["error"]["code"] == "training_fee_not_configured"


async def test_drives_cannot_use_per_decision_evidence_methods(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "drive-method")
    inst = (await client.post("/api/v1/recruiter/institutions", json={"name": "NIT Trichy"},
                              headers=ctx["headers"])).json()
    response = await client.post("/api/v1/recruiter/campus-drives", json={
        "name": "Drive", "institution_id": inst["id"], "affiliation_verification_method": "STUDENT_ID_DOCUMENT",
    }, headers=ctx["headers"])
    assert response.status_code == 422, response.text


async def test_assigned_interviewer_gets_an_in_app_notice_and_times_need_a_zone(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "interview-notice")
    program = await _published(client, ctx, affiliation_verification_method="NONE")
    await apply_to_program(client, program["id"], email="notice@example.com")
    enrollment = await _enrollment(client, ctx, program["id"])
    await _act(client, ctx, program["id"], enrollment["id"], "eligibility", {"status": "ELIGIBLE"})
    for to in ("SCREENING", "SHORTLISTED"):
        await _status(client, ctx, enrollment["application_id"], to)
    interviewer = await staff_headers(client, ctx, "INTERVIEWER")
    me = (await client.get("/api/v1/recruiter/auth/me", headers=interviewer)).json()
    url = f"/api/v1/recruiter/applications/{enrollment['application_id']}/interviews"
    naive = await client.post(url, json={"stage": "Tech", "scheduled_at": "2027-01-10T10:00:00",
                                         "interviewer_user_id": me["id"]}, headers=ctx["headers"])
    assert naive.status_code == 422
    ok = await client.post(url, json={"stage": "Tech", "scheduled_at": "2027-01-10T10:00:00+05:30",
                                      "interviewer_user_id": me["id"]}, headers=ctx["headers"])
    assert ok.status_code == 201, ok.text
    notices = (await client.get("/api/v1/recruiter/notifications", headers=interviewer)).json()
    notices = notices["items"] if isinstance(notices, dict) else notices
    assigned = [n for n in notices if n["type"] == "INTERVIEW_ASSIGNED"]
    assert len(assigned) == 1
    assert assigned[0]["related_entity_type"] == "APPLICATION"
    assert "04:30 UTC" in assigned[0]["message"]  # stored and shown as the right instant
