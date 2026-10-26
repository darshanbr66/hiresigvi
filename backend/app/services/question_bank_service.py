"""The organization's question bank (docs/assessment.md § 10).

Items are versioned: changing a question's content creates a new immutable
`QuestionBankItemVersion` (with its own answer key) and moves
`current_version_number` forward; old versions are never edited, so a test
that snapshotted one keeps exactly what candidates saw. Every query is
scoped to the caller's organization explicitly *and* by RLS; ids from
another organization behave exactly like unknown ones.
"""

import string
import uuid
from dataclasses import dataclass

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, NotFoundError, UnprocessableError
from app.core.text import collapse_whitespace, normalize_name
from app.models.assessment import CHOICE_QUESTION_TYPES, Question, QuestionType
from app.models.question_bank import (
    QUESTION_BANK_TRANSITIONS,
    QuestionBankAnswerKey,
    QuestionBankItem,
    QuestionBankItemTag,
    QuestionBankItemVersion,
    QuestionBankStatus,
    QuestionCategory,
    QuestionDifficulty,
    QuestionTag,
)
from app.models.user import User
from app.schemas.question_bank import (
    BankItemCreateRequest,
    BankItemUpdateRequest,
    BankQuestionContent,
    CategoryCreateRequest,
    CategoryUpdateRequest,
)
from app.services import activity_service

_OPTION_KEYS = string.ascii_uppercase
_CONTENT_FIELDS = set(BankQuestionContent.model_fields)


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# --- Categories ---


async def list_categories(db: AsyncSession, organization_id: uuid.UUID) -> list[QuestionCategory]:
    result = await db.execute(
        select(QuestionCategory)
        .where(QuestionCategory.organization_id == organization_id)
        .order_by(QuestionCategory.name)
    )
    return list(result.scalars().all())


def category_paths(categories: list[QuestionCategory]) -> dict[uuid.UUID, str]:
    by_id = {c.id: c for c in categories}
    paths: dict[uuid.UUID, str] = {}

    def path(category: QuestionCategory) -> str:
        if category.id in paths:
            return paths[category.id]
        names = [category.name]
        seen = {category.id}
        parent_id = category.parent_id
        while parent_id is not None and parent_id in by_id and parent_id not in seen:
            seen.add(parent_id)
            parent = by_id[parent_id]
            names.append(parent.name)
            parent_id = parent.parent_id
        paths[category.id] = " › ".join(reversed(names))
        return paths[category.id]

    for category in categories:
        path(category)
    return paths


async def category_question_counts(
    db: AsyncSession, organization_id: uuid.UUID
) -> dict[uuid.UUID, int]:
    result = await db.execute(
        select(QuestionBankItem.category_id, func.count(QuestionBankItem.id))
        .where(
            QuestionBankItem.organization_id == organization_id,
            QuestionBankItem.category_id.is_not(None),
            QuestionBankItem.status != QuestionBankStatus.ARCHIVED,
        )
        .group_by(QuestionBankItem.category_id)
    )
    return {category_id: count for category_id, count in result.all()}


async def _require_category(
    db: AsyncSession, organization_id: uuid.UUID, category_id: uuid.UUID
) -> QuestionCategory:
    category = await db.get(QuestionCategory, category_id)
    if category is None or category.organization_id != organization_id:
        raise NotFoundError("Category not found.")
    return category


async def create_category(
    db: AsyncSession, *, actor: User, payload: CategoryCreateRequest
) -> QuestionCategory:
    assert actor.organization_id is not None
    if payload.parent_id is not None:
        parent = await _require_category(db, actor.organization_id, payload.parent_id)
        if not parent.is_active:
            raise ConflictError("The parent category is inactive.")
    category = QuestionCategory(
        organization_id=actor.organization_id,
        name=payload.name,
        normalized_name=normalize_name(payload.name),
        parent_id=payload.parent_id,
        description=payload.description,
    )
    db.add(category)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as exc:
        raise ConflictError(
            f'A category named "{payload.name}" already exists here.', code="category_exists"
        ) from exc
    await activity_service.record_activity(
        db,
        organization_id=actor.organization_id,
        actor=actor,
        action="QUESTION_CATEGORY_CREATED",
        entity_type="question_category",
        entity_id=category.id,
        entity_label=category.name,
        description=f'Question category "{category.name}" was created.',
    )
    return category


