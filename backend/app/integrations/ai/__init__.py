from app.core.config import get_settings
from app.integrations.ai.anthropic_provider import AnthropicLLMProvider
from app.integrations.ai.base import (
    AIProviderAuthError,
    AIProviderBlockedError,
    AIProviderEmptyResponseError,
    AIProviderError,
    AIProviderNotConfiguredError,
    AIProviderRateLimitError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
    ChatMessage,
    ChatProvider,
    EmbeddingProvider,
    EmbeddingTaskType,
    LLMProvider,
    ScreeningVerdict,
)
from app.integrations.ai.fallback_provider import FallbackLLMProvider
from app.integrations.ai.gemini_embedding_provider import GeminiEmbeddingProvider
from app.integrations.ai.gemini_provider import GeminiChatProvider
from app.integrations.ai.gemini_screening_provider import GeminiLLMProvider
from app.integrations.ai.groq_screening_provider import GroqLLMProvider
from app.integrations.ai.internal_ai_reasoning_provider import InternalAIReasoningProvider
from app.integrations.ai.ollama_provider import OllamaLLMProvider
from app.integrations.ai.openai_provider import OpenAILLMProvider

__all__ = [
    "AIProviderAuthError",
    "AIProviderBlockedError",
    "AIProviderEmptyResponseError",
    "AIProviderError",
    "AIProviderNotConfiguredError",
    "AIProviderRateLimitError",
    "AIProviderTimeoutError",
    "AIProviderUnavailableError",
    "ChatMessage",
    "ChatProvider",
    "EmbeddingProvider",
    "EmbeddingTaskType",
    "FallbackLLMProvider",
    "GeminiChatProvider",
    "GeminiEmbeddingProvider",
    "GeminiLLMProvider",
    "GroqLLMProvider",
    "InternalAIReasoningProvider",
    "LLMProvider",
    "ScreeningVerdict",
    "AnthropicLLMProvider",
    "OpenAILLMProvider",
    "OllamaLLMProvider",
    "get_chat_provider",
    "get_embedding_provider",
    "get_internal_ai_reasoning_provider",
    "get_llm_provider",
]


class _UnconfiguredLLMProvider(LLMProvider):
    name = "none"
    model = "none"

    async def screen_candidate(
        self, *, resume_text: str, job_title: str, job_description: str
    ) -> ScreeningVerdict:
        raise AIProviderNotConfiguredError(
            "AI screening is currently unavailable because no AI provider is configured. "
            "Set OLLAMA_BASE_URL for a free local model, GROQ_API_KEY / GEMINI_API_KEY, or "
            "ANTHROPIC_API_KEY / OPENAI_API_KEY (see README)."
        )


def get_llm_provider() -> LLMProvider:
    """Free/local Ollama is preferred when explicitly enabled
    (`OLLAMA_BASE_URL` set) — no per-request cost, no API key, nothing
    sent to a third party. Falls back to a paid provider only if the
    caller configured one instead; Anthropic wins if both paid keys are
    set (arbitrary but deterministic — an organization only ever
    configures one in practice).

    Otherwise Groq and Gemini form a fallback chain: by default Groq first
    and Gemini as the single fallback attempt when Groq fails
    (`AI_SCREENING_PROVIDER` picks which goes first). With only one of the
    two keys set, that provider is used on its own."""
    settings = get_settings()
    if settings.ollama_base_url:
        return OllamaLLMProvider(base_url=settings.ollama_base_url, model=settings.ollama_model)
    if settings.anthropic_api_key:
        return AnthropicLLMProvider(api_key=settings.anthropic_api_key)
    if settings.openai_api_key:
        return OpenAILLMProvider(api_key=settings.openai_api_key)

    chain: list[LLMProvider] = []
    groq_key = settings.groq_api_key.get_secret_value().strip() if settings.groq_api_key else ""
    if groq_key:
        chain.append(
            GroqLLMProvider(
                api_key=groq_key,
                model=settings.groq_screening_model,
                timeout_seconds=settings.groq_request_timeout_seconds,
                reasoning_effort=settings.groq_screening_reasoning_effort,
            )
        )
    gemini_key = (
        settings.gemini_api_key.get_secret_value().strip() if settings.gemini_api_key else ""
    )
    if gemini_key:
        chain.append(
            GeminiLLMProvider(
                api_key=gemini_key,
                model=settings.gemini_screening_model,
                timeout_seconds=settings.internal_ai_request_timeout_seconds,
                thinking_budget=settings.gemini_screening_thinking_budget,
            )
        )
    chain.sort(key=lambda provider: provider.name != settings.ai_screening_provider)
    if not chain:
        return _UnconfiguredLLMProvider()
    if len(chain) == 1:
        return chain[0]
    return FallbackLLMProvider(chain)


