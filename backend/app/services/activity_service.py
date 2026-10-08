"""Activity/audit trail (app/models/activity.py). Every write goes through
`record_activity` — nothing else inserts into this table, and nothing ever
updates a row. The one removal path is `delete_activity`, reachable only
through the ORG_ADMIN-gated `activity.delete` permission and always scoped
to the caller's own organization; each removal is written to the
structured application log so it is never wholly untraceable (CLAUDE.md
§ 3)."""

import uuid
from collections.abc import Collection
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.activity import Activity
from app.models.user import User

logger = get_logger(__name__)


#: Activity.entity_label's column length.
_ENTITY_LABEL_MAX = 255


def _clip_label(label: str | None) -> str | None:
    """A label is a display snapshot built from user-entered names, and a
    candidate's full name has no length limit — so a long one is shortened
    to fit rather than failing the action it records."""
    if label is None or len(label) <= _ENTITY_LABEL_MAX:
        return label
    return label[: _ENTITY_LABEL_MAX - 1] + "…"


async def record_activity(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    actor: User | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    entity_label: str | None = None,
    description: str | None = None,
    reason: str | None = None,
) -> Activity:
    activity = Activity(
        organization_id=organization_id,
        actor_user_id=actor.id if actor else None,
        actor_name=actor.full_name if actor else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_label=_clip_label(entity_label),
        description=description,
        reason=reason,
    )
    db.add(activity)
    await db.flush()
    return activity


async def list_activities(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    search: str | None = None,
    action: str | None = None,
    entity_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Activity]:
    query = select(Activity).where(Activity.organization_id == organization_id)

    if action:
        query = query.where(Activity.action == action)
    if entity_type:
        query = query.where(Activity.entity_type == entity_type)
    if search:
        term = f"%{search.strip()}%"
        query = query.where(
            or_(
                Activity.entity_label.ilike(term),
                Activity.actor_name.ilike(term),
                Activity.description.ilike(term),
                Activity.reason.ilike(term),
                Activity.action.ilike(term),
            )
        )

    query = query.order_by(Activity.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(query)
    return list(result.scalars().all())


async def delete_activity(
    db: AsyncSession, *, organization_id: uuid.UUID, activity_id: uuid.UUID, actor: User
) -> bool:
    """Deletes one activity row that belongs to `organization_id`. The
    organization filter is part of the DELETE itself (not a check-then-
    delete), so an id from another tenant simply matches nothing — the
    caller cannot distinguish "not yours" from "doesn't exist". Returns
    whether a row was removed."""
    result = await db.execute(
        delete(Activity)
        .where(Activity.id == activity_id, Activity.organization_id == organization_id)
        .returning(Activity.action, Activity.entity_type, Activity.entity_label)
    )
    row = result.first()
    if row is None:
        return False

    logger.info(
        "Activity entry deleted",
        extra={
            "extra_fields": {
                "organization_id": str(organization_id),
                "activity_id": str(activity_id),
                "deleted_by_user_id": str(actor.id),
                "action": row.action,
                "entity_type": row.entity_type,
            }
        },
    )
    return True


async def count_activities(db: AsyncSession, organization_id: uuid.UUID) -> int:
    total = await db.scalar(
        select(func.count(Activity.id)).where(Activity.organization_id == organization_id)
    )
    return total or 0


async def delete_activities(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    activity_ids: Collection[uuid.UUID],
    actor: User,
) -> int:
    """Deletes the given entries in ONE statement and returns how many rows
    were actually removed. The organization filter is part of the DELETE, so
    ids belonging to another tenant, or already deleted, simply match nothing
    — they are never touched and are not counted. One concise log line
    records the bulk action; it deliberately does not create an Activity per
    deleted row."""
    unique_ids = set(activity_ids)
    if not unique_ids:
        return 0

    result = await db.execute(
        delete(Activity).where(
            Activity.organization_id == organization_id, Activity.id.in_(unique_ids)
        )
    )
    deleted = cast(CursorResult[Any], result).rowcount
    logger.info(
        "Activity entries bulk deleted",
        extra={
            "extra_fields": {
                "organization_id": str(organization_id),
                "deleted_by_user_id": str(actor.id),
                "requested": len(unique_ids),
                "deleted": deleted,
            }
        },
    )
    return deleted


async def delete_all_activities(
    db: AsyncSession, *, organization_id: uuid.UUID, actor: User
) -> int:
    """Deletes every activity entry of ONE organization — the caller's own,
    never anyone else's — and returns the count."""
    result = await db.execute(delete(Activity).where(Activity.organization_id == organization_id))
    deleted = cast(CursorResult[Any], result).rowcount
    logger.info(
        "All activity entries deleted for organization",
        extra={
            "extra_fields": {
                "organization_id": str(organization_id),
                "deleted_by_user_id": str(actor.id),
                "deleted": deleted,
            }
        },
    )
    return deleted

