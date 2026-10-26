"""Draft/publish, new question types, answer-key privacy, automatic and
manual grading, negative marking and tampering (docs/assessment.md § 6,
§ 10)."""

from typing import Any

from httpx import AsyncClient

from app.models.user import User
from tests.org_setup import bootstrap_org, link_token, open_job, staff_headers

ASSESSMENTS = "/api/v1/recruiter/assessments"


def mixed_assessment(**extra: Any) -> dict:
    return {
        "title": "Engineering screen",
        "instructions": "Answer every question.",
        "duration_minutes": 45,
        "pass_score": 60,
        "questions": [
            {"type": "MCQ_SINGLE", "prompt": "2 + 2 = ?", "points": 2, "negative_points": 1, "options": [
                {"label": "3"}, {"label": "4", "is_correct": True}]},
            {"type": "TRUE_FALSE", "prompt": "Python is compiled to bytecode.", "points": 1, "options": [
                {"label": "True", "is_correct": True}, {"label": "False"}]},
            {"type": "SHORT_ANSWER", "prompt": "Capital of France?", "points": 2,
             "accepted_answers": ["Paris"], "grading_notes": "Accept Paris only."},
            {"type": "DESCRIPTIVE", "prompt": "Explain recursion.", "points": 5,
             "grading_notes": "PRIVATE-RUBRIC: base case + recursive step."},
            {"type": "CODING", "prompt": "Reverse a string.", "points": 10},
        ],
        **extra,
    }


async def invite_and_open(
    client: AsyncClient, ctx: dict, assessment_id: str, *, email: str
) -> dict:
    """A recruiter-created candidate + application moved to SCREENING,
    invited, and the invitation opened by token."""
    job = ctx.get("job") or await open_job(client, ctx, title=f"Role for {email}")
    candidate = await client.post(
        "/api/v1/recruiter/candidates",
        json={"email": email, "full_name": "Test Candidate"},
        headers=ctx["headers"],
    )
    assert candidate.status_code == 201, candidate.text
    application = await client.post(
        "/api/v1/recruiter/applications",
        json={"candidate_id": candidate.json()["id"], "job_id": job["id"]},
        headers=ctx["headers"],
    )
    assert application.status_code == 201, application.text
    application_id = application.json()["id"]
    for status in ("UNDER_REVIEW", "SCREENING"):
        moved = await client.post(
            f"/api/v1/recruiter/applications/{application_id}/status",
            json={"to_status": status},
            headers=ctx["headers"],
        )
        assert moved.status_code == 200, moved.text
    invitation = await client.post(
        f"{ASSESSMENTS}/invite",
        json={"assessment_id": assessment_id, "application_id": application_id},
        headers=ctx["headers"],
    )
    assert invitation.status_code == 201, invitation.text
    token = link_token(invitation.json()["invitation_link"])
    started = await client.post(f"/api/v1/public/assessment/{token}/start")
    assert started.status_code == 200, started.text
    return {
        "token": token,
        "view": started.json(),
        "application_id": application_id,
        "invitation_id": invitation.json()["id"],
    }


async def submit(client: AsyncClient, token: str, answers: list[dict]) -> dict:
    response = await client.post(f"/api/v1/public/assessment/{token}/submit", json={"answers": answers})
    assert response.status_code == 200, response.text
    return response.json()


async def _create(client: AsyncClient, ctx: dict, payload: dict) -> dict:
    response = await client.post(ASSESSMENTS, json=payload, headers=ctx["headers"])
    assert response.status_code == 201, response.text
    return response.json()


def _answers_for(view: dict, assessment: dict, *, short: str, descriptive: str, code: str,
                 mcq_correct: bool = True) -> list[dict]:
    by_prompt = {q["prompt"]: q for q in assessment["questions"]}
    public = {q["prompt"]: q for q in view["questions"]}
    mcq = by_prompt["2 + 2 = ?"]
    mcq_choice = [o["id"] for o in mcq["options"] if o["is_correct"] == mcq_correct][:1]
    tf = by_prompt["Python is compiled to bytecode."]
    return [
        {"question_id": public["2 + 2 = ?"]["id"], "selected_option_ids": mcq_choice},
        {"question_id": public["Python is compiled to bytecode."]["id"],
         "selected_option_ids": [o["id"] for o in tf["options"] if o["is_correct"]]},
        {"question_id": public["Capital of France?"]["id"], "text_answer": short},
        {"question_id": public["Explain recursion."]["id"], "text_answer": descriptive},
        {"question_id": public["Reverse a string."]["id"], "text_answer": code},
    ]


