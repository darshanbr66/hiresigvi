"""Gemini as a resume-screening `LLMProvider` — the same shared prompt
(prompts.py) every other screening adapter uses, sent through the existing
Gemini structured-output client (`InternalAIReasoningProvider.
generate_structured`, which constrains the reply to a JSON schema natively
instead of hoping prose parses). Same `GEMINI_API_KEY`, no new secret or
SDK. Selected by `get_llm_provider()` when no dedicated screening provider
(Ollama/Anthropic/OpenAI) is configured — by default as the fallback behind
Groq (app/integrations/ai/__init__.py).
"""

from typing import Any

from app.integrations.ai.base import LLMProvider, ScreeningVerdict, parse_structured_verdict
from app.integrations.ai.internal_ai_reasoning_provider import InternalAIReasoningProvider
from app.integrations.ai.prompts import RECOMMENDATIONS, SYSTEM_PROMPT, build_user_prompt

_STRING_LIST: dict[str, Any] = {"type": "ARRAY", "items": {"type": "STRING"}}

#: Gemini's OpenAPI-subset response schema mirroring ScreeningVerdict.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "decision": {"type": "STRING", "enum": ["MATCH", "NOT_MATCH"]},
        "overall_score": {"type": "INTEGER"},
        "recommendation": {"type": "STRING", "enum": list(RECOMMENDATIONS)},
        "summary": {"type": "STRING"},
        "matched_requirements": _STRING_LIST,
        "missing_requirements": _STRING_LIST,
        "matching_skills": _STRING_LIST,
        "missing_skills": _STRING_LIST,
        "strengths": _STRING_LIST,
        "concerns": _STRING_LIST,
        "experience_assessment": {"type": "STRING"},
        "education_assessment": {"type": "STRING"},
    },
    "required": [
        "decision",
        "overall_score",
        "recommendation",
        "summary",
        "matched_requirements",
        "missing_requirements",
        "matching_skills",
        "missing_skills",
        "strengths",
        "concerns",
        "experience_assessment",
        "education_assessment",
    ],
}


class GeminiLLMProvider(LLMProvider):
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 45.0,
        thinking_budget: int | None = None,
    ) -> None:
        self.model = model
        # None sends no `thinkingConfig` at all (models that reject it);
        # 0 disables thinking on models that support it, so the whole
        # output budget goes to the JSON verdict instead of hidden reasoning.
        self._thinking_budget = thinking_budget
        self._client = InternalAIReasoningProvider(
            api_key=api_key, model=model, timeout_seconds=timeout_seconds
        )

    async def screen_candidate(
        self, *, resume_text: str, job_title: str, job_description: str
    ) -> ScreeningVerdict:
        data = await self._client.generate_structured(
            system_prompt=SYSTEM_PROMPT,
            user_content=build_user_prompt(
                resume_text=resume_text, job_title=job_title, job_description=job_description
            ),
            response_schema=RESPONSE_SCHEMA,
            max_output_tokens=2048,
            thinking_budget=self._thinking_budget,
        )
        return parse_structured_verdict(data)
