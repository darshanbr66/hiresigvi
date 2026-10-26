"""Question bank: categories/tags, per-type validation, search, versioning,
status, permissions, tenant isolation and reuse in assessments without
touching historical results (docs/assessment.md § 10)."""

from typing import Any

from httpx import AsyncClient

from app.models.user import User
from tests.org_setup import bootstrap_org, staff_headers

BANK = "/api/v1/recruiter/question-bank"


def mcq(prompt: str = "Which keyword defines a function in Python?", **extra: Any) -> dict:
    return {
        "type": "MCQ_SINGLE",
        "prompt": prompt,
        "points": 2,
        "options": [
            {"label": "def", "is_correct": True},
            {"label": "func", "is_correct": False},
            {"label": "lambda", "is_correct": False},
        ],
        "explanation": "def starts a function definition.",
        **extra,
    }


async def _create(client: AsyncClient, ctx: dict, payload: dict) -> dict:
    response = await client.post(f"{BANK}/questions", json=payload, headers=ctx["headers"])
    assert response.status_code == 201, response.text
    return response.json()


async def _category(client: AsyncClient, ctx: dict, name: str, parent_id: str | None = None) -> dict:
    response = await client.post(
        f"{BANK}/categories", json={"name": name, "parent_id": parent_id}, headers=ctx["headers"]
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_categories_are_hierarchical_and_unique_among_siblings(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "qb-categories")
    programming = await _category(client, ctx, "Programming")
    python = await _category(client, ctx, "Python", programming["id"])
    assert python["path"] == "Programming › Python"

    duplicate = await client.post(
        f"{BANK}/categories", json={"name": " python ", "parent_id": programming["id"]}, headers=ctx["headers"]
    )
    assert duplicate.status_code == 409
    # The same name under a different parent is fine.
    robotics = await _category(client, ctx, "Robotics")
    assert (await _category(client, ctx, "Python", robotics["id"]))["path"] == "Robotics › Python"


async def test_question_types_are_validated(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "qb-validation")
    invalid = [
        mcq(options=[{"label": "a", "is_correct": True}, {"label": "b", "is_correct": True}]),
        {"type": "TRUE_FALSE", "prompt": "Is 2 prime?", "options": [
            {"label": "True", "is_correct": True}, {"label": "False", "is_correct": False},
            {"label": "Maybe", "is_correct": False}]},
        {"type": "DESCRIPTIVE", "prompt": "Explain GIL.", "accepted_answers": ["lock"]},
        {"type": "CODING", "prompt": "Reverse a list.", "points": 5, "negative_points": 1},
        mcq(points=1, negative_points=2),
        {"type": "SHORT_ANSWER", "prompt": "Capital of France?", "options": [{"label": "Paris"}]},
    ]
    for payload in invalid:
        response = await client.post(f"{BANK}/questions", json=payload, headers=ctx["headers"])
        assert response.status_code == 422, (payload, response.text)

    for payload in (
        mcq(),
        {"type": "MCQ_MULTI", "prompt": "Pick the primes", "options": [
            {"label": "2", "is_correct": True}, {"label": "4"}, {"label": "7", "is_correct": True}]},
        {"type": "TRUE_FALSE", "prompt": "Python is dynamically typed.", "options": [
            {"label": "True", "is_correct": True}, {"label": "False"}]},
        {"type": "SHORT_ANSWER", "prompt": "Capital of France?", "accepted_answers": ["Paris"]},
        {"type": "DESCRIPTIVE", "prompt": "Explain the GIL.", "points": 5, "grading_notes": "Mention threads."},
        {"type": "CODING", "prompt": "Reverse a linked list.", "points": 10},
    ):
        await _create(client, ctx, payload)


async def test_search_filters_and_pagination(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "qb-search")
    programming = await _category(client, ctx, "Programming")
    python = await _category(client, ctx, "Python", programming["id"])
    mechanical = await _category(client, ctx, "Mechanical engineering")
    await _create(client, ctx, {**mcq("Python decorators wrap functions?"), "category_id": python["id"],
                                "difficulty": "HARD", "tags": ["Decorators", "core"], "status": "PUBLISHED"})
    await _create(client, ctx, {**mcq("What is a Python list?"), "category_id": python["id"], "tags": ["core"]})
    await _create(client, ctx, {"type": "DESCRIPTIVE", "prompt": "Explain torque.",
                                "category_id": mechanical["id"], "difficulty": "EASY"})

    async def search(**params: Any) -> tuple[list[dict], int]:
        response = await client.get(f"{BANK}/questions", params=params, headers=ctx["headers"])
        assert response.status_code == 200, response.text
        return response.json(), int(response.headers["X-Total-Count"])

    # A parent category includes its sub-categories.
    assert (await search(category_id=programming["id"]))[1] == 2
    assert (await search(q="python decorators"))[1] == 1
    assert (await search(difficulty="EASY"))[1] == 1
    assert (await search(type="DESCRIPTIVE"))[1] == 1
    assert (await search(status="PUBLISHED"))[1] == 1
    tags = {t["name"]: t for t in (await client.get(f"{BANK}/tags", headers=ctx["headers"])).json()}
    assert tags["core"]["question_count"] == 2
    assert (await search(tag_id=tags["Decorators"]["id"]))[1] == 1
    page, total = await search(limit=1, offset=1)
    assert total == 3 and len(page) == 1
    # List rows never carry the answer key.
    listing = await client.get(f"{BANK}/questions", headers=ctx["headers"])
    assert "is_correct" not in listing.text and "explanation" not in listing.text


async def test_editing_content_creates_a_new_version(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "qb-versions")
    item = await _create(client, ctx, mcq())
    assert item["current_version"]["version_number"] == 1

    # Metadata-only changes never create a version.
    same = await client.patch(
        f"{BANK}/questions/{item['id']}", json={"difficulty": "HARD", "tags": ["basics"]}, headers=ctx["headers"]
    )
    assert same.json()["version_count"] == 1
    assert same.json()["tags"] == ["basics"]

    edited = await client.patch(
        f"{BANK}/questions/{item['id']}",
        json={"content": mcq("Which keyword starts a function?")},
        headers=ctx["headers"],
    )
    body = edited.json()
    assert body["version_count"] == 2
    assert body["current_version"]["version_number"] == 2
    versions = (await client.get(f"{BANK}/questions/{item['id']}/versions", headers=ctx["headers"])).json()
    assert [v["prompt"] for v in versions] == [
        "Which keyword starts a function?",
        "Which keyword defines a function in Python?",
    ]


async def test_status_lifecycle_and_archived_items_are_hidden(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "qb-status")
    item = await _create(client, ctx, mcq())
    url = f"{BANK}/questions/{item['id']}/status"
    assert (await client.post(url, json={"status": "PUBLISHED"}, headers=ctx["headers"])).status_code == 200
    assert (await client.post(url, json={"status": "ARCHIVED"}, headers=ctx["headers"])).status_code == 200
    # ARCHIVED -> PUBLISHED must go back through DRAFT.
    assert (await client.post(url, json={"status": "PUBLISHED"}, headers=ctx["headers"])).status_code == 409
    listing = await client.get(f"{BANK}/questions", headers=ctx["headers"])
    assert listing.headers["X-Total-Count"] == "0"
    archived = await client.get(f"{BANK}/questions", params={"include_archived": True}, headers=ctx["headers"])
    assert archived.headers["X-Total-Count"] == "1"
    edit = await client.patch(
        f"{BANK}/questions/{item['id']}", json={"difficulty": "EASY"}, headers=ctx["headers"]
    )
    assert edit.status_code == 409


async def test_permissions(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "qb-permissions")
    item = await _create(client, ctx, mcq())
    manager = await staff_headers(client, ctx, "HIRING_MANAGER")
    interviewer = await staff_headers(client, ctx, "INTERVIEWER")

    assert (await client.get(f"{BANK}/questions/{item['id']}", headers=manager)).status_code == 200
    assert (await client.post(f"{BANK}/questions", json=mcq(), headers=manager)).status_code == 403
    assert (
        await client.post(f"{BANK}/questions/{item['id']}/status", json={"status": "PUBLISHED"}, headers=manager)
    ).status_code == 403
    assert (await client.get(f"{BANK}/questions", headers=interviewer)).status_code == 403
    assert (await client.get(f"{BANK}/questions", headers={})).status_code == 401


async def test_tenant_isolation(client: AsyncClient, super_admin: User) -> None:
    ctx = await bootstrap_org(client, "qb-iso-a")
    intruder = await bootstrap_org(client, "qb-iso-b")
    item = await _create(client, ctx, {**mcq(), "status": "PUBLISHED"})
    version_id = item["current_version"]["id"]

    assert (await client.get(f"{BANK}/questions/{item['id']}", headers=intruder["headers"])).status_code == 404
    assert (
        await client.patch(f"{BANK}/questions/{item['id']}", json={"difficulty": "EASY"}, headers=intruder["headers"])
    ).status_code == 404
    assert (await client.get(f"{BANK}/questions", headers=intruder["headers"])).headers["X-Total-Count"] == "0"
    # Another organization can't build a test from this organization's question.
    stolen = await client.post(
        "/api/v1/recruiter/assessments",
        json={"title": "Stolen", "instructions": "x", "questions": [{"bank_version_id": version_id}]},
        headers=intruder["headers"],
    )
    assert stolen.status_code == 404


async def test_only_published_current_versions_can_be_added_to_a_test(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await bootstrap_org(client, "qb-draft-guard")
    item = await _create(client, ctx, mcq())
    payload = {"title": "Test", "instructions": "Answer all.", "questions": [
        {"bank_version_id": item["current_version"]["id"]}]}
    draft = await client.post("/api/v1/recruiter/assessments", json=payload, headers=ctx["headers"])
    assert draft.status_code == 422
    assert draft.json()["error"]["code"] == "bank_question_not_published"

    await client.post(f"{BANK}/questions/{item['id']}/status", json={"status": "PUBLISHED"}, headers=ctx["headers"])
    old_version = item["current_version"]["id"]
    await client.patch(
        f"{BANK}/questions/{item['id']}", json={"content": mcq("Edited prompt?")}, headers=ctx["headers"]
    )
    outdated = await client.post("/api/v1/recruiter/assessments", json=payload, headers=ctx["headers"])
    assert outdated.status_code == 422
    assert outdated.json()["error"]["code"] == "bank_question_outdated"
    assert old_version != (
        await client.get(f"{BANK}/questions/{item['id']}", headers=ctx["headers"])
    ).json()["current_version"]["id"]


async def test_reuse_across_assessments_and_history_survives_bank_edits(
    client: AsyncClient, super_admin: User
) -> None:
    from tests.test_assessment_grading import invite_and_open, submit

    ctx = await bootstrap_org(client, "qb-reuse")
    item = await _create(client, ctx, {**mcq(), "status": "PUBLISHED"})
    version_id = item["current_version"]["id"]

    created = []
    for title in ("Campus test", "Technical test"):
        response = await client.post(
            "/api/v1/recruiter/assessments",
            json={"title": title, "instructions": "Answer all.", "pass_score": 50,
                  "questions": [{"bank_version_id": version_id, "points": 4}]},
            headers=ctx["headers"],
        )
        assert response.status_code == 201, response.text
        created.append(response.json())
    for assessment in created:
        question = assessment["questions"][0]
        assert question["source_bank_version_id"] == version_id
        assert question["prompt"] == "Which keyword defines a function in Python?"
        assert question["points"] == 4
        assert [o["is_correct"] for o in question["options"]] == [True, False, False]
    assert (await client.get(f"{BANK}/questions/{item['id']}", headers=ctx["headers"])).json()[
        "assessment_usage_count"
    ] == 2

    attempt = await invite_and_open(client, ctx, created[0]["id"], email="hist@example.com")
    view = attempt["view"]
    correct = [o["id"] for o in created[0]["questions"][0]["options"] if o["is_correct"]]
    await submit(client, attempt["token"], [{"question_id": view["questions"][0]["id"], "selected_option_ids": correct}])

    # Editing the bank question afterwards changes neither the assessment
    # snapshot nor the attempt's result.
    await client.patch(
        f"{BANK}/questions/{item['id']}",
        json={"content": mcq("Totally different question?")},
        headers=ctx["headers"],
    )
    reloaded = (await client.get(f"/api/v1/recruiter/assessments/{created[0]['id']}", headers=ctx["headers"])).json()
    assert reloaded["questions"][0]["prompt"] == "Which keyword defines a function in Python?"
    result = (
        await client.get(f"/api/v1/recruiter/applications/{attempt['application_id']}/assessment", headers=ctx["headers"])
    ).json()["result"]
    assert result["score"] == 4 and result["passed"] is True


async def test_bank_edits_never_change_a_tests_answer_key_marks_or_grading(
    client: AsyncClient, super_admin: User
) -> None:
    """The snapshot covers everything that decides a score — options and
    which is correct, marks, accepted answers, explanation — not just the
    prompt. A candidate who answers *after* the bank changes is still graded
    against the test as it was built."""
    from tests.test_assessment_grading import invite_and_open, submit

    ctx = await bootstrap_org(client, "qb-snapshot")
    choice = await _create(client, ctx, {**mcq(), "status": "PUBLISHED"})
    short = await _create(client, ctx, {
        "type": "SHORT_ANSWER", "prompt": "SQL keyword to sort rows?", "points": 3,
        "accepted_answers": ["ORDER BY"], "status": "PUBLISHED",
    })
    test = (await client.post("/api/v1/recruiter/assessments", json={
        "title": "Snapshot test", "instructions": "Answer.", "pass_score": 50,
        "questions": [
            {"bank_version_id": choice["current_version"]["id"]},
            {"bank_version_id": short["current_version"]["id"]},
        ],
    }, headers=ctx["headers"])).json()
    status = await client.post(
        f"/api/v1/recruiter/assessments/{test['id']}/status", json={"status": "PUBLISHED"}, headers=ctx["headers"]
    )
    assert status.status_code == 200, status.text
    before = test["questions"]

    # Rewrite both bank questions: a different correct option, marks,
    # explanation and accepted answer.
    edited_choice = await client.patch(f"{BANK}/questions/{choice['id']}", json={"content": {
        **mcq(), "points": 9, "explanation": "Changed.",
        "options": [{"label": "def", "is_correct": False}, {"label": "func", "is_correct": True},
                    {"label": "lambda", "is_correct": False}],
    }}, headers=ctx["headers"])
    assert edited_choice.status_code == 200, edited_choice.text
    assert edited_choice.json()["current_version"]["version_number"] == 2
    edited_short = await client.patch(f"{BANK}/questions/{short['id']}", json={"content": {
        "type": "SHORT_ANSWER", "prompt": "SQL keyword to sort rows?", "points": 3, "accepted_answers": ["SORT"],
    }}, headers=ctx["headers"])
    assert edited_short.status_code == 200, edited_short.text

    after = (await client.get(f"/api/v1/recruiter/assessments/{test['id']}", headers=ctx["headers"])).json()["questions"]
    assert after == before
    assert after[0]["points"] == 2 and after[0]["explanation"] == "def starts a function definition."
    assert after[1]["accepted_answers"] == ["ORDER BY"]

    # Answered after the edits, graded against the original key.
    attempt = await invite_and_open(client, ctx, test["id"], email="snapshot@example.com")
    public = {q["prompt"]: q["id"] for q in attempt["view"]["questions"]}
    original_correct = [o["id"] for o in before[0]["options"] if o["is_correct"]]
    await submit(client, attempt["token"], [
        {"question_id": public[before[0]["prompt"]], "selected_option_ids": original_correct},
        {"question_id": public["SQL keyword to sort rows?"], "text_answer": "order by"},
    ])
    result = (await client.get(
        f"/api/v1/recruiter/applications/{attempt['application_id']}/assessment", headers=ctx["headers"]
    )).json()["result"]
    assert (result["score"], result["max_score"], result["passed"]) == (5, 5, True)
