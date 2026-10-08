"""Hidden employee ordering for the org chart: new employees are appended to
their department's list, ORG_ADMIN can rearrange a list, and the order is
persisted and returned by the API (never derived from names on the client).

`display_order` is internal — it is asserted here against the database, and
asserted to be absent from every API response."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import rls_bypass
from app.models.team_hierarchy import Employee
from app.models.user import User
from app.services import activity_service
from tests.test_team_hierarchy import _bootstrap_org, _create_recruiter_headers, _org_admin_headers

NAMES = ["Rajesh", "Yashaswini", "Rajkumar", "Darshan"]  # deliberately not alphabetical


async def _org(client: AsyncClient, slug: str) -> tuple[dict, dict]:
    org = await _bootstrap_org(client, slug)
    return org, await _org_admin_headers(client, org)


async def _department(client: AsyncClient, headers: dict, name: str) -> str:
    response = await client.post("/api/v1/recruiter/departments", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _add(
    client: AsyncClient, headers: dict, name: str, department_id: str | None, domain: str
) -> dict:
    response = await client.post(
        "/api/v1/recruiter/employees",
        json={"full_name": name, "email": f"{name.lower()}@{domain}", "department_id": department_id},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _add_all(
    client: AsyncClient, headers: dict, names: list[str], department_id: str | None, domain: str
) -> dict[str, str]:
    """Creates the employees in the given order and returns {name: id}."""
    return {name: (await _add(client, headers, name, department_id, domain))["id"] for name in names}


async def _listed_names(client: AsyncClient, headers: dict, department_id: str | None = None) -> list[str]:
    query = f"?department_id={department_id}" if department_id else ""
    response = await client.get(f"/api/v1/recruiter/employees{query}", headers=headers)
    assert response.status_code == 200, response.text
    rows = response.json()
    if department_id is None:
        return [row["full_name"] for row in rows]
    return [row["full_name"] for row in rows if row["department_id"] == department_id]


async def _stored_orders(db_session: AsyncSession, domain: str) -> dict[str, int]:
    async with rls_bypass(db_session):
        rows = await db_session.execute(
            select(Employee.full_name, Employee.display_order).where(Employee.email.endswith(f"@{domain}"))
        )
    return dict(rows.tuples().all())


async def _reorder(client: AsyncClient, headers: dict, ids: list[str]):
    return await client.patch(
        "/api/v1/recruiter/employees/reorder", json={"employee_ids": ids}, headers=headers
    )


# --- creation ----------------------------------------------------------------


async def test_new_employees_are_ordered_by_creation_not_by_name(
    client: AsyncClient, db_session: AsyncSession, super_admin: User
) -> None:
    domain = "create-order.dev"
    _, headers = await _org(client, "ordering-create")
    dept = await _department(client, headers, "Engineering")
    await _add_all(client, headers, NAMES, dept, domain)

    assert await _listed_names(client, headers, dept) == NAMES  # not A-Z
    assert await _stored_orders(db_session, domain) == {
        "Rajesh": 1,
        "Yashaswini": 2,
        "Rajkumar": 3,
        "Darshan": 4,
    }


async def test_each_department_and_the_unassigned_group_is_its_own_scope(
    client: AsyncClient, db_session: AsyncSession, super_admin: User
) -> None:
    domain = "scopes.dev"
    _, headers = await _org(client, "ordering-scopes")
    design = await _department(client, headers, "Design")
    data = await _department(client, headers, "Data")
    await _add_all(client, headers, ["Zed", "Amy"], design, domain)
    await _add_all(client, headers, ["Yan", "Bo"], data, domain)
    await _add_all(client, headers, ["Xi", "Cy"], None, domain)

    assert await _stored_orders(db_session, domain) == {
        "Zed": 1, "Amy": 2, "Yan": 1, "Bo": 2, "Xi": 1, "Cy": 2,
    }
    assert await _listed_names(client, headers, design) == ["Zed", "Amy"]
    assert await _listed_names(client, headers, data) == ["Yan", "Bo"]


async def test_display_order_is_never_exposed_by_the_api(client: AsyncClient, super_admin: User) -> None:
    _, headers = await _org(client, "ordering-hidden")
    dept = await _department(client, headers, "Engineering")
    created = await _add(client, headers, "Rajesh", dept, "hidden.dev")
    assert "display_order" not in created

    listing = (await client.get("/api/v1/recruiter/employees", headers=headers)).json()
    assert all("display_order" not in row for row in listing)
    reordered = await _reorder(client, headers, [created["id"]])
    assert all("display_order" not in row for row in reordered.json())


# --- reordering --------------------------------------------------------------


async def test_org_admin_can_move_an_employee_to_the_top(
    client: AsyncClient, db_session: AsyncSession, super_admin: User
) -> None:
    domain = "to-top.dev"
    _, headers = await _org(client, "ordering-top")
    dept = await _department(client, headers, "Engineering")
    ids = await _add_all(client, headers, NAMES, dept, domain)

    response = await _reorder(
        client, headers, [ids["Darshan"], ids["Rajesh"], ids["Yashaswini"], ids["Rajkumar"]]
    )

    assert response.status_code == 200, response.text
    assert [row["full_name"] for row in response.json()] == ["Darshan", "Rajesh", "Yashaswini", "Rajkumar"]
    # persisted, dense and duplicate-free — and what a fresh request returns
    assert await _stored_orders(db_session, domain) == {
        "Darshan": 1, "Rajesh": 2, "Yashaswini": 3, "Rajkumar": 4,
    }
    assert await _listed_names(client, headers, dept) == ["Darshan", "Rajesh", "Yashaswini", "Rajkumar"]


async def test_org_admin_can_move_an_employee_between_two_others_and_to_the_bottom(
    client: AsyncClient, db_session: AsyncSession, super_admin: User
) -> None:
    domain = "middle-bottom.dev"
    _, headers = await _org(client, "ordering-middle")
    dept = await _department(client, headers, "Engineering")
    ids = await _add_all(client, headers, NAMES, dept, domain)

    # Darshan between Rajesh and Yashaswini
    await _reorder(client, headers, [ids["Rajesh"], ids["Darshan"], ids["Yashaswini"], ids["Rajkumar"]])
    assert await _listed_names(client, headers, dept) == ["Rajesh", "Darshan", "Yashaswini", "Rajkumar"]

    # Rajesh to the bottom
    await _reorder(client, headers, [ids["Darshan"], ids["Yashaswini"], ids["Rajkumar"], ids["Rajesh"]])
    assert await _listed_names(client, headers, dept) == ["Darshan", "Yashaswini", "Rajkumar", "Rajesh"]
    assert sorted((await _stored_orders(db_session, domain)).values()) == [1, 2, 3, 4]


async def test_unassigned_employees_can_be_reordered(
    client: AsyncClient, db_session: AsyncSession, super_admin: User
) -> None:
    domain = "unassigned.dev"
    _, headers = await _org(client, "ordering-unassigned")
    ids = await _add_all(client, headers, ["Una", "Uri", "Uma"], None, domain)

    response = await _reorder(client, headers, [ids["Uma"], ids["Una"], ids["Uri"]])

    assert response.status_code == 200, response.text
    assert await _stored_orders(db_session, domain) == {"Uma": 1, "Una": 2, "Uri": 3}


async def test_reordering_one_department_leaves_others_untouched(
    client: AsyncClient, db_session: AsyncSession, super_admin: User
) -> None:
    domain = "isolated-scopes.dev"
    _, headers = await _org(client, "ordering-isolated")
    design = await _department(client, headers, "Design")
    data = await _department(client, headers, "Data")
    design_ids = await _add_all(client, headers, ["Zed", "Amy"], design, domain)
    await _add_all(client, headers, ["Yan", "Bo"], data, domain)

    await _reorder(client, headers, [design_ids["Amy"], design_ids["Zed"]])

    assert await _stored_orders(db_session, domain) == {"Amy": 1, "Zed": 2, "Yan": 1, "Bo": 2}


# --- moves keep both lists tidy ---------------------------------------------


async def test_moving_an_employee_appends_them_to_the_new_list_and_closes_the_gap(
    client: AsyncClient, db_session: AsyncSession, super_admin: User
) -> None:
    domain = "moves.dev"
    _, headers = await _org(client, "ordering-moves")
    design = await _department(client, headers, "Design")
    data = await _department(client, headers, "Data")
    design_ids = await _add_all(client, headers, ["Zed", "Amy", "Kim"], design, domain)
    await _add_all(client, headers, ["Yan", "Bo"], data, domain)

    moved = await client.post(
        f"/api/v1/recruiter/employees/{design_ids['Amy']}/move",
        json={"department_id": data},
        headers=headers,
    )
    assert moved.status_code == 200, moved.text

    assert await _listed_names(client, headers, design) == ["Zed", "Kim"]
    assert await _listed_names(client, headers, data) == ["Yan", "Bo", "Amy"]
    assert await _stored_orders(db_session, domain) == {"Zed": 1, "Kim": 2, "Yan": 1, "Bo": 2, "Amy": 3}


# --- inactive employees ------------------------------------------------------


async def test_inactive_employees_keep_their_place_and_never_cause_duplicates(
    client: AsyncClient, db_session: AsyncSession, super_admin: User
) -> None:
    domain = "inactive.dev"
    _, headers = await _org(client, "ordering-inactive")
    dept = await _department(client, headers, "Engineering")
    ids = await _add_all(client, headers, ["Ann", "Bob", "Cat", "Dan"], dept, domain)
    await client.post(f"/api/v1/recruiter/employees/{ids['Bob']}/deactivate", json={}, headers=headers)

    # a new hire never lands on Bob's (or anyone's) slot
    eve_id = (await _add(client, headers, "Eve", dept, domain))["id"]
    assert (await _stored_orders(db_session, domain))["Eve"] == 5

    # the chart only shows active employees, so the request lists exactly those
    incomplete = await _reorder(client, headers, [ids["Dan"], ids["Ann"], ids["Cat"]])
    assert incomplete.status_code == 409  # Eve is active but missing from the list
    response = await _reorder(client, headers, [ids["Dan"], ids["Ann"], ids["Cat"], eve_id])
    assert response.status_code == 200, response.text

    stored = await _stored_orders(db_session, domain)
    assert sorted(stored.values()) == [1, 2, 3, 4, 5]  # no duplicates, nothing lost
    assert stored["Bob"] == 2  # the inactive employee wasn't displaced
    ordered_active = [name for name, _ in sorted(stored.items(), key=lambda kv: kv[1]) if name != "Bob"]
    assert ordered_active == ["Dan", "Ann", "Cat", "Eve"]

    # reactivating puts them back where they were
    await client.post(f"/api/v1/recruiter/employees/{ids['Bob']}/reactivate", headers=headers)
    assert await _listed_names(client, headers, dept) == ["Dan", "Bob", "Ann", "Cat", "Eve"]


async def test_only_active_employees_can_be_reordered(client: AsyncClient, super_admin: User) -> None:
    _, headers = await _org(client, "ordering-only-active")
    dept = await _department(client, headers, "Engineering")
    ids = await _add_all(client, headers, ["Ann", "Bob"], dept, "only-active.dev")
    await client.post(f"/api/v1/recruiter/employees/{ids['Bob']}/deactivate", json={}, headers=headers)

    response = await _reorder(client, headers, [ids["Bob"], ids["Ann"]])

    assert response.status_code == 422, response.text
    assert await _listed_names(client, headers, dept) == ["Ann", "Bob"]


# --- validation --------------------------------------------------------------


async def test_duplicate_ids_are_rejected_and_change_nothing(client: AsyncClient, super_admin: User) -> None:
    _, headers = await _org(client, "ordering-dupes")
    dept = await _department(client, headers, "Engineering")
    ids = await _add_all(client, headers, NAMES, dept, "dupes.dev")
    order = [ids["Darshan"], ids["Darshan"], ids["Rajesh"], ids["Yashaswini"], ids["Rajkumar"]]

    response = await _reorder(client, headers, order)

    assert response.status_code == 422, response.text
    assert await _listed_names(client, headers, dept) == NAMES


async def test_unknown_malformed_and_empty_id_lists_are_rejected(client: AsyncClient, super_admin: User) -> None:
    _, headers = await _org(client, "ordering-invalid")
    dept = await _department(client, headers, "Engineering")
    ids = await _add_all(client, headers, NAMES, dept, "invalid.dev")

    unknown = await _reorder(client, headers, [str(uuid.uuid4()), *ids.values()])
    assert unknown.status_code == 404, unknown.text

    malformed = await _reorder(client, headers, ["not-a-uuid"])
    assert malformed.status_code == 422

    empty = await _reorder(client, headers, [])
    assert empty.status_code == 422

    assert await _listed_names(client, headers, dept) == NAMES


async def test_partial_lists_and_lists_spanning_departments_are_rejected(
    client: AsyncClient, super_admin: User
) -> None:
    _, headers = await _org(client, "ordering-scope-rules")
    design = await _department(client, headers, "Design")
    data = await _department(client, headers, "Data")
    design_ids = await _add_all(client, headers, ["Zed", "Amy", "Kim"], design, "scope-rules.dev")
    data_ids = await _add_all(client, headers, ["Yan"], data, "scope-rules.dev")

    # leaves Kim out: ambiguous, so refused rather than guessed
    partial = await _reorder(client, headers, [design_ids["Amy"], design_ids["Zed"]])
    assert partial.status_code == 409, partial.text

    across = await _reorder(client, headers, [design_ids["Zed"], data_ids["Yan"]])
    assert across.status_code == 422, across.text

    assert await _listed_names(client, headers, design) == ["Zed", "Amy", "Kim"]


# --- permissions and tenant isolation ---------------------------------------


async def test_read_only_roles_cannot_reorder(client: AsyncClient, super_admin: User) -> None:
    """HR (RECRUITER) may reorder (it holds `employee.manage`); a
    HIRING_MANAGER only reads the hierarchy."""
    org, admin_headers = await _org(client, "ordering-rbac")
    recruiter_headers = await _create_recruiter_headers(
        client, admin_headers, "ordering-rbac", role="HIRING_MANAGER"
    )
    dept = await _department(client, admin_headers, "Engineering")
    ids = await _add_all(client, admin_headers, NAMES, dept, "rbac.dev")
    reversed_ids = list(reversed(list(ids.values())))

    as_recruiter = await _reorder(client, recruiter_headers, reversed_ids)
    assert as_recruiter.status_code == 403

    anonymous = await client.patch(
        "/api/v1/recruiter/employees/reorder", json={"employee_ids": reversed_ids}
    )
    assert anonymous.status_code == 401

    assert await _listed_names(client, admin_headers, dept) == NAMES
    # ...but the hiring manager still sees the persisted order
    assert await _listed_names(client, recruiter_headers, dept) == NAMES


async def test_employees_of_another_organization_cannot_be_reordered(
    client: AsyncClient, super_admin: User
) -> None:
    _, headers_a = await _org(client, "ordering-tenant-a")
    _, headers_b = await _org(client, "ordering-tenant-b")
    dept_a = await _department(client, headers_a, "Engineering")
    dept_b = await _department(client, headers_b, "Engineering")
    ids_a = await _add_all(client, headers_a, NAMES, dept_a, "tenant-a.dev")
    ids_b = await _add_all(client, headers_b, ["Bea", "Ben"], dept_b, "tenant-b.dev")

    # Org B tries to reorder org A's employees — including mixed with its own
    foreign = await _reorder(client, headers_b, list(reversed(list(ids_a.values()))))
    assert foreign.status_code == 404, foreign.text
    mixed = await _reorder(client, headers_b, [ids_a["Rajesh"], ids_b["Bea"], ids_b["Ben"]])
    assert mixed.status_code == 404, mixed.text

    assert await _listed_names(client, headers_a, dept_a) == NAMES
    assert await _listed_names(client, headers_b, dept_b) == ["Bea", "Ben"]


# --- atomicity ---------------------------------------------------------------


async def test_a_failure_part_way_leaves_the_previous_order_intact(
    client: AsyncClient, db_session: AsyncSession, super_admin: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    domain = "atomic.dev"
    _, headers = await _org(client, "ordering-atomic")
    dept = await _department(client, headers, "Engineering")
    ids = await _add_all(client, headers, NAMES, dept, domain)

    async def failing_record_activity(*args: object, **kwargs: object) -> None:
        raise RuntimeError("audit log unavailable")

    # The renumbering has been flushed by the time the audit entry is written.
    monkeypatch.setattr(activity_service, "record_activity", failing_record_activity)
    with pytest.raises(RuntimeError, match="audit log unavailable"):
        await _reorder(client, headers, list(reversed(list(ids.values()))))
    monkeypatch.undo()

    assert await _listed_names(client, headers, dept) == NAMES
    assert await _stored_orders(db_session, domain) == {"Rajesh": 1, "Yashaswini": 2, "Rajkumar": 3, "Darshan": 4}


async def test_reordering_is_recorded_in_the_activity_log(client: AsyncClient, super_admin: User) -> None:
    _, headers = await _org(client, "ordering-audit")
    dept = await _department(client, headers, "Engineering")
    ids = await _add_all(client, headers, ["Ann", "Bob"], dept, "audit.dev")

    await _reorder(client, headers, [ids["Bob"], ids["Ann"]])

    actions = [a["action"] for a in (await client.get("/api/v1/recruiter/activities", headers=headers)).json()]
    assert "EMPLOYEE_REORDERED" in actions
