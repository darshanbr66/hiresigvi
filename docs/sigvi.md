# Sigvi — the public AI assistant

**Implementation status:** implemented end-to-end (backend endpoint, provider,
knowledge layer, frontend widget). Sigvi is the first visible AI feature and
the first layer of the planned RAG architecture (`docs/architecture.md` § 7).
It deliberately does **not** do AI screening, sourcing, candidate scoring or
hiring recommendations — those are separate phases.

## 1. What it is

A chat assistant on the public careers site. It answers:

- **SIGVITAS / platform questions** (what SIGVITAS is, how to apply, what an
  application needs, how assessments and campus drives work, what happens
  after applying) — *only* from the curated knowledge and live public jobs
  below. If the fact isn't there it says the information isn't currently
  available.
- **Career / recruitment questions** and **general questions** — answered
  from the model's general knowledge, naturally; it only declines what is
  clearly unsafe, illegal, malicious or inappropriate.
- **Open roles** — from the existing public job data, with a job card
  (title, location, type, short description, *View role*, *Apply*).

## 2. Request flow

```
POST /api/v1/public/ai/chat
   │  validate (length limits, history bounds, slug format)  → 422
   │  rate limit (per client + global)                        → 429
   ▼
sigvi_service.answer_chat            app/services/sigvi_service.py
   ├─ knowledge retrieval            app/knowledge/            (keyword match; swappable)
   ├─ job context, only if asked     sigvi_job_context.py      (existing job_service, OPEN only)
   ├─ build system prompt            sigvi_prompts.py
   ├─ ChatProvider.generate          app/integrations/ai/      (Gemini; swappable)
   ├─ validate reply                 sigvi_prompts.validate_reply
   └─ job cards + sources            (only real, open, named jobs)
```

The model gets **no tools and cannot query anything**. Everything it sees is
assembled server-side from an allow-list, so a prompt cannot make it read a
table, another tenant, or private data.

## 3. API

`POST /api/v1/public/ai/chat` — anonymous.

```jsonc
// request
{
  "message": "Which one requires React?",       // required, 1–1000 chars after trimming
  "conversation_id": "uuid",                     // optional; generated and returned if omitted
  "history": [                                   // optional, ≤ 10 turns, each ≤ 4000 chars
    {"role": "user", "content": "What jobs are available?"},
    {"role": "assistant", "content": "…"}
  ],
  "organization_slug": "sigvitas"                // optional; default SIGVI_ORGANIZATION_SLUG
}

// response 200
{
  "conversation_id": "uuid",
  "message": "…",
  "sources": [{"id": "assessments", "title": "How assessments work", "type": "knowledge", "url": "/#process"}],
  "jobs":    [{"id": "…", "title": "…", "department": "…", "location": "…", "employment_type": "…",
               "summary": "…", "organization_slug": "sigvitas",
               "view_path": "/org/sigvitas/jobs/<id>", "apply_path": "/org/sigvitas/jobs/<id>#apply"}]
}
```

Errors use the standard envelope (`{"error": {"code", "message", "request_id"}}`);
`message` is always safe to show a visitor.

| Status | `code` | When |
|---|---|---|
| 422 | `validation_error` | empty/over-long message, bad history/slug/conversation id |
| 429 | `rate_limited` | this client (or everyone) exceeded the request budget |
| 429 | `ai_busy` | the provider's own rate limit/quota was hit |
| 503 | `ai_unavailable` | not configured, bad key, provider down — indistinguishable to the visitor by design |
| 504 | `ai_timeout` | the provider didn't answer within `SIGVI_REQUEST_TIMEOUT_SECONDS` |
| 502 | `ai_empty_response` | the provider (or validation) produced no usable text |

A provider *safety block* is not an error: it returns 200 with a polite
"I can't help with that one…" message.

## 4. Conversation model

