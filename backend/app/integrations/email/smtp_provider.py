import asyncio
import smtplib
import ssl
from collections.abc import Sequence
from email.message import EmailMessage

from pydantic import SecretStr

from app.core.logging import get_logger
from app.integrations.email.base import EmailError, EmailProvider, as_address_list
from app.integrations.email.branding import from_header

logger = get_logger(__name__)

_SMTP_TIMEOUT_SECONDS = 20
# Port 465 is implicit-TLS (SMTPS); every other port (587 submission) starts
# in plaintext and upgrades with STARTTLS when `use_tls` is set.
_IMPLICIT_TLS_PORT = 465


class SmtpEmailProvider(EmailProvider):
    """Real SMTP delivery via the standard library — no third-party
    dependency. `smtplib` is blocking, so the actual send runs in a worker
    thread and never stalls the event loop.

    Failure messages are deliberately generic per failure class: they are
    shown to the recruiter, and must never contain the password or a raw
    server response (which can echo the username or account details).
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: SecretStr,
        from_email: str,
        from_name: str,
        use_tls: bool,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from_email = from_email
        self._from_name = from_name
        self._use_tls = use_tls

    def _build_message(
        self,
        *,
        to: list[str],
        cc: list[str],
        bcc: list[str],
        subject: str,
        html: str,
        text: str | None,
        reply_to: str | None,
    ) -> EmailMessage:
        message = EmailMessage()
        message["From"] = from_header(self._from_email, self._from_name)
        message["To"] = ", ".join(to)
        if cc:
            message["Cc"] = ", ".join(cc)
        # `send_message` delivers to Bcc recipients and strips the header from
        # the copy that goes over the wire, so they stay hidden from the rest.
        if bcc:
            message["Bcc"] = ", ".join(bcc)
        message["Subject"] = subject
        if reply_to:
            message["Reply-To"] = reply_to
        message.set_content(
            text if text is not None else "This message requires an HTML-capable email client."
        )
        message.add_alternative(html, subtype="html")
        return message

    def _send_blocking(self, message: EmailMessage) -> None:
        context = ssl.create_default_context()
        if self._port == _IMPLICIT_TLS_PORT:
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                self._host, self._port, timeout=_SMTP_TIMEOUT_SECONDS, context=context
            )
        else:
            server = smtplib.SMTP(self._host, self._port, timeout=_SMTP_TIMEOUT_SECONDS)
        with server:
            if self._port != _IMPLICIT_TLS_PORT and self._use_tls:
                server.starttls(context=context)
            server.login(self._username, self._password.get_secret_value())
            server.send_message(message)

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
        try:
            message = self._build_message(
                to=as_address_list(to),
                cc=list(cc),
                bcc=list(bcc),
                subject=subject,
                html=html,
                text=text,
                reply_to=reply_to,
            )
        except ValueError as exc:
            # EmailMessage rejects header values containing newlines — an
            # invalid subject/recipient, not a server problem.
            raise EmailError("The email subject or recipient address is invalid.") from exc

        try:
            await asyncio.to_thread(self._send_blocking, message)
        except smtplib.SMTPAuthenticationError as exc:
            raise EmailError(
                "The email server rejected the configured SMTP credentials."
            ) from exc
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as exc:
            raise EmailError("The email server refused the sender or recipient address.") from exc
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            # Log the exception *type* only — the message text of an SMTP
            # error can include server-supplied detail we don't forward.
            logger.warning(
                "SMTP send failed", extra={"extra_fields": {"error_type": type(exc).__name__}}
            )
            raise EmailError("Could not deliver the email through the SMTP server.") from exc
