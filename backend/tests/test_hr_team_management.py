"""HR (the RECRUITER role) manages the team and the department hierarchy
(migration d7e8f9a0b1c2): the same `user.*`, `department.manage` and
`employee.manage` permissions ORG_ADMIN holds — but never anything that
creates, removes, or acts on an Org Admin, or changes HR's own role
(`user.manage_admins`, ORG_ADMIN only — app/services/user_service.py).
Tenant isolation is unchanged: another organization's users, departments
and employees are invisible (404), never forbidden-but-present.
"""

import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import rls_bypass
from app.models.team_hierarchy import Employee
from app.models.user import User
from tests.conftest import SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD, login


async def _bootstrap_org(client: AsyncClient, slug: str) -> dict:
    super_admin_tokens = await login(client, email=SUPER_ADMIN_EMAIL, password=SUPER_ADMIN_PASSWORD)
    payload = {
        "name": f"Org {slug}",
        "slug": slug,
        "admin_email": f"admin@{slug}.dev",
        "admin_password": "AcmeAdminPass1",
        "admin_full_name": "Org Admin",
    }
    response = await client.post(
        "/api/v1/admin/organizations",
        json=payload,
        headers={"Authorization": f"Bearer {super_admin_tokens['access_token']}"},
    )
    assert response.status_code == 201, response.text
    return payload


