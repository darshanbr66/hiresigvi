"""Assessments: recruiter creation, candidate invitation (opaque-token
access, docs/assessment.md § 4), and MCQ auto-scoring on submission."""

from httpx import AsyncClient

from app.models.user import User
from tests.conftest import SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD, login, make_minimal_pdf
from tests.public_apply import apply_publicly

_ASSESSMENT_PAYLOAD = {
    "title": "Python Basics",
    "instructions": "Answer all questions within the time limit.",
    "duration_minutes": 30,
    "pass_score": 50,
    "questions": [
        {
            "prompt": "Which of these are valid Python data types?",
            "type": "MCQ_MULTI",
            "points": 2,
            "options": [
                {"label": "list", "is_correct": True},
                {"label": "dict", "is_correct": True},
                {"label": "fruit", "is_correct": False},
            ],
        },
        {
            "prompt": "What does len([1,2,3]) return?",
            "type": "MCQ_SINGLE",
            "points": 1,
            "options": [
                {"label": "2", "is_correct": False},
                {"label": "3", "is_correct": True},
            ],
        },
    ],
}


async def _bootstrap_org_with_screening_application(client: AsyncClient, slug: str) -> dict:
    super_admin_tokens = await login(client, email=SUPER_ADMIN_EMAIL, password=SUPER_ADMIN_PASSWORD)
    org_payload = {
        "name": "Acme Corp",
        "slug": slug,
        "admin_email": f"admin@{slug}.dev",
        "admin_password": "AcmeAdminPass1",
        "admin_full_name": "Acme Admin",
    }
    await client.post(
        "/api/v1/admin/organizations",
        json=org_payload,
        headers={"Authorization": f"Bearer {super_admin_tokens['access_token']}"},
    )
    admin_tokens = await login(
        client, email=org_payload["admin_email"], password=org_payload["admin_password"]
    )
    headers = {"Authorization": f"Bearer {admin_tokens['access_token']}"}

    job = (
        await client.post(
            "/api/v1/recruiter/jobs",
            json={"title": "Backend Engineer", "description": "Python role."},
            headers=headers,
        )
    ).json()
    await client.patch(
        f"/api/v1/recruiter/jobs/{job['id']}", json={"status": "OPEN"}, headers=headers
    )

    resume_bytes = make_minimal_pdf("Jane Candidate")
    apply_response = await apply_publicly(
        client,
        slug,
        job["id"],
        email="jane@example.com",
        resume=("resume.pdf", resume_bytes, "application/pdf"),
        isolate_welcome_email=True,
    )
    application_id = apply_response.json()["id"]

    # Move the application to SCREENING — assessment invitations are only
    # legal from that state (app/workflows/application_workflow.py).
    await client.post(
        f"/api/v1/recruiter/applications/{application_id}/status",
        json={"to_status": "UNDER_REVIEW"},
        headers=headers,
    )
    await client.post(
        f"/api/v1/recruiter/applications/{application_id}/status",
        json={"to_status": "SCREENING"},
        headers=headers,
    )

    return {"headers": headers, "application_id": application_id}


