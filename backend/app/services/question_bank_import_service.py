"""Bulk import into the question bank from a PDF, DOCX, XLSX or CSV file
(docs/assessment.md § 10a).

Two steps, so nothing is saved that HR hasn't reviewed:

1. `preview` parses the file with the shared rule-based parser
   (app/integrations/documents/question_import.py — the same one the
   assessment form's import uses), runs every question through the bank's
   own content rules, resolves subject/topic names to existing categories,
   and flags likely duplicates. It writes nothing.
2. `import_items` saves the reviewed questions — all of them in the
   request's single transaction, or none if any is invalid — creating
   missing subjects/topics as categories.

Nothing is sent to any external service, and parsed answer keys only ever
go back to the authenticated staff user who uploaded the file.
"""

import csv
import io
import uuid
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.core.text import collapse_whitespace, normalize_name
from app.integrations.documents.question_import import ParsedQuestion, parse_questions
from app.models.question_bank import (
    QuestionBankItem,
    QuestionBankItemVersion,
    QuestionCategory,
    QuestionDifficulty,
)
from app.models.user import User
from app.schemas.question_bank import (
    BankImportCandidate,
    BankImportItem,
    BankImportPreview,
    BankImportRequest,
    BankImportResult,
    BankItemCreateRequest,
    BankOptionInput,
    BankQuestionContent,
    CategoryCreateRequest,
)
from app.services import activity_service, question_bank_service

TEMPLATE_HEADER = [
    "question",
    "option_1",
    "option_2",
    "option_3",
    "option_4",
    "correct",
    "type",
    "difficulty",
    "subject",
    "topic",
    "marks",
    "tags",
    "explanation",
]
TEMPLATE_ROWS = [
    [
        "Which keyword defines a function in Python?",
        "func",
        "def",
        "lambda",
        "fn",
        "2",
        "single",
        "easy",
        "Programming",
        "Python",
        "1",
        "basics",
        "def starts a function definition.",
    ],
    [
        "Which of these are SQL aggregate functions?",
        "COUNT",
        "JOIN",
        "SUM",
        "WHERE",
        "A,C",
        "multiple",
        "medium",
        "Databases",
        "SQL",
        "2",
        "aggregates",
        "",
    ],
    [
        "A PID controller has three terms.",
        "True",
        "False",
        "",
        "",
        "A",
        "true_false",
        "easy",
        "Robotics",
        "Control",
        "1",
        "",
        "",
    ],
]