async def _headers(client: AsyncClient, email: str, password: str) -> dict:
    tokens = await login(client, email=email, password=password)
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _create_user(
    client: AsyncClient, headers: dict, slug: str, local: str, role: str
) -> dict:
    response = await client.post(
        "/api/v1/recruiter/users",
        json={
            "email": f"{local}@{slug}.dev",
            "full_name": local.title(),
            "password": "TeamMemberPass1",
            "role": role,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _org_with_hr(client: AsyncClient, slug: str) -> dict:
    """An organization with its ORG_ADMIN and one HR (RECRUITER) account."""
    org = await _bootstrap_org(client, slug)
    admin_headers = await _headers(client, org["admin_email"], org["admin_password"])
    hr = await _create_user(client, admin_headers, slug, "hr", "RECRUITER")
    hr_headers = await _headers(client, hr["email"], "TeamMemberPass1")
    me = await client.get("/api/v1/recruiter/auth/me", headers=admin_headers)
    return {
        "slug": slug,
        "admin_headers": admin_headers,
        "admin_id": me.json()["id"],
        "hr": hr,
        "hr_headers": hr_headers,
    }


# --- Team accounts ----------------------------------------------------------------


async def test_hr_can_view_add_and_update_team_members(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _org_with_hr(client, "hr-team-manage")
    hr_headers = ctx["hr_headers"]

    listed = await client.get("/api/v1/recruiter/users", headers=hr_headers)
    assert listed.status_code == 200, listed.text
    emails = {user["email"] for user in listed.json()}
    assert {"admin@hr-team-manage.dev", "hr@hr-team-manage.dev"} <= emails

    one = await client.get(f"/api/v1/recruiter/users/{ctx['admin_id']}", headers=hr_headers)
    assert one.status_code == 200
    assert one.json()["roles"] == ["ORG_ADMIN"]

    member = await _create_user(client, hr_headers, ctx["slug"], "newhire", "INTERVIEWER")
    assert member["roles"] == ["INTERVIEWER"]

    role_change = await client.patch(
        f"/api/v1/recruiter/users/{member['id']}",
        json={"role": "HIRING_MANAGER"},
        headers=hr_headers,
    )
    assert role_change.status_code == 200, role_change.text
    assert role_change.json()["roles"] == ["HIRING_MANAGER"]

    deactivated = await client.patch(
        f"/api/v1/recruiter/users/{member['id']}",
        json={"is_active": False, "reason": "Left the team"},
        headers=hr_headers,
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False
    deactivated_login = await client.post(
        "/api/v1/recruiter/auth/login",
        json={"email": member["email"], "password": "TeamMemberPass1"},
    )
    assert deactivated_login.status_code == 401

    reactivated = await client.patch(
        f"/api/v1/recruiter/users/{member['id']}",
        json={"is_active": True},
        headers=hr_headers,
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["is_active"] is True

    # Audited with HR as the actor, exactly as for an admin.
    activities = await client.get("/api/v1/recruiter/activities", headers=ctx["admin_headers"])
    actions = {a["action"] for a in activities.json()}
    assert {"USER_CREATED", "USER_ROLE_CHANGED", "USER_DEACTIVATED", "USER_REACTIVATED"} <= actions


async def test_hr_cannot_create_or_promote_an_org_admin(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _org_with_hr(client, "hr-no-admin-grant")
    hr_headers = ctx["hr_headers"]
    member = await _create_user(client, hr_headers, ctx["slug"], "member", "RECRUITER")

    create_admin = await client.post(
        "/api/v1/recruiter/users",
        json={
            "email": "sneaky@hr-no-admin-grant.dev",
            "full_name": "Sneaky Admin",
            "password": "SneakyAdminPass1",
            "role": "ORG_ADMIN",
        },
        headers=hr_headers,
    )
    assert create_admin.status_code == 403
    sneaky_login = await client.post(
        "/api/v1/recruiter/auth/login",
        json={"email": "sneaky@hr-no-admin-grant.dev", "password": "SneakyAdminPass1"},
    )
    assert sneaky_login.status_code == 401  # the account was never created

    promote = await client.patch(
        f"/api/v1/recruiter/users/{member['id']}", json={"role": "ORG_ADMIN"}, headers=hr_headers
    )
    assert promote.status_code == 403
    still_recruiter = await client.get(
        f"/api/v1/recruiter/users/{member['id']}", headers=hr_headers
    )
    assert still_recruiter.json()["roles"] == ["RECRUITER"]


async def test_hr_cannot_change_their_own_role(client: AsyncClient, super_admin: User) -> None:
    ctx = await _org_with_hr(client, "hr-no-self-role")
    for new_role in ("ORG_ADMIN", "HIRING_MANAGER"):
        response = await client.patch(
            f"/api/v1/recruiter/users/{ctx['hr']['id']}",
            json={"role": new_role},
            headers=ctx["hr_headers"],
        )
        assert response.status_code == 403, new_role
    me = await client.get("/api/v1/recruiter/auth/me", headers=ctx["hr_headers"])
    assert me.json()["roles"] == ["RECRUITER"]

    self_deactivate = await client.patch(
        f"/api/v1/recruiter/users/{ctx['hr']['id']}",
        json={"is_active": False},
        headers=ctx["hr_headers"],
    )
    assert self_deactivate.status_code == 409  # the existing self-deactivation guard


async def test_hr_cannot_demote_deactivate_or_reactivate_an_org_admin(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _org_with_hr(client, "hr-admin-untouchable")
    admin_headers, hr_headers = ctx["admin_headers"], ctx["hr_headers"]
    second_admin = await _create_user(client, admin_headers, ctx["slug"], "admin2", "ORG_ADMIN")

    demote = await client.patch(
        f"/api/v1/recruiter/users/{second_admin['id']}",
        json={"role": "RECRUITER"},
        headers=hr_headers,
    )
    assert demote.status_code == 403
    deactivate = await client.patch(
        f"/api/v1/recruiter/users/{second_admin['id']}",
        json={"is_active": False},
        headers=hr_headers,
    )
    assert deactivate.status_code == 403

    # An admin deactivates the second admin; HR still cannot bring them back.
    by_admin = await client.patch(
        f"/api/v1/recruiter/users/{second_admin['id']}",
        json={"is_active": False},
        headers=admin_headers,
    )
    assert by_admin.status_code == 200
    reactivate = await client.patch(
        f"/api/v1/recruiter/users/{second_admin['id']}",
        json={"is_active": True},
        headers=hr_headers,
    )
    assert reactivate.status_code == 403

    final = await client.get(f"/api/v1/recruiter/users/{second_admin['id']}", headers=hr_headers)
    assert final.json()["roles"] == ["ORG_ADMIN"]
    assert final.json()["is_active"] is False


async def test_org_admin_still_manages_admins(client: AsyncClient, super_admin: User) -> None:
    ctx = await _org_with_hr(client, "admin-manages-admins")
    admin_headers = ctx["admin_headers"]

    promoted = await client.patch(
        f"/api/v1/recruiter/users/{ctx['hr']['id']}",
        json={"role": "ORG_ADMIN"},
        headers=admin_headers,
    )
    assert promoted.status_code == 200
    assert promoted.json()["roles"] == ["ORG_ADMIN"]

    demoted = await client.patch(
        f"/api/v1/recruiter/users/{ctx['hr']['id']}",
        json={"role": "RECRUITER"},
        headers=admin_headers,
    )
    assert demoted.status_code == 200
    assert demoted.json()["roles"] == ["RECRUITER"]

    new_admin = await _create_user(client, admin_headers, ctx["slug"], "admin3", "ORG_ADMIN")
    deactivated = await client.patch(
        f"/api/v1/recruiter/users/{new_admin['id']}",
        json={"is_active": False},
        headers=admin_headers,
    )
    assert deactivated.status_code == 200


# --- Departments and department membership -----------------------------------------


async def test_hr_manages_departments_and_membership(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _org_with_hr(client, "hr-departments")
    hr_headers = ctx["hr_headers"]

    engineering = await client.post(
        "/api/v1/recruiter/departments", json={"name": "Engineering"}, headers=hr_headers
    )
    assert engineering.status_code == 201, engineering.text
    design = await client.post(
        "/api/v1/recruiter/departments", json={"name": "Design"}, headers=hr_headers
    )
    eng_id, design_id = engineering.json()["id"], design.json()["id"]

    renamed = await client.patch(
        f"/api/v1/recruiter/departments/{eng_id}",
        json={"description": "Builds the product"},
        headers=hr_headers,
    )
    assert renamed.status_code == 200
    assert renamed.json()["description"] == "Builds the product"

    employee = await client.post(
        "/api/v1/recruiter/employees",
        json={"full_name": "Asha", "email": "asha@hr-departments.dev", "department_id": eng_id},
        headers=hr_headers,
    )
    assert employee.status_code == 201, employee.text
    employee_id = employee.json()["id"]

    updated = await client.patch(
        f"/api/v1/recruiter/employees/{employee_id}",
        json={"designation": "Senior Engineer"},
        headers=hr_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["designation"] == "Senior Engineer"

    moved = await client.post(
        f"/api/v1/recruiter/employees/{employee_id}/move",
        json={"department_id": design_id},
        headers=hr_headers,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["department_name"] == "Design"

    removed = await client.post(
        f"/api/v1/recruiter/employees/{employee_id}/move",
        json={"department_id": None},
        headers=hr_headers,
    )
    assert removed.status_code == 200
    assert removed.json()["department_id"] is None

    deactivated = await client.post(
        f"/api/v1/recruiter/employees/{employee_id}/deactivate",
        json={"reason": "Contract ended"},
        headers=hr_headers,
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["employment_status"] == "INACTIVE"
    reactivated = await client.post(
        f"/api/v1/recruiter/employees/{employee_id}/reactivate", headers=hr_headers
    )
    assert reactivated.status_code == 200

    deleted = await client.post(
        f"/api/v1/recruiter/departments/{eng_id}/delete",
        json={"reason": "Merged into Design"},
        headers=hr_headers,
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_at"] is not None


# --- Tenant isolation -----------------------------------------------------------------


async def test_hr_cannot_reach_another_organizations_team_or_departments(
    client: AsyncClient, super_admin: User
) -> None:
    ours = await _org_with_hr(client, "hr-tenant-ours")
    theirs = await _org_with_hr(client, "hr-tenant-theirs")
    hr_headers = ours["hr_headers"]

    their_dept = (
        await client.post(
            "/api/v1/recruiter/departments", json={"name": "Their Dept"},
            headers=theirs["admin_headers"],
        )
    ).json()
    their_employee = (
        await client.post(
            "/api/v1/recruiter/employees",
            json={"full_name": "Them", "email": "them@hr-tenant-theirs.dev",
                  "department_id": their_dept["id"]},
            headers=theirs["admin_headers"],
        )
    ).json()

    listed = await client.get("/api/v1/recruiter/users", headers=hr_headers)
    assert all(u["email"].endswith("@hr-tenant-ours.dev") for u in listed.json())

    their_user = f"/api/v1/recruiter/users/{theirs['hr']['id']}"
    assert (await client.get(their_user, headers=hr_headers)).status_code == 404
    for body in ({"is_active": False}, {"role": "HIRING_MANAGER"}):
        assert (await client.patch(their_user, json=body, headers=hr_headers)).status_code == 404

    their_dept_url = f"/api/v1/recruiter/departments/{their_dept['id']}"
    assert (await client.get(their_dept_url, headers=hr_headers)).status_code == 404
    rename = await client.patch(their_dept_url, json={"name": "Hijacked"}, headers=hr_headers)
    assert rename.status_code == 404
    delete = await client.post(
        f"{their_dept_url}/delete", json={"reason": "x"}, headers=hr_headers
    )
    assert delete.status_code == 404

    move = await client.post(
        f"/api/v1/recruiter/employees/{their_employee['id']}/move",
        json={"department_id": None},
        headers=hr_headers,
    )
    assert move.status_code == 404
    deactivate = await client.post(
        f"/api/v1/recruiter/employees/{their_employee['id']}/deactivate",
        json={},
        headers=hr_headers,
    )
    assert deactivate.status_code == 404

    # Nothing changed on their side.
    still = await client.get(their_dept_url, headers=theirs["admin_headers"])
    assert still.json()["name"] == "Their Dept"
    assert still.json()["employee_count"] == 1
    their_hr = await client.get(their_user, headers=theirs["admin_headers"])
    assert their_hr.json()["is_active"] is True
    assert their_hr.json()["roles"] == ["RECRUITER"]


# --- Unrelated admin-only capabilities stay restricted --------------------------------


async def test_hr_does_not_gain_unrelated_admin_capabilities(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _org_with_hr(client, "hr-no-extra-admin")
    hr_headers = ctx["hr_headers"]

    activities = await client.get("/api/v1/recruiter/activities", headers=ctx["admin_headers"])
    activity_id = activities.json()[0]["id"]
    delete_activity = await client.delete(
        f"/api/v1/recruiter/activities/{activity_id}", headers=hr_headers
    )
    assert delete_activity.status_code == 403

    platform_admin = await client.get("/api/v1/admin/organizations", headers=hr_headers)
    assert platform_admin.status_code == 403


# --- Removing an employee who left -----------------------------------------------------


async def _employee(
    client: AsyncClient, headers: dict, slug: str, name: str, **fields: object
) -> dict:
    response = await client.post(
        "/api/v1/recruiter/employees",
        json={"full_name": name, "email": f"{name.lower()}@{slug}.dev", **fields},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_hr_removes_an_employee_from_every_view_and_history_stays(
    client: AsyncClient, super_admin: User, db_session: AsyncSession
) -> None:
    ctx = await _org_with_hr(client, "hr-remove-employee")
    hr_headers, slug = ctx["hr_headers"], ctx["slug"]
    dept = (
        await client.post(
            "/api/v1/recruiter/departments", json={"name": "Sales"}, headers=hr_headers
        )
    ).json()
    leaver = await _employee(client, hr_headers, slug, "Leaver", department_id=dept["id"])
    report = await _employee(
        client, hr_headers, slug, "Report", department_id=dept["id"], manager_id=leaver["id"]
    )

    removed = await client.post(
        f"/api/v1/recruiter/employees/{leaver['id']}/delete",
        json={"reason": "Resigned"},
        headers=hr_headers,
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["deleted_at"] is not None

    everyone = await client.get("/api/v1/recruiter/employees", headers=hr_headers)
    assert [e["full_name"] for e in everyone.json()] == ["Report"]
    in_dept = await client.get(
        "/api/v1/recruiter/employees", params={"department_id": dept["id"]}, headers=hr_headers
    )
    assert [e["full_name"] for e in in_dept.json()] == ["Report"]
    dept_after = await client.get(f"/api/v1/recruiter/departments/{dept['id']}", headers=hr_headers)
    assert dept_after.json()["employee_count"] == 1
    # ...and is nobody's manager any more.
    report_after = await client.get(
        f"/api/v1/recruiter/employees/{report['id']}", headers=hr_headers
    )
    assert report_after.json()["manager_id"] is None
    assert report_after.json()["manager_name"] is None

    # Removing again is a 404, like any employee that isn't there.
    again = await client.post(
        f"/api/v1/recruiter/employees/{leaver['id']}/delete", json={}, headers=hr_headers
    )
    assert again.status_code == 404

    # History: the row is archived in place, and the removal is audited.
    async with rls_bypass(db_session):
        row = await db_session.scalar(
            select(Employee).where(Employee.id == uuid.UUID(leaver["id"]))
        )
    assert row is not None
    assert row.full_name == "Leaver" and row.deletion_reason == "Resigned"
    assert str(row.deleted_by_user_id) == ctx["hr"]["id"]
    activities = await client.get("/api/v1/recruiter/activities", headers=ctx["admin_headers"])
    removal = next(a for a in activities.json() if a["action"] == "EMPLOYEE_DELETED")
    assert removal["entity_id"] == leaver["id"]
    assert removal["reason"] == "Resigned"

    # A rehire with the same email can be added again.
    await _employee(client, hr_headers, slug, "Leaver", department_id=dept["id"])


async def test_hr_cannot_remove_an_org_admins_or_their_own_employee_record(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _org_with_hr(client, "hr-remove-guarded")
    hr_headers, slug = ctx["hr_headers"], ctx["slug"]
    admin_record = await _employee(client, hr_headers, slug, "Adminrecord", user_id=ctx["admin_id"])
    own_record = await _employee(client, hr_headers, slug, "Ownrecord", user_id=ctx["hr"]["id"])

    for record in (admin_record, own_record):
        response = await client.post(
            f"/api/v1/recruiter/employees/{record['id']}/delete", json={}, headers=hr_headers
        )
        assert response.status_code == 403, record["full_name"]
    listed = await client.get("/api/v1/recruiter/employees", headers=hr_headers)
    assert {"Adminrecord", "Ownrecord"} <= {e["full_name"] for e in listed.json()}

    # An Org Admin keeps the capability.
    by_admin = await client.post(
        f"/api/v1/recruiter/employees/{own_record['id']}/delete",
        json={"reason": "Left"},
        headers=ctx["admin_headers"],
    )
    assert by_admin.status_code == 200


async def test_employee_removal_is_tenant_isolated_and_role_restricted(
    client: AsyncClient, super_admin: User
) -> None:
    ours = await _org_with_hr(client, "hr-remove-ours")
    theirs = await _org_with_hr(client, "hr-remove-theirs")
    their_employee = await _employee(client, theirs["admin_headers"], theirs["slug"], "Them")
    our_employee = await _employee(client, ours["admin_headers"], ours["slug"], "Us")

    cross = await client.post(
        f"/api/v1/recruiter/employees/{their_employee['id']}/delete",
        json={},
        headers=ours["hr_headers"],
    )
    assert cross.status_code == 404
    still_there = await client.get("/api/v1/recruiter/employees", headers=theirs["admin_headers"])
    assert "Them" in {e["full_name"] for e in still_there.json()}

    for role in ("HIRING_MANAGER", "INTERVIEWER"):
        user = await _create_user(
            client, ours["admin_headers"], ours["slug"], role.lower().replace("_", ""), role
        )
        headers = await _headers(client, user["email"], "TeamMemberPass1")
        denied = await client.post(
            f"/api/v1/recruiter/employees/{our_employee['id']}/delete", json={}, headers=headers
        )
        assert denied.status_code == 403, role