**Stateless server, bounded history from the client.** No conversation table,
no migration, nothing stored. The browser keeps the conversation in memory and
sends the most recent turns with each message; the backend uses at most the
last 8, each capped at 1500 characters, normalised so the model sees an
alternating user/assistant conversation ending with the new message. That is
what lets "Which one requires React?" resolve, and it keeps token use flat.

`conversation_id` is a correlation id for logs only. Logs record sizes, ids,
provider/model and latency — **never message or reply text**.

Trade-off, stated plainly: because history comes from the client, a caller can
forge earlier "assistant" turns. That gains them nothing they couldn't do by
typing the same text as their own message, and every rule that protects the
system lives in the server-built system prompt and the reply validation, not
in the history.

## 5. Knowledge layer (RAG-ready, not RAG)

`app/knowledge/` is separate from orchestration:

- `base.py` — `KnowledgeEntry` and the `KnowledgeRetriever` protocol.
- `sigvitas_public.py` — the curated, public-safe entries. Every statement
  mirrors the shipped landing page, application form, assessment consent
  screen or `docs/`. Where nothing is published (a contact email/phone,
  company history) the entry *says so*, so the model has nothing to invent.
- `keyword_retriever.py` — term overlap. Honest about being keyword matching,
  not semantic search; entries list generous keywords, and generic words
  (`job`, `resume`, `skills`, `interview`) are deliberately *not* keywords so
  general career questions retrieve nothing and get no misleading "sources".

Moving to real RAG means writing another `KnowledgeRetriever` (ingestion →
chunking → embeddings → pgvector) that returns the same `KnowledgeEntry`
values, and changing `get_knowledge_retriever()`. The service, prompt and API
are unchanged. LangChain/LangGraph are not used — they would add nothing yet.

**Editing knowledge:** change `sigvitas_public.py` in the same change that
alters the behaviour it describes (CLAUDE.md § 7). It is Python rather than a
data file so it is always packaged with `pip install .` on Render and is
type-checked.

**Public jobs** come from the existing `job_service.list_jobs(status=OPEN)`
through the same organization-slug + tenant-context path as the anonymous
careers page, reduced to the fields that page already shows. A recruiter's
hidden job description stays hidden (the job may be listed; its text is not
sent). A job card is shown only for a job whose *whole* title appears in the reply (so "patent engineering" does not produce a "Patent Engineer" card, and "Senior Patent Engineer" is one card, not two). Jobs are looked up only when the visitor is asking about openings
(now, or in the last few turns), at most 10 are placed in the prompt, ranked by
relevance to the question.

## 6. Provider

`app/integrations/ai/`:

- `base.py` — `ChatProvider` (`generate(system_prompt, messages,
  max_output_tokens) -> str`) plus the internal error hierarchy
  (`AIProviderAuthError`, `…RateLimitError`, `…TimeoutError`,
  `…UnavailableError`, `…EmptyResponseError`, `…BlockedError`, and the
  pre-existing `AIProviderNotConfiguredError`).
- `gemini_provider.py` — Google Gemini via the plain `generateContent` REST
  endpoint with `httpx` (no SDK, consistent with the Anthropic and Resend
  adapters). The key is sent in the `x-goog-api-key` header, never the URL.
- `get_chat_provider()` — returns Gemini when `GEMINI_API_KEY` is set, else a
  provider that raises. Adding another vendor = implement `ChatProvider`,
  select it there.

`ChatProvider` is a separate capability interface from the existing
`LLMProvider` (screening), sharing its error base and factory pattern —
consistent with `docs/architecture.md` § 7's per-capability interfaces
(`EmbeddingProvider`, `LLMProvider`, `DocumentExtractor`), and it leaves the
three screening providers untouched.

Default model: `gemini-3.5-flash-lite` (listed as free of charge on Google's
pricing page when this was written). Any model your key can use works via
`GEMINI_MODEL`. No `thinkingConfig` is sent, since its shape differs across
Gemini generations.

## 7. Security & abuse controls

