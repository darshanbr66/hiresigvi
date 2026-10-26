"""Recruiter-facing question bank (docs/assessment.md § 10). Reading needs
`question_bank.read`; every change needs `question_bank.manage`. These
responses include answer keys and are staff-only — nothing here is reachable
from a candidate-facing route."""

import uuid

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission
from app.core.exceptions import UnprocessableError
from app.db.session import get_db
from app.integrations.documents.question_import import MAX_IMPORT_BYTES
from app.models.assessment import QuestionType
from app.models.question_bank import (
    QuestionBankItem,
    QuestionBankItemVersion,
    QuestionBankStatus,
    QuestionDifficulty,
)
from app.models.user import User
from app.schemas.question_bank import (
    BankBulkStatusRequest,
    BankBulkStatusResult,
    BankImportPreview,
    BankImportRequest,
    BankImportResult,
    BankItemCreateRequest,
    BankItemListEntry,
    BankItemPreview,
    BankItemResponse,
    BankItemStatusRequest,
    BankItemUpdateRequest,
    BankOptionResponse,
    BankPreviewOption,
    BankVersionResponse,
    CategoryCreateRequest,
    CategoryResponse,
    CategoryUpdateRequest,
    TagCreateRequest,
    TagResponse,
)
from app.services import question_bank_import_service, question_bank_service

router = APIRouter(prefix="/question-bank", tags=["recruiter-question-bank"])

MAX_PAGE_SIZE = 100


async def _paths(db: AsyncSession, organization_id: uuid.UUID) -> dict[uuid.UUID, str]:
    return question_bank_service.category_paths(
        await question_bank_service.list_categories(db, organization_id)
    )


def _version_response(version: QuestionBankItemVersion) -> BankVersionResponse:
    key = version.answer_key
    correct = set(key.correct_option_keys)
    return BankVersionResponse(
        id=version.id,
        version_number=version.version_number,
        type=version.type,
        prompt=version.prompt,
        points=version.points,
        negative_points=version.negative_points,
        options=[
            BankOptionResponse(key=o["key"], label=o["label"], is_correct=o["key"] in correct)
            for o in version.options
        ],
        accepted_answers=list(key.accepted_answers),
        case_sensitive=key.case_sensitive,
        grading_notes=key.grading_notes,
        explanation=key.explanation,
        created_at=version.created_at,
    )