async def update_category(
    db: AsyncSession,
    *,
    actor: User,
    category_id: uuid.UUID,
    payload: CategoryUpdateRequest,
) -> QuestionCategory:
    assert actor.organization_id is not None
    category = await _require_category(db, actor.organization_id, category_id)
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("name"):
        category.name = updates["name"]
        category.normalized_name = normalize_name(updates["name"])
    if "description" in updates:
        category.description = updates["description"]
    if updates.get("is_active") is not None:
        category.is_active = updates["is_active"]
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as exc:
        raise ConflictError("A category with this name already exists here.") from exc
    await activity_service.record_activity(
        db,
        organization_id=actor.organization_id,
        actor=actor,
        action="QUESTION_CATEGORY_UPDATED",
        entity_type="question_category",
        entity_id=category.id,
        entity_label=category.name,
        description=f'Question category "{category.name}" was updated.',
    )
    return category


async def _category_subtree_ids(
    db: AsyncSession, organization_id: uuid.UUID, root_id: uuid.UUID
) -> list[uuid.UUID]:
    """The category and every descendant (recursive), so filtering by
    "Programming" also finds questions filed under "Programming › Python"."""
    base = (
        select(QuestionCategory.id)
        .where(QuestionCategory.id == root_id, QuestionCategory.organization_id == organization_id)
        .cte("subtree", recursive=True)
    )
    tree = base.union_all(
        select(QuestionCategory.id).where(QuestionCategory.parent_id == base.c.id)
    )
    result = await db.execute(select(tree.c.id))
    return list(result.scalars().all())


# --- Tags ---


async def list_tags(db: AsyncSession, organization_id: uuid.UUID) -> list[tuple[QuestionTag, int]]:
    result = await db.execute(
        select(QuestionTag, func.count(QuestionBankItemTag.item_id))
        .outerjoin(QuestionBankItemTag, QuestionBankItemTag.tag_id == QuestionTag.id)
        .where(QuestionTag.organization_id == organization_id)
        .group_by(QuestionTag.id)
        .order_by(QuestionTag.name)
    )
    return [(tag, count) for tag, count in result.all()]


async def resolve_tags(
    db: AsyncSession, organization_id: uuid.UUID, names: list[str]
) -> list[QuestionTag]:
    """The organization's tags with these names, creating any that don't
    exist yet (HR adds tags simply by using them)."""
    wanted: dict[str, str] = {}
    for name in names:
        cleaned = collapse_whitespace(name)[:80]
        if cleaned:
            wanted.setdefault(normalize_name(cleaned), cleaned)
    if not wanted:
        return []
    existing = await db.execute(
        select(QuestionTag).where(
            QuestionTag.organization_id == organization_id,
            QuestionTag.normalized_name.in_(list(wanted)),
        )
    )
    tags = {tag.normalized_name: tag for tag in existing.scalars().all()}
    for normalized, display in wanted.items():
        if normalized not in tags:
            tag = QuestionTag(organization_id=organization_id, name=display, normalized_name=normalized)
            db.add(tag)
            tags[normalized] = tag
    await db.flush()
    return [tags[n] for n in wanted]


async def _set_item_tags(db: AsyncSession, item: QuestionBankItem, tags: list[QuestionTag]) -> None:
    current = await db.execute(
        select(QuestionBankItemTag).where(QuestionBankItemTag.item_id == item.id)
    )
    for link in current.scalars().all():
        await db.delete(link)
    await db.flush()
    for tag in tags:
        db.add(QuestionBankItemTag(item_id=item.id, tag_id=tag.id))
    await db.flush()


# --- Items & versions ---


def _version_rows(
    content: BankQuestionContent,
) -> tuple[list[dict[str, str]], list[str]]:
    options: list[dict[str, str]] = []
    correct: list[str] = []
    if content.type in CHOICE_QUESTION_TYPES:
        for index, option in enumerate(content.options):
            key = _OPTION_KEYS[index]
            options.append({"key": key, "label": option.label.strip()})
            if option.is_correct:
                correct.append(key)
    return options, correct


async def _add_version(
    db: AsyncSession,
    *,
    item: QuestionBankItem,
    version_number: int,
    content: BankQuestionContent,
    actor: User,
) -> QuestionBankItemVersion:
    options, correct = _version_rows(content)
    version = QuestionBankItemVersion(
        organization_id=item.organization_id,
        item_id=item.id,
        version_number=version_number,
        type=content.type,
        prompt=content.prompt.strip(),
        points=content.points,
        negative_points=content.negative_points,
        options=options,
        created_by=actor.id,
    )
    db.add(version)
    await db.flush()
    db.add(
        QuestionBankAnswerKey(
            organization_id=item.organization_id,
            version_id=version.id,
            correct_option_keys=correct,
            accepted_answers=content.accepted_answers,
            case_sensitive=content.case_sensitive,
            grading_notes=content.grading_notes,
            explanation=content.explanation,
        )
    )
    await db.flush()
    return version


