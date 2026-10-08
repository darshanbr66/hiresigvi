"""An ordered chain of screening providers behind the single `LLMProvider`
interface — e.g. Groq first, Gemini as the fallback (docs/ai-screening.md).

Each provider is tried at most once, in order. The next one is tried only
when the previous one *failed* (any `AIProviderError`: timeout, connection
failure, 429/5xx, auth, a malformed or truncated reply). A verdict — MATCH
or NOT_MATCH, any score — is a successful result and ends the chain: a
NOT_MATCH is never "retried" on another model. When every provider fails,
the combined failure is raised for the caller to record as a FAILED run.

After `screen_candidate`, `name`/`model` identify the provider that
produced the final result (or the last one that failed), and
`failed_attempts` lists the failures before it.
"""

from collections.abc import Sequence

from app.core.logging import get_logger
from app.integrations.ai.base import (
    AIProviderError,
    LLMProvider,
    ScreeningAttemptFailure,
    ScreeningVerdict,
)

logger = get_logger(__name__)


class FallbackLLMProvider(LLMProvider):
    def __init__(self, providers: Sequence[LLMProvider]) -> None:
        if not providers:
            raise ValueError("FallbackLLMProvider needs at least one provider.")
        self._providers = list(providers)
        self.name = self._providers[0].name
        self.model = self._providers[0].model
        self._failed: list[ScreeningAttemptFailure] = []

    @property
    def failed_attempts(self) -> Sequence[ScreeningAttemptFailure]:
        return tuple(self._failed)

    async def screen_candidate(
        self, *, resume_text: str, job_title: str, job_description: str
    ) -> ScreeningVerdict:
        self._failed = []
        for provider in self._providers:
            self.name, self.model = provider.name, provider.model
            try:
                return await provider.screen_candidate(
                    resume_text=resume_text, job_title=job_title, job_description=job_description
                )
            except AIProviderError as exc:
                self._failed.append(
                    ScreeningAttemptFailure(
                        provider=provider.name, model=provider.model, error=str(exc)
                    )
                )
                logger.warning(
                    "AI screening provider failed",
                    extra={
                        "extra_fields": {
                            "provider": provider.name,
                            "model": provider.model,
                            "error_type": type(exc).__name__,
                            "has_fallback": provider is not self._providers[-1],
                        }
                    },
                )
        raise AIProviderError(
            "All AI screening providers failed: "
            + "; ".join(f"{f.provider} ({f.model}): {f.error}" for f in self._failed)
        )