class _UnconfiguredChatProvider(ChatProvider):
    name = "none"
    model = "none"

    async def generate(
        self, *, system_prompt: str, messages: list[ChatMessage], max_output_tokens: int
    ) -> str:
        raise AIProviderNotConfiguredError("No chat provider is configured (GEMINI_API_KEY).")


def get_chat_provider() -> ChatProvider:
    """The provider behind Sigvi, the public assistant. Gemini when
    `GEMINI_API_KEY` is set; otherwise a provider that raises — the endpoint
    then reports "temporarily unavailable" instead of inventing an answer.
    To add another vendor, implement `ChatProvider` and select it here."""
    settings = get_settings()
    api_key = settings.gemini_api_key.get_secret_value().strip() if settings.gemini_api_key else ""
    if api_key:
        return GeminiChatProvider(
            api_key=api_key,
            model=settings.gemini_model,
            timeout_seconds=settings.sigvi_request_timeout_seconds,
        )
    return _UnconfiguredChatProvider()


class _UnconfiguredEmbeddingProvider(EmbeddingProvider):
    name = "none"
    model = "none"
    dimension = 0

    async def embed(
        self, texts: list[str], *, task_type: EmbeddingTaskType = "RETRIEVAL_DOCUMENT"
    ) -> list[list[float]]:
        raise AIProviderNotConfiguredError(
            "Resume/job embedding is currently unavailable because no AI provider is "
            "configured. Set GEMINI_API_KEY (see README)."
        )


def get_embedding_provider() -> EmbeddingProvider:
    """The provider behind resume chunking and the internal AI matching
    engine's semantic retrieval. Gemini when `GEMINI_API_KEY` is set (the
    same key Sigvi uses — this is a separate capability, not a separate
    integration); otherwise a provider that raises, so a resume upload
    still succeeds (chunking is best-effort, see resume_chunking_service.py)
    rather than silently storing a fabricated vector."""
    settings = get_settings()
    api_key = settings.gemini_api_key.get_secret_value().strip() if settings.gemini_api_key else ""
    if api_key:
        return GeminiEmbeddingProvider(
            api_key=api_key,
            model=settings.gemini_embedding_model,
            dimension=settings.gemini_embedding_dimensions,
            timeout_seconds=settings.internal_ai_request_timeout_seconds,
        )
    return _UnconfiguredEmbeddingProvider()


def get_internal_ai_reasoning_provider() -> InternalAIReasoningProvider | None:
    """The provider behind the internal AI's natural-language answers and
    intent classification. `None` when unconfigured — callers fall back to
    the deterministic-only path (structured data, no narrative explanation)
    rather than failing the whole request, since the matching engine's
    scores and evidence are useful on their own (see match_engine.py)."""
    settings = get_settings()
    api_key = settings.gemini_api_key.get_secret_value().strip() if settings.gemini_api_key else ""
    if not api_key:
        return None
    return InternalAIReasoningProvider(
        api_key=api_key,
        model=settings.gemini_model,
        timeout_seconds=settings.internal_ai_request_timeout_seconds,
    )
