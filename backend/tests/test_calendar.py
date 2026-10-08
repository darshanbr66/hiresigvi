"""Calendar events: CRUD, ownership, permissions, tenant isolation,
timezone handling, and reminder firing/idempotency (the DB-backed poll —
no Celery/Redis worker is provisioned)."""

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from app.models.user import User
from tests.conftest import SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD, login


async def _bootstrap_org_with_two_users(client: AsyncClient, slug: str) -> dict:
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
    admin_tokens = await login(client, email=org_payload["admin_email"], password=org_payload["admin_password"])
    admin_headers = {"Authorization": f"Bearer {admin_tokens['access_token']}"}

    await client.post(
        "/api/v1/recruiter/users",
        json={
            "email": f"recruiter@{slug}.dev",
            "password": "RecruiterPass1",
            "full_name": "Rita Recruiter",
            "role": "RECRUITER",
        },
        headers=admin_headers,
    )
    recruiter_tokens = await login(client, email=f"recruiter@{slug}.dev", password="RecruiterPass1")
    recruiter_headers = {"Authorization": f"Bearer {recruiter_tokens['access_token']}"}

    return {"admin_headers": admin_headers, "recruiter_headers": recruiter_headers}


def _iso(dt: datetime) -> str:
    return dt.isoformat()