def template_csv() -> str:
    """A small, Excel-friendly template (UTF-8 with BOM, CRLF rows). None of
    the example cells start with =, +, - or @, so nothing is read as a
    formula."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(TEMPLATE_HEADER)
    writer.writerows(TEMPLATE_ROWS)
    return "﻿" + buffer.getvalue()


def _prompt_key(prompt: str) -> str:
    return collapse_whitespace(prompt).casefold()


def _content(question: ParsedQuestion) -> BankQuestionContent:
    # model_validate so the parser's plain-string type is coerced to
    # QuestionType by pydantic and a bad value still raises ValidationError.
    return BankQuestionContent.model_validate(
        {
            "type": question.type,
            "prompt": question.prompt,
            "points": question.points,
            "options": [
                BankOptionInput(label=o.label, is_correct=o.is_correct) for o in question.options
            ],
            "accepted_answers": question.accepted_answers,
            "explanation": question.explanation,
        }
    )


@dataclass
class _Categories:
    """Existing categories, keyed for subject (top-level) / topic (child)
    lookup by normalized name."""

    by_key: dict[tuple[uuid.UUID | None, str], QuestionCategory]

    @classmethod
    async def load(cls, db: AsyncSession, organization_id: uuid.UUID) -> "_Categories":
        categories = await question_bank_service.list_categories(db, organization_id)
        return cls(by_key={(c.parent_id, c.normalized_name): c for c in categories})

    def find(self, parent_id: uuid.UUID | None, name: str) -> QuestionCategory | None:
        return self.by_key.get((parent_id, normalize_name(name)))


def _resolve_preview_category(
    categories: _Categories, subject: str | None, topic: str | None
) -> tuple[str | None, bool, str | None]:
    """(display path, creates_category, issue)."""
    if not subject:
        return None, False, ("A topic needs a subject." if topic else None)
    subject_name = collapse_whitespace(subject)
    parent = categories.find(None, subject_name)
    if parent is not None and not parent.is_active:
        return subject_name, False, f'The subject "{parent.name}" is inactive. Reactivate it first.'
    if not topic:
        return (parent.name if parent else subject_name), parent is None, None
    topic_name = collapse_whitespace(topic)
    child = categories.find(parent.id, topic_name) if parent else None
    if parent is not None and child is not None and not child.is_active:
        return f"{parent.name} › {child.name}", False, f'The topic "{child.name}" is inactive.'
    path = f"{parent.name if parent else subject_name} › {child.name if child else topic_name}"
    return path, child is None, None


async def _existing_prompts(
    db: AsyncSession, organization_id: uuid.UUID, keys: set[str]
) -> dict[str, uuid.UUID]:
    """Bank items (any status) whose current text matches one of `keys`."""
    if not keys:
        return {}
    normalized = func.lower(
        func.regexp_replace(func.btrim(QuestionBankItemVersion.prompt), r"\s+", " ", "g")
    )
    rows = await db.execute(
        select(QuestionBankItem.id, QuestionBankItemVersion.prompt)
        .join(QuestionBankItemVersion, QuestionBankItemVersion.item_id == QuestionBankItem.id)
        .where(
            QuestionBankItem.organization_id == organization_id,
            QuestionBankItemVersion.version_number == QuestionBankItem.current_version_number,
            normalized.in_(keys),
        )
    )
    return {_prompt_key(prompt): item_id for item_id, prompt in rows.all()}


async def preview(
    db: AsyncSession, *, organization_id: uuid.UUID, content: bytes, filename: str
) -> BankImportPreview:
    result = parse_questions(content=content, filename=filename)
    categories = await _Categories.load(db, organization_id)
    existing = await _existing_prompts(
        db, organization_id, {_prompt_key(q.prompt) for q in result.questions}
    )

    seen: set[str] = set()
    candidates: list[BankImportCandidate] = []
    for question in result.questions:
        issue: str | None = None
        try:
            content_model: BankQuestionContent | None = _content(question)
        except ValidationError as exc:
            content_model = None
            issue = exc.errors()[0]["msg"].removeprefix("Value error, ")
        path, creates, category_issue = _resolve_preview_category(
            categories, question.subject, question.topic
        )
        key = _prompt_key(question.prompt)
        candidates.append(
            BankImportCandidate(
                source=question.source,
                content=content_model,
                prompt=question.prompt,
                issue=issue or category_issue,
                difficulty=QuestionDifficulty(question.difficulty or "MEDIUM"),
                subject=question.subject,
                topic=question.topic,
                category_path=path,
                creates_category=creates,
                tags=question.tags,
                duplicate_of_item_id=existing.get(key),
                duplicate_in_file=key in seen,
            )
        )
        seen.add(key)
    return BankImportPreview(filename=filename, questions=candidates, warnings=result.warnings)


async def _category_for(
    db: AsyncSession, actor: User, categories: _Categories, item: BankImportItem, created: list[int]
) -> uuid.UUID | None:
    if not item.subject or not item.subject.strip():
        return None

    async def ensure(parent_id: uuid.UUID | None, name: str) -> QuestionCategory:
        found = categories.find(parent_id, name)
        if found is not None:
            if not found.is_active:
                raise ConflictError(
                    f'"{found.name}" is inactive. Reactivate it or choose another subject/topic.',
                    code="category_inactive",
                )
            return found
        category = await question_bank_service.create_category(
            db, actor=actor, payload=CategoryCreateRequest(name=name, parent_id=parent_id)
        )
        categories.by_key[(parent_id, category.normalized_name)] = category
        created[0] += 1
        return category

    subject = await ensure(None, collapse_whitespace(item.subject))
    if not item.topic or not item.topic.strip():
        return subject.id
    return (await ensure(subject.id, collapse_whitespace(item.topic))).id


async def import_items(
    db: AsyncSession, *, actor: User, payload: BankImportRequest
) -> BankImportResult:
    assert actor.organization_id is not None
    categories = await _Categories.load(db, actor.organization_id)
    created = [0]
    item_ids: list[uuid.UUID] = []
    for item in payload.items:
        category_id = await _category_for(db, actor, categories, item, created)
        saved = await question_bank_service.create_item(
            db,
            actor=actor,
            payload=BankItemCreateRequest(
                **item.content.model_dump(),
                category_id=category_id,
                difficulty=item.difficulty,
                tags=item.tags,
                status=payload.status,
            ),
            record_activity=False,
        )
        item_ids.append(saved.id)

    await activity_service.record_activity(
        db,
        organization_id=actor.organization_id,
        actor=actor,
        action="QUESTION_BANK_IMPORTED",
        entity_type="question_bank_item",
        entity_id=item_ids[0],
        entity_label=f"{len(item_ids)} question(s)",
        description=(
            f"{len(item_ids)} question(s) were imported into the question bank as "
            f"{payload.status.value.lower()}."
        ),
    )
    return BankImportResult(
        imported=len(item_ids),
        item_ids=item_ids,
        categories_created=created[0],
        status=payload.status,
    )