async def test_draft_assessments_cannot_be_assigned_until_published(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "grade-draft")
    ctx["job"] = await open_job(client, ctx)
    draft = await _create(client, ctx, mixed_assessment(status="DRAFT"))
    assert draft["status"] == "DRAFT"
    published_only = (await client.get(ASSESSMENTS, params={"status": "PUBLISHED"}, headers=ctx["headers"])).json()
    assert published_only == []

    candidate = (await client.post("/api/v1/recruiter/candidates", json={"email": "d@example.com",
                 "full_name": "D"}, headers=ctx["headers"])).json()
    application = (await client.post("/api/v1/recruiter/applications", json={
        "candidate_id": candidate["id"], "job_id": ctx["job"]["id"]}, headers=ctx["headers"])).json()
    for status in ("UNDER_REVIEW", "SCREENING"):
        await client.post(f"/api/v1/recruiter/applications/{application['id']}/status",
                          json={"to_status": status}, headers=ctx["headers"])
    refused = await client.post(f"{ASSESSMENTS}/invite", json={
        "assessment_id": draft["id"], "application_id": application["id"]}, headers=ctx["headers"])
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "assessment_not_published"

    published = await client.post(f"{ASSESSMENTS}/{draft['id']}/status", json={"status": "PUBLISHED"},
                                  headers=ctx["headers"])
    assert published.status_code == 200 and published.json()["status"] == "PUBLISHED"
    invited = await client.post(f"{ASSESSMENTS}/invite", json={
        "assessment_id": draft["id"], "application_id": application["id"]}, headers=ctx["headers"])
    assert invited.status_code == 201
    # Once someone is invited it can't go back to draft.
    back = await client.post(f"{ASSESSMENTS}/{draft['id']}/status", json={"status": "DRAFT"},
                             headers=ctx["headers"])
    assert back.status_code == 409


async def test_candidate_never_sees_answer_keys(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "grade-privacy")
    assessment = await _create(client, ctx, mixed_assessment())

    preview = await client.get(f"{ASSESSMENTS}/{assessment['id']}/preview", headers=ctx["headers"])
    assert preview.status_code == 200
    attempt = await invite_and_open(client, ctx, assessment["id"], email="p@example.com")
    view_text = (await client.get(f"/api/v1/public/assessment/{attempt['token']}")).text
    for payload in (preview.text, view_text):
        assert "is_correct" not in payload
        assert "accepted_answers" not in payload
        assert "Paris" not in payload
        assert "PRIVATE-RUBRIC" not in payload
        assert "grading_notes" not in payload
    types = {q["prompt"]: (q["type"], len(q["options"])) for q in attempt["view"]["questions"]}
    assert types["Capital of France?"] == ("SHORT_ANSWER", 0)
    assert types["Python is compiled to bytecode."] == ("TRUE_FALSE", 2)


async def test_auto_and_manual_grading_end_to_end(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "grade-flow")
    assessment = await _create(client, ctx, mixed_assessment())
    attempt = await invite_and_open(client, ctx, assessment["id"], email="g@example.com")

    submitted = await submit(client, attempt["token"], _answers_for(
        attempt["view"], assessment, short="  paris ", descriptive="A function calling itself.",
        code="def rev(s): return s[::-1]"))
    # The candidate gets a confirmation only — no score, no pass/fail.
    assert set(submitted) == {"submitted_at"}

    invitation = (await client.get(
        f"/api/v1/recruiter/applications/{attempt['application_id']}/assessment", headers=ctx["headers"])).json()
    result = invitation["result"]
    assert result["grading_status"] == "PENDING_MANUAL_REVIEW"
    assert result["passed"] is None
    assert result["score"] == 5  # 2 (MCQ) + 1 (T/F) + 2 (short answer)
    assert result["max_score"] == 20

    pending = (await client.get(f"{ASSESSMENTS}/pending-review", headers=ctx["headers"])).json()
    assert [p["invitation_id"] for p in pending] == [attempt["invitation_id"]]

    review_url = f"{ASSESSMENTS}/attempts/{attempt['invitation_id']}"
    review = (await client.get(f"{review_url}/review", headers=ctx["headers"])).json()
    by_prompt = {a["prompt"]: a for a in review["answers"]}
    assert by_prompt["Explain recursion."]["needs_manual_grading"] is True
    assert by_prompt["Explain recursion."]["grading_notes"].startswith("PRIVATE-RUBRIC")
    assert by_prompt["Capital of France?"]["awarded_points"] == 2

    too_many = await client.post(f"{review_url}/grades", json={"grades": [
        {"question_id": by_prompt["Explain recursion."]["question_id"], "awarded_points": 6}]},
        headers=ctx["headers"])
    assert too_many.status_code == 422
    assert too_many.json()["error"]["code"] == "marks_exceed_maximum"
    choice = await client.post(f"{review_url}/grades", json={"grades": [
        {"question_id": by_prompt["2 + 2 = ?"]["question_id"], "awarded_points": 0}]}, headers=ctx["headers"])
    assert choice.status_code == 422

    partial = (await client.post(f"{review_url}/grades", json={"grades": [
        {"question_id": by_prompt["Explain recursion."]["question_id"], "awarded_points": 4,
         "comment": "Good, missed base case wording."}]}, headers=ctx["headers"])).json()
    assert partial["result"]["grading_status"] == "PENDING_MANUAL_REVIEW"

    final = (await client.post(f"{review_url}/grades", json={"grades": [
        {"question_id": by_prompt["Reverse a string."]["question_id"], "awarded_points": 7}]},
        headers=ctx["headers"])).json()
    assert final["result"]["grading_status"] == "FINAL"
    assert final["result"]["score"] == 16
    assert final["result"]["percentage"] == 80
    assert final["result"]["passed"] is True
    graded = {a["prompt"]: a for a in final["answers"]}["Explain recursion."]
    assert graded["grading_method"] == "MANUAL"
    assert graded["graded_by_name"] == "Acme Admin"
    assert graded["grader_comment"] == "Good, missed base case wording."


