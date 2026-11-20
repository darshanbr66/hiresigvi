"""Orchestration for Zodi — the public AI assistant on the Zodiac Patent
careers site (internally the `sigvi` feature).

    request
      -> intent / context handling   (does this need public jobs?)
      -> knowledge retrieval         (app/knowledge — swappable for RAG)
      -> LLM provider                (app/integrations/ai — swappable vendor)
      -> response validation         (sigvi_prompts.validate_reply)
      -> response (message, sources, job cards)

The model is given no tools and no query ability: everything it can see is
assembled here from an allow-list (curated public knowledge + public fields of
OPEN jobs). It cannot reach the database, other tenants or private data.
The service is stateless — conversation history is supplied, bounded, by the
client and nothing about a conversation is persisted or logged beyond sizes.
"""

import time
import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import (
    AppError,
    BadGatewayError,
    GatewayTimeoutError,
    ServiceUnavailableError,
    TooManyRequestsError,
)
from app.core.logging import get_logger
from app.integrations.ai import (
    AIProviderBlockedError,
    AIProviderEmptyResponseError,
    AIProviderError,
    AIProviderRateLimitError,
    AIProviderTimeoutError,
    ChatMessage,
    ChatProvider,
)
from app.knowledge import KnowledgeEntry, get_knowledge_retriever
from app.schemas.sigvi import ChatHistoryMessage, ChatRequest, ChatResponse, ChatSource
from app.services import organization_service
from app.services import sigvi_job_context as jobs_ctx
from app.services.sigvi_prompts import (
    DECLINED_REPLY,
    build_system_prompt,
    validate_reply,
)

logger = get_logger(__name__)

_HISTORY_MESSAGES_USED = 8
_HISTORY_MESSAGE_CHARS = 1500
_KNOWLEDGE_ENTRIES = 3

UNAVAILABLE_MESSAGE = "Zodi is temporarily unavailable. Please try again in a little while."
BUSY_MESSAGE = "Zodi is getting a lot of questions right now. Please try again in a moment."
TIMEOUT_MESSAGE = "Zodi took too long to respond. Please try again."
EMPTY_MESSAGE = "Zodi couldn't come up with an answer. Please try rephrasing your question."


def _sanitize(text: str) -> str:
    """Drops control characters (keeps newlines/tabs) and trims."""
    return "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32).strip()


def build_conversation(history: Sequence[ChatHistoryMessage], message: str) -> list[ChatMessage]:
    """The bounded turn list sent to the provider: the most recent history,
    each turn capped, starting with a user turn, roles alternating (adjacent
    same-role turns merged), and ending with the visitor's new message."""
    turns: list[ChatMessage] = []
    for item in history[-_HISTORY_MESSAGES_USED:]:
        content = _sanitize(item.content)[:_HISTORY_MESSAGE_CHARS]
        if not content:
            continue
        if not turns and item.role == "assistant":
            continue  # a conversation the model sees must open with the visitor
        if turns and turns[-1].role == item.role:
            turns[-1] = ChatMessage(item.role, f"{turns[-1].content}\n\n{content}")
        else:
            turns.append(ChatMessage(item.role, content))

    if turns and turns[-1].role == "user":
        turns[-1] = ChatMessage("user", f"{turns[-1].content}\n\n{message}")
    else:
        turns.append(ChatMessage("user", message))
    return turns


def _retrieve_knowledge(
    message: str, history: Sequence[ChatHistoryMessage]
) -> list[KnowledgeEntry]:
    retriever = get_knowledge_retriever()
    entries = retriever.retrieve(message, limit=_KNOWLEDGE_ENTRIES)
    if entries:
        return entries
    # A short follow-up ("and what's required?") carries no topic of its own:
    # retry with the previous question, but only when nothing matched.
    previous = next((t.content for t in reversed(history) if t.role == "user"), None)
    if previous:
        return retriever.retrieve(f"{previous} {message}", limit=_KNOWLEDGE_ENTRIES)
    return []


