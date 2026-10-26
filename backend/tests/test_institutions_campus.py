"""Institution registry, campus-drive institution association, affiliation
verification and campus reporting (docs/campus-hiring.md § 1a / § 3a / § 5)."""

from httpx import AsyncClient

from app.models.user import User
from tests.org_setup import bootstrap_org, link_token, open_job
from tests.public_apply import apply_to_campus_drive, verify_campus_email


async def _institution(client: AsyncClient, ctx: dict, **fields) -> dict:
    response = await client.post(
        "/api/v1/recruiter/institutions", json={"name": "IIT Madras", **fields}, headers=ctx["headers"]
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _active_drive(client: AsyncClient, ctx: dict, job: dict, **fields) -> tuple[dict, str]:
    created = await client.post(
        "/api/v1/recruiter/campus-drives",
        json={"name": "Fall Drive", "job_id": job["id"], **fields},
        headers=ctx["headers"],
    )
    assert created.status_code == 201, created.text
    drive = created.json()
    activated = await client.patch(
        f"/api/v1/recruiter/campus-drives/{drive['id']}", json={"status": "ACTIVE"}, headers=ctx["headers"]
    )
    assert activated.status_code == 200, activated.text
    return drive, link_token(drive["application_link"])


async def _drive_applications(client: AsyncClient, ctx: dict, **params) -> list[dict]:
    response = await client.get("/api/v1/recruiter/applications", params=params, headers=ctx["headers"])
    assert response.status_code == 200, response.text
    return response.json()


async def test_institution_registry_is_unique_per_org_by_normalized_name(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "inst-unique")
    first = await _institution(client, ctx, email_domains=["@SMAIL.IITM.ac.in"], city="Chennai")
    assert first["email_domains"] == ["smail.iitm.ac.in"]

    duplicate = await client.post(
        "/api/v1/recruiter/institutions", json={"name": "  iit   MADRAS "}, headers=ctx["headers"]
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "institution_exists"

    # A second organization can register the same name independently.
    other = await bootstrap_org(client, "inst-unique-other")
    assert (await _institution(client, other))["id"] != first["id"]
    listing = (await client.get("/api/v1/recruiter/institutions", headers=other["headers"])).json()
    assert [i["id"] for i in listing] != [first["id"]]
    assert len(listing) == 1


async def test_drive_is_associated_with_its_registered_institution(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "inst-drive-assoc")
    job = await open_job(client, ctx)
    institution = await _institution(client, ctx)
    drive, _ = await _active_drive(client, ctx, job, institution_id=institution["id"])
    assert drive["institution_id"] == institution["id"]
    assert drive["institution_name"] == "IIT Madras"
    assert drive["college_name"] == "IIT Madras"

    # The legacy college_name shape resolves to the same registry row
    # instead of creating a second spelling.
    legacy = await client.post(
        "/api/v1/recruiter/campus-drives",
        json={"name": "Spring Drive", "job_id": job["id"], "college_name": "iit madras"},
        headers=ctx["headers"],
    )
    assert legacy.status_code == 201, legacy.text
    assert legacy.json()["institution_id"] == institution["id"]
    institutions = (await client.get("/api/v1/recruiter/institutions", headers=ctx["headers"])).json()
    assert len(institutions) == 1
    assert institutions[0]["campus_drive_count"] == 2

    inline = await client.post(
        "/api/v1/recruiter/campus-drives",
        json={"name": "NIT Drive", "job_id": job["id"], "new_institution": {"name": "NIT Trichy"}},
        headers=ctx["headers"],
    )
    assert inline.status_code == 201
    assert inline.json()["institution_name"] == "NIT Trichy"

    both = await client.post(
        "/api/v1/recruiter/campus-drives",
        json={
            "name": "Bad",
            "job_id": job["id"],
            "institution_id": institution["id"],
            "college_name": "Other",
        },
        headers=ctx["headers"],
    )
    assert both.status_code == 422


async def test_drive_rejects_another_orgs_or_inactive_institution(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "inst-cross-a")
    other = await bootstrap_org(client, "inst-cross-b")
    foreign = await _institution(client, other)
    job = await open_job(client, ctx)

    response = await client.post(
        "/api/v1/recruiter/campus-drives",
        json={"name": "Drive", "job_id": job["id"], "institution_id": foreign["id"]},
        headers=ctx["headers"],
    )
    assert response.status_code == 404
    assert (
        await client.get(f"/api/v1/recruiter/institutions/{foreign['id']}", headers=ctx["headers"])
    ).status_code == 404

    own = await _institution(client, ctx, name="Closed College")
    await client.patch(
        f"/api/v1/recruiter/institutions/{own['id']}", json={"is_active": False}, headers=ctx["headers"]
    )
    inactive = await client.post(
        "/api/v1/recruiter/campus-drives",
        json={"name": "Drive", "job_id": job["id"], "institution_id": own["id"]},
        headers=ctx["headers"],
    )
    assert inactive.status_code == 404


async def test_email_domain_verification_requires_a_registered_domain(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "inst-domain-required")
    job = await open_job(client, ctx)
    institution = await _institution(client, ctx)
    response = await client.post(
        "/api/v1/recruiter/campus-drives",
        json={
            "name": "Drive",
            "job_id": job["id"],
            "institution_id": institution["id"],
            "affiliation_verification_method": "EMAIL_DOMAIN",
        },
        headers=ctx["headers"],
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "institution_has_no_email_domain"


async def test_campus_link_application_keeps_the_drive_and_ignores_tampered_ids(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "inst-tamper")
    job = await open_job(client, ctx)
    other_job = await open_job(client, ctx, title="Other Role")
    madras = await _institution(client, ctx)
    trichy = await _institution(client, ctx, name="NIT Trichy")
    drive, token = await _active_drive(client, ctx, job, institution_id=madras["id"])
    other_drive, _ = await _active_drive(client, ctx, other_job, institution_id=trichy["id"])

    response = await apply_to_campus_drive(
        client,
        token,
        email="asha@example.com",
        # Fields the server must never trust from a candidate.
        campus_drive_id=other_drive["id"],
        institution_id=trichy["id"],
        job_id=other_job["id"],
    )
    assert response.status_code == 201, response.text

    applications = await _drive_applications(client, ctx, campus_drive_id=drive["id"])
    assert [a["candidate_email"] for a in applications] == ["asha@example.com"]
    assert applications[0]["job_id"] == job["id"]
    assert await _drive_applications(client, ctx, campus_drive_id=other_drive["id"]) == []

    # HR can filter by institution: only the drive's institution matches.
    assert len(await _drive_applications(client, ctx, institution_id=madras["id"])) == 1
    assert await _drive_applications(client, ctx, institution_id=trichy["id"]) == []


async def test_opening_a_link_never_verifies_affiliation_by_itself(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "inst-manual-verify")
    job = await open_job(client, ctx)
    institution = await _institution(client, ctx)
    drive, token = await _active_drive(
        client, ctx, job, institution_id=institution["id"], affiliation_verification_method="MANUAL"
    )
    assert (await apply_to_campus_drive(client, token, email="ravi@example.com")).status_code == 201

    verifications = (
        await client.get(f"/api/v1/recruiter/campus-drives/{drive['id']}/verifications", headers=ctx["headers"])
    ).json()
    assert len(verifications) == 1
    record = verifications[0]
    assert record["status"] == "PENDING"
    assert record["method"] is None

    base = f"/api/v1/recruiter/campus-drives/{drive['id']}/verifications/{record['id']}/review"
    no_note = await client.post(base, json={"status": "REJECTED"}, headers=ctx["headers"])
    assert no_note.status_code == 409
    verified = await client.post(
        base, json={"status": "VERIFIED", "note": "Placement office confirmed."}, headers=ctx["headers"]
    )
    assert verified.status_code == 200, verified.text
    body = verified.json()
    assert body["status"] == "VERIFIED"
    assert body["method"] == "MANUAL"
    assert body["reviewed_by_name"] == "Acme Admin"
    assert body["reviewed_at"] is not None

    funnel = (
        await client.get(f"/api/v1/recruiter/campus-drives/{drive['id']}/funnel", headers=ctx["headers"])
    ).json()
    assert funnel["verified"] == 1 and funnel["verification_pending"] == 0


async def test_email_domain_match_verifies_only_matching_verified_emails(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "inst-domain-verify")
    job = await open_job(client, ctx)
    institution = await _institution(client, ctx, email_domains=["iitm.ac.in"])
    drive, token = await _active_drive(
        client, ctx, job, institution_id=institution["id"], affiliation_verification_method="EMAIL_DOMAIN"
    )
    assert (await apply_to_campus_drive(client, token, email="meera@cse.iitm.ac.in")).status_code == 201
    assert (await apply_to_campus_drive(client, token, email="kiran@notiitm.ac.in")).status_code == 201

    records = {
        r["candidate_email"]: r
        for r in (
            await client.get(
                f"/api/v1/recruiter/campus-drives/{drive['id']}/verifications", headers=ctx["headers"]
            )
        ).json()
    }
    assert records["meera@cse.iitm.ac.in"]["status"] == "VERIFIED"
    assert records["meera@cse.iitm.ac.in"]["method"] == "EMAIL_DOMAIN"
    assert records["kiran@notiitm.ac.in"]["status"] == "PENDING"


async def test_student_list_verification(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "inst-roster")
    job = await open_job(client, ctx)
    institution = await _institution(client, ctx)
    drive, token = await _active_drive(
        client, ctx, job, institution_id=institution["id"], affiliation_verification_method="STUDENT_LIST"
    )
    roster = f"/api/v1/recruiter/campus-drives/{drive['id']}/roster"

    # Applies before the list arrives: pending.
    assert (await apply_to_campus_drive(client, token, email="late@example.com")).status_code == 201
    added = await client.post(
        roster,
        json={"entries": [
            {"email": "Late@Example.com", "student_identifier": "CS21B001"},
            {"email": "early@example.com"},
            {"email": "early@example.com"},
        ]},
        headers=ctx["headers"],
    )
    assert added.status_code == 200, added.text
    assert added.json() == {"added": 2, "total": 2}

    # On the list at application time: verified immediately.
    assert (await apply_to_campus_drive(client, token, email="early@example.com")).status_code == 201
    statuses = {
        r["candidate_email"]: (r["status"], r["method"])
        for r in (
            await client.get(
                f"/api/v1/recruiter/campus-drives/{drive['id']}/verifications", headers=ctx["headers"]
            )
        ).json()
    }
    assert statuses == {
        "late@example.com": ("VERIFIED", "STUDENT_LIST"),
        "early@example.com": ("VERIFIED", "STUDENT_LIST"),
    }


async def test_campus_report_distinguishes_drives_and_filters_by_institution(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "inst-report")
    job = await open_job(client, ctx)
    job_two = await open_job(client, ctx, title="Second Role")
    madras = await _institution(client, ctx)
    trichy = await _institution(client, ctx, name="NIT Trichy")
    drive_a, token_a = await _active_drive(
        client, ctx, job, institution_id=madras["id"], affiliation_verification_method="MANUAL"
    )
    drive_b, token_b = await _active_drive(client, ctx, job_two, institution_id=trichy["id"])
    await apply_to_campus_drive(client, token_a, email="one@example.com")
    await apply_to_campus_drive(client, token_a, email="two@example.com")
    await apply_to_campus_drive(client, token_b, email="three@example.com")

    rows = {
        r["drive_id"]: r
        for r in (await client.get("/api/v1/recruiter/campus-drives/report", headers=ctx["headers"])).json()
    }
    assert rows[drive_a["id"]]["applications"] == 2
    assert rows[drive_a["id"]]["verification_pending"] == 2
    assert rows[drive_a["id"]]["institution_name"] == "IIT Madras"
    assert rows[drive_b["id"]]["applications"] == 1
    assert rows[drive_b["id"]]["verification_pending"] == 0

    filtered = (
        await client.get(
            "/api/v1/recruiter/campus-drives/report",
            params={"institution_id": trichy["id"]},
            headers=ctx["headers"],
        )
    ).json()
    assert [r["drive_id"] for r in filtered] == [drive_b["id"]]

    overview = (await client.get("/api/v1/recruiter/reports/overview", headers=ctx["headers"])).json()
    by_drive = {d["drive_id"]: d for d in overview["campus_drives"]}
    assert by_drive[drive_a["id"]]["institution_name"] == "IIT Madras"


async def test_one_profile_across_drives_and_reapply_window_still_applies(
    client: AsyncClient, super_admin: User
) -> None:
    """A second drive application by the same person is governed by the
    existing reapply rules (one self-service application per window) and
    never creates a second candidate profile."""
    ctx = await bootstrap_org(client, "inst-two-drives")
    job = await open_job(client, ctx)
    job_two = await open_job(client, ctx, title="Second Role")
    institution = await _institution(client, ctx)
    _, token_a = await _active_drive(client, ctx, job, institution_id=institution["id"])
    _, token_b = await _active_drive(client, ctx, job_two, institution_id=institution["id"])

    assert (await apply_to_campus_drive(client, token_a, email="same@example.com")).status_code == 201
    code_request = await client.post(
        f"/api/v1/public/campus-drive/{token_b}/email-verification/request",
        json={"email": "same@example.com"},
    )
    assert code_request.status_code == 409
    assert code_request.json()["error"]["code"] == "already_applied"

    candidates = (await client.get("/api/v1/recruiter/candidates", headers=ctx["headers"])).json()
    assert [c["email"] for c in candidates] == ["same@example.com"]


async def test_campus_verification_and_report_are_tenant_isolated(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "inst-iso-a")
    intruder = await bootstrap_org(client, "inst-iso-b")
    job = await open_job(client, ctx)
    institution = await _institution(client, ctx)
    drive, token = await _active_drive(
        client, ctx, job, institution_id=institution["id"], affiliation_verification_method="MANUAL"
    )
    await apply_to_campus_drive(client, token, email="private@example.com")
    record = (
        await client.get(f"/api/v1/recruiter/campus-drives/{drive['id']}/verifications", headers=ctx["headers"])
    ).json()[0]

    base = f"/api/v1/recruiter/campus-drives/{drive['id']}"
    assert (await client.get(f"{base}/verifications", headers=intruder["headers"])).status_code == 404
    assert (await client.get(f"{base}/roster", headers=intruder["headers"])).status_code == 404
    review = await client.post(
        f"{base}/verifications/{record['id']}/review",
        json={"status": "VERIFIED"},
        headers=intruder["headers"],
    )
    assert review.status_code == 404
    report = (await client.get("/api/v1/recruiter/campus-drives/report", headers=intruder["headers"])).json()
    assert report == []


async def test_existing_campus_otp_flow_is_unchanged(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "inst-otp")
    job = await open_job(client, ctx)
    _, token = await _active_drive(client, ctx, job, college_name="Anna University")
    # The verification token from the one-time code is still required.
    verified = await verify_campus_email(client, token, "otp@example.com")
    assert verified
    bad = await apply_to_campus_drive(client, token, email="otp@example.com", token="not-a-real-token")
    assert bad.status_code in (400, 401, 403, 409, 422)
