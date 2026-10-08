"""Groq as the primary resume-screening provider, Gemini as the fallback.

The HTTP layer is faked with `httpx.MockTransport` (tests never reach the
real Groq or Gemini APIs; no real key is used) — the one legitimate mocking
boundary for an external paid API (CLAUDE.md § 5). Everything above it is
real: the Groq adapter, the shared verdict parsing, the fallback chain,
provider selection, and the screening service's persistence.

Covers: the Groq request (shared prompt, strict JSON schema, auth header),
verdict parsing, failures mapped to `AIProviderError`s; the fallback rules
(a verdict — MATCH or NOT_MATCH — is never retried; only provider failures
fall back, exactly once); selection/priority; and end to end, that the
persisted run names the provider/model that actually produced it and never
holds an API key.
"""

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from pydantic import SecretStr

import app.integrations.ai as ai_module
from app.core.config import Settings, get_settings
from app.integrations.ai import (
    AIProviderAuthError,
    AIProviderEmptyResponseError,
    AIProviderError,
    AIProviderRateLimitError,
    AIProviderTimeoutError,
    AIProviderUnavailableError,
    FallbackLLMProvider,
    GeminiLLMProvider,
    GroqLLMProvider,
    ScreeningVerdict,
    get_llm_provider,
)
from app.integrations.ai.groq_screening_provider import VERDICT_JSON_SCHEMA
from app.integrations.ai.prompts import SYSTEM_PROMPT
from app.models.user import User
from app.services import screening_service
from tests.test_screening import _bootstrap_org_with_applied_application

GROQ_KEY = "gsk-test-groq-key-not-real"
GEMINI_KEY = "test-gemini-key-not-real"
GROQ_MODEL = "openai/gpt-oss-120b"
GEMINI_MODEL = "gemini-2.5-flash"
_REAL_ASYNC_CLIENT = httpx.AsyncClient

VERDICT: dict[str, Any] = {
    "decision": "MATCH",
    "overall_score": 90,
    "recommendation": "STRONG_MATCH",
    "summary": "Strong backend profile.",
    "matched_requirements": ["Python", "PostgreSQL"],
    "missing_requirements": [],
    "matching_skills": ["Python", "PostgreSQL"],
    "missing_skills": [],
    "strengths": ["5 years of backend work"],
    "concerns": [],
    "experience_assessment": "Meets the bar.",
    "education_assessment": "Relevant degree.",
}
NOT_MATCH = VERDICT | {
    "decision": "NOT_MATCH",
    "overall_score": 15,
    "recommendation": "NOT_A_MATCH",
    "matched_requirements": [],
    "missing_requirements": ["Python", "PostgreSQL"],
}
GEMINI_VERDICT = VERDICT | {"overall_score": 77, "summary": "Gemini's assessment."}

_WITHOUT_SUMMARY = {k: v for k, v in VERDICT.items() if k != "summary"}

Handler = Callable[[httpx.Request], httpx.Response]


def groq_reply(payload: Any, finish_reason: str = "stop") -> httpx.Response:
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": finish_reason,
                }
            ]
        },
    )


def gemini_reply(payload: Any) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [
                {"content": {"parts": [{"text": json.dumps(payload)}]}, "finishReason": "STOP"}
            ]
        },
    )


class FakeUpstream:
    """Routes every outbound request by host to a per-provider handler and
    counts calls, so a test can assert which providers were (not) called."""

    def __init__(self, groq: Handler, gemini: Handler | None = None) -> None:
        self.groq = groq
        self.gemini = gemini or (lambda _request: gemini_reply(GEMINI_VERDICT))
        self.calls: dict[str, int] = {"groq": 0, "gemini": 0}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "api.groq.com":
            self.calls["groq"] += 1
            return self.groq(request)
        if request.url.host == "generativelanguage.googleapis.com":
            self.calls["gemini"] += 1
            return self.gemini(request)
        raise AssertionError(f"unexpected outbound request to {request.url}")


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeUpstream]:
    def install(groq: Handler, gemini: Handler | None = None) -> FakeUpstream:
        fake = FakeUpstream(groq, gemini)

        def factory(**kwargs: Any) -> httpx.AsyncClient:
            return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(fake), **kwargs)

        # Both adapters use the module-level `httpx.AsyncClient`.
        monkeypatch.setattr(httpx, "AsyncClient", factory)
        return fake

    return install


@pytest.fixture
def settings_with(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Settings]:
    def apply(**overrides: object) -> Settings:
        base: dict[str, object] = {
            "ollama_base_url": None,
            "anthropic_api_key": None,
            "openai_api_key": None,
            "groq_api_key": SecretStr(GROQ_KEY),
            "gemini_api_key": SecretStr(GEMINI_KEY),
            "groq_screening_model": GROQ_MODEL,
            "gemini_screening_model": GEMINI_MODEL,
            "ai_screening_provider": "groq",
        }
        patched = get_settings().model_copy(update=base | overrides)
        monkeypatch.setattr(ai_module, "get_settings", lambda: patched)
        return patched

    return apply