def _map_provider_error(exc: AIProviderError) -> AppError:
    """Internal provider failures -> friendly, non-revealing responses. The
    provider's own message (which may carry upstream detail) is logged by the
    caller and never forwarded."""
    if isinstance(exc, AIProviderRateLimitError):
        return TooManyRequestsError(BUSY_MESSAGE, code="ai_busy")
    if isinstance(exc, AIProviderTimeoutError):
        return GatewayTimeoutError(TIMEOUT_MESSAGE, code="ai_timeout")
    if isinstance(exc, AIProviderEmptyResponseError):
        return BadGatewayError(EMPTY_MESSAGE, code="ai_empty_response")
    # Not configured, bad key, provider down, malformed reply: from the
    # visitor's side they are all "unavailable"; the cause is in the logs.
    return ServiceUnavailableError(UNAVAILABLE_MESSAGE, code="ai_unavailable")


async def answer_chat(
    db: AsyncSession, provider: ChatProvider, request: ChatRequest
) -> ChatResponse:
    settings = get_settings()
    started = time.perf_counter()
    conversation_id = request.conversation_id or uuid.uuid4()
    slug = request.organization_slug or settings.sigvi_organization_slug
    message = _sanitize(request.message)

    knowledge = _retrieve_knowledge(message, request.history)

    # The recruitment team's published careers email, if any — the only
    # contact the assistant may give (never guessed).
    organization = await organization_service.get_organization_by_slug(db, slug)
    contact_email = organization.careers_contact_email if organization else None

    all_jobs: list[jobs_ctx.PublicJob] = []
    shown_jobs: list[jobs_ctx.PublicJob] = []
    jobs_block: str | None = None
    if jobs_ctx.needs_job_context(message, request.history):
        all_jobs = await jobs_ctx.load_public_jobs(db, slug)
        previous = next((t.content for t in reversed(request.history) if t.role == "user"), "")
        shown_jobs = jobs_ctx.select_relevant_jobs(all_jobs, f"{previous} {message}")
        jobs_block = jobs_ctx.format_jobs_block(shown_jobs, len(all_jobs))

    programs_block: str | None = None
    programs_in_context = 0
    if jobs_ctx.needs_program_context(message, request.history):
        programs = await jobs_ctx.load_public_programs(db, slug)
        programs_in_context = min(len(programs), jobs_ctx.MAX_PROGRAMS_IN_PROMPT)
        programs_block = jobs_ctx.format_programs_block(programs)

    # Reads are done: release the pooled DB connection now rather than
    # holding it open across the (slow) model call.
    await db.commit()

    system_prompt = build_system_prompt(
        knowledge=knowledge,
        jobs_block=jobs_block,
        programs_block=programs_block,
        contact_email=contact_email,
    )
    conversation = build_conversation(request.history, message)
    log_fields = {
        "conversation_id": str(conversation_id),
        "provider": provider.name,
        "model": provider.model,
        "message_chars": len(message),
        "history_messages": len(conversation) - 1,
        "knowledge_entries": [entry.id for entry in knowledge],
        "jobs_in_context": len(shown_jobs),
        "programs_in_context": programs_in_context,
    }

    try:
        raw = await provider.generate(
            system_prompt=system_prompt,
            messages=conversation,
            max_output_tokens=settings.sigvi_max_output_tokens,
        )
    except AIProviderBlockedError:
        logger.info("Zodi request declined by provider", extra={"extra_fields": log_fields})
        return ChatResponse(conversation_id=conversation_id, message=DECLINED_REPLY)
    except AIProviderError as exc:
        logger.warning(
            "Zodi provider error",
            extra={
                "extra_fields": {
                    **log_fields,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            },
        )
        raise _map_provider_error(exc) from exc

    secrets = [settings.gemini_api_key.get_secret_value()] if settings.gemini_api_key else []
    reply = validate_reply(raw, secrets=secrets)
    if reply is None:
        logger.warning("Zodi empty reply", extra={"extra_fields": log_fields})
        raise _map_provider_error(AIProviderEmptyResponseError("Empty reply after validation."))

    cards = jobs_ctx.find_mentioned_jobs(reply, shown_jobs, slug)
    sources = [
        ChatSource(id=entry.id, title=entry.title, type="knowledge", url=entry.url)
        for entry in knowledge
    ] + [
        ChatSource(id=str(card.id), title=card.title, type="job", url=card.view_path)
        for card in cards
    ]

    logger.info(
        "Zodi answered",
        extra={
            "extra_fields": {
                **log_fields,
                "reply_chars": len(reply),
                "job_cards": len(cards),
                "latency_ms": round((time.perf_counter() - started) * 1000),
            }
        },
    )
    return ChatResponse(conversation_id=conversation_id, message=reply, sources=sources, jobs=cards)