async def create_item(
    db: AsyncSession, *, actor: User, payload: BankItemCreateRequest, record_activity: bool = True
) -> QuestionBankItem:
    """`record_activity=False` lets a bulk import log one summary entry
    instead of one per question."""
    assert actor.organization_id is not None
    if payload.category_id is not None:
        await _require_category(db, actor.organization_id, payload.category_id)
    item = QuestionBankItem(
        organization_id=actor.organization_id,
        category_id=payload.category_id,
        difficulty=payload.difficulty,
        status=payload.status,
        current_version_number=1,
        created_by=actor.id,
        updated_by=actor.id,
    )
    db.add(item)
    await db.flush()
    await _add_version(
        db,
        item=item,
        version_number=1,
        content=BankQuestionContent.model_validate(payload.model_dump(include=_CONTENT_FIELDS)),
        actor=actor,
    )
    await _set_item_tags(db, item, await resolve_tags(db, actor.organization_id, payload.tags))
    if not record_activity:
        return await require_item(db, actor.organization_id, item.id)
    await activity_service.record_activity(
        db,
        organization_id=actor.organization_id,
        actor=actor,
        action="QUESTION_BANK_ITEM_CREATED",
        entity_type="question_bank_item",
        entity_id=item.id,
        entity_label=_label(payload.prompt),
        description=f"A {payload.type.value.replace('_', ' ').lower()} question was added to the question bank.",
    )
    return await require_item(db, actor.organization_id, item.id)


def _label(prompt: str) -> str:
    text = collapse_whitespace(prompt)
    return text if len(text) <= 80 else f"{text[:77]}…"


async def get_item(
    db: AsyncSession, organization_id: uuid.UUID, item_id: uuid.UUID
) -> QuestionBankItem | None:
    result = await db.execute(
        select(QuestionBankItem)
        .where(QuestionBankItem.id == item_id, QuestionBankItem.organization_id == organization_id)
        .options(
            selectinload(QuestionBankItem.category),
            selectinload(QuestionBankItem.tags),
            selectinload(QuestionBankItem.versions).selectinload(QuestionBankItemVersion.answer_key),
        )
        .execution_options(populate_existing=True)
    )
    return result.unique().scalar_one_or_none()


async def require_item(
    db: AsyncSession, organization_id: uuid.UUID, item_id: uuid.UUID
) -> QuestionBankItem:
    item = await get_item(db, organization_id, item_id)
    if item is None:
        raise NotFoundError("Question not found.")
    return item


def current_version(item: QuestionBankItem) -> QuestionBankItemVersion:
    for version in item.versions:
        if version.version_number == item.current_version_number:
            return version
    raise NotFoundError("Question version not found.")  # unreachable: created together


def content_of(version: QuestionBankItemVersion) -> BankQuestionContent:
    key = version.answer_key
    correct = set(key.correct_option_keys)
    return BankQuestionContent(
        type=version.type,
        prompt=version.prompt,
        points=version.points,
        negative_points=version.negative_points,
        options=[
            {"label": option["label"], "is_correct": option["key"] in correct}
            for option in version.options
        ],
        accepted_answers=list(key.accepted_answers),
        case_sensitive=key.case_sensitive,
        grading_notes=key.grading_notes,
        explanation=key.explanation,
    )


async def update_item(
    db: AsyncSession, *, actor: User, item_id: uuid.UUID, payload: BankItemUpdateRequest
) -> QuestionBankItem:
    assert actor.organization_id is not None
    item = await require_item(db, actor.organization_id, item_id)
    if item.status == QuestionBankStatus.ARCHIVED:
        raise ConflictError("Restore this question to draft before editing it.", code="question_archived")

    changes: list[str] = []
    if payload.content is not None:
        if payload.content.model_dump() != content_of(current_version(item)).model_dump():
            next_number = item.current_version_number + 1
            await _add_version(
                db, item=item, version_number=next_number, content=payload.content, actor=actor
            )
            item.current_version_number = next_number
            changes.append(f"content (now version {next_number})")
    if payload.clear_category:
        item.category_id = None
        changes.append("category")
    elif payload.category_id is not None and payload.category_id != item.category_id:
        await _require_category(db, actor.organization_id, payload.category_id)
        item.category_id = payload.category_id
        changes.append("category")
    if payload.difficulty is not None and payload.difficulty != item.difficulty:
        item.difficulty = payload.difficulty
        changes.append("difficulty")
    if payload.tags is not None:
        await _set_item_tags(db, item, await resolve_tags(db, actor.organization_id, payload.tags))
        changes.append("tags")

    if not changes:
        return item
    item.updated_by = actor.id
    await db.flush()
    await activity_service.record_activity(
        db,
        organization_id=actor.organization_id,
        actor=actor,
        action="QUESTION_BANK_ITEM_UPDATED",
        entity_type="question_bank_item",
        entity_id=item.id,
        entity_label=_label(current_version(await require_item(db, actor.organization_id, item.id)).prompt),
        description=f"Question bank question updated: {', '.join(changes)}.",
    )
    return await require_item(db, actor.organization_id, item.id)


