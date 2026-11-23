"""Reasoning capability behind the internal recruiter/admin AI ("Recruitment
Intelligence" — app/services/internal_ai/). Deliberately a separate provider
interface from `ChatProvider` (Sigvi, public) and `LLMProvider` (screening
verdicts): all three happen to be Gemini-backed today, but this one is the
only one ever given retrieved candidate/application/job data, so it must
never be reachable from, or accidentally merged into, the public chatbot's
code path (CLAUDE.md § 9: "Internal AI must NEVER be available to public
visitors").

Two capabilities:
- `generate_text`: a free-form natural-language answer (the internal chat
  reply, or a match's `explanation`/`potential_concerns` narrative).
- `generate_structured`: a JSON-schema-constrained call (intent
  classification, entity extraction) — using Gemini's native
  `responseSchema` support rather than asking the model to "output JSON" in
  prose and hoping, so a malformed reply is a provider error, not a parsing
  guess.

Same plain REST-over-httpx style as gemini_provider.py / gemini_embedding_
provider.py, same `GEMINI_API_KEY`, same error hierarchy.
"""

import base64
import json
from collections.abc import Sequence
from typing import Any

import httpx

from app.core.logging import get_logger
from app.integrations.ai.base import (
    AIProviderAuthError,
    AIProviderBlockedError,
    AIProviderEmptyResponseError,
    AIProviderError,
    AIProviderRateLimitError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
    ChatMessage,
    InlineFile,
)

logger = get_logger(__name__)

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_BLOCKED_FINISH_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}


class InternalAIReasoningProvider:
    name = "gemini"

    def __init__(self, *, api_key: str, model: str, timeout_seconds: float = 20.0) -> None:
        self._api_key = api_key
        self.model = model
        self._timeout = timeout_seconds

    async def generate_text(
        self, *, system_prompt: str, messages: list[ChatMessage], max_output_tokens: int
    ) -> str:
        payload = await self._call(
            system_prompt=system_prompt,
            messages=messages,
            max_output_tokens=max_output_tokens,
            response_schema=None,
        )
        return _extract_text(payload)

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_content: str,
        response_schema: dict[str, Any],
        max_output_tokens: int = 512,
        thinking_budget: int | None = None,
        attachments: Sequence[InlineFile] = (),
    ) -> dict[str, Any]:
        """`response_schema` is a Gemini-flavored OpenAPI subset schema
        (`type`/`properties`/`required`/`enum` — no `$ref`). Gemini is
        constrained to emit only JSON matching it; a reply that still fails
        to parse (including one cut off at `maxOutputTokens`) is treated as
        an empty response, never guessed at.

        `thinking_budget` is sent as `thinkingConfig.thinkingBudget` only
        when given: thinking models (e.g. gemini-2.5-flash) count thinking
        tokens against `maxOutputTokens`, while some models reject a
        `thinkingConfig` outright — so callers opt in per model.

        `attachments` are sent as inline file parts of the user turn (a PDF
        or image Gemini reads natively — the academic-document check), after
        the text."""
        payload = await self._call(
            system_prompt=system_prompt,
            messages=[ChatMessage("user", user_content)],
            max_output_tokens=max_output_tokens,
            response_schema=response_schema,
            thinking_budget=thinking_budget,
            attachments=attachments,
        )
        text = _extract_text(payload)
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            raise AIProviderEmptyResponseError(
                "Gemini's structured response was not valid JSON."
            ) from exc
        if not isinstance(parsed, dict):
            raise AIProviderEmptyResponseError("Gemini's structured response was not an object.")
        return parsed

    async def _call(
        self,
        *,
        system_prompt: str,
        messages: list[ChatMessage],
        max_output_tokens: int,
        response_schema: dict[str, Any] | None,
        thinking_budget: int | None = None,
        attachments: Sequence[InlineFile] = (),
    ) -> dict[str, Any]:
        generation_config: dict[str, Any] = {
            "maxOutputTokens": max_output_tokens,
            "temperature": 0.2,
        }
        if response_schema is not None:
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseSchema"] = response_schema
        if thinking_budget is not None:
            generation_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}

        contents: list[dict[str, Any]] = [
            {
                "role": "user" if message.role == "user" else "model",
                "parts": [{"text": message.content}],
            }
            for message in messages
        ]
        if attachments:
            contents[-1]["parts"].extend(
                {
                    "inline_data": {
                        "mime_type": file.mime_type,
                        "data": base64.b64encode(file.data).decode("ascii"),
                    }
                }
                for file in attachments
            )
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": generation_config,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{_BASE_URL}/{self.model}:generateContent",
                    headers={"x-goog-api-key": self._api_key, "content-type": "application/json"},
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise AIProviderTimeoutError("Gemini request timed out.") from exc
        except httpx.HTTPError as exc:
            raise AIProviderUnavailableError(
                f"Could not reach Gemini ({type(exc).__name__})."
            ) from exc

        if response.status_code >= 400:
            raise _error_for_status(response)

        try:
            return dict(response.json())
        except ValueError as exc:
            raise AIProviderUnavailableError("Gemini returned a non-JSON response.") from exc


def _upstream_error(response: httpx.Response) -> tuple[str, str]:
    try:
        error = response.json().get("error", {})
        return str(error.get("status", "")), str(error.get("message", ""))[:300]
    except (ValueError, AttributeError):
        return "", ""


def _error_for_status(response: httpx.Response) -> AIProviderError:
    status = response.status_code
    upstream_status, upstream_message = _upstream_error(response)
    logger.warning(
        "Gemini internal-AI request failed",
        extra={
            "extra_fields": {
                "status_code": status,
                "upstream_status": upstream_status,
                "upstream_message": upstream_message,
            }
        },
    )
    key_rejected = "API key" in upstream_message or upstream_status == "UNAUTHENTICATED"
    if status in (401, 403) or key_rejected:
        return AIProviderAuthError(f"Gemini rejected the credentials ({status}).")
    if status == 429:
        return AIProviderRateLimitError("Gemini rate limit or quota reached.")
    return AIProviderUnavailableError(f"Gemini returned an error ({status}).")


def _extract_text(payload: dict[str, Any]) -> str:
    block_reason = (payload.get("promptFeedback") or {}).get("blockReason")
    if block_reason:
        raise AIProviderBlockedError(f"Gemini blocked the prompt ({block_reason}).")

    candidates = payload.get("candidates") or []
    if not candidates:
        raise AIProviderEmptyResponseError("Gemini returned no candidates.")

    candidate = candidates[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()
    if text:
        return text

    if candidate.get("finishReason") in _BLOCKED_FINISH_REASONS:
        raise AIProviderBlockedError(f"Gemini declined to answer ({candidate['finishReason']}).")
    raise AIProviderEmptyResponseError("Gemini returned an empty response.")
