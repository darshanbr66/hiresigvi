"""Configurable program policy (docs/programs.md § 1a, § 2, § 5f): the
organization's default program settings and who may change them, defaults
copied into new programs only, configuration versions, per-level study-year
rules and academic thresholds, application/selection limits, the optional
interview and bond, zero funding, the funding preview, and internships."""

from httpx import AsyncClient

from app.models.user import User
from tests.org_setup import bootstrap_org, staff_headers
from tests.test_program_funding_training import _propose, _selected
from tests.test_programs import (
    PROGRAMS,
    _act,
    _enrollment,
    _public,
    _published,
    _status,
    apply_to_program,
)

SETTINGS = "/api/v1/recruiter/program-settings"


def _settings_payload(**overrides) -> dict:
    return {
        "training_duration_options": [6, 12],
        "training_fee_amount": "50000",
        "currency": "INR",
        "company_funding_type": "PERCENTAGE",
        "company_funding_value": "50",
        "funding_requires_independent_approval": True,
        "internship_duration_months": 6,
        "bond_required": True,
        "bond_duration_months": 36,
        "eligible_education_levels": ["MTECH", "PHD"],
        "study_year_rules": {"MTECH": [2]},
        **overrides,
    }


# --- Organization default settings ---


async def test_settings_start_from_the_proposed_defaults_and_only_admins_edit(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "settings-edit")
    initial = (await client.get(SETTINGS, headers=ctx["headers"])).json()
    assert initial["training_duration_options"] == [6, 12]
    assert (initial["training_fee_amount"], initial["currency"]) == ("50000.00", "INR")
    assert (initial["company_funding_type"], initial["company_funding_value"]) == ("PERCENTAGE", "50.00")
    assert (initial["preview_company_contribution"], initial["preview_candidate_payable"]) == (
        "25000.00", "25000.00",
    )
    assert initial["internship_duration_months"] == 6
    assert initial["bond_required"] is True and initial["bond_duration_months"] == 36
    assert initial["study_year_rules"] == {"MTECH": [2]}
    assert initial["can_edit"] is True

    recruiter = await staff_headers(client, ctx, "RECRUITER")
    as_recruiter = (await client.get(SETTINGS, headers=recruiter)).json()
    assert as_recruiter["can_edit"] is False
    refused = await client.put(SETTINGS, json=_settings_payload(training_fee_amount="1"), headers=recruiter)
    assert refused.status_code == 403

    # Validation: a bond needs its duration; fixed funding can't exceed the fee.
    no_bond_term = await client.put(
        SETTINGS, json=_settings_payload(bond_duration_months=None), headers=ctx["headers"]
    )
    assert no_bond_term.status_code == 422
    too_much = await client.put(
        SETTINGS,
        json=_settings_payload(company_funding_type="FIXED_AMOUNT", company_funding_value="60000"),
        headers=ctx["headers"],
    )
    assert too_much.status_code == 422
    bad_years = await client.put(
        SETTINGS, json=_settings_payload(study_year_rules={"NOPE": [1]}), headers=ctx["headers"]
    )
    assert bad_years.status_code == 422

    updated = await client.put(
        SETTINGS,
        json=_settings_payload(
            training_duration_options=[3, 9],
            company_funding_type="FIXED_AMOUNT",
            company_funding_value="20000",
            bond_required=False,
            bond_duration_months=None,
        ),
        headers=ctx["headers"],
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["training_duration_options"] == [3, 9]
    assert body["preview_candidate_payable"] == "30000.00"
    assert body["bond_required"] is False and body["bond_duration_months"] is None
    assert body["updated_by_name"] == "Acme Admin"

    activity = (await client.get("/api/v1/recruiter/activities", headers=ctx["headers"])).json()
    items = activity["items"] if isinstance(activity, dict) else activity
    assert any(a["action"] == "PROGRAM_SETTINGS_UPDATED" for a in items)


async def test_defaults_apply_to_new_programs_only_and_are_tenant_scoped(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "settings-defaults")
    other = await bootstrap_org(client, "settings-other", name="Other Corp")
    await client.put(
        SETTINGS, json=_settings_payload(training_fee_amount="80000"), headers=other["headers"]
    )

    created = await client.post(
        PROGRAMS, json={"title": "Program", "description": "D"}, headers=ctx["headers"]
    )
    assert created.status_code == 201, created.text
    program = created.json()
    # This organization's defaults — not the other organization's edit.
    assert program["training_fee_amount"] == "50000.00"
    assert program["company_funding_value"] == "50.00"
    assert program["bond_duration_months"] == 36
    assert program["internship_duration_months"] == 6
    assert program["eligible_education_levels"] == ["MTECH", "PHD"]
    assert program["study_year_rules"] == {"MTECH": [2]}

    # Values given on creation win over the defaults.
    explicit = (
        await client.post(
            PROGRAMS,
            json={"title": "P2", "description": "D", "training_duration_options": [3]},
            headers=ctx["headers"],
        )
    ).json()
    assert explicit["training_duration_options"] == [3]

    # Changing a default never rewrites an existing program.
    await client.put(SETTINGS, json=_settings_payload(training_fee_amount="70000"), headers=ctx["headers"])
    unchanged = (await client.get(f"{PROGRAMS}/{program['id']}", headers=ctx["headers"])).json()
    assert unchanged["training_fee_amount"] == "50000.00"
    assert (await client.get(SETTINGS, headers=other["headers"])).json()["training_fee_amount"] == "80000.00"


# --- Versions ---


async def test_published_programs_keep_an_immutable_version_history(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "prog-versions")
    program = await _published(client, ctx, affiliation_verification_method="NONE")
    pid = program["id"]
    assert program["current_version"] == 1
    await apply_to_program(client, pid, email="v1@example.com")
    assert (await _enrollment(client, ctx, pid))["program_version_number"] == 1

    await client.patch(
        f"{PROGRAMS}/{pid}",
        json={"capacity": 5, "eligibility_notes": "Updated notes."},
        headers=ctx["headers"],
    )
    versions = (await client.get(f"{PROGRAMS}/{pid}/versions", headers=ctx["headers"])).json()
    assert [v["version_number"] for v in versions] == [2, 1]
    assert versions[0]["event"] == "UPDATED"
    assert set(versions[0]["changed_fields"]) == {"capacity", "eligibility_notes"}
    assert versions[1]["snapshot"]["capacity"] is None and versions[0]["snapshot"]["capacity"] == 5
    assert versions[0]["created_by_name"] == "Acme Admin"

    # Another organization can't read the history.
    other = await bootstrap_org(client, "prog-versions-other", name="Other Corp")
    assert (await client.get(f"{PROGRAMS}/{pid}/versions", headers=other["headers"])).status_code == 404


# --- Eligibility rules and limits ---


async def test_per_level_study_years_and_academic_thresholds(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "prog-thresholds")
    program = await _published(
        client, ctx, affiliation_verification_method="NONE",
        eligible_study_years=[], study_year_rules={"MTECH": [2], "PHD": []},
        minimum_cgpa="7.5", minimum_percentage="70",
    )
    pid = program["id"]
    public = (await client.get(_public(pid))).json()
    assert public["minimum_cgpa"] == "7.50" and public["study_year_rules"] == {"MTECH": [2], "PHD": []}

    first_year = await apply_to_program(client, pid, email="a@example.com", study_year="1", cgpa="9")
    assert first_year.status_code == 422
    assert first_year.json()["error"]["code"] == "program_ineligible"
    low = await apply_to_program(client, pid, email="b@example.com", cgpa="6.9", percentage_score="65")
    assert low.status_code == 422
    missing = await apply_to_program(client, pid, email="c@example.com")
    assert missing.status_code == 422

    # Either threshold qualifies; a PhD in any year qualifies.
    by_percentage = await apply_to_program(client, pid, email="d@example.com", percentage_score="72")
    assert by_percentage.status_code == 201, by_percentage.text
    phd = await apply_to_program(
        client, pid, email="e@example.com", education_level="PHD", study_year="4", cgpa="8"
    )
    assert phd.status_code == 201, phd.text


async def test_application_limit_and_selection_capacity(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "prog-limits")
    program = await _published(
        client, ctx, affiliation_verification_method="NONE", requires_interview=False,
        capacity=1, max_applications=2,
    )
    pid = program["id"]
    assert (await apply_to_program(client, pid, email="one@example.com")).status_code == 201
    assert (await apply_to_program(client, pid, email="two@example.com")).status_code == 201
    third = await apply_to_program(client, pid, email="three@example.com")
    assert third.status_code == 409
    assert third.json()["error"]["code"] == "program_application_limit"

    enrollments = (await client.get(f"{PROGRAMS}/{pid}/enrollments", headers=ctx["headers"])).json()
    results = []
    for enrollment in enrollments:
        await _act(client, ctx, pid, enrollment["id"], "eligibility", {"status": "ELIGIBLE"})
        for to in ("SCREENING", "SHORTLISTED", "INTERVIEW"):
            assert (await _status(client, ctx, enrollment["application_id"], to)).status_code == 200
        # No interview is required, so selection needs no completed interview…
        results.append(await _status(client, ctx, enrollment["application_id"], "SELECTED"))
    # …but only one place exists.
    assert results[0].status_code == 200, results[0].text
    assert results[1].status_code == 409
    assert results[1].json()["error"]["code"] == "program_capacity_reached"
    detail = (await client.get(f"{PROGRAMS}/{pid}", headers=ctx["headers"])).json()
    assert detail["selected_count"] == 1


# --- Bond, zero funding, preview ---


async def test_bond_can_be_turned_off_and_zero_funding_is_a_valid_standard(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "prog-no-bond")
    program, eid, _ = await _selected(
        client, ctx, bond_required=False, bond_duration_months=None, bond_terms_summary=None,
        company_funding_type="PERCENTAGE", company_funding_value="0",
        funding_requires_independent_approval=False,
    )
    pid = program["id"]
    assert program["bond_required"] is False and program["company_funding_value"] == "0.00"
    public = (await client.get(_public(pid))).json()
    assert public["bond_required"] is False and public["bond_duration_months"] is None
    assert "commitment" not in public["disclosure_text"].lower()

    # A proposal that contributes nothing is refused (declining funding is
    # the not-eligible decision instead).
    zero = await _act(client, ctx, pid, eid, "funding-proposals", {"funding_type": "PERCENTAGE", "value": "0"})
    assert zero.status_code == 422
    revision = await _propose(client, ctx, pid, eid, ctx["headers"])
    approved = (await _act(client, ctx, pid, eid, f"funding-proposals/{revision['id']}/decision",
                           {"decision": "APPROVED"})).json()
    assert approved["scholarship_status"] == "APPROVED"
    assert approved["bond_applies_reason"] is None and approved["bond_duration_months"] is None
    bond = await _act(client, ctx, pid, eid, "bond", {"start_date": "2027-01-01"})
    assert bond.status_code == 409


async def test_funding_preview_uses_the_authoritative_calculation(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "prog-preview")
    preview = await client.post(
        f"{PROGRAMS}/funding-preview",
        json={"fee": "50000", "funding_type": "PERCENTAGE", "value": "33.33"},
        headers=ctx["headers"],
    )
    assert preview.json() == {"fee": "50000.00", "company_contribution": "16665.00", "candidate_payable": "33335.00"}
    over = await client.post(
        f"{PROGRAMS}/funding-preview",
        json={"fee": "100", "funding_type": "FIXED_AMOUNT", "value": "101"},
        headers=ctx["headers"],
    )
    assert over.status_code == 422


# --- Internships ---


async def test_internship_lifecycle_evaluation_and_employment_gate(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "prog-internship")
    program, eid, _ = await _selected(
        client, ctx, tracks_training=False, tracks_internship=True, internship_duration_months=6,
        internship_stipend_amount="15000", internship_stipend_currency="INR",
        internship_terms="A paid internship before the employment review.",
        internship_required_for_employment=True,
    )
    pid = program["id"]
    public = (await client.get(_public(pid))).json()
    assert public["internship_duration_months"] == 6 and public["internship_stipend_amount"] == "15000.00"

    # The employment review waits for a qualifying internship.
    early = await _act(client, ctx, pid, eid, "employment-review", {"status": "IN_REVIEW"})
    assert early.status_code == 409
    assert early.json()["error"]["code"] == "internship_completion_required"

    recruiter = await staff_headers(client, ctx, "RECRUITER")
    stipend = await _act(client, ctx, pid, eid, "internship",
                         {"stipend_amount": "1", "stipend_currency": "INR"}, headers=recruiter)
    assert stipend.status_code == 403
    planned = await _act(client, ctx, pid, eid, "internship", {"role_title": "Research Intern", "mode": "ON_SITE"},
                         headers=recruiter)
    assert planned.status_code == 200, planned.text
    internship = planned.json()["internship"]
    assert internship["status"] == "PLANNED" and internship["duration_months"] == 6
    # The stipend is snapshotted, but only finance users see it.
    assert internship["stipend_amount"] is None
    admin_view = (await client.get(f"{PROGRAMS}/{pid}/enrollments/{eid}", headers=ctx["headers"])).json()
    assert admin_view["internship"]["stipend_amount"] == "15000.00"
    assert admin_view["stage"] == "Internship planned"

    no_start = await _act(client, ctx, pid, eid, "internship/status", {"status": "ACTIVE"})
    assert no_start.status_code == 422
    active = (await _act(client, ctx, pid, eid, "internship/status",
                         {"status": "ACTIVE", "on_date": "2027-01-01"})).json()
    assert active["internship"]["end_date"] == "2027-07-01"
    assert active["stage"] == "Internship in progress"
    # Program edits after planning don't change it.
    await client.patch(f"{PROGRAMS}/{pid}", json={"internship_duration_months": 3, "terms_version": "v2"},
                       headers=ctx["headers"])

    not_later = await _act(client, ctx, pid, eid, "internship/extension",
                           {"new_end_date": "2027-06-01", "reason": "More time."})
    assert not_later.status_code == 422
    extended = (await _act(client, ctx, pid, eid, "internship/extension",
                           {"new_end_date": "2027-09-01", "reason": "Project extended."})).json()
    assert extended["internship"]["end_date"] == "2027-09-01"
    assert extended["internship"]["original_end_date"] == "2027-07-01"
    assert extended["internship"]["extension_count"] == 1
    assert extended["internship"]["duration_months"] == 6

    no_outcome = await _act(client, ctx, pid, eid, "internship/status",
                            {"status": "COMPLETED", "on_date": "2027-09-01"})
    assert no_outcome.status_code == 422
    not_qualified = (await _act(client, ctx, pid, eid, "internship/status", {
        "status": "COMPLETED", "on_date": "2027-09-01", "outcome": "NOT_QUALIFIED", "rating": 2})).json()
    assert not_qualified["internship"]["outcome"] == "NOT_QUALIFIED"
    assert [e["event"] for e in not_qualified["internship"]["events"]] == [
        "PLANNED", "ACTIVE", "EXTENDED", "COMPLETED",
    ]
    # Completed is final, and a non-qualifying evaluation doesn't open the review.
    assert (await _act(client, ctx, pid, eid, "internship/status",
                       {"status": "WITHDRAWN", "note": "x"})).status_code == 409
    assert (await _act(client, ctx, pid, eid, "employment-review", {"status": "IN_REVIEW"})).status_code == 409

    report = (await client.get(f"{PROGRAMS}/{pid}/report", headers=ctx["headers"])).json()
    assert report["internship_completed"] == 1 and report["internship_qualified"] == 0


async def test_internships_need_a_tracking_program_and_a_selected_candidate(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "prog-internship-gates")
    plain, eid, _ = await _selected(client, ctx)
    not_tracked = await _act(client, ctx, plain["id"], eid, "internship", {})
    assert not_tracked.status_code == 409
    assert not_tracked.json()["error"]["code"] == "internship_not_tracked"

    program = await _published(
        client, ctx, affiliation_verification_method="NONE", tracks_internship=True,
        internship_duration_months=6,
    )
    await apply_to_program(client, program["id"], email="pending@example.com")
    enrollment = await _enrollment(client, ctx, program["id"])
    unselected = await _act(client, ctx, program["id"], enrollment["id"], "internship", {})
    assert unselected.status_code == 409
    assert unselected.json()["error"]["code"] == "selection_required"

    ended = await _act(client, ctx, plain["id"], eid, "internship/status", {"status": "ACTIVE"})
    assert ended.status_code in (404, 409)
