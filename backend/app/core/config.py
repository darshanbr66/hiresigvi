from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, sourced from environment variables / .env.

    Never hardcode secrets here — every sensitive value has no default and
    must come from the environment.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"

    database_url: str

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    cors_allow_origins: list[str] = Field(default_factory=list)

    # The candidate-facing frontend's own origin — used to build links a
    # recruiter shares (assessment invitations, campus-drive application
    # links) that resolve on the frontend, not the API. Defaults to the
    # local Vite dev server; production sets this to the deployed frontend
    # URL (e.g. the Vercel domain) via the environment.
    frontend_base_url: str = "http://localhost:5173"

    resume_storage_dir: str = "uploads/resumes"
    max_resume_size_mb: int = 10
    allowed_resume_extensions: tuple[str, ...] = (".pdf", ".doc", ".docx")
    # The optional 10th marks card on the self-service form (PDF or a
    # PNG/JPEG photo/scan), stored through the same storage abstraction as
    # resumes (app/services/candidate_document_service.py).
    max_marks_card_size_mb: int = 5

    # Resume *file bytes* storage backend (CLAUDE.md § 2: "Resume storage !=
    # DB blob") — see app/integrations/storage. "local" keeps the existing
    # disk-based dev behavior; "mongodb_gridfs" is the production backend,
    # selected via RESUME_STORAGE_PROVIDER on Render (docs/deployment.md
    # § 11). This is independent of MongoDB's *database* config below,
    # which is only meaningful when this is "mongodb_gridfs".
    resume_storage_provider: Literal["local", "mongodb_gridfs"] = "local"

    # MongoDB is used ONLY for resume file bytes via GridFS — never for
    # candidate/application/organization data, which stays in Postgres
    # (CLAUDE.md § 1). Unset by default: a bare `Settings()` for local
    # development on the "local" storage provider must not require a
    # MongoDB deployment to exist.
    mongodb_uri: str | None = None
    mongodb_database: str = "ai_recruitment"

    # Outbound email. Resend (HTTPS) is used when RESEND_API_KEY and EMAIL_FROM
    # are set — it is the production provider, since hosts like Render's free
    # web services block outbound SMTP ports. Otherwise SMTP is used when fully
    # configured (local development). See app/integrations/email/__init__.py.
    # The SMTP password and Resend API key are SecretStr so they can never
    # leak through a repr/log of Settings — each is only unwrapped inside its
    # provider at send time.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from_email: str | None = None
    smtp_from_name: str = "AI Recruitment Platform"
    smtp_use_tls: bool = True

    # `email_from` is the Resend sender: a verified-domain address, optionally
    # with a display name, e.g. `SIGVITAS <hr@yourdomain.com>`.
    resend_api_key: SecretStr | None = None
    email_from: str | None = None

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None

    # Free/local AI screening via Ollama (https://ollama.com) — preferred
    # over the paid providers above when explicitly enabled. Unset by
    # default: an unset base URL means "not opted in", not "try
    # localhost and fail" — the base URL is only ever meaningful once
    # someone has actually installed and started Ollama locally.
    ollama_base_url: str | None = None
    ollama_model: str = "llama3.1"

    # Sigvi — the public AI assistant (POST /api/v1/public/ai/chat). Gemini is
    # the chat provider (free tier is enough for development/demo use); the
    # key is a SecretStr so it can never leak through a repr/log of Settings.
    # Unset means "assistant unavailable", never a canned/fake answer.
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    # Empirically measured (2026-09-22, real API): a plain, short prompt to
    # this model took ~26-32s end to end even with no `thinkingConfig` (which
    # this model rejects as an invalid argument, so it cannot be tuned down
    # further) — 20s was cutting a genuinely-succeeding request off before it
    # could finish, producing a spurious "Sigvi is temporarily unavailable"
    # for what would have been a normal reply. This budget reflects the
    # model's real observed latency, not a guess.
    sigvi_request_timeout_seconds: float = 45.0
    sigvi_max_output_tokens: int = 700
    # The careers site Sigvi answers about when the request names no
    # organization (this deployment serves one: docs/architecture.md).
    sigvi_organization_slug: str = "sigvitas"
    # In-process limits (per instance): per client, and across all clients —
    # the global cap protects the provider's free-tier quota even if a caller
    # rotates client identifiers.
    sigvi_rate_limit_per_minute: int = 8
    sigvi_global_rate_limit_per_minute: int = 40

    # Internal AI ("Recruitment Intelligence" — POST /api/v1/recruiter/ai/*).
    # Authenticated-only, never reachable from the public Sigvi surface above
    # (see app/services/internal_ai/, app/integrations/ai/
    # internal_ai_reasoning_provider.py). Shares GEMINI_API_KEY — no new
    # secret — but its own tuning/model settings, so changing Sigvi's never
    # accidentally changes this and vice versa.
    gemini_embedding_model: str = "gemini-embedding-001"
    # Gemini's embedding output is natively 3072-dimensional but supports
    # Matryoshka truncation via `outputDimensionality`; 768 keeps
    # resume_chunks.embedding and its similarity index compact (Google's own
    # guidance: 768/1536/3072 are the recommended sizes). Changing this after
    # any embeddings have been stored requires re-embedding everything — the
    # column width is fixed at migration time.
    gemini_embedding_dimensions: int = 768
    # Same empirical basis as `sigvi_request_timeout_seconds` above.
    internal_ai_request_timeout_seconds: float = 45.0
    internal_ai_max_output_tokens: int = 1200

    # Gemini resume screening (the last-fallback screening provider —
    # app/integrations/ai/__init__.py `get_llm_provider`). Its own model
    # setting, deliberately separate from GEMINI_MODEL above, so moving
    # screening to another model never changes Sigvi or the internal AI.
    # Empirically measured (2026-09-24, real API): gemini-3.5-flash-lite
    # returned sustained 503 "high demand" errors, so screening uses
    # gemini-2.5-flash. That model thinks by default and counts thinking
    # tokens against maxOutputTokens — with thinking on, ~1,965 of the
    # 2,048-token budget went to thinking and the JSON verdict was cut off.
    # Hence a thinking budget of 0 (thinking disabled). Set it empty to send
    # no `thinkingConfig` at all, for a model that rejects one.
    gemini_screening_model: str = "gemini-2.5-flash"
    gemini_screening_thinking_budget: int | None = 0

    # Groq resume screening (OpenAI-compatible API) — the PRIMARY screening
    # provider when its key is set, with Gemini above as the fallback
    # (app/integrations/ai/__init__.py `get_llm_provider`). SecretStr so the
    # key never leaks through a repr/log of Settings; backend-only.
    # Model: openai/gpt-oss-120b — a Groq *production* (not preview) model,
    # 131k context, and one of the few that support strict json_schema
    # structured outputs (constrained decoding of the verdict). Checked
    # against console.groq.com/docs/models + /structured-outputs, 2026-10.
    groq_api_key: SecretStr | None = None
    groq_screening_model: str = "openai/gpt-oss-120b"
    # gpt-oss is a reasoning model: "low" keeps its hidden reasoning short.
    # Set it empty to send no reasoning parameters, for a model that rejects
    # them (e.g. llama-3.3-70b-versatile).
    groq_screening_reasoning_effort: str | None = "low"
    # Groq is fast (a verdict takes a few seconds); a short budget leaves
    # room for the Gemini fallback within the same request.
    groq_request_timeout_seconds: float = 30.0
    # Which of Groq / Gemini is tried first when both keys are set; the
    # other is the single fallback attempt. Ignored when only one is set.
    ai_screening_provider: Literal["groq", "gemini"] = "groq"

    # Candidate application flow (public careers site).
    # Region assumed for a mobile number typed without a "+<country code>"
    # prefix (ISO 3166-1 alpha-2). Numbers with an explicit prefix are always
    # parsed as written.
    default_phone_region: str = "IN"
    # Email one-time-code verification (app/services/email_verification_service.py).
    email_otp_ttl_minutes: int = 10
    email_otp_max_attempts: int = 5
    email_otp_resend_cooldown_seconds: int = 60
    email_otp_max_sends_per_hour: int = 5
    # How long a verified email may be used to submit an application.
    email_verification_token_ttl_minutes: int = 60
    # Per-client (IP) request budgets for the anonymous candidate endpoints,
    # per 10-minute window — in-process, the DB-backed per-email limits above
    # are the authoritative ones.
    public_otp_request_limit_per_window: int = 10
    public_otp_verify_limit_per_window: int = 30
    public_apply_limit_per_window: int = 10
    # A candidate may self-apply again this many calendar months after their
    # previous self-service application (app/services/reapply_service.py).
    # HR can grant an earlier, single-use reapply.
    candidate_reapply_cooldown_months: int = 3
    # Talk to Admin: longest message body accepted, in characters.
    admin_message_max_length: int = 4000

    @field_validator(
        "gemini_screening_thinking_budget", "groq_screening_reasoning_effort", mode="before"
    )
    @classmethod
    def _blank_thinking_budget_means_none(cls, value: object) -> object:
        # An empty env var means "send no thinkingConfig" / "no reasoning
        # parameters", not a parse error.
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _validate_mongodb_configured_when_selected(self) -> "Settings":
        if self.resume_storage_provider == "mongodb_gridfs" and not self.mongodb_uri:
            raise ValueError(
                "MONGODB_URI is required when RESUME_STORAGE_PROVIDER=mongodb_gridfs."
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    # Required fields are sourced from the environment/.env at runtime, which
    # mypy can't see — hence the targeted ignore rather than making these
    # fields Optional (which would be a lie about their actual contract).
    return Settings()  # type: ignore[call-arg]
