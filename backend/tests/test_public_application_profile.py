"""The public application form's candidate profile: every field is
mandatory (first HR meeting) except the optional LinkedIn/GitHub URLs and
10th marks (tests/test_candidate_registration_fields.py), the experienced-only fields exactly when the
candidate declares themselves EXPERIENCED, plus the new identity fields
(DOB, place of birth, languages) and mobile-number normalization. The
backend enforces all of it even when the React form is bypassed."""

from httpx import AsyncClient

from app.models.user import User
from tests.public_apply import apply_publicly, verify_email
from tests.test_public_applications import _bootstrap_org_with_open_job


async def _candidate_by_email(client: AsyncClient, ctx: dict, email: str) -> dict:
    listing = await client.get("/api/v1/recruiter/candidates", headers=ctx["admin_headers"])
    (candidate,) = [c for c in listing.json() if c["email"] == email]
    return candidate


async def _candidates(client: AsyncClient, ctx: dict) -> list[dict]:
    return (await client.get("/api/v1/recruiter/candidates", headers=ctx["admin_headers"])).json()


async def test_experienced_applicant_profile_is_stored(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "profile-experienced")

    response = await apply_publicly(
        client,
        ctx["slug"],
        ctx["job_id"],
        email="eli@profile-experienced.dev",
        full_name="Eli Experienced",
        phone="098765 43210",
        date_of_birth="1990-02-20",
        place_of_birth="Madurai",
        languages=["english", "Tamil", "ENGLISH", "Hindi"],
        candidate_type="EXPERIENCED",
        years_experience="6",
        notice_period_days="30",
        current_title="Senior Engineer",
        current_company="Globex",
        current_location="Pune",
        preferred_location="Bengaluru",
        qualification="B.Tech Computer Science",
        linkedin_url="linkedin.com/in/eli",
        github_url="https://github.com/eli",
    )
    assert response.status_code == 201, response.text

    candidate = await _candidate_by_email(client, ctx, "eli@profile-experienced.dev")
    assert candidate["candidate_type"] == "EXPERIENCED"
    assert candidate["years_experience"] == 6
    assert candidate["notice_period_days"] == 30
    assert candidate["immediate_joiner"] is False
    assert candidate["current_title"] == "Senior Engineer"
    assert candidate["current_company"] == "Globex"
    assert candidate["location"] == "Pune"
    assert candidate["preferred_location"] == "Bengaluru"
    assert candidate["qualification"] == "B.Tech Computer Science"
    assert candidate["linkedin_url"] == "https://linkedin.com/in/eli"
    assert candidate["github_url"] == "https://github.com/eli"
    # New identity fields, normalized.
    assert candidate["phone"] == "+919876543210"
    assert candidate["date_of_birth"] == "1990-02-20"
    assert candidate["place_of_birth"] == "Madurai"
    assert candidate["languages"] == ["English", "Tamil", "Hindi"]
    assert candidate["email_verified_at"] is not None
    assert candidate["source"] == "PORTAL"


async def test_immediate_joiner_has_zero_notice_period(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "profile-immediate")

    response = await apply_publicly(
        client,
        ctx["slug"],
        ctx["job_id"],
        email="ida@profile-immediate.dev",
        candidate_type="EXPERIENCED",
        years_experience="3",
        current_title="Engineer",
        current_company="Initech",
        immediate_joiner="true",
    )
    assert response.status_code == 201, response.text

    candidate = await _candidate_by_email(client, ctx, "ida@profile-immediate.dev")
    assert candidate["immediate_joiner"] is True
    assert candidate["notice_period_days"] == 0


async def test_fresher_carries_no_experience_role_or_notice_period(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "profile-fresher")

    response = await apply_publicly(
        client,
        ctx["slug"],
        ctx["job_id"],
        email="fay@profile-fresher.dev",
        candidate_type="FRESHER",
        notice_period_days="60",
        current_company="Should Be Dropped",
    )
    assert response.status_code == 201, response.text

    candidate = await _candidate_by_email(client, ctx, "fay@profile-fresher.dev")
    assert candidate["candidate_type"] == "FRESHER"
    assert candidate["years_experience"] == 0
    assert candidate["notice_period_days"] is None
    assert candidate["current_company"] is None


async def test_every_field_is_mandatory_server_side(
    client: AsyncClient, super_admin: User
) -> None:
    """Bypassing the React form: each missing field on its own is a 422,
    and nothing is created."""
    ctx = await _bootstrap_org_with_open_job(client, "profile-required")
    token = await verify_email(client, ctx["slug"], "req@profile-required.dev")

    for field in (
        "full_name",
        "phone",
        "date_of_birth",
        "place_of_birth",
        "languages",
        "candidate_type",
        "current_location",
        "preferred_location",
        "qualification",
        "email_verification_token",
    ):
        overrides: dict = {field: None}
        response = await apply_publicly(
            client,
            ctx["slug"],
            ctx["job_id"],
            email="req@profile-required.dev",
            token=token if field != "email_verification_token" else "",
            **({} if field == "email_verification_token" else overrides),
        )
        assert response.status_code == 422, (field, response.text)

    # Blank strings are "missing" too.
    blank = await apply_publicly(
        client,
        ctx["slug"],
        ctx["job_id"],
        email="req@profile-required.dev",
        token=token,
        place_of_birth="   ",
    )
    assert blank.status_code == 422

    assert await _candidates(client, ctx) == []


async def test_experienced_applicant_must_provide_experience_role_and_notice_period(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "profile-experienced-required")
    token = await verify_email(client, ctx["slug"], "a@profile-experienced-required.dev")
    complete = {
        "candidate_type": "EXPERIENCED",
        "years_experience": "4",
        "notice_period_days": "30",
        "current_title": "Engineer",
        "current_company": "Globex",
    }
    for missing in ("years_experience", "notice_period_days", "current_title", "current_company"):
        response = await apply_publicly(
            client,
            ctx["slug"],
            ctx["job_id"],
            email="a@profile-experienced-required.dev",
            token=token,
            **{**complete, missing: None},
        )
        assert response.status_code == 422, (missing, response.text)

    assert await _candidates(client, ctx) == []


async def test_identity_fields_are_validated(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_open_job(client, "profile-identity")
    token = await verify_email(client, ctx["slug"], "v@profile-identity.dev")

    for field, bad_value in (
        ("phone", "12345"),
        ("phone", "not a number"),
        ("date_of_birth", "2099-01-01"),
        ("date_of_birth", "2020-01-01"),  # under the minimum applicant age
        ("languages", ["<script>"]),
        ("languages", ["C++"]),
        ("linkedin_url", "https://evil.example.com/in/eli"),
        ("linkedin_url", "javascript:alert(1)"),
        ("github_url", "https://notgithub.com/eli"),
    ):
        response = await apply_publicly(
            client,
            ctx["slug"],
            ctx["job_id"],
            email="v@profile-identity.dev",
            token=token,
            **{field: bad_value},
        )
        assert response.status_code == 422, (field, bad_value, response.text)

    assert await _candidates(client, ctx) == []