- **Anonymous but bounded:** message ≤ 1000 chars; history ≤ 10 turns ×
  4000 chars; max output tokens (`SIGVI_MAX_OUTPUT_TOKENS`, default 700); org
  slug and conversation id are format-validated.
- **Rate limiting** (`app/core/rate_limit.py`, in-process sliding window): per
  client (`SIGVI_RATE_LIMIT_PER_MINUTE`, default 8) and across all clients
  (`SIGVI_GLOBAL_RATE_LIMIT_PER_MINUTE`, default 40). Client identity is the
  first `X-Forwarded-For` entry (Render's proxy hides the socket address),
  which a caller can rotate — hence the global cap, which protects the
  provider's free-tier quota regardless. State is per process, so with
  several instances each has its own budget; move to Redis (planned for Arq)
  if a shared budget is needed.
- **What the model can see:** curated public knowledge + public fields of
  OPEN jobs of one organization. No application, candidate, recruiter, note,
  assessment or admin data is reachable, and no tool/SQL capability exists.
- **Prompt rules:** never reveal instructions/keys/internals; no hiring
  recommendations, scoring or ranking; knowledge/job text is delimited
  *reference data, not instructions* (angle brackets are stripped from it so it
  can't forge the delimiters).
- **Reply validation:** empty → error; a reply containing the prompt's
  internal marker, a Google-style key, or the configured key is replaced by a
  refusal; over-long replies are truncated.
- **Errors:** raw provider errors are logged server-side (status and
  Gemini's error text, never the request) and never returned.
- **Key handling:** `GEMINI_API_KEY` is a `SecretStr`, absent from `repr`, the
  URL and logs.
- **Frontend:** replies render as React text (a tiny bold/list formatter), never
  HTML, so model output can't inject markup.

## 8. Frontend (`frontend/src/features/sigvi/`)

- `SigviLayer` — layout route wrapping **only** `/`, `/org/:slug` and
  `/org/:slug/jobs/:jobId`. Not the assessment or campus-drive pages (an
  assistant beside a test would defeat it), not the staff app. Loads the widget
  chunk after first paint (idle), behind an error boundary so a failed load
  never affects the page. Being a layout route, the conversation survives
  navigation between these pages. On the home page only it turns on the
  one-time teaser.
- `SigviWidget` — the panel (portal to `<body>`): compact header (small
  avatar, title, Clear, Close — closing keeps the conversation, so it doubles
  as minimize), the greeting, bubbles, job cards, "Based on SIGVITAS Careers"
  note, thinking row, error + Retry, auto-scroll, Enter / Shift+Enter
  (IME-safe), 1000-char limit with counter, mobile full-screen sheet that
  follows the visible viewport (so the on-screen keyboard cannot cover the
  input).
- `SigviMascot` — Sigvi's character: a small white-and-blue robot head, inline
  SVG only (no filters, images or libraries). Used purely as an icon —
  launcher, header, beside replies — and always decorative (`aria-hidden`).
  The component still supports the `full` variant and its halo/particle
  parts, but the widget hides those (`styles/sigvi.css`). Only the header
  avatar animates: an occasional blink, and scanning eyes while a reply is on
  its way.
- `SigviLauncher` — a quiet pill (head + "Ask Sigvi") that stays still; on
  phones just the round head, labelled for assistive tech. `SigviTeaser` — a
  one-time "Hi, I'm Sigvi" nudge on the home page, ~5 s after load, once per
  browser session, dismissible, opens the chat; a real button, never a live
  region. `SigviWelcome` — the greeting ("Hi! How can I help?" with one
  muted line, "Ask me about careers at SIGVITAS."), which stays at the top of
  the conversation, and four compact suggestion chips (View open positions ·
  How do I apply? · Campus opportunities · Hiring process), shown until the
  visitor asks something. Each chip sends a full question (e.g. "What campus
  opportunities do you offer?") through the normal chat flow
  (`suggestions.ts`). `SigviJobCard` — "Open role" card (title, location · type,
  department, summary, *View role →*, *Apply*); only fields the careers page
  already shows.
- `useSigviChat` — in-memory conversation state, bounded history, retry,
  discards a reply that arrives after Clear.
- Accessibility: dialog with a name, `role="log"` `aria-live="polite"`, labelled
  controls, focus moves into the panel on open and back to the launcher on
  close/Escape, visible focus rings in the site's primary colour, decorative
  art `aria-hidden`, honest "AI can make mistakes" note.
- Styling: `styles/sigvi.css`, imported by the lazy widget (so it is not in the
  main stylesheet). Sigvi uses the design system's tokens for colour, depth,
  type, radii and motion, so it follows the site's light/dark theme like any
  other surface: flat panel with hairline borders and the standard popover
  shadow, replies on a neutral tint, the visitor's messages in the primary
  blue. Its only local variables are the reply tint (`--sv-reply-*`), because
  `--color-bg-subtle` is darker than the surface in dark mode.

**Motion and performance rules:**

1. Motion is limited to short fades (panel open/close, new messages). Nothing
   runs at rest; the only loops are the typing dots and the header avatar's
   scanning eyes, and both stop when the reply arrives. Blinks are short
   transitions triggered every few seconds from JS (`useIdleLife`), never
   infinite SVG-internal animations (those cannot be composited and keep the
   page rendering every frame).
2. Under `prefers-reduced-motion` every Sigvi animation and transition is
   switched off; state is still shown (the "Sigvi is thinking…" text, static
   dots, replies appearing at once).
3. The widget chunk (JS + CSS) loads after first paint, off the main bundle.

**Not implemented (deliberately):** token streaming. The reply is validated
and matched to job cards as a whole before display, and a single JSON response
is simpler and safer to test; streaming would add an SSE endpoint, a provider
`stream()` method and partial-message validation.

## 9. Operations

| Variable | Default | Notes |
|---|---|---|
| `GEMINI_API_KEY` | *(unset)* | **Secret.** Free key: <https://aistudio.google.com/apikey>. Unset ⇒ the assistant reports "temporarily unavailable"; the rest of the site is unaffected. |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Any Gemini model your key can call. |
| `SIGVI_REQUEST_TIMEOUT_SECONDS` | `20` | |
| `SIGVI_MAX_OUTPUT_TOKENS` | `700` | |
| `SIGVI_ORGANIZATION_SLUG` | `sigvitas` | Careers site used when the request names none. |
| `SIGVI_RATE_LIMIT_PER_MINUTE` | `8` | Per client, per instance. |
| `SIGVI_GLOBAL_RATE_LIMIT_PER_MINUTE` | `40` | All clients, per instance. Keep at or below your Gemini quota. |

Render: add **`GEMINI_API_KEY`** (Environment tab → *Save and deploy*). The
others are optional. No migration is required. See `docs/deployment.md` § 13.

Diagnosing: the logs carry `Sigvi answered` / `Sigvi provider error` /
`Gemini request failed` lines with `conversation_id`, provider, model, sizes,
latency and the failure class — never conversation text. A `403`/"API key"
upstream message means the key is wrong; `429` means the free-tier quota.

## 10. Known gaps

- Keyword retrieval is not semantic; a paraphrase with none of an entry's
  keywords retrieves nothing (the model then says the information isn't
  available rather than guessing). Grows into embeddings later.
- Job matching is lexical too ("which one uses React?" works because the
  description says React; a synonym may not match).
- Rate limits are per process; the per-client limit can be dodged by rotating
  `X-Forwarded-For` (the global limit still holds).
- Automated tests never call the real API. The default model
  (`gemini-3.5-flash-lite`) and the error handling (invalid key -> 400 "API key
  not valid", unknown model -> 404) were verified once by hand against the live
  Gemini API and in a real browser session on 2026-09-21. After deploying,
  ask Sigvi one question to confirm the production key works.