async def test_negative_marking_and_tampered_option_ids(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "grade-negative")
    assessment = await _create(client, ctx, {
        "title": "MCQ only", "instructions": "x", "pass_score": 50, "questions": [
            {"type": "MCQ_SINGLE", "prompt": "Q1", "points": 2, "negative_points": 1, "options": [
                {"label": "a", "is_correct": True}, {"label": "b"}]},
            {"type": "MCQ_SINGLE", "prompt": "Q2", "points": 2, "negative_points": 1, "options": [
                {"label": "a", "is_correct": True}, {"label": "b"}]},
        ]})
    attempt = await invite_and_open(client, ctx, assessment["id"], email="n@example.com")
    q1, q2 = assessment["questions"]
    wrong_q1 = [o["id"] for o in q1["options"] if not o["is_correct"]]
    # Q2's correct option id smuggled into Q1's answer must be dropped, not
    # count as a different (or correct) selection.
    q2_correct = [o["id"] for o in q2["options"] if o["is_correct"]]
    await submit(client, attempt["token"], [
        {"question_id": q1["id"], "selected_option_ids": [*wrong_q1, *q2_correct]},
        {"question_id": q2["id"], "selected_option_ids": q2_correct},
    ])
    result = (await client.get(
        f"/api/v1/recruiter/applications/{attempt['application_id']}/assessment", headers=ctx["headers"])).json()["result"]
    # Q1 (foreign id dropped, wrong option left): -1. Q2 correct: +2.
    assert result["score"] == 1
    assert result["grading_status"] == "FINAL"
    assert result["passed"] is False


async def test_grading_permissions_and_tenant_isolation(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "grade-iso-a")
    intruder = await bootstrap_org(client, "grade-iso-b")
    assessment = await _create(client, ctx, mixed_assessment())
    attempt = await invite_and_open(client, ctx, assessment["id"], email="i@example.com")
    await submit(client, attempt["token"], _answers_for(
        attempt["view"], assessment, short="Paris", descriptive="x", code="y"))
    review_url = f"{ASSESSMENTS}/attempts/{attempt['invitation_id']}"

    assert (await client.get(f"{review_url}/review", headers=intruder["headers"])).status_code == 404
    assert (await client.get(f"{ASSESSMENTS}/{assessment['id']}/preview", headers=intruder["headers"])).status_code == 404
    question_id = (await client.get(f"{review_url}/review", headers=ctx["headers"])).json()["answers"][3]["question_id"]
    foreign_grade = await client.post(f"{review_url}/grades", json={"grades": [
        {"question_id": question_id, "awarded_points": 5}]}, headers=intruder["headers"])
    assert foreign_grade.status_code == 404

    interviewer = await staff_headers(client, ctx, "INTERVIEWER")
    assert (await client.post(f"{review_url}/grades", json={"grades": [
        {"question_id": question_id, "awarded_points": 5}]}, headers=interviewer)).status_code == 403
    manager = await staff_headers(client, ctx, "HIRING_MANAGER")
    assert (await client.post(f"{review_url}/grades", json={"grades": [
        {"question_id": question_id, "awarded_points": 5}]}, headers=manager)).status_code == 200


async def test_question_edits_are_locked_once_invited(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "grade-locked")
    assessment = await _create(client, ctx, mixed_assessment())
    await invite_and_open(client, ctx, assessment["id"], email="l@example.com")
    replaced = await client.patch(f"{ASSESSMENTS}/{assessment['id']}", json={
        "questions": mixed_assessment()["questions"][:1]}, headers=ctx["headers"])
    assert replaced.status_code == 409