def groq_provider(reasoning_effort: str | None = "low") -> GroqLLMProvider:
    return GroqLLMProvider(
        api_key=GROQ_KEY, model=GROQ_MODEL, timeout_seconds=5, reasoning_effort=reasoning_effort
    )


def chain() -> FallbackLLMProvider:
    return FallbackLLMProvider(
        [
            groq_provider(),
            GeminiLLMProvider(
                api_key=GEMINI_KEY, model=GEMINI_MODEL, timeout_seconds=5, thinking_budget=0
            ),
        ]
    )


async def screen(provider: Any) -> ScreeningVerdict:
    verdict: ScreeningVerdict = await provider.screen_candidate(
        resume_text="Jane. Python, PostgreSQL, FastAPI. 5 years.",
        job_title="Backend Engineer",
        job_description="Python, PostgreSQL.",
    )
    return verdict


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("slow", request=request)


def _connect_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


# --- Groq adapter -------------------------------------------------------------------


async def test_groq_sends_the_shared_prompt_with_a_strict_schema(upstream) -> None:
    fake = upstream(lambda _request: groq_reply(VERDICT))

    verdict = await screen(groq_provider())

    request = fake.requests[0]
    assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {GROQ_KEY}"
    assert GROQ_KEY not in str(request.url)
    body = json.loads(request.content)
    assert body["model"] == GROQ_MODEL
    assert body["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    user_text = body["messages"][1]["content"]
    assert "Backend Engineer" in user_text and "Jane" in user_text
    response_format = body["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"] == VERDICT_JSON_SCHEMA
    assert body["reasoning_effort"] == "low"
    assert body["include_reasoning"] is False
    assert verdict.decision == "MATCH" and verdict.overall_score == 90


def test_groq_schema_is_the_shared_verdict_contract_in_strict_form() -> None:
    schema = VERDICT_JSON_SCHEMA
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert set(schema["properties"]) <= set(ScreeningVerdict.model_fields)
    assert schema["properties"]["decision"]["enum"] == ["MATCH", "NOT_MATCH"]
    assert schema["properties"]["overall_score"]["type"] == "integer"
    assert schema["properties"]["strengths"] == {"type": "array", "items": {"type": "string"}}


async def test_no_reasoning_parameters_when_reasoning_effort_is_unset(upstream) -> None:
    fake = upstream(lambda _request: groq_reply(VERDICT))
    await screen(groq_provider(reasoning_effort=None))
    body = json.loads(fake.requests[0].content)
    assert "reasoning_effort" not in body and "include_reasoning" not in body


async def test_groq_not_match_and_clamped_score(upstream) -> None:
    upstream(lambda _request: groq_reply(NOT_MATCH | {"overall_score": -3}))
    verdict = await screen(groq_provider())
    assert verdict.decision == "NOT_MATCH"
    assert verdict.overall_score == 0


@pytest.mark.parametrize(
    ("handler", "error"),
    [
        (_timeout, AIProviderTimeoutError),
        (_connect_error, AIProviderUnavailableError),
        (lambda _r: httpx.Response(401, json={"error": {"message": "x"}}), AIProviderAuthError),
        (lambda _r: httpx.Response(429, json={"error": {}}), AIProviderRateLimitError),
        (lambda _r: httpx.Response(413, json={"error": {}}), AIProviderRateLimitError),
        (lambda _r: httpx.Response(503, text="<html>down</html>"), AIProviderUnavailableError),
        (lambda _r: httpx.Response(200, text="not json"), AIProviderUnavailableError),
        (lambda _r: httpx.Response(200, json={"choices": []}), AIProviderEmptyResponseError),
        (lambda _r: groq_reply("this is not json"), AIProviderEmptyResponseError),
        (lambda _r: groq_reply('["a", "list"]'), AIProviderEmptyResponseError),
        (lambda _r: groq_reply(json.dumps(VERDICT)[:80], "length"), AIProviderEmptyResponseError),
        (lambda _r: groq_reply(VERDICT | {"decision": "MAYBE"}), AIProviderError),
        (lambda _r: groq_reply(_WITHOUT_SUMMARY), AIProviderError),
    ],
    ids=[
        "timeout", "connect", "auth", "429", "413", "5xx", "non-json-body", "no-choices",
        "content-not-json", "content-not-object", "truncated", "invalid-decision", "missing-field",
    ],
)
async def test_groq_failures_surface_as_provider_errors(
    upstream, handler: Handler, error: type[AIProviderError]
) -> None:
    upstream(handler)
    with pytest.raises(error) as exc_info:
        await screen(groq_provider())
    assert GROQ_KEY not in str(exc_info.value)


# --- Fallback chain -----------------------------------------------------------------


@pytest.mark.parametrize("reply", [VERDICT, NOT_MATCH], ids=["MATCH", "NOT_MATCH"])
async def test_a_groq_verdict_is_final_and_gemini_is_never_called(upstream, reply) -> None:
    fake = upstream(lambda _request: groq_reply(reply))
    provider = chain()

    verdict = await screen(provider)

    assert verdict.decision == reply["decision"]
    assert verdict.overall_score == reply["overall_score"]
    assert fake.calls == {"groq": 1, "gemini": 0}
    assert (provider.name, provider.model) == ("groq", GROQ_MODEL)
    assert provider.failed_attempts == ()


@pytest.mark.parametrize(
    "groq_handler",
    [
        _timeout,
        _connect_error,
        lambda _r: httpx.Response(429, json={"error": {"code": "rate_limit_exceeded"}}),
        lambda _r: httpx.Response(500, json={"error": {"message": "internal"}}),
        lambda _r: httpx.Response(503, json={"error": {"message": "over capacity"}}),
        lambda _r: groq_reply("{not json"),
        lambda _r: groq_reply(VERDICT | {"overall_score": "high"}),
    ],
    ids=["timeout", "connect", "429", "500", "503", "malformed-json", "invalid-structure"],
)
async def test_groq_failure_falls_back_to_gemini_once(upstream, groq_handler: Handler) -> None:
    fake = upstream(groq_handler)
    provider = chain()

    verdict = await screen(provider)

    assert verdict.summary == "Gemini's assessment."
    assert fake.calls == {"groq": 1, "gemini": 1}
    assert (provider.name, provider.model) == ("gemini", GEMINI_MODEL)
    [failure] = provider.failed_attempts
    assert (failure.provider, failure.model) == ("groq", GROQ_MODEL)
    assert failure.error


async def test_both_providers_failing_raises_once_each_never_loops(upstream) -> None:
    fake = upstream(
        lambda _r: httpx.Response(503, json={}),
        lambda _r: httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}}),
    )
    provider = chain()

    with pytest.raises(AIProviderError) as exc_info:
        await screen(provider)

    assert fake.calls == {"groq": 1, "gemini": 1}
    message = str(exc_info.value)
    assert "groq" in message and "gemini" in message
    assert GROQ_KEY not in message and GEMINI_KEY not in message
    assert [f.provider for f in provider.failed_attempts] == ["groq", "gemini"]