async def test_create_assessment_with_questions(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-create")

    response = await client.post(
        "/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=ctx["headers"]
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["title"] == "Python Basics"
    assert len(body["questions"]) == 2
    assert len(body["questions"][0]["options"]) == 3

    listing = await client.get("/api/v1/recruiter/assessments", headers=ctx["headers"])
    assert listing.status_code == 200
    assert listing.json()[0]["question_count"] == 2


async def test_invite_candidate_moves_application_to_assessment_invited(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-invite")
    assessment = (
        await client.post(
            "/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=ctx["headers"]
        )
    ).json()

    response = await client.post(
        "/api/v1/recruiter/assessments/invite",
        json={"assessment_id": assessment["id"], "application_id": ctx["application_id"]},
        headers=ctx["headers"],
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "SENT"
    assert body["invitation_link"] is not None
    assert "/assessment/" in body["invitation_link"]

    application = (
        await client.get(
            f"/api/v1/recruiter/applications/{ctx['application_id']}", headers=ctx["headers"]
        )
    ).json()
    assert application["status"] == "ASSESSMENT_INVITED"


def _extract_token(invitation_link: str) -> str:
    return invitation_link.rsplit("/", 1)[-1]


async def test_candidate_can_start_and_submit_via_token_no_auth(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-full-flow")
    assessment = (
        await client.post(
            "/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=ctx["headers"]
        )
    ).json()
    invitation = (
        await client.post(
            "/api/v1/recruiter/assessments/invite",
            json={"assessment_id": assessment["id"], "application_id": ctx["application_id"]},
            headers=ctx["headers"],
        )
    ).json()
    token = _extract_token(invitation["invitation_link"])

    # Candidate view: no auth header, no is_correct anywhere in the payload.
    view = await client.get(f"/api/v1/public/assessment/{token}")
    assert view.status_code == 200
    view_body = view.json()
    assert view_body["assessment_title"] == "Python Basics"
    assert "is_correct" not in view.text

    start = await client.post(f"/api/v1/public/assessment/{token}/start")
    assert start.status_code == 200
    assert start.json()["status"] == "STARTED"

    application_after_start = (
        await client.get(
            f"/api/v1/recruiter/applications/{ctx['application_id']}", headers=ctx["headers"]
        )
    ).json()
    assert application_after_start["status"] == "ASSESSMENT_STARTED"

    q1_id = view_body["questions"][0]["id"]
    q1_correct_ids = [
        o["id"] for o in assessment["questions"][0]["options"] if o["is_correct"]
    ]
    q2_id = view_body["questions"][1]["id"]
    q2_correct_ids = [
        o["id"] for o in assessment["questions"][1]["options"] if o["is_correct"]
    ]

    submit = await client.post(
        f"/api/v1/public/assessment/{token}/submit",
        json={
            "answers": [
                {"question_id": q1_id, "selected_option_ids": q1_correct_ids},
                {"question_id": q2_id, "selected_option_ids": q2_correct_ids},
            ]
        },
    )
    assert submit.status_code == 200, submit.text
    result = submit.json()
    # Candidate-facing result carries no score — recruiters/admins see it
    # separately via the recruiter endpoint below (ZPHIREX platform
    # overhaul § 4: never expose the score to the candidate).
    assert set(result.keys()) == {"submitted_at"}

    application_after_submit = (
        await client.get(
            f"/api/v1/recruiter/applications/{ctx['application_id']}", headers=ctx["headers"]
        )
    ).json()
    assert application_after_submit["status"] == "ASSESSMENT_COMPLETED"

    recruiter_view = (
        await client.get(
            f"/api/v1/recruiter/applications/{ctx['application_id']}/assessment",
            headers=ctx["headers"],
        )
    ).json()
    assert recruiter_view["status"] == "SUBMITTED"
    assert recruiter_view["result"]["percentage"] == 100


async def test_partial_wrong_answers_score_correctly(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-partial")
    assessment = (
        await client.post(
            "/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=ctx["headers"]
        )
    ).json()
    invitation = (
        await client.post(
            "/api/v1/recruiter/assessments/invite",
            json={"assessment_id": assessment["id"], "application_id": ctx["application_id"]},
            headers=ctx["headers"],
        )
    ).json()
    token = _extract_token(invitation["invitation_link"])
    view = (await client.get(f"/api/v1/public/assessment/{token}")).json()
    await client.post(f"/api/v1/public/assessment/{token}/start")

    q1_id = view["questions"][0]["id"]
    q2_id = view["questions"][1]["id"]
    wrong_q2_option = next(
        o["id"] for o, opt in zip(view["questions"][1]["options"], assessment["questions"][1]["options"])
        if not opt["is_correct"]
    )

    submit = await client.post(
        f"/api/v1/public/assessment/{token}/submit",
        json={
            "answers": [
                {"question_id": q1_id, "selected_option_ids": []},
                {"question_id": q2_id, "selected_option_ids": [wrong_q2_option]},
            ]
        },
    )
    assert submit.status_code == 200
    assert set(submit.json().keys()) == {"submitted_at"}  # no score shown to the candidate

    recruiter_view = (
        await client.get(
            f"/api/v1/recruiter/applications/{ctx['application_id']}/assessment",
            headers=ctx["headers"],
        )
    ).json()
    assert recruiter_view["result"]["score"] == 0
    assert recruiter_view["result"]["passed"] is False


async def test_invalid_token_returns_generic_not_found(client: AsyncClient) -> None:
    response = await client.get("/api/v1/public/assessment/not-a-real-token")
    assert response.status_code == 404


async def test_cannot_submit_twice(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-resubmit")
    assessment = (
        await client.post(
            "/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=ctx["headers"]
        )
    ).json()
    invitation = (
        await client.post(
            "/api/v1/recruiter/assessments/invite",
            json={"assessment_id": assessment["id"], "application_id": ctx["application_id"]},
            headers=ctx["headers"],
        )
    ).json()
    token = _extract_token(invitation["invitation_link"])
    view = (await client.get(f"/api/v1/public/assessment/{token}")).json()
    q1_id = view["questions"][0]["id"]

    await client.post(f"/api/v1/public/assessment/{token}/start")
    await client.post(
        f"/api/v1/public/assessment/{token}/submit",
        json={"answers": [{"question_id": q1_id, "selected_option_ids": []}]},
    )
    second = await client.post(
        f"/api/v1/public/assessment/{token}/submit",
        json={"answers": [{"question_id": q1_id, "selected_option_ids": []}]},
    )
    assert second.status_code == 400
    assert second.json()["error"]["code"] == "already_submitted"


async def test_invite_fails_when_application_not_in_screening(
    client: AsyncClient, super_admin: User
) -> None:
    super_admin_tokens = await login(client, email=SUPER_ADMIN_EMAIL, password=SUPER_ADMIN_PASSWORD)
    org_payload = {
        "name": "Acme Corp",
        "slug": "assess-wrong-state",
        "admin_email": "admin@assess-wrong-state.dev",
        "admin_password": "AcmeAdminPass1",
        "admin_full_name": "Acme Admin",
    }
    await client.post(
        "/api/v1/admin/organizations",
        json=org_payload,
        headers={"Authorization": f"Bearer {super_admin_tokens['access_token']}"},
    )
    admin_tokens = await login(
        client, email=org_payload["admin_email"], password=org_payload["admin_password"]
    )
    headers = {"Authorization": f"Bearer {admin_tokens['access_token']}"}

    job = (
        await client.post(
            "/api/v1/recruiter/jobs",
            json={"title": "Backend Engineer", "description": "..."},
            headers=headers,
        )
    ).json()
    candidate = (
        await client.post(
            "/api/v1/recruiter/candidates",
            json={"email": "c1@example.com", "full_name": "Cara Candidate"},
            headers=headers,
        )
    ).json()
    application = (
        await client.post(
            "/api/v1/recruiter/applications",
            json={"candidate_id": candidate["id"], "job_id": job["id"]},
            headers=headers,
        )
    ).json()
    assessment = (
        await client.post("/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=headers)
    ).json()

    response = await client.post(
        "/api/v1/recruiter/assessments/invite",
        json={"assessment_id": assessment["id"], "application_id": application["id"]},
        headers=headers,
    )
    assert response.status_code == 409


def _csv_upload(content: bytes, filename: str = "questions.csv"):
    return {"file": (filename, content, "text/csv")}


async def test_parse_questions_from_csv_returns_preview_without_persisting(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-import-csv")
    csv_bytes = (
        b"question,option_1,option_2,option_3,correct\n"
        b"What is 2+2?,3,4,5,2\n"
        b"Unanswerable row,A,B,C,\n"
    )

    response = await client.post(
        "/api/v1/recruiter/assessments/parse-questions",
        files=_csv_upload(csv_bytes),
        headers=ctx["headers"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["questions"]) == 1
    assert body["questions"][0]["prompt"] == "What is 2+2?"
    assert len(body["warnings"]) == 1

    # Nothing was persisted — parsing is preview-only.
    listing = await client.get("/api/v1/recruiter/assessments", headers=ctx["headers"])
    assert listing.json() == []


async def test_parse_questions_preview_can_be_submitted_as_a_real_assessment(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-import-then-create")
    csv_bytes = b"question,option_1,option_2,correct\nCapital of France?,Berlin,Paris,2\n"

    parsed = (
        await client.post(
            "/api/v1/recruiter/assessments/parse-questions",
            files=_csv_upload(csv_bytes),
            headers=ctx["headers"],
        )
    ).json()

    create_response = await client.post(
        "/api/v1/recruiter/assessments",
        json={
            "title": "Imported Assessment",
            "instructions": "Answer everything.",
            "duration_minutes": 20,
            "pass_score": 50,
            "questions": parsed["questions"],
        },
        headers=ctx["headers"],
    )
    assert create_response.status_code == 201, create_response.text
    assert len(create_response.json()["questions"]) == 1


async def test_parse_questions_rejects_unsupported_file_type(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-import-badtype")
    response = await client.post(
        "/api/v1/recruiter/assessments/parse-questions",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        headers=ctx["headers"],
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "question_import_failed"


async def test_retest_preserves_original_attempt_and_creates_a_new_one(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-retest")
    assessment = (
        await client.post(
            "/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=ctx["headers"]
        )
    ).json()
    invitation = (
        await client.post(
            "/api/v1/recruiter/assessments/invite",
            json={"assessment_id": assessment["id"], "application_id": ctx["application_id"]},
            headers=ctx["headers"],
        )
    ).json()
    token = _extract_token(invitation["invitation_link"])
    view = (await client.get(f"/api/v1/public/assessment/{token}")).json()
    await client.post(f"/api/v1/public/assessment/{token}/start")

    q1_id = view["questions"][0]["id"]
    q2_id = view["questions"][1]["id"]
    submit = await client.post(
        f"/api/v1/public/assessment/{token}/submit",
        json={
            "answers": [
                {"question_id": q1_id, "selected_option_ids": []},
                {"question_id": q2_id, "selected_option_ids": []},
            ]
        },
    )
    assert submit.status_code == 200

    # Retesting without a reason is rejected.
    no_reason = await client.post(
        "/api/v1/recruiter/assessments/retest",
        json={"application_id": ctx["application_id"], "reason": ""},
        headers=ctx["headers"],
    )
    assert no_reason.status_code == 422

    retest = await client.post(
        "/api/v1/recruiter/assessments/retest",
        json={
            "application_id": ctx["application_id"],
            "reason": "Candidate experienced network interruption.",
        },
        headers=ctx["headers"],
    )
    assert retest.status_code == 201, retest.text
    retest_body = retest.json()
    assert retest_body["attempt_number"] == 2
    assert retest_body["status"] == "SENT"
    assert retest_body["result"] is None
    assert retest_body["retest_reason"] == "Candidate experienced network interruption."

    application_after_retest = (
        await client.get(
            f"/api/v1/recruiter/applications/{ctx['application_id']}", headers=ctx["headers"]
        )
    ).json()
    assert application_after_retest["status"] == "ASSESSMENT_INVITED"

    attempts = await client.get(
        f"/api/v1/recruiter/applications/{ctx['application_id']}/assessment/attempts",
        headers=ctx["headers"],
    )
    assert attempts.status_code == 200
    attempt_bodies = attempts.json()
    assert len(attempt_bodies) == 2
    assert attempt_bodies[0]["attempt_number"] == 1
    assert attempt_bodies[0]["status"] == "SUBMITTED"
    assert attempt_bodies[0]["result"]["passed"] is False
    assert attempt_bodies[1]["attempt_number"] == 2
    assert attempt_bodies[1]["status"] == "SENT"

    activities = await client.get("/api/v1/recruiter/activities", headers=ctx["headers"])
    actions = [a["action"] for a in activities.json()]
    assert "ASSESSMENT_RETEST_CREATED" in actions

    # The retest token is a fresh submission surface — completing it should
    # not touch the original attempt's result.
    retest_token = _extract_token(retest_body["invitation_link"])
    retest_view = (await client.get(f"/api/v1/public/assessment/{retest_token}")).json()
    await client.post(f"/api/v1/public/assessment/{retest_token}/start")
    retest_q1 = retest_view["questions"][0]["id"]
    retest_q1_correct = [
        o["id"] for o in assessment["questions"][0]["options"] if o["is_correct"]
    ]
    retest_q2 = retest_view["questions"][1]["id"]
    retest_q2_correct = [
        o["id"] for o in assessment["questions"][1]["options"] if o["is_correct"]
    ]
    retest_submit = await client.post(
        f"/api/v1/public/assessment/{retest_token}/submit",
        json={
            "answers": [
                {"question_id": retest_q1, "selected_option_ids": retest_q1_correct},
                {"question_id": retest_q2, "selected_option_ids": retest_q2_correct},
            ]
        },
    )
    assert retest_submit.status_code == 200

    final_attempts = (
        await client.get(
            f"/api/v1/recruiter/applications/{ctx['application_id']}/assessment/attempts",
            headers=ctx["headers"],
        )
    ).json()
    assert final_attempts[0]["result"]["passed"] is False  # original attempt untouched
    assert final_attempts[1]["result"]["passed"] is True


async def test_retest_rejected_before_current_attempt_is_submitted(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-retest-early")
    assessment = (
        await client.post(
            "/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=ctx["headers"]
        )
    ).json()
    await client.post(
        "/api/v1/recruiter/assessments/invite",
        json={"assessment_id": assessment["id"], "application_id": ctx["application_id"]},
        headers=ctx["headers"],
    )

    response = await client.post(
        "/api/v1/recruiter/assessments/retest",
        json={"application_id": ctx["application_id"], "reason": "Too early."},
        headers=ctx["headers"],
    )
    assert response.status_code == 409


async def test_parse_questions_requires_assessment_manage_permission(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/recruiter/assessments/parse-questions",
        files=_csv_upload(b"question,option_1,option_2,correct\nQ?,A,B,1\n"),
    )
    assert response.status_code == 401


async def _invite_and_submit_first_attempt(client: AsyncClient, ctx: dict, assessment: dict) -> None:
    invitation = (
        await client.post(
            "/api/v1/recruiter/assessments/invite",
            json={"assessment_id": assessment["id"], "application_id": ctx["application_id"]},
            headers=ctx["headers"],
        )
    ).json()
    token = _extract_token(invitation["invitation_link"])
    view = (await client.get(f"/api/v1/public/assessment/{token}")).json()
    await client.post(f"/api/v1/public/assessment/{token}/start")
    submit = await client.post(
        f"/api/v1/public/assessment/{token}/submit",
        json={
            "answers": [
                {"question_id": view["questions"][0]["id"], "selected_option_ids": []},
                {"question_id": view["questions"][1]["id"], "selected_option_ids": []},
            ]
        },
    )
    assert submit.status_code == 200


async def test_retest_with_an_existing_assessment_uses_the_chosen_assessment(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-retest-existing")
    first_assessment = (
        await client.post(
            "/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=ctx["headers"]
        )
    ).json()
    other_payload = {**_ASSESSMENT_PAYLOAD, "title": "Advanced Python"}
    other_assessment = (
        await client.post(
            "/api/v1/recruiter/assessments", json=other_payload, headers=ctx["headers"]
        )
    ).json()
    await _invite_and_submit_first_attempt(client, ctx, first_assessment)

    retest = await client.post(
        "/api/v1/recruiter/assessments/retest",
        json={
            "application_id": ctx["application_id"],
            "reason": "Give them a different assessment.",
            "assessment_choice": "EXISTING",
            "assessment_id": other_assessment["id"],
        },
        headers=ctx["headers"],
    )
    assert retest.status_code == 201, retest.text
    body = retest.json()
    assert body["assessment_id"] == other_assessment["id"]
    assert body["assessment_title"] == "Advanced Python"
    assert body["attempt_number"] == 2

    # The original attempt's own assessment link is untouched.
    attempts = (
        await client.get(
            f"/api/v1/recruiter/applications/{ctx['application_id']}/assessment/attempts",
            headers=ctx["headers"],
        )
    ).json()
    assert attempts[0]["assessment_id"] == first_assessment["id"]
    assert attempts[1]["assessment_id"] == other_assessment["id"]


async def test_retest_with_a_new_assessment_creates_it_via_the_normal_create_flow(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-retest-new")
    assessment = (
        await client.post(
            "/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=ctx["headers"]
        )
    ).json()
    await _invite_and_submit_first_attempt(client, ctx, assessment)

    new_payload = {**_ASSESSMENT_PAYLOAD, "title": "Retest-only Assessment"}
    retest = await client.post(
        "/api/v1/recruiter/assessments/retest",
        json={
            "application_id": ctx["application_id"],
            "reason": "Needs a fresh set of questions.",
            "assessment_choice": "NEW",
            "new_assessment": new_payload,
        },
        headers=ctx["headers"],
    )
    assert retest.status_code == 201, retest.text
    body = retest.json()
    assert body["assessment_title"] == "Retest-only Assessment"

    listing = await client.get("/api/v1/recruiter/assessments", headers=ctx["headers"])
    titles = [a["title"] for a in listing.json()]
    assert "Retest-only Assessment" in titles


async def test_retest_existing_choice_requires_assessment_id(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-retest-existing-missing")
    assessment = (
        await client.post(
            "/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=ctx["headers"]
        )
    ).json()
    await _invite_and_submit_first_attempt(client, ctx, assessment)

    response = await client.post(
        "/api/v1/recruiter/assessments/retest",
        json={
            "application_id": ctx["application_id"],
            "reason": "Missing target assessment.",
            "assessment_choice": "EXISTING",
        },
        headers=ctx["headers"],
    )
    assert response.status_code == 422


async def test_delete_assessment_soft_deletes_and_records_an_activity(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-delete-happy")
    assessment = (
        await client.post(
            "/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=ctx["headers"]
        )
    ).json()

    delete_response = await client.post(
        f"/api/v1/recruiter/assessments/{assessment['id']}/delete",
        json={"reason": "Outdated question set."},
        headers=ctx["headers"],
    )
    assert delete_response.status_code == 200, delete_response.text
    assert delete_response.json()["deleted_at"] is not None

    listing = await client.get("/api/v1/recruiter/assessments", headers=ctx["headers"])
    assert assessment["id"] not in [a["id"] for a in listing.json()]

    second_delete = await client.post(
        f"/api/v1/recruiter/assessments/{assessment['id']}/delete",
        json={"reason": "Again."},
        headers=ctx["headers"],
    )
    assert second_delete.status_code == 409

    activities = await client.get("/api/v1/recruiter/activities", headers=ctx["headers"])
    delete_entries = [a for a in activities.json() if a["action"] == "ASSESSMENT_DELETED"]
    assert len(delete_entries) == 1
    assert delete_entries[0]["entity_id"] == assessment["id"]


async def test_deleted_assessment_cannot_be_used_for_a_new_invitation(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-delete-invite-guard")
    assessment = (
        await client.post(
            "/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=ctx["headers"]
        )
    ).json()
    await client.post(
        f"/api/v1/recruiter/assessments/{assessment['id']}/delete",
        json={"reason": "Retired."},
        headers=ctx["headers"],
    )

    response = await client.post(
        "/api/v1/recruiter/assessments/invite",
        json={"assessment_id": assessment["id"], "application_id": ctx["application_id"]},
        headers=ctx["headers"],
    )
    assert response.status_code == 404


async def test_get_assessment_returns_full_detail_for_viewing(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-view")
    created = (
        await client.post(
            "/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=ctx["headers"]
        )
    ).json()
    assert created["has_invitations"] is False

    response = await client.get(
        f"/api/v1/recruiter/assessments/{created['id']}", headers=ctx["headers"]
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == _ASSESSMENT_PAYLOAD["title"]
    assert body["instructions"] == _ASSESSMENT_PAYLOAD["instructions"]
    assert len(body["questions"]) == 2
    assert body["questions"][0]["options"][0]["is_correct"] is not None
    assert body["has_invitations"] is False


async def test_update_assessment_applies_only_provided_fields(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-update")
    created = (
        await client.post(
            "/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=ctx["headers"]
        )
    ).json()

    response = await client.patch(
        f"/api/v1/recruiter/assessments/{created['id']}",
        json={"title": "Python Basics (v2)", "pass_score": 70},
        headers=ctx["headers"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Python Basics (v2)"
    assert body["pass_score"] == 70
    assert body["instructions"] == _ASSESSMENT_PAYLOAD["instructions"]  # untouched
    assert len(body["questions"]) == 2  # untouched

    activities = await client.get("/api/v1/recruiter/activities", headers=ctx["headers"])
    actions = [a["action"] for a in activities.json()]
    assert "ASSESSMENT_UPDATED" in actions


async def test_update_assessment_can_replace_questions_before_any_invitation(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-update-questions")
    created = (
        await client.post(
            "/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=ctx["headers"]
        )
    ).json()

    new_questions = [
        {
            "prompt": "What is 2 + 2?",
            "type": "MCQ_SINGLE",
            "points": 1,
            "options": [
                {"label": "3", "is_correct": False},
                {"label": "4", "is_correct": True},
            ],
        }
    ]
    response = await client.patch(
        f"/api/v1/recruiter/assessments/{created['id']}",
        json={"questions": new_questions},
        headers=ctx["headers"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["questions"]) == 1
    assert body["questions"][0]["prompt"] == "What is 2 + 2?"


async def test_update_assessment_questions_blocked_once_invitation_exists(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-update-guard")
    created = (
        await client.post(
            "/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=ctx["headers"]
        )
    ).json()
    await client.post(
        "/api/v1/recruiter/assessments/invite",
        json={"assessment_id": created["id"], "application_id": ctx["application_id"]},
        headers=ctx["headers"],
    )

    detail = (
        await client.get(f"/api/v1/recruiter/assessments/{created['id']}", headers=ctx["headers"])
    ).json()
    assert detail["has_invitations"] is True

    response = await client.patch(
        f"/api/v1/recruiter/assessments/{created['id']}",
        json={"questions": [{"prompt": "New?", "type": "MCQ_SINGLE", "points": 1, "options": [
            {"label": "Yes", "is_correct": True}, {"label": "No", "is_correct": False},
        ]}]},
        headers=ctx["headers"],
    )
    assert response.status_code == 409

    # Non-structural fields remain editable even after invitations exist.
    still_editable = await client.patch(
        f"/api/v1/recruiter/assessments/{created['id']}",
        json={"duration_minutes": 45},
        headers=ctx["headers"],
    )
    assert still_editable.status_code == 200
    assert still_editable.json()["duration_minutes"] == 45


async def _start_attempt(client: AsyncClient, ctx: dict) -> str:
    assessment = (
        await client.post(
            "/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=ctx["headers"]
        )
    ).json()
    invitation = (
        await client.post(
            "/api/v1/recruiter/assessments/invite",
            json={"assessment_id": assessment["id"], "application_id": ctx["application_id"]},
            headers=ctx["headers"],
        )
    ).json()
    token = _extract_token(invitation["invitation_link"])
    await client.post(f"/api/v1/public/assessment/{token}/start")
    return token


async def test_monitoring_events_recorded_while_started(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-monitoring")
    token = await _start_attempt(client, ctx)

    response = await client.post(
        f"/api/v1/public/assessment/{token}/events",
        json={
            "events": [
                {"event_type": "MONITORING_CONSENT_GIVEN", "occurred_at": "2026-01-01T10:00:00Z"},
                {"event_type": "TAB_SWITCH", "occurred_at": "2026-01-01T10:05:00Z"},
                {
                    "event_type": "FULLSCREEN_EXIT",
                    "occurred_at": "2026-01-01T10:06:00Z",
                    "duration_ms": 4200,
                },
            ]
        },
    )
    assert response.status_code == 204, response.text

    events = (
        await client.get(
            f"/api/v1/recruiter/applications/{ctx['application_id']}/assessment/events",
            headers=ctx["headers"],
        )
    ).json()
    assert [e["event_type"] for e in events] == [
        "MONITORING_CONSENT_GIVEN",
        "TAB_SWITCH",
        "FULLSCREEN_EXIT",
    ]
    assert events[2]["duration_ms"] == 4200


async def test_monitoring_events_rejected_before_start_and_after_submit(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-monitoring-guard")
    assessment = (
        await client.post(
            "/api/v1/recruiter/assessments", json=_ASSESSMENT_PAYLOAD, headers=ctx["headers"]
        )
    ).json()
    invitation = (
        await client.post(
            "/api/v1/recruiter/assessments/invite",
            json={"assessment_id": assessment["id"], "application_id": ctx["application_id"]},
            headers=ctx["headers"],
        )
    ).json()
    token = _extract_token(invitation["invitation_link"])

    before_start = await client.post(
        f"/api/v1/public/assessment/{token}/events",
        json={"events": [{"event_type": "TAB_SWITCH", "occurred_at": "2026-01-01T10:00:00Z"}]},
    )
    assert before_start.status_code == 409

    view = (await client.get(f"/api/v1/public/assessment/{token}")).json()
    await client.post(f"/api/v1/public/assessment/{token}/start")
    q1_id = view["questions"][0]["id"]
    await client.post(
        f"/api/v1/public/assessment/{token}/submit",
        json={"answers": [{"question_id": q1_id, "selected_option_ids": []}]},
    )

    after_submit = await client.post(
        f"/api/v1/public/assessment/{token}/events",
        json={"events": [{"event_type": "TAB_SWITCH", "occurred_at": "2026-01-01T10:10:00Z"}]},
    )
    assert after_submit.status_code == 409


async def test_monitoring_events_require_assessment_read_permission(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_screening_application(client, "assess-monitoring-perm")
    token = await _start_attempt(client, ctx)
    await client.post(
        f"/api/v1/public/assessment/{token}/events",
        json={"events": [{"event_type": "TAB_SWITCH", "occurred_at": "2026-01-01T10:00:00Z"}]},
    )

    unauthenticated = await client.get(
        f"/api/v1/recruiter/applications/{ctx['application_id']}/assessment/events"
    )
    assert unauthenticated.status_code == 401
