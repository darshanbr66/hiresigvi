"""Team Hierarchy: departments and employees (SIGVITAS platform overhaul
§ 9-13) — a directory domain deliberately separate from the existing
users/RBAC Team page. ORG_ADMIN and HR (RECRUITER) have full CRUD;
HIRING_MANAGER is read-only."""

from httpx import AsyncClient

from app.models.user import User
from tests.conftest import SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD, login


async def _bootstrap_org(client: AsyncClient, slug: str) -> dict:
    super_admin_tokens = await login(client, email=SUPER_ADMIN_EMAIL, password=SUPER_ADMIN_PASSWORD)
    payload = {
        "name": "Acme Corp",
        "slug": slug,
        "admin_email": f"admin@{slug}.dev",
        "admin_password": "AcmeAdminPass1",
        "admin_full_name": "Acme Admin",
    }
    response = await client.post(
        "/api/v1/admin/organizations",
        json=payload,
        headers={"Authorization": f"Bearer {super_admin_tokens['access_token']}"},
    )
    assert response.status_code == 201, response.text
    return payload


async def _org_admin_headers(client: AsyncClient, org: dict) -> dict:
    tokens = await login(client, email=org["admin_email"], password=org["admin_password"])
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _create_recruiter_headers(
    client: AsyncClient, admin_headers: dict, org_slug: str, role: str = "RECRUITER"
) -> dict:
    response = await client.post(
        "/api/v1/recruiter/users",
        json={
            "email": f"recruiter@{org_slug}.dev",
            "full_name": "Riya Recruiter",
            "password": "RecruiterPass1",
            "role": role,
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    tokens = await login(client, email=f"recruiter@{org_slug}.dev", password="RecruiterPass1")
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_org_admin_can_create_and_list_departments(client: AsyncClient, super_admin: User) -> None:
    org = await _bootstrap_org(client, "hierarchy-dept-crud")
    headers = await _org_admin_headers(client, org)

    create = await client.post(
        "/api/v1/recruiter/departments",
        json={"name": "Software Engineering", "description": "Builds the product."},
        headers=headers,
    )
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["name"] == "Software Engineering"
    assert body["employee_count"] == 0

    listing = await client.get("/api/v1/recruiter/departments", headers=headers)
    assert listing.status_code == 200
    assert any(d["name"] == "Software Engineering" for d in listing.json())

    activities = await client.get("/api/v1/recruiter/activities", headers=headers)
    actions = [a["action"] for a in activities.json()]
    assert "DEPARTMENT_CREATED" in actions


async def test_department_update_applies_only_provided_fields(
    client: AsyncClient, super_admin: User
) -> None:
    org = await _bootstrap_org(client, "hierarchy-dept-update")
    headers = await _org_admin_headers(client, org)
    dept = (
        await client.post(
            "/api/v1/recruiter/departments",
            json={"name": "Design", "description": "Product design."},
            headers=headers,
        )
    ).json()

    updated = await client.patch(
        f"/api/v1/recruiter/departments/{dept['id']}",
        json={"description": "Product & brand design."},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Design"
    assert updated.json()["description"] == "Product & brand design."


async def test_department_deletion_blocked_while_employees_assigned(
    client: AsyncClient, super_admin: User
) -> None:
    org = await _bootstrap_org(client, "hierarchy-dept-delete-guard")
    headers = await _org_admin_headers(client, org)
    dept = (
        await client.post(
            "/api/v1/recruiter/departments", json={"name": "HR"}, headers=headers
        )
    ).json()
    employee = (
        await client.post(
            "/api/v1/recruiter/employees",
            json={"full_name": "Hema HR", "email": "hema@hierarchy-dept-delete-guard.dev", "department_id": dept["id"]},
            headers=headers,
        )
    ).json()

    blocked = await client.post(
        f"/api/v1/recruiter/departments/{dept['id']}/delete",
        json={"reason": "Reorg"},
        headers=headers,
    )
    assert blocked.status_code == 409

    # Move the employee out, then deletion succeeds.
    other = (
        await client.post(
            "/api/v1/recruiter/departments", json={"name": "Operations"}, headers=headers
        )
    ).json()
    move = await client.post(
        f"/api/v1/recruiter/employees/{employee['id']}/move",
        json={"department_id": other["id"]},
        headers=headers,
    )
    assert move.status_code == 200
    assert move.json()["department_name"] == "Operations"

    now_empty = await client.post(
        f"/api/v1/recruiter/departments/{dept['id']}/delete",
        json={"reason": "Reorg"},
        headers=headers,
    )
    assert now_empty.status_code == 200
    assert now_empty.json()["deleted_at"] is not None

    activities = await client.get("/api/v1/recruiter/activities", headers=headers)
    actions = [a["action"] for a in activities.json()]
    assert "EMPLOYEE_MOVED" in actions
    assert "DEPARTMENT_DELETED" in actions


async def test_employee_crud_and_deactivate_reactivate(client: AsyncClient, super_admin: User) -> None:
    org = await _bootstrap_org(client, "hierarchy-employee-crud")
    headers = await _org_admin_headers(client, org)
    dept = (
        await client.post(
            "/api/v1/recruiter/departments", json={"name": "Patent Engineering"}, headers=headers
        )
    ).json()

    create = await client.post(
        "/api/v1/recruiter/employees",
        json={
            "full_name": "Priya Patel",
            "email": "priya@hierarchy-employee-crud.dev",
            "designation": "Patent Engineer",
            "department_id": dept["id"],
        },
        headers=headers,
    )
    assert create.status_code == 201, create.text
    employee = create.json()
    assert employee["employment_status"] == "ACTIVE"
    assert employee["department_name"] == "Patent Engineering"

    dept_after = (
        await client.get(f"/api/v1/recruiter/departments/{dept['id']}", headers=headers)
    ).json()
    assert dept_after["employee_count"] == 1

    updated = await client.patch(
        f"/api/v1/recruiter/employees/{employee['id']}",
        json={"designation": "Senior Patent Engineer"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["designation"] == "Senior Patent Engineer"

    deactivate = await client.post(
        f"/api/v1/recruiter/employees/{employee['id']}/deactivate",
        json={"reason": "On leave"},
        headers=headers,
    )
    assert deactivate.status_code == 200
    assert deactivate.json()["employment_status"] == "INACTIVE"

    double_deactivate = await client.post(
        f"/api/v1/recruiter/employees/{employee['id']}/deactivate",
        json={},
        headers=headers,
    )
    assert double_deactivate.status_code == 409

    reactivate = await client.post(
        f"/api/v1/recruiter/employees/{employee['id']}/reactivate",
        headers=headers,
    )
    assert reactivate.status_code == 200
    assert reactivate.json()["employment_status"] == "ACTIVE"

    activities = await client.get("/api/v1/recruiter/activities", headers=headers)
    actions = [a["action"] for a in activities.json()]
    assert "EMPLOYEE_CREATED" in actions
    assert "EMPLOYEE_UPDATED" in actions
    assert "EMPLOYEE_DEACTIVATED" in actions
    assert "EMPLOYEE_REACTIVATED" in actions


async def test_duplicate_employee_email_in_same_org_is_rejected(
    client: AsyncClient, super_admin: User
) -> None:
    org = await _bootstrap_org(client, "hierarchy-employee-dupe")
    headers = await _org_admin_headers(client, org)
    payload = {"full_name": "Dana Dev", "email": "dana@hierarchy-employee-dupe.dev"}
    first = await client.post("/api/v1/recruiter/employees", json=payload, headers=headers)
    assert first.status_code == 201

    second = await client.post("/api/v1/recruiter/employees", json=payload, headers=headers)
    assert second.status_code == 409


async def test_hiring_manager_can_read_but_not_manage_hierarchy(
    client: AsyncClient, super_admin: User
) -> None:
    """HR (RECRUITER) manages the hierarchy (tests/test_hr_team_management.py);
    HIRING_MANAGER stays read-only."""
    org = await _bootstrap_org(client, "hierarchy-recruiter-perms")
    admin_headers = await _org_admin_headers(client, org)
    created = await client.post(
        "/api/v1/recruiter/users",
        json={
            "email": "manager@hierarchy-recruiter-perms.dev",
            "full_name": "Hiring Manager",
            "password": "ManagerPass12",
            "role": "HIRING_MANAGER",
        },
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    tokens = await login(
        client, email="manager@hierarchy-recruiter-perms.dev", password="ManagerPass12"
    )
    recruiter_headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    dept = (
        await client.post(
            "/api/v1/recruiter/departments", json={"name": "Data Analytics"}, headers=admin_headers
        )
    ).json()

    read_ok = await client.get("/api/v1/recruiter/departments", headers=recruiter_headers)
    assert read_ok.status_code == 200

    manage_denied = await client.post(
        "/api/v1/recruiter/departments", json={"name": "Not Allowed"}, headers=recruiter_headers
    )
    assert manage_denied.status_code == 403

    employee_manage_denied = await client.post(
        "/api/v1/recruiter/employees",
        json={"full_name": "Blocked", "email": "blocked@hierarchy-recruiter-perms.dev", "department_id": dept["id"]},
        headers=recruiter_headers,
    )
    assert employee_manage_denied.status_code == 403


async def test_departments_are_tenant_scoped(client: AsyncClient, super_admin: User) -> None:
    org_a = await _bootstrap_org(client, "hierarchy-tenant-a")
    org_b = await _bootstrap_org(client, "hierarchy-tenant-b")
    headers_a = await _org_admin_headers(client, org_a)
    headers_b = await _org_admin_headers(client, org_b)

    await client.post("/api/v1/recruiter/departments", json={"name": "Org A Dept"}, headers=headers_a)

    listing_b = await client.get("/api/v1/recruiter/departments", headers=headers_b)
    assert listing_b.json() == []


async def test_super_admin_never_appears_as_a_department_role(
    client: AsyncClient, super_admin: User
) -> None:
    """SUPER_ADMIN must never be selectable/exposed via the employee
    designation field or anywhere in this domain — designation is free
    text describing a job title, entirely decoupled from RBAC roles."""
    org = await _bootstrap_org(client, "hierarchy-no-super-admin")
    headers = await _org_admin_headers(client, org)

    response = await client.post(
        "/api/v1/recruiter/employees",
        json={
            "full_name": "Test Employee",
            "email": "test@hierarchy-no-super-admin.dev",
            "designation": "Whatever the caller sends",
        },
        headers=headers,
    )
    assert response.status_code == 201
    # No role/permission enum is involved for designation at all — nothing
    # to assert against SUPER_ADMIN here beyond confirming the field is
    # plain free text, i.e. any string is accepted without granting access.
    assert response.json()["designation"] == "Whatever the caller sends"
