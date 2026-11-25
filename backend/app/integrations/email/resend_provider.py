from collections.abc import Sequence

import httpx
from pydantic import SecretStr

from app.core.logging import get_logger
from app.integrations.email.base import EmailError, EmailProvider, as_address_list
from app.integrations.email.branding import from_header_for

logger = get_logger(__name__)

_RESEND_API_URL = "https://api.resend.com/emails"
_RESEND_TIMEOUT_SECONDS = 10.0

# Resend's error `name`s (https://resend.com/docs/api-reference/errors) that
# mean "the API key is not usable", whatever the HTTP status.
_API_KEY_ERROR_NAMES = frozenset(
    {
        "missing_api_key",
        "invalid_api_key",
        "restricted_api_key",
        "suspended_api_key",
        "invalid_permission",
    }
)


class ResendEmailProvider(EmailProvider):
    """Email over HTTPS via Resend's REST API — no SDK dependency, a plain
    authenticated POST. This is the production provider: hosts such as
    Render's free web services block outbound SMTP ports (25/465/587), but
    HTTPS on 443 is always open. Required env vars: `RESEND_API_KEY`,
    `EMAIL_FROM` (see docs/deployment.md).

    Failure messages are deliberately generic per failure class, exactly like
    the SMTP provider's: they are shown to the recruiter and must never carry
    the API key or a raw provider response. The status code and Resend's
    error *name* (never its free text) go to the log for diagnosis.
    """

    def __init__(self, *, api_key: SecretStr, from_email: str) -> None:
        self._api_key = api_key
        self._from_email = from_email

    async def send(
        self,
        *,
        to: str | Sequence[str],
        subject: str,
        html: str,
        text: str | None = None,
        reply_to: str | None = None,
        cc: Sequence[str] = (),
        bcc: Sequence[str] = (),
    ) -> None:
        payload: dict[str, object] = {
            "from": from_header_for(self._from_email),
            "to": as_address_list(to),
            "subject": subject,
            "html": html,
        }
        if text is not None:
            payload["text"] = text
        if reply_to:
            payload["reply_to"] = reply_to
        if cc:
            payload["cc"] = list(cc)
        if bcc:
            payload["bcc"] = list(bcc)
        async with httpx.AsyncClient(timeout=_RESEND_TIMEOUT_SECONDS) as client:
            try:
                response = await client.post(
                    _RESEND_API_URL,
                    headers={"Authorization": f"Bearer {self._api_key.get_secret_value()}"},
                    json=payload,
                )
            except httpx.HTTPError as exc:
                # Type only: an httpx message can include the request URL/host.
                logger.warning(
                    "Resend send failed",
                    extra={
                        "extra_fields": {
                            "provider": "resend",
                            "error_type": type(exc).__name__,
                        }
                    },
                )
                raise EmailError("Could not reach the email service.") from exc

        if not response.is_success:
            error_name = _error_name(response)
            logger.warning(
                "Resend send failed",
                extra={
                    "extra_fields": {
                        "provider": "resend",
                        "status_code": response.status_code,
                        "error_name": error_name,
                    }
                },
            )
            raise _delivery_error(response.status_code, error_name)


def _error_name(response: httpx.Response) -> str | None:
    """Resend's machine-readable error name, or None. Only the short `name`
    is ever read — never `message`, which is free text."""
    try:
        body = response.json()
    except ValueError:
        return None
    name = body.get("name") if isinstance(body, dict) else None
    return name if isinstance(name, str) else None


def _delivery_error(status_code: int, error_name: str | None) -> EmailError:
    if status_code == 401 or error_name in _API_KEY_ERROR_NAMES:
        return EmailError("The email service rejected the configured API key.")
    if status_code == 429:
        return EmailError(
            "The email service is not accepting more messages right now "
            "(rate or sending limit). Try again later."
        )
    if status_code in (400, 403, 422):
        # Includes Resend's 403 for an unverified sender domain and for
        # testing-only accounts sending to a non-owner address.
        return EmailError("The email service refused the sender or recipient address.")
    return EmailError("Could not deliver the email through the email service.")