async def test_failed_attempts_reset_between_calls(upstream) -> None:
    responses = iter([httpx.Response(503, json={}), groq_reply(VERDICT)])
    fake = upstream(lambda _r: next(responses))
    provider = chain()

    await screen(provider)
    assert provider.name == "gemini" and len(provider.failed_attempts) == 1
    await screen(provider)
    assert provider.name == "groq" and provider.failed_attempts == ()
    assert fake.calls == {"groq": 2, "gemini": 1}


async def test_api_keys_never_appear_in_logs(upstream, caplog: pytest.LogCaptureFixture) -> None:
    upstream(
        lambda _r: httpx.Response(429, json={"error": {"message": "slow down"}}),
        lambda _r: httpx.Response(500, json={"error": {"message": "boom"}}),
    )
    with caplog.at_level(logging.DEBUG), pytest.raises(AIProviderError):
        await screen(chain())

    logged = "\n".join(
        f"{record.getMessage()} {getattr(record, 'extra_fields', '')}" for record in caplog.records
    )
    assert "Groq screening request failed" in logged
    assert GROQ_KEY not in logged and GEMINI_KEY not in logged


# --- Selection ----------------------------------------------------------------------


def test_groq_is_primary_and_gemini_the_fallback_by_default(settings_with) -> None:
    settings_with()
    provider = get_llm_provider()
    assert isinstance(provider, FallbackLLMProvider)
    assert [type(p) for p in provider._providers] == [GroqLLMProvider, GeminiLLMProvider]
    assert (provider.name, provider.model) == ("groq", GROQ_MODEL)


def test_ai_screening_provider_setting_reorders_the_chain(settings_with) -> None:
    settings_with(ai_screening_provider="gemini")
    provider = get_llm_provider()
    assert isinstance(provider, FallbackLLMProvider)
    assert [p.name for p in provider._providers] == ["gemini", "groq"]


def test_groq_alone_when_only_its_key_is_set(settings_with) -> None:
    settings_with(gemini_api_key=None, groq_screening_reasoning_effort=None)
    provider = get_llm_provider()
    assert isinstance(provider, GroqLLMProvider)
    assert provider.model == GROQ_MODEL
    assert provider._reasoning_effort is None