async def test_create_and_fetch_event(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_two_users(client, "cal-create")
    start = datetime.now(UTC) + timedelta(days=1)

    create_resp = await client.post(
        "/api/v1/recruiter/calendar/events",
        json={
            "title": "Interview with Priya",
            "event_type": "INTERVIEW",
            "start_at": _iso(start),
            "end_at": _iso(start + timedelta(hours=1)),
            "timezone": "Asia/Kolkata",
        },
        headers=ctx["admin_headers"],
    )
    assert create_resp.status_code == 201, create_resp.text
    body = create_resp.json()
    assert body["title"] == "Interview with Priya"
    assert body["status"] == "SCHEDULED"
    assert body["organizer_name"] == "Acme Admin"

    fetched = await client.get(f"/api/v1/recruiter/calendar/events/{body['id']}", headers=ctx["admin_headers"])
    assert fetched.status_code == 200
    assert fetched.json()["title"] == "Interview with Priya"


async def test_end_before_start_is_rejected(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_two_users(client, "cal-bad-range")
    start = datetime.now(UTC) + timedelta(days=1)

    response = await client.post(
        "/api/v1/recruiter/calendar/events",
        json={
            "title": "Backwards event",
            "start_at": _iso(start),
            "end_at": _iso(start - timedelta(hours=1)),
            "timezone": "UTC",
        },
        headers=ctx["admin_headers"],
    )
    assert response.status_code == 422


async def test_list_events_filters_by_date_range(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_two_users(client, "cal-range")
    now = datetime.now(UTC)

    async def _create(title: str, start: datetime) -> None:
        await client.post(
            "/api/v1/recruiter/calendar/events",
            json={
                "title": title,
                "start_at": _iso(start),
                "end_at": _iso(start + timedelta(hours=1)),
                "timezone": "UTC",
            },
            headers=ctx["admin_headers"],
        )

    await _create("This week", now + timedelta(days=2))
    await _create("Next month", now + timedelta(days=40))

    in_range = await client.get(
        "/api/v1/recruiter/calendar/events",
        params={"start": _iso(now), "end": _iso(now + timedelta(days=7))},
        headers=ctx["admin_headers"],
    )
    titles = [e["title"] for e in in_range.json()]
    assert "This week" in titles
    assert "Next month" not in titles


async def test_only_the_organizer_can_edit_or_delete(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_two_users(client, "cal-organizer")
    start = datetime.now(UTC) + timedelta(days=1)

    create_resp = await client.post(
        "/api/v1/recruiter/calendar/events",
        json={"title": "Team sync", "start_at": _iso(start), "end_at": _iso(start + timedelta(hours=1)), "timezone": "UTC"},
        headers=ctx["admin_headers"],
    )
    event_id = create_resp.json()["id"]

    forbidden_edit = await client.patch(
        f"/api/v1/recruiter/calendar/events/{event_id}",
        json={"title": "Hijacked"},
        headers=ctx["recruiter_headers"],
    )
    assert forbidden_edit.status_code == 403

    forbidden_delete = await client.delete(
        f"/api/v1/recruiter/calendar/events/{event_id}", headers=ctx["recruiter_headers"]
    )
    assert forbidden_delete.status_code == 403

    # Anyone with calendar.read can still see it.
    visible = await client.get(f"/api/v1/recruiter/calendar/events/{event_id}", headers=ctx["recruiter_headers"])
    assert visible.status_code == 200

    allowed_edit = await client.patch(
        f"/api/v1/recruiter/calendar/events/{event_id}",
        json={"title": "Renamed by organizer"},
        headers=ctx["admin_headers"],
    )
    assert allowed_edit.status_code == 200
    assert allowed_edit.json()["title"] == "Renamed by organizer"


async def test_events_are_tenant_isolated(client: AsyncClient, super_admin: User) -> None:
    org_a = await _bootstrap_org_with_two_users(client, "cal-tenant-a")
    org_b = await _bootstrap_org_with_two_users(client, "cal-tenant-b")
    start = datetime.now(UTC) + timedelta(days=1)

    create_resp = await client.post(
        "/api/v1/recruiter/calendar/events",
        json={"title": "Org A event", "start_at": _iso(start), "end_at": _iso(start + timedelta(hours=1)), "timezone": "UTC"},
        headers=org_a["admin_headers"],
    )
    event_id = create_resp.json()["id"]

    cross_tenant = await client.get(f"/api/v1/recruiter/calendar/events/{event_id}", headers=org_b["admin_headers"])
    assert cross_tenant.status_code == 404

    org_b_list = await client.get(
        "/api/v1/recruiter/calendar/events",
        params={"start": _iso(start - timedelta(days=1)), "end": _iso(start + timedelta(days=1))},
        headers=org_b["admin_headers"],
    )
    assert not any(e["id"] == event_id for e in org_b_list.json())


async def test_timezone_is_preserved_for_display_while_instant_is_unambiguous(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_two_users(client, "cal-timezone")
    start = datetime(2026, 12, 25, 9, 30, tzinfo=UTC)

    create_resp = await client.post(
        "/api/v1/recruiter/calendar/events",
        json={
            "title": "Christmas standup",
            "start_at": _iso(start),
            "end_at": _iso(start + timedelta(minutes=30)),
            "timezone": "Asia/Kolkata",
        },
        headers=ctx["admin_headers"],
    )
    body = create_resp.json()
    assert body["timezone"] == "Asia/Kolkata"
    # The stored instant round-trips exactly regardless of the display zone.
    assert datetime.fromisoformat(body["start_at"]) == start


async def test_attendees_must_belong_to_the_same_organization(client: AsyncClient, super_admin: User) -> None:
    org_a = await _bootstrap_org_with_two_users(client, "cal-attendee-a")
    org_b = await _bootstrap_org_with_two_users(client, "cal-attendee-b")
    start = datetime.now(UTC) + timedelta(days=1)

    org_b_me = await client.get("/api/v1/recruiter/auth/me", headers=org_b["admin_headers"])
    org_b_user_id = org_b_me.json()["id"]

    response = await client.post(
        "/api/v1/recruiter/calendar/events",
        json={
            "title": "Cross-tenant invite",
            "start_at": _iso(start),
            "end_at": _iso(start + timedelta(hours=1)),
            "timezone": "UTC",
            "attendee_ids": [org_b_user_id],
        },
        headers=org_a["admin_headers"],
    )
    assert response.status_code == 404


async def test_attendee_options_are_available_without_user_read_and_stay_tenant_scoped(
    client: AsyncClient, super_admin: User
) -> None:
    """Picking a meeting attendee is not user administration: a
    HIRING_MANAGER has `calendar.read` but not `user.read`, and must still be
    able to list the people they may invite — from their own organization
    only."""
    org_a = await _bootstrap_org_with_two_users(client, "cal-attendee-opts-a")
    org_b = await _bootstrap_org_with_two_users(client, "cal-attendee-opts-b")

    manager = await client.post(
        "/api/v1/recruiter/users",
        json={
            "email": "manager@cal-attendee-opts-a.dev",
            "full_name": "Hiring Manager",
            "password": "ManagerPass12",
            "role": "HIRING_MANAGER",
        },
        headers=org_a["admin_headers"],
    )
    assert manager.status_code == 201, manager.text
    manager_tokens = await login(
        client, email="manager@cal-attendee-opts-a.dev", password="ManagerPass12"
    )
    manager_headers = {"Authorization": f"Bearer {manager_tokens['access_token']}"}

    # The Team directory is ORG_ADMIN/HR only.
    assert (await client.get("/api/v1/recruiter/users", headers=manager_headers)).status_code == 403

    from_manager = await client.get(
        "/api/v1/recruiter/calendar/attendee-options", headers=manager_headers
    )
    assert from_manager.status_code == 200, from_manager.text
    assert {option["email"] for option in from_manager.json()} == {
        "admin@cal-attendee-opts-a.dev",
        "recruiter@cal-attendee-opts-a.dev",
        "manager@cal-attendee-opts-a.dev",
    }

    response = await client.get(
        "/api/v1/recruiter/calendar/attendee-options", headers=org_a["recruiter_headers"]
    )
    assert response.status_code == 200, response.text
    emails = {option["email"] for option in response.json()}
    assert emails == {
        "admin@cal-attendee-opts-a.dev",
        "recruiter@cal-attendee-opts-a.dev",
        "manager@cal-attendee-opts-a.dev",
    }

    org_b_emails = {
        option["email"]
        for option in (
            await client.get(
                "/api/v1/recruiter/calendar/attendee-options", headers=org_b["recruiter_headers"]
            )
        ).json()
    }
    assert org_b_emails.isdisjoint(emails)


async def test_attendee_options_can_be_invited_and_are_persisted(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_two_users(client, "cal-attendee-opts-invite")
    start = datetime.now(UTC) + timedelta(days=1)

    options = (
        await client.get("/api/v1/recruiter/calendar/attendee-options", headers=ctx["recruiter_headers"])
    ).json()
    admin_id = next(o["id"] for o in options if o["email"].startswith("admin@"))

    created = await client.post(
        "/api/v1/recruiter/calendar/events",
        json={
            "title": "Panel interview",
            "start_at": _iso(start),
            "end_at": _iso(start + timedelta(hours=1)),
            "timezone": "UTC",
            "attendee_ids": [admin_id],
        },
        headers=ctx["recruiter_headers"],
    )
    assert created.status_code == 201, created.text
    assert created.json()["attendee_ids"] == [admin_id]

    fetched = await client.get(
        f"/api/v1/recruiter/calendar/events/{created.json()['id']}", headers=ctx["recruiter_headers"]
    )
    assert fetched.json()["attendee_ids"] == [admin_id]


async def test_calendar_read_requires_permission(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_two_users(client, "cal-permission")

    await client.post(
        "/api/v1/recruiter/users",
        json={
            "email": "interviewer@cal-permission.dev",
            "password": "SomePassword1",
            "full_name": "Ivy Interviewer",
            "role": "INTERVIEWER",
        },
        headers=ctx["admin_headers"],
    )
    interviewer_tokens = await login(client, email="interviewer@cal-permission.dev", password="SomePassword1")
    interviewer_headers = {"Authorization": f"Bearer {interviewer_tokens['access_token']}"}

    # INTERVIEWER has calendar.read but not calendar.manage.
    now = datetime.now(UTC)
    read_resp = await client.get(
        "/api/v1/recruiter/calendar/events",
        params={"start": _iso(now), "end": _iso(now + timedelta(days=1))},
        headers=interviewer_headers,
    )
    assert read_resp.status_code == 200

    create_resp = await client.post(
        "/api/v1/recruiter/calendar/events",
        json={"title": "Nope", "start_at": _iso(now), "end_at": _iso(now + timedelta(hours=1)), "timezone": "UTC"},
        headers=interviewer_headers,
    )
    assert create_resp.status_code == 403


async def test_reminder_fires_exactly_once_and_notifies_attendees_but_not_the_organizer(
    client: AsyncClient, super_admin: User
) -> None:
    """The organizer scheduled the event themselves and is excluded from
    the reminder by default — only attendees are notified, unless the
    organizer explicitly added themselves as an attendee too (see the next
    test)."""
    ctx = await _bootstrap_org_with_two_users(client, "cal-reminder")
    recruiter_me = await client.get("/api/v1/recruiter/auth/me", headers=ctx["recruiter_headers"])
    recruiter_id = recruiter_me.json()["id"]

    # A reminder "due" 1 minute from now, with a 60-minute lead time — due now.
    start = datetime.now(UTC) + timedelta(minutes=1)
    create_resp = await client.post(
        "/api/v1/recruiter/calendar/events",
        json={
            "title": "Interview with Priya",
            "start_at": _iso(start),
            "end_at": _iso(start + timedelta(hours=1)),
            "timezone": "UTC",
            "reminder_minutes_before": 60,
            "attendee_ids": [recruiter_id],
        },
        headers=ctx["admin_headers"],
    )
    assert create_resp.status_code == 201, create_resp.text

    # The reminder-check is piggybacked on the notification poll.
    admin_notifications = await client.get("/api/v1/recruiter/notifications", headers=ctx["admin_headers"])
    assert not any(n["type"] == "CALENDAR_REMINDER" for n in admin_notifications.json())

    recruiter_notifications = await client.get("/api/v1/recruiter/notifications", headers=ctx["recruiter_headers"])
    assert any(n["type"] == "CALENDAR_REMINDER" for n in recruiter_notifications.json())
    reminder = next(n for n in recruiter_notifications.json() if n["type"] == "CALENDAR_REMINDER")
    assert reminder["related_entity_type"] == "CALENDAR_EVENT"

    # Polling again must never fire (and re-notify for) the same reminder.
    recruiter_notifications_second_poll = await client.get(
        "/api/v1/recruiter/notifications", headers=ctx["recruiter_headers"]
    )
    reminder_count = sum(
        1 for n in recruiter_notifications_second_poll.json() if n["type"] == "CALENDAR_REMINDER"
    )
    assert reminder_count == 1


async def test_organizer_receives_reminder_only_if_explicitly_also_an_attendee(
    client: AsyncClient, super_admin: User
) -> None:
    ctx = await _bootstrap_org_with_two_users(client, "cal-reminder-self")
    admin_me = await client.get("/api/v1/recruiter/auth/me", headers=ctx["admin_headers"])
    admin_id = admin_me.json()["id"]
    start = datetime.now(UTC) + timedelta(minutes=1)

    await client.post(
        "/api/v1/recruiter/calendar/events",
        json={
            "title": "Solo prep block",
            "start_at": _iso(start),
            "end_at": _iso(start + timedelta(hours=1)),
            "timezone": "UTC",
            "reminder_minutes_before": 60,
            "attendee_ids": [admin_id],
        },
        headers=ctx["admin_headers"],
    )

    admin_notifications = await client.get("/api/v1/recruiter/notifications", headers=ctx["admin_headers"])
    assert any(n["type"] == "CALENDAR_REMINDER" for n in admin_notifications.json())


async def test_search_matches_title_type_and_organizer(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_two_users(client, "cal-search")
    start = datetime.now(UTC) + timedelta(days=1)

    await client.post(
        "/api/v1/recruiter/calendar/events",
        json={
            "title": "Interview with Priya",
            "event_type": "INTERVIEW",
            "start_at": _iso(start),
            "end_at": _iso(start + timedelta(hours=1)),
            "timezone": "UTC",
        },
        headers=ctx["admin_headers"],
    )
    await client.post(
        "/api/v1/recruiter/calendar/events",
        json={
            "title": "Team standup",
            "event_type": "TEAM_MEETING",
            "start_at": _iso(start),
            "end_at": _iso(start + timedelta(hours=1)),
            "timezone": "UTC",
        },
        headers=ctx["admin_headers"],
    )

    by_title = await client.get(
        "/api/v1/recruiter/calendar/events",
        params={"start": _iso(start - timedelta(days=1)), "end": _iso(start + timedelta(days=1)), "search": "priya"},
        headers=ctx["admin_headers"],
    )
    titles = [e["title"] for e in by_title.json()]
    assert titles == ["Interview with Priya"]

    by_type = await client.get(
        "/api/v1/recruiter/calendar/events",
        params={
            "start": _iso(start - timedelta(days=1)),
            "end": _iso(start + timedelta(days=1)),
            "search": "team_meeting",
        },
        headers=ctx["admin_headers"],
    )
    assert [e["title"] for e in by_type.json()] == ["Team standup"]

    by_organizer = await client.get(
        "/api/v1/recruiter/calendar/events",
        params={
            "start": _iso(start - timedelta(days=1)),
            "end": _iso(start + timedelta(days=1)),
            "search": "acme admin",
        },
        headers=ctx["admin_headers"],
    )
    assert len(by_organizer.json()) == 2


async def test_event_lifecycle_is_recorded_in_activity(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_two_users(client, "cal-activity")
    recruiter_me = await client.get("/api/v1/recruiter/auth/me", headers=ctx["recruiter_headers"])
    recruiter_id = recruiter_me.json()["id"]
    start = datetime.now(UTC) + timedelta(days=1)

    create_resp = await client.post(
        "/api/v1/recruiter/calendar/events",
        json={
            "title": "Interview with Priya",
            "event_type": "INTERVIEW",
            "start_at": _iso(start),
            "end_at": _iso(start + timedelta(hours=1)),
            "timezone": "UTC",
            "attendee_ids": [recruiter_id],
        },
        headers=ctx["admin_headers"],
    )
    event_id = create_resp.json()["id"]

    await client.patch(
        f"/api/v1/recruiter/calendar/events/{event_id}",
        json={"title": "Interview with Priya (rescheduled)", "attendee_ids": []},
        headers=ctx["admin_headers"],
    )
    await client.delete(f"/api/v1/recruiter/calendar/events/{event_id}", headers=ctx["admin_headers"])

    activities = (await client.get("/api/v1/recruiter/activities", headers=ctx["admin_headers"])).json()
    calendar_activities = [a for a in activities if a["entity_type"] == "calendar_event"]
    actions = {a["action"] for a in calendar_activities}
    assert {"CALENDAR_EVENT_CREATED", "CALENDAR_EVENT_UPDATED", "CALENDAR_EVENT_DELETED"} == actions

    updated_activity = next(a for a in calendar_activities if a["action"] == "CALENDAR_EVENT_UPDATED")
    assert "attendee" in updated_activity["description"].lower()
    for activity in calendar_activities:
        # Safe metadata only — never sensitive candidate/description text.
        assert activity["entity_label"] in (
            "Interview with Priya",
            "Interview with Priya (rescheduled)",
        )


async def test_reminder_does_not_fire_before_it_is_due(client: AsyncClient, super_admin: User) -> None:
    ctx = await _bootstrap_org_with_two_users(client, "cal-reminder-not-due")
    start = datetime.now(UTC) + timedelta(days=7)

    await client.post(
        "/api/v1/recruiter/calendar/events",
        json={
            "title": "Far future interview",
            "start_at": _iso(start),
            "end_at": _iso(start + timedelta(hours=1)),
            "timezone": "UTC",
            "reminder_minutes_before": 15,
        },
        headers=ctx["admin_headers"],
    )

    notifications = await client.get("/api/v1/recruiter/notifications", headers=ctx["admin_headers"])
    assert not any(n["type"] == "CALENDAR_REMINDER" for n in notifications.json())