async def _item_response(db: AsyncSession, item: QuestionBankItem) -> BankItemResponse:
    paths = await _paths(db, item.organization_id)
    return BankItemResponse(
        id=item.id,
        status=item.status,
        difficulty=item.difficulty,
        category_id=item.category_id,
        category_path=paths.get(item.category_id) if item.category_id else None,
        tags=[tag.name for tag in item.tags],
        current_version=_version_response(question_bank_service.current_version(item)),
        version_count=len(item.versions),
        assessment_usage_count=await question_bank_service.usage_count(db, item.id),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


# --- Categories ---


@router.get("/categories", response_model=list[CategoryResponse])
async def list_categories(
    current_user: User = Depends(require_permission("question_bank.read")),
    db: AsyncSession = Depends(get_db),
) -> list[CategoryResponse]:
    assert current_user.organization_id is not None
    categories = await question_bank_service.list_categories(db, current_user.organization_id)
    paths = question_bank_service.category_paths(categories)
    counts = await question_bank_service.category_question_counts(db, current_user.organization_id)
    responses = [
        CategoryResponse(
            id=c.id,
            name=c.name,
            parent_id=c.parent_id,
            description=c.description,
            is_active=c.is_active,
            path=paths[c.id],
            question_count=counts.get(c.id, 0),
        )
        for c in categories
    ]
    return sorted(responses, key=lambda c: c.path.lower())


@router.post("/categories", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
async def create_category(
    payload: CategoryCreateRequest,
    current_user: User = Depends(require_permission("question_bank.manage")),
    db: AsyncSession = Depends(get_db),
) -> CategoryResponse:
    assert current_user.organization_id is not None
    category = await question_bank_service.create_category(db, actor=current_user, payload=payload)
    paths = await _paths(db, current_user.organization_id)
    return CategoryResponse(
        id=category.id,
        name=category.name,
        parent_id=category.parent_id,
        description=category.description,
        is_active=category.is_active,
        path=paths[category.id],
    )


@router.patch("/categories/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: uuid.UUID,
    payload: CategoryUpdateRequest,
    current_user: User = Depends(require_permission("question_bank.manage")),
    db: AsyncSession = Depends(get_db),
) -> CategoryResponse:
    assert current_user.organization_id is not None
    category = await question_bank_service.update_category(
        db, actor=current_user, category_id=category_id, payload=payload
    )
    paths = await _paths(db, current_user.organization_id)
    return CategoryResponse(
        id=category.id,
        name=category.name,
        parent_id=category.parent_id,
        description=category.description,
        is_active=category.is_active,
        path=paths[category.id],
    )


# --- Tags ---


@router.get("/tags", response_model=list[TagResponse])
async def list_tags(
    current_user: User = Depends(require_permission("question_bank.read")),
    db: AsyncSession = Depends(get_db),
) -> list[TagResponse]:
    assert current_user.organization_id is not None
    rows = await question_bank_service.list_tags(db, current_user.organization_id)
    return [TagResponse(id=tag.id, name=tag.name, question_count=count) for tag, count in rows]


@router.post("/tags", response_model=TagResponse, status_code=status.HTTP_201_CREATED)
async def create_tag(
    payload: TagCreateRequest,
    current_user: User = Depends(require_permission("question_bank.manage")),
    db: AsyncSession = Depends(get_db),
) -> TagResponse:
    assert current_user.organization_id is not None
    tags = await question_bank_service.resolve_tags(db, current_user.organization_id, [payload.name])
    if not tags:
        raise UnprocessableError("Tag name is required.")
    return TagResponse(id=tags[0].id, name=tags[0].name)


# --- File import (docs/assessment.md § 10a) ---


@router.get("/import/template")
async def download_import_template(
    _: User = Depends(require_permission("question_bank.manage")),
) -> Response:
    return Response(
        content=question_bank_import_service.template_csv().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="question-bank-import-template.csv"'},
    )


@router.post("/import/preview", response_model=BankImportPreview)
async def preview_import(
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission("question_bank.manage")),
    db: AsyncSession = Depends(get_db),
) -> BankImportPreview:
    """Parses and checks the file; saves nothing."""
    assert current_user.organization_id is not None
    content = await file.read(MAX_IMPORT_BYTES + 1)
    return await question_bank_import_service.preview(
        db,
        organization_id=current_user.organization_id,
        content=content,
        filename=file.filename or "",
    )


@router.post("/import", response_model=BankImportResult, status_code=status.HTTP_201_CREATED)
async def import_questions(
    payload: BankImportRequest,
    current_user: User = Depends(require_permission("question_bank.manage")),
    db: AsyncSession = Depends(get_db),
) -> BankImportResult:
    """Saves the reviewed questions — all in one transaction, or none."""
    return await question_bank_import_service.import_items(db, actor=current_user, payload=payload)


# --- Questions ---


@router.get("/questions", response_model=list[BankItemListEntry])
async def search_questions(
    response: Response,
    q: str | None = Query(default=None, max_length=200),
    category_id: uuid.UUID | None = Query(default=None),
    difficulty: QuestionDifficulty | None = Query(default=None),
    question_type: QuestionType | None = Query(default=None, alias="type"),
    item_status: QuestionBankStatus | None = Query(default=None, alias="status"),
    tag_id: list[uuid.UUID] = Query(default_factory=list),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=25, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    current_user: User = Depends(require_permission("question_bank.read")),
    db: AsyncSession = Depends(get_db),
) -> list[BankItemListEntry]:
    """Filtered, paged search; the total match count is in `X-Total-Count`
    (same convention as the Applications list). Tag filters match any of
    the given tags; a category filter includes its sub-categories."""
    assert current_user.organization_id is not None
    rows, total = await question_bank_service.search_items(
        db,
        current_user.organization_id,
        question_bank_service.ItemFilters(
            search=q,
            category_id=category_id,
            difficulty=difficulty,
            question_type=question_type,
            status=item_status,
            tag_ids=tuple(tag_id),
            include_archived=include_archived,
        ),
        limit=limit,
        offset=offset,
    )
    response.headers["X-Total-Count"] = str(total)
    paths = await _paths(db, current_user.organization_id)
    return [
        BankItemListEntry(
            id=row.item.id,
            status=row.item.status,
            difficulty=row.item.difficulty,
            type=row.version.type,
            prompt=row.version.prompt,
            points=row.version.points,
            category_id=row.item.category_id,
            category_path=paths.get(row.item.category_id) if row.item.category_id else None,
            tags=[tag.name for tag in row.item.tags],
            version_number=row.version.version_number,
            current_version_id=row.version.id,
            updated_at=row.item.updated_at,
        )
        for row in rows
    ]


@router.post("/questions/bulk-status", response_model=BankBulkStatusResult)
async def change_questions_status(
    payload: BankBulkStatusRequest,
    current_user: User = Depends(require_permission("question_bank.manage")),
    db: AsyncSession = Depends(get_db),
) -> BankBulkStatusResult:
    """Publish (or archive, or return to draft) several questions in one
    all-or-nothing change — how a freshly imported batch of drafts becomes
    selectable in the test builder."""
    items = await question_bank_service.change_status_bulk(
        db, actor=current_user, item_ids=payload.item_ids, status=payload.status
    )
    return BankBulkStatusResult(status=payload.status, item_ids=[item.id for item in items])


@router.post("/questions", response_model=BankItemResponse, status_code=status.HTTP_201_CREATED)
async def create_question(
    payload: BankItemCreateRequest,
    current_user: User = Depends(require_permission("question_bank.manage")),
    db: AsyncSession = Depends(get_db),
) -> BankItemResponse:
    item = await question_bank_service.create_item(db, actor=current_user, payload=payload)
    return await _item_response(db, item)


@router.get("/questions/{item_id}", response_model=BankItemResponse)
async def get_question(
    item_id: uuid.UUID,
    current_user: User = Depends(require_permission("question_bank.read")),
    db: AsyncSession = Depends(get_db),
) -> BankItemResponse:
    assert current_user.organization_id is not None
    item = await question_bank_service.require_item(db, current_user.organization_id, item_id)
    return await _item_response(db, item)


@router.get("/questions/{item_id}/versions", response_model=list[BankVersionResponse])
async def list_versions(
    item_id: uuid.UUID,
    current_user: User = Depends(require_permission("question_bank.read")),
    db: AsyncSession = Depends(get_db),
) -> list[BankVersionResponse]:
    assert current_user.organization_id is not None
    item = await question_bank_service.require_item(db, current_user.organization_id, item_id)
    return [_version_response(v) for v in reversed(item.versions)]


@router.get("/questions/{item_id}/preview", response_model=BankItemPreview)
async def preview_question(
    item_id: uuid.UUID,
    current_user: User = Depends(require_permission("question_bank.read")),
    db: AsyncSession = Depends(get_db),
) -> BankItemPreview:
    """The candidate's view of the current version — no answer key."""
    assert current_user.organization_id is not None
    item = await question_bank_service.require_item(db, current_user.organization_id, item_id)
    version = question_bank_service.current_version(item)
    return BankItemPreview(
        type=version.type,
        prompt=version.prompt,
        points=version.points,
        options=[BankPreviewOption(key=o["key"], label=o["label"]) for o in version.options],
    )


@router.patch("/questions/{item_id}", response_model=BankItemResponse)
async def update_question(
    item_id: uuid.UUID,
    payload: BankItemUpdateRequest,
    current_user: User = Depends(require_permission("question_bank.manage")),
    db: AsyncSession = Depends(get_db),
) -> BankItemResponse:
    item = await question_bank_service.update_item(
        db, actor=current_user, item_id=item_id, payload=payload
    )
    return await _item_response(db, item)


@router.post("/questions/{item_id}/status", response_model=BankItemResponse)
async def change_question_status(
    item_id: uuid.UUID,
    payload: BankItemStatusRequest,
    current_user: User = Depends(require_permission("question_bank.manage")),
    db: AsyncSession = Depends(get_db),
) -> BankItemResponse:
    item = await question_bank_service.change_status(
        db, actor=current_user, item_id=item_id, status=payload.status
    )
    return await _item_response(db, item)