def test_gemini_alone_when_groq_key_is_blank(settings_with) -> None:
    settings_with(groq_api_key=SecretStr("  "))
    assert isinstance(get_llm_provider(), GeminiLLMProvider)


def test_a_dedicated_screening_provider_still_takes_precedence(settings_with) -> None:
    settings_with(anthropic_api_key="sk-test")
    assert isinstance(get_llm_provider(), ai_module.AnthropicLLMProvider)


def test_groq_settings_defaults_and_secrecy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", GROQ_KEY)
    monkeypatch.setenv("GROQ_SCREENING_REASONING_EFFORT", "")
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="postgresql+psycopg://u:p@localhost/db",
        jwt_secret_key="x" * 32,
    )
    assert settings.groq_screening_model == "openai/gpt-oss-120b"
    assert settings.ai_screening_provider == "groq"
    assert settings.gemini_screening_model == "gemini-2.5-flash"
    assert settings.groq_screening_reasoning_effort is None
    assert GROQ_KEY not in repr(settings) and GROQ_KEY not in str(settings.model_dump())


# --- End to end through the screening service --------------------------------------


@pytest.fixture
def real_chain(monkeypatch: pytest.MonkeyPatch, settings_with) -> None:
    """The screening service gets the *real* provider selection (Groq ->
    Gemini) — only the HTTP transport underneath is fake."""
    settings_with()
    monkeypatch.setattr(screening_service, "get_llm_provider", ai_module.get_llm_provider)


async def _screen_via_api(client: AsyncClient, ctx: dict) -> dict:
    response = await client.post(
        f"/api/v1/recruiter/applications/{ctx['application_id']}/screening",
        headers=ctx["headers"],
    )
    assert response.status_code == 201, response.text
    body: dict = response.json()
    return body


async def test_groq_success_is_persisted_as_groq(
    client: AsyncClient, super_admin: User, real_chain, upstream
) -> None:
    fake = upstream(lambda _r: groq_reply(NOT_MATCH))
    ctx = await _bootstrap_org_with_applied_application(client, "groq-primary-ok")
    fake.calls = {"groq": 0, "gemini": 0}

    body = await _screen_via_api(client, ctx)

    assert fake.calls == {"groq": 1, "gemini": 0}  # one screening, no duplicate
    assert body["status"] == "COMPLETED"
    assert (body["provider"], body["model"]) == ("groq", GROQ_MODEL)
    assert body["decision"] == "NOT_MATCH" and body["overall_score"] == 15
    assert body["missing_requirements"] == ["Python", "PostgreSQL"]
    assert body["error_message"] is None


async def test_groq_failure_is_persisted_as_gemini_with_audit_note(
    client: AsyncClient, super_admin: User, real_chain, upstream
) -> None:
    fake = upstream(lambda _r: httpx.Response(429, json={"error": {"code": "rate_limit"}}))
    ctx = await _bootstrap_org_with_applied_application(client, "groq-fallback-ok")
    fake.calls = {"groq": 0, "gemini": 0}

    body = await _screen_via_api(client, ctx)

    assert fake.calls == {"groq": 1, "gemini": 1}
    assert body["status"] == "COMPLETED"
    assert (body["provider"], body["model"]) == ("gemini", GEMINI_MODEL)
    assert body["overall_score"] == 77 and body["decision"] == "MATCH"
    assert "groq" in body["error_message"] and "rate limit" in body["error_message"]
    assert GROQ_KEY not in json.dumps(body) and GEMINI_KEY not in json.dumps(body)


async def test_both_failing_marks_the_run_failed(
    client: AsyncClient, super_admin: User, real_chain, upstream
) -> None:
    fake = upstream(_timeout, lambda _r: httpx.Response(503, json={}))
    ctx = await _bootstrap_org_with_applied_application(client, "groq-both-fail")
    fake.calls = {"groq": 0, "gemini": 0}

    body = await _screen_via_api(client, ctx)

    assert fake.calls == {"groq": 1, "gemini": 1}
    assert body["status"] == "FAILED"
    assert body["overall_score"] is None and body["decision"] is None
    assert (body["provider"], body["model"]) == ("gemini", GEMINI_MODEL)
    assert "groq" in body["error_message"] and "gemini" in body["error_message"]
    assert GROQ_KEY not in json.dumps(body) and GEMINI_KEY not in json.dumps(body)

    history = await client.get(
        f"/api/v1/recruiter/applications/{ctx['application_id']}/screening",
        headers=ctx["headers"],
    )
    assert history.status_code == 200, history.text
    runs = history.json()
    # (Both FAILED runs share the test transaction's created_at, so their
    # relative order is not asserted — only that this run is in the history.)
    assert body["id"] in {run["id"] for run in runs}
    for run in runs:
        assert GROQ_KEY not in json.dumps(run) and GEMINI_KEY not in json.dumps(run)
