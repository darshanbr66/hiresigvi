"""Groq as a resume-screening `LLMProvider` — a plain authenticated POST to
Groq's OpenAI-compatible Chat Completions endpoint, no SDK dependency
(mirrors openai_provider.py / gemini_provider.py). Same shared prompt
(prompts.py) and the same verdict JSON shape as every other adapter: the
schema is derived from the Gemini adapter's `RESPONSE_SCHEMA`, so there is
one definition of the screening contract, not two.

The reply is constrained with `response_format: json_schema` in strict mode
(constrained decoding — the model cannot produce another shape), and still
validated through `parse_structured_verdict` like Gemini's. Strict mode is
only supported by some Groq models (the GPT-OSS family at the time of
writing — https://console.groq.com/docs/structured-outputs); a model without
it is rejected by Groq with a 4xx, which surfaces as an `AIProviderError`.

The API key travels in the `Authorization` header only — never the URL,
never a log line, never an exception message.
"""

import json
from typing import Any

import httpx

from app.core.logging import get_logger
from app.integrations.ai.base import (
    AIProviderAuthError,
    AIProviderEmptyResponseError,
    AIProviderError,
    AIProviderRateLimitError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
    LLMProvider,
    ScreeningVerdict,
    parse_structured_verdict,
)
from app.integrations.ai.gemini_screening_provider import RESPONSE_SCHEMA
from app.integrations.ai.prompts import SYSTEM_PROMPT, build_user_prompt

logger = get_logger(__name__)

BASE_URL = "https://api.groq.com/openai/v1"

#: Reasoning models count reasoning tokens against this cap; the verdict
#: itself is ~750-1,000 tokens (docs/ai-screening.md).
_MAX_COMPLETION_TOKENS = 4096


def _to_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Gemini's OpenAPI-subset schema (`"type": "OBJECT"`) -> standard JSON
    Schema as strict mode requires it: lowercase types, and
    `additionalProperties: false` on every object (all properties are
    already `required`)."""
    converted: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "type":
            converted[key] = str(value).lower()
        elif key == "properties":
            converted[key] = {name: _to_json_schema(prop) for name, prop in value.items()}
        elif key == "items":
            converted[key] = _to_json_schema(value)
        else:
            converted[key] = value
    if converted.get("type") == "object":
        converted["additionalProperties"] = False
    return converted


VERDICT_JSON_SCHEMA: dict[str, Any] = _to_json_schema(RESPONSE_SCHEMA)


class GroqLLMProvider(LLMProvider):
    name = "groq"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        reasoning_effort: str | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._timeout = timeout_seconds
        # None sends no reasoning parameters (models that reject them);
        # "low" keeps a reasoning model's hidden thinking short so the
        # completion budget goes to the verdict.
        self._reasoning_effort = reasoning_effort

    async def screen_candidate(
        self, *, resume_text: str, job_title: str, job_description: str
    ) -> ScreeningVerdict:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_user_prompt(
                        resume_text=resume_text,
                        job_title=job_title,
                        job_description=job_description,
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "screening_verdict",
                    "strict": True,
                    "schema": VERDICT_JSON_SCHEMA,
                },
            },
            "temperature": 0.2,
            "max_completion_tokens": _MAX_COMPLETION_TOKENS,
        }
        if self._reasoning_effort:
            body["reasoning_effort"] = self._reasoning_effort
            body["include_reasoning"] = False

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{BASE_URL}/chat/completions",
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
            raise _error_for_status(response)

        try:
            payload = response.json()
        except ValueError as exc:
            raise AIProviderUnavailableError("Groq returned a non-JSON response.") from exc

        return parse_structured_verdict(_extract_object(payload))


def _extract_object(payload: Any) -> dict[str, Any]:
    try:
        choice = payload["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIProviderEmptyResponseError("Groq returned an unexpected response shape.") from exc
    if choice.get("finish_reason") == "length":
        raise AIProviderEmptyResponseError("Groq's reply was cut off at the token limit.")
    if not isinstance(content, str) or not content.strip():
        raise AIProviderEmptyResponseError("Groq returned an empty response.")
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AIProviderEmptyResponseError(
            "Groq's structured response was not valid JSON."
        ) from exc
    if not isinstance(data, dict):
        raise AIProviderEmptyResponseError("Groq's structured response was not an object.")
    return data


def _upstream_error(response: httpx.Response) -> tuple[str, str]:
    try:
        error = response.json().get("error", {})
        code = str(error.get("code") or error.get("type") or "")
        return code, str(error.get("message", ""))[:300]
    except (ValueError, AttributeError):
        return "", ""


def _error_for_status(response: httpx.Response) -> AIProviderError:
    status = response.status_code
    upstream_code, upstream_message = _upstream_error(response)
    # Server-side diagnosis only: status + Groq's own error code/message
    # (never the request, which holds the resume, nor the key).
    logger.warning(
        "Groq screening request failed",
        extra={
            "extra_fields": {
                "status_code": status,
                "upstream_code": upstream_code,
                "upstream_message": upstream_message,
            }
        },
    )
    if status in (401, 403):
        return AIProviderAuthError(f"Groq rejected the credentials ({status}).")
    # 413 is Groq's "request too large for your tokens-per-minute limit".
    if status in (413, 429):
        return AIProviderRateLimitError(f"Groq rate limit or quota reached ({status}).")
    return AIProviderUnavailableError(f"Groq returned an error ({status}).")
