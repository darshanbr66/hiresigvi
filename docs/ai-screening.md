# AI / RAG Screening

**Implementation status:** A working, non-RAG MVP is implemented — real
resume text extraction (pypdf/python-docx), a real LLM call (Anthropic or
OpenAI, provider chosen via whichever `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`
is set — see `app/integrations/ai/`), and a structured result persisted to
`ScreeningRun` (`app/models/screening.py`), triggered by an explicit
recruiter action from the application detail page. What's *not* built from
the design below: the `JobRequirement`/`ResumeChunk`/pgvector retrieval
pipeline and the `AIRun`/`RequirementEvaluation`/`EvaluationEvidence`
per-requirement normalization — § 5's embedding provider was never chosen
(still true), so the whole RAG layer stayed out of scope rather than being
half-built. The rest of this document describes that original, fuller
design; `ScreeningRun` re-uses its re-run/traceability principles (§ 3-4)
at a coarser grain — one row per run, with the model's full structured
verdict, no per-requirement evidence citations. If a provider isn't
configured, screening fails with a clear error rather than fabricating a
result (`app/integrations/ai/base.py::AIProviderNotConfiguredError`).

**Submission-time screening gate (candidate intake):** in addition to the
recruiter-triggered run, every self-service application (careers site or
campus drive link) is screened automatically once it has been committed. The
flow is in `docs/recruitment-workflow.md` § 6. The verdict now includes a
binary `decision` (`MATCH` / `NOT_MATCH`) and `matched_requirements` /
`missing_requirements`, all persisted on `ScreeningRun`. `NOT_MATCH` moves
the application to the advisory `AI_SCREENED_OUT` status, which HR can
override. An unavailable or failing provider never screens anyone out.

**Provider selection** (`app/integrations/ai/__init__.py::get_llm_provider`):
Ollama if `OLLAMA_BASE_URL` is set, then Anthropic (`ANTHROPIC_API_KEY`), then
OpenAI (`OPENAI_API_KEY`). That way a dedicated screening provider always
takes precedence. Otherwise **Groq** (`GROQ_API_KEY`) is the primary provider
and **Gemini** (`GEMINI_API_KEY`) is the fallback. With only one of the two
keys set, that provider is used on its own. `AI_SCREENING_PROVIDER`
(`groq` by default, or `gemini`) chooses which of the two goes first when
both keys are set.

