"""The sender display name on every outgoing email ("From: Zodiac Patent
<…>"), shared by the SMTP and Resend providers.

ZPHIREX is the platform brand and Zodiac Patent the company brand; the
former company name (SIGVITAS) must never reach a recipient. The display
name comes from configuration (`SMTP_FROM_NAME`, or the display part of
`EMAIL_FROM`), so this is the one place that guarantees it: an empty name,
or one that still carries the former name, is replaced by
`DEFAULT_SENDER_NAME` (and logged, so the configuration gets fixed).

Only the display name is touched — the sending address and the mailbox's
credentials are deployment configuration and stay exactly as configured.
"""

from email.utils import formataddr, parseaddr

from app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_SENDER_NAME = "Zodiac Patent"
_FORBIDDEN_NAMES = ("sigvitas",)


def sender_display_name(configured: str | None) -> str:
    name = " ".join((configured or "").split())
    if not name:
        return DEFAULT_SENDER_NAME
    if any(forbidden in name.casefold() for forbidden in _FORBIDDEN_NAMES):
        logger.warning(
            "The configured email sender name carries a retired brand; using %r instead. "
            "Fix SMTP_FROM_NAME / EMAIL_FROM.",
            DEFAULT_SENDER_NAME,
        )
        return DEFAULT_SENDER_NAME
    return name


def from_header(address: str, configured_name: str | None) -> str:
    """`Display Name <address>` with a brand-safe display name."""
    return formataddr((sender_display_name(configured_name), address))


def from_header_for(email_from: str) -> str:
    """`EMAIL_FROM` (either `Name <address>` or a bare address) with a
    brand-safe display name — a bare address gets the default name, so
    mail clients never fall back to the mailbox's own account name."""
    name, address = parseaddr(email_from)
    return from_header(address or email_from.strip(), name)