async def change_status(
    db: AsyncSession, *, actor: User, item_id: uuid.UUID, status: QuestionBankStatus
) -> QuestionBankItem:
    assert actor.organization_id is not None
    item = await require_item(db, actor.organization_id, item_id)
    if status == item.status:
        return item
    if status not in QUESTION_BANK_TRANSITIONS[item.status]:
        raise ConflictError(
            f"A {item.status.value.lower()} question can't be moved to {status.value.lower()}."
        )
    previous = item.status
    item.status = status
    item.updated_by = actor.id
    await db.flush()
    await activity_service.record_activity(
        db,
        organization_id=actor.organization_id,
        actor=actor,
        action=f"QUESTION_BANK_ITEM_{status.value}",
        entity_type="question_bank_item",
        entity_id=item.id,
        entity_label=_label(current_version(item).prompt),
        description=(
            f"Question bank question moved from {previous.value.lower()} to {status.value.lower()}."
        ),
    )
    return await require_item(db, actor.organization_id, item.id)


async def change_status_bulk(
    db: AsyncSession,
    *,
    actor: User,
    item_ids: list[uuid.UUID],
    status: QuestionBankStatus,
) -> list[QuestionBankItem]:
    """Moves several questions at once — typically publishing a freshly
    imported batch, which arrives as drafts. All or nothing: every id must
    be this organization's and allowed to make the move, or nothing
    changes. Items already in `status` are left as they are."""
    assert actor.organization_id is not None
    ids = list(dict.fromkeys(item_ids))
    result = await db.execute(
        select(QuestionBankItem).where(
            QuestionBankItem.id.in_(ids), QuestionBankItem.organization_id == actor.organization_id
        )
    )
    found = {item.id: item for item in result.scalars().all()}
    if len(found) != len(ids):
        raise NotFoundError("One or more questions were not found.")
    blocked = [
        item
        for item in found.values()
        if item.status != status and status not in QUESTION_BANK_TRANSITIONS[item.status]
    ]
    if blocked:
        sources = sorted({item.status.value.lower() for item in blocked})
        raise ConflictError(
            f"{len(blocked)} of the selected questions can't be moved to {status.value.lower()} "
            f"(they are {' / '.join(sources)}). Nothing was changed.",
            code="question_status_blocked",
        )
    for item_id in ids:
        await change_status(db, actor=actor, item_id=item_id, status=status)
    return [await require_item(db, actor.organization_id, item_id) for item_id in ids]


@dataclass(frozen=True)
class ItemFilters:
    search: str | None = None
    category_id: uuid.UUID | None = None
    difficulty: QuestionDifficulty | None = None
    question_type: QuestionType | None = None
    status: QuestionBankStatus | None = None
    tag_ids: tuple[uuid.UUID, ...] = ()
    include_archived: bool = False


@dataclass(frozen=True)
class ItemRow:
    item: QuestionBankItem
    version: QuestionBankItemVersion


