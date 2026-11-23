"""Vendor adapters for `EducationAIProvider` (app/integrations/ai/education.py).

* Gemini — multimodal: reads the uploaded PDF or image itself (inline
  file part), so a scanned marks card works. Same `GEMINI_API_KEY` and the
  same structured-output client as the screening adapter.
* Groq — text only: used as the fallback for the JD analysis, and for a
  document only when the PDF has a text layer (`extracted_text`); a scan or
  an image can't be read by it and fails over cleanly instead of guessing.

`FallbackEducationAIProvider` tries them in order (first success wins, a
result is never "retried" on another model), mirroring
`FallbackLLMProvider` for screening. Every failure is an `AIProviderError`;
nothing here ever fabricates a result.
"""

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx

from app.core.logging import get_logger
from app.integrations.ai.base import (
    AIProviderError,
    AIProviderNotConfiguredError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
    InlineFile,
)
from app.integrations.ai.education import (
    DOCUMENT_SYSTEM_PROMPT,
    JOB_SYSTEM_PROMPT,
    AcademicDocumentAnalysis,
    EducationAIProvider,
    JobQualificationAnalysis,
    VocabularyEntry,
    document_user_prompt,
    gemini_document_schema,
    gemini_job_schema,
    job_user_prompt,
    parse_model,
    to_json_schema,
)
from app.integrations.ai.groq_screening_provider import (
    BASE_URL as GROQ_BASE_URL,
)
from app.integrations.ai.groq_screening_provider import (
    _error_for_status as groq_error_for_status,
)
from app.integrations.ai.groq_screening_provider import (
    _extract_object as groq_extract_object,
)
from app.integrations.ai.internal_ai_reasoning_provider import InternalAIReasoningProvider

logger = get_logger(__name__)


class GeminiEducationProvider(EducationAIProvider):
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        thinking_budget: int | None = None,
    ) -> None:
        self.model = model
        self._thinking_budget = thinking_budget
        self._client = InternalAIReasoningProvider(
            api_key=api_key, model=model, timeout_seconds=timeout_seconds
        )

    async def analyze_job_qualifications(
        self, *, job_title: str, job_text: str, vocabulary: Sequence[VocabularyEntry]
    ) -> JobQualificationAnalysis:
        data = await self._client.generate_structured(
            system_prompt=JOB_SYSTEM_PROMPT,
            user_content=job_user_prompt(
                job_title=job_title, job_text=job_text, vocabulary=vocabulary
            ),
            response_schema=gemini_job_schema([v.key for v in vocabulary]),
            max_output_tokens=1024,
            thinking_budget=self._thinking_budget,
        )
        return parse_model(JobQualificationAnalysis, data, "qualification")

    async def analyze_academic_document(
        self,
        *,
        content: bytes,
        content_type: str,
        extracted_text: str | None,
        selected: VocabularyEntry,
        selected_branch: str | None,
        vocabulary: Sequence[VocabularyEntry],
    ) -> AcademicDocumentAnalysis:
        # Gemini reads the file itself — the text layer (if any) isn't sent
        # as well, so the document is processed once.
        data = await self._client.generate_structured(
            system_prompt=DOCUMENT_SYSTEM_PROMPT,
            user_content=document_user_prompt(
                selected=selected,
                selected_branch=selected_branch,
                vocabulary=vocabulary,
                extracted_text=None,
            ),
            response_schema=gemini_document_schema([v.key for v in vocabulary]),
            max_output_tokens=1024,
            thinking_budget=self._thinking_budget,
            attachments=[InlineFile(mime_type=content_type, data=content)],
        )
        return parse_model(AcademicDocumentAnalysis, data, "document")