**Fallback rules** (`fallback_provider.py::FallbackLLMProvider`): each
provider is tried **at most once**, in order. The fallback runs only when the
primary *fails* with an `AIProviderError`: a timeout, a connection failure,
401/403, 429 (or Groq's 413 "request too large for your TPM limit"), 5xx, a
non-JSON or truncated reply, or a reply that doesn't validate as a verdict. A
verdict is a successful result whatever it says. `NOT_MATCH` with score 15 is
final and is never "re-asked" of Gemini. When both providers fail, the run is
`FAILED` as before, and a self-service application is kept for manual review.
There is no retry loop. The `ScreeningRun` records the provider/model that
**actually produced** the result (`provider = "gemini"` after a fallback). On
a `COMPLETED` run that needed the fallback, `error_message` keeps the failed
attempt for audit, e.g. `Fallback used after: groq (openai/gpt-oss-120b): Groq
rate limit or quota reached (429).` That text is always our own exception
message, never the upstream body, the request, or a key. The recruiter UI
shows `error_message` only for `FAILED` runs, and candidates never see any
screening data.

**Groq adapter** (`groq_screening_provider.py::GroqLLMProvider`): a plain
authenticated POST to Groq's OpenAI-compatible
`https://api.groq.com/openai/v1/chat/completions`. It needs no SDK, and the
key travels only in the `Authorization` header. It sends the same shared
prompt (`prompts.py`) as every adapter, with `response_format: json_schema`
in **strict** mode (constrained decoding). The JSON schema is derived from
the Gemini adapter's `RESPONSE_SCHEMA`, so the two share one definition of
the verdict contract. Both adapters validate the reply through
`base.parse_structured_verdict`, which clamps the score to 0–100 and treats
anything invalid as an `AIProviderError`. The default model,
`GROQ_SCREENING_MODEL=openai/gpt-oss-120b`, is a Groq **production** model
(not preview) with a 131k context. It is one of the few Groq models that
supports strict structured outputs (checked against Groq's model and
structured-output docs, 2026-10). It is a reasoning model, so screening sends
`reasoning_effort=low` (`GROQ_SCREENING_REASONING_EFFORT`) and
`include_reasoning=false`. If that setting is empty, no reasoning parameters
are sent, which is needed for non-reasoning models. A model without strict
structured-output support is rejected by Groq and fails over to Gemini, so
pick one that supports it. Groq's free tier for this model is about 8K tokens
per minute. A very long resume plus job description can exceed it (413/429),
and that case falls back to Gemini. `GROQ_REQUEST_TIMEOUT_SECONDS` (default
30) leaves room for the fallback in the same request. Tests fake the HTTP
layer (`tests/test_groq_screening_fallback.py`).

Screening has its own model setting, `GEMINI_SCREENING_MODEL` (default
`gemini-2.5-flash`), separate from `GEMINI_MODEL`. That way, changing the
screening model never changes Sigvi or the internal AI, which stay on
`GEMINI_MODEL`. `gemini-2.5-flash` thinks by default and counts thinking
tokens against `maxOutputTokens`. With thinking on, almost the whole
2,048-token budget went to thinking and the JSON verdict was cut off.
Screening therefore sends `thinkingConfig.thinkingBudget = 0`
(`GEMINI_SCREENING_THINKING_BUDGET`, default `0`). If that setting is empty,
no `thinkingConfig` is sent at all, which is needed for models that reject it
(e.g. `gemini-3.5-flash-lite`). With thinking off, a verdict uses about
750–1,000 output tokens, so the 2,048 cap is unchanged. A reply cut off at the
token limit is invalid JSON and fails as an `AIProviderError`, like any other
malformed reply. Tests fake the HTTP layer
(`tests/test_gemini_screening_provider.py`), and
`backend/scripts/verify_ai_screening.py` runs a manual check against the
real API with a realistic job description and resumes.

## 1. Design goal

AI screening must be traceable, re-runnable without destroying history, and
decoupled from the Candidate model (`CLAUDE.md` § 2: "AI != Candidate").
Concretely: a screening result is always a property of a `ScreeningRun`
against an `Application` — never a field mutated directly on `Candidate` or
`Application` — so re-screening after a resume update or a prompt change
produces a new, independently inspectable run.

## 2. Pipeline

```
Resume (file)
  → DocumentExtractor.extract(file) → plain text                    [Phase 5]
  → ResumeDocument (stores extracted_text, extraction_engine)
  → chunking service → ResumeChunk[] (content, token_count)
  → EmbeddingProvider.embed(chunk.content) → embedding vector(N)
  → stored in resume_chunks.embedding (pgvector column)

Job → JobRequirement[] (label, description, weight, is_mandatory)

ScreeningRun (per Application, triggered manually by a recruiter or          [Phase 6]
              automatically on application submission — configurable)
  → for each JobRequirement:
      AIRun (provider, model, prompt_version, input_hash recorded)
        → retrieval: top-k ResumeChunks by pgvector similarity to the
          requirement (embedding-to-embedding, or embedding-to-query-text)
        → LLMProvider.evaluate(requirement, retrieved_chunks) →
          verdict (MEETS/PARTIAL/DOES_NOT_MEET/INSUFFICIENT_EVIDENCE) + rationale
        → RequirementEvaluation (verdict, score, rationale)
          → EvaluationEvidence[] (resume_chunk_id, similarity_score, excerpt) per supporting chunk
  → ScreeningRun.overall_score = aggregation of RequirementEvaluations
    (weighted by JobRequirement.weight; aggregation function documented in
    code, not just implied by the schema, once implemented)
```

## 3. Traceability requirements (what must be reconstructable later)

For any `RequirementEvaluation`, it must be possible to answer, from stored
data alone:

- What was evaluated → `job_requirement_id` → `JobRequirement`.
- Against which application/job → `AIRun.screening_run_id` →
  `ScreeningRun.application_id`.
- Which provider/model/prompt version produced it → `AIRun.provider`,
  `AIRun.model`, `AIRun.prompt_version`.
- What evidence supported the result → `EvaluationEvidence` rows → the exact
  `ResumeChunk`s and their similarity scores.
- When it happened → `AIRun.started_at`/`completed_at`.

This is why `AIRun` sits between `ScreeningRun` and `RequirementEvaluation`
rather than requirement evaluations hanging directly off the run as bare
rows with provider/model duplicated on each — one `AIRun` row per
requirement (or per run, if a single LLM call evaluates all requirements at
once — an implementation choice made in Phase 6, not fixed here) keeps the
provenance data normalized.

## 4. Re-running screening

A new `ScreeningRun` is a new row; nothing about a prior run is edited or
deleted. "Current score" for display purposes is defined as the most recent
`ScreeningRun` with `status=COMPLETED` for that application — a derived
read, not a stored/mutated pointer. This means:

- Comparing two screening runs (e.g. after a resume re-upload) is just
  reading two rows.
- An AI provider outage or bug that produces a bad run is isolated to that
  run's rows; it doesn't corrupt or require rolling back prior state.

## 5. Provider abstraction

`backend/app/integrations/ai/` defines the seams; concrete adapters live
beside the interfaces and are selected by configuration:

```python
class DocumentExtractor(Protocol):
    def extract(self, file_bytes: bytes, content_type: str) -> str: ...

class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    @property
    def dimension(self) -> int: ...

class LLMProvider(Protocol):
    def evaluate_requirement(
        self, requirement: str, evidence_chunks: list[str]
    ) -> RequirementVerdict: ...
```

Domain services in `services/screening/` depend only on these interfaces
(injected via configuration/DI), never on a specific vendor SDK. No
orchestration framework (LangChain/LangGraph) is assumed to exist; if one is
introduced later, it is confined inside a concrete adapter implementing
these same interfaces — `services/screening/` does not change.

## 6. Open items (not decided in Phase 0)

- Which embedding provider/model, and therefore the `vector(N)` dimension
  for `resume_chunks.embedding` — deferred to Phase 5, when it's chosen
  alongside a cost/latency evaluation.
- ~~Whether `ScreeningRun` triggers automatically on application submission or
  only on explicit recruiter action~~ — decided: self-service applications
  are screened automatically on submission (see the note at the top), and
  recruiters can still re-run screening on demand. Per-organization
  configuration of the automatic gate is not built.
- Chunking strategy (fixed-size vs. semantic/section-aware) — deferred to
  Phase 5, informed by real resume samples.