async def search_items(
    db: AsyncSession,
    organization_id: uuid.UUID,
    filters: ItemFilters,
    *,
    limit: int,
    offset: int,
) -> tuple[list[ItemRow], int]:
    """Filtered, paged list over each item's *current* version. Returns the
    page and the total number of matches."""
    conditions: list[ColumnElement[bool]] = [
        QuestionBankItem.organization_id == organization_id,
        QuestionBankItemVersion.organization_id == organization_id,
    ]
    if filters.status is not None:
        conditions.append(QuestionBankItem.status == filters.status)
    elif not filters.include_archived:
        conditions.append(QuestionBankItem.status != QuestionBankStatus.ARCHIVED)
    if filters.difficulty is not None:
        conditions.append(QuestionBankItem.difficulty == filters.difficulty)
    if filters.question_type is not None:
        conditions.append(QuestionBankItemVersion.type == filters.question_type)
    if filters.category_id is not None:
        subtree = await _category_subtree_ids(db, organization_id, filters.category_id)
        if not subtree:
            return [], 0
        conditions.append(QuestionBankItem.category_id.in_(subtree))
    if filters.tag_ids:
        conditions.append(
            QuestionBankItem.id.in_(
                select(QuestionBankItemTag.item_id).where(
                    QuestionBankItemTag.tag_id.in_(filters.tag_ids)
                )
            )
        )
    if filters.search and filters.search.strip():
        # Every word must appear somewhere the recruiter would look for it:
        # the question text, the subject/topic path it's filed under (so
        # "react" finds "Programming › React" questions whose prompt never
        # says React), or one of its tags.
        paths = category_paths(await list_categories(db, organization_id))
        for word in filters.search.split()[:5]:
            pattern = f"%{_escape_like(word)}%"
            lowered = word.lower()
            category_ids = [cid for cid, path in paths.items() if lowered in path.lower()]
            matches: list[ColumnElement[bool]] = [
                QuestionBankItemVersion.prompt.ilike(pattern, escape="\\"),
                QuestionBankItem.id.in_(
                    select(QuestionBankItemTag.item_id)
                    .join(QuestionTag, QuestionTag.id == QuestionBankItemTag.tag_id)
                    .where(
                        QuestionTag.organization_id == organization_id,
                        QuestionTag.name.ilike(pattern, escape="\\"),
                    )
                ),
            ]
            if category_ids:
                matches.append(QuestionBankItem.category_id.in_(category_ids))
            conditions.append(or_(*matches))

    join = and_(
        QuestionBankItemVersion.item_id == QuestionBankItem.id,
        QuestionBankItemVersion.version_number == QuestionBankItem.current_version_number,
    )
    total = await db.scalar(
        select(func.count(QuestionBankItem.id)).join(QuestionBankItemVersion, join).where(*conditions)
    )
    result = await db.execute(
        select(QuestionBankItem, QuestionBankItemVersion)
        .join(QuestionBankItemVersion, join)
        .where(*conditions)
        .options(selectinload(QuestionBankItem.tags))
        .order_by(QuestionBankItem.updated_at.desc(), QuestionBankItem.id)
        .limit(limit)
        .offset(offset)
    )
    rows = [ItemRow(item=item, version=version) for item, version in result.unique().all()]
    return rows, int(total or 0)


async def usage_count(db: AsyncSession, item_id: uuid.UUID) -> int:
    """Distinct assessments holding a snapshot of any version of this item."""
    count = await db.scalar(
        select(func.count(func.distinct(Question.assessment_id)))
        .join(QuestionBankItemVersion, QuestionBankItemVersion.id == Question.source_bank_version_id)
        .where(QuestionBankItemVersion.item_id == item_id)
    )
    return int(count or 0)


async def require_snapshot_source(
    db: AsyncSession,
    organization_id: uuid.UUID,
    version_id: uuid.UUID,
    *,
    already_in_test: bool = False,
) -> QuestionBankItemVersion:
    """A version a test may copy: this organization's, its item PUBLISHED,
    and the item's *current* version (an outdated version can't be added
    fresh — that's what "latest published" means for the test builder).
    `already_in_test`: the assessment being edited already holds this
    exact version, so re-saving it keeps it even if the bank has moved on."""
    result = await db.execute(
        select(QuestionBankItemVersion, QuestionBankItem)
        .join(QuestionBankItem, QuestionBankItem.id == QuestionBankItemVersion.item_id)
        .where(
            QuestionBankItemVersion.id == version_id,
            QuestionBankItemVersion.organization_id == organization_id,
            QuestionBankItem.organization_id == organization_id,
        )
        .options(selectinload(QuestionBankItemVersion.answer_key))
    )
    row = result.first()
    if row is None:
        raise NotFoundError("Question bank question not found.")
    version, item = row
    if already_in_test:
        return version
    if item.status != QuestionBankStatus.PUBLISHED:
        raise UnprocessableError(
            "Only published question-bank questions can be added to a test.",
            code="bank_question_not_published",
        )
    if version.version_number != item.current_version_number:
        raise UnprocessableError(
            "That question has been edited since it was selected — reload the question bank "
            "and add its latest version.",
            code="bank_question_outdated",
        )
    return version