class GroqEducationProvider(EducationAIProvider):
    name = "groq"

    def __init__(self, *, api_key: str, model: str, timeout_seconds: float) -> None:
        self.model = model
        self._api_key = api_key
        self._timeout = timeout_seconds

    async def analyze_job_qualifications(
        self, *, job_title: str, job_text: str, vocabulary: Sequence[VocabularyEntry]
    ) -> JobQualificationAnalysis:
        data = await self._complete(
            system_prompt=JOB_SYSTEM_PROMPT,
            user_content=job_user_prompt(
                job_title=job_title, job_text=job_text, vocabulary=vocabulary
            ),
            schema_name="job_qualifications",
            schema=to_json_schema(gemini_job_schema([v.key for v in vocabulary])),
        )
        return parse_model(JobQualificationAnalysis, data, "qualification")

    async def analyze_academic_document(
        self,
        *,
        content: bytes,
        content_type: str,
        extracted_text: str | None,
        selected: VocabularyEntry,
        selected_branch: str | None,
        vocabulary: Sequence[VocabularyEntry],
    ) -> AcademicDocumentAnalysis:
        if not extracted_text:
            raise AIProviderUnavailableError(
                "Groq can only read a document with a text layer; this one has none."
            )
        data = await self._complete(
            system_prompt=DOCUMENT_SYSTEM_PROMPT,
            user_content=document_user_prompt(
                selected=selected,
                selected_branch=selected_branch,
                vocabulary=vocabulary,
                extracted_text=extracted_text,
            ),
            schema_name="academic_document",
            schema=to_json_schema(gemini_document_schema([v.key for v in vocabulary])),
        )
        return parse_model(AcademicDocumentAnalysis, data, "document")

    async def _complete(
        self, *, system_prompt: str, user_content: str, schema_name: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
            "temperature": 0.1,
            "max_completion_tokens": 2048,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{GROQ_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "content-type": "application/json",
                    },
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise AIProviderTimeoutError("Groq request timed out.") from exc
        except httpx.HTTPError as exc:
            raise AIProviderUnavailableError(
                f"Could not reach Groq ({type(exc).__name__})."
            ) from exc
        if response.status_code >= 400:
            raise groq_error_for_status(response)
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise AIProviderUnavailableError("Groq returned a non-JSON response.") from exc
        return groq_extract_object(payload)


class FallbackEducationAIProvider(EducationAIProvider):
    def __init__(self, providers: Sequence[EducationAIProvider]) -> None:
        if not providers:
            raise ValueError("FallbackEducationAIProvider needs at least one provider.")
        self._providers = list(providers)
        self.name = self._providers[0].name
        self.model = self._providers[0].model

    async def _first_success[T](
        self, call: Callable[[EducationAIProvider], Awaitable[T]], what: str
    ) -> T:
        errors: list[str] = []
        for provider in self._providers:
            self.name, self.model = provider.name, provider.model
            try:
                return await call(provider)
            except AIProviderError as exc:
                errors.append(f"{provider.name} ({provider.model}): {exc}")
                logger.warning(
                    "Education AI provider failed",
                    extra={
                        "extra_fields": {
                            "provider": provider.name,
                            "model": provider.model,
                            "task": what,
                            "error_type": type(exc).__name__,
                        }
                    },
                )
        raise AIProviderError(f"All education AI providers failed ({what}): " + "; ".join(errors))

    async def analyze_job_qualifications(
        self, *, job_title: str, job_text: str, vocabulary: Sequence[VocabularyEntry]
    ) -> JobQualificationAnalysis:
        return await self._first_success(
            lambda p: p.analyze_job_qualifications(
                job_title=job_title, job_text=job_text, vocabulary=vocabulary
            ),
            "job qualifications",
        )

    async def analyze_academic_document(
        self,
        *,
        content: bytes,
        content_type: str,
        extracted_text: str | None,
        selected: VocabularyEntry,
        selected_branch: str | None,
        vocabulary: Sequence[VocabularyEntry],
    ) -> AcademicDocumentAnalysis:
        return await self._first_success(
            lambda p: p.analyze_academic_document(
                content=content,
                content_type=content_type,
                extracted_text=extracted_text,
                selected=selected,
                selected_branch=selected_branch,
                vocabulary=vocabulary,
            ),
            "academic document",
        )


class UnconfiguredEducationAIProvider(EducationAIProvider):
    name = "none"
    model = "none"

    async def analyze_job_qualifications(
        self, *, job_title: str, job_text: str, vocabulary: Sequence[VocabularyEntry]
    ) -> JobQualificationAnalysis:
        raise AIProviderNotConfiguredError("No education AI provider is configured.")

    async def analyze_academic_document(
        self,
        *,
        content: bytes,
        content_type: str,
        extracted_text: str | None,
        selected: VocabularyEntry,
        selected_branch: str | None,
        vocabulary: Sequence[VocabularyEntry],
    ) -> AcademicDocumentAnalysis:
        raise AIProviderNotConfiguredError("No education AI provider is configured.")
