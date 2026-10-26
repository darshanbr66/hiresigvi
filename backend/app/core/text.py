"""Small text-normalization helpers shared by registries that enforce
"one row per name" (institutions, question categories/tags)."""


def collapse_whitespace(value: str) -> str:
    """Trims and collapses internal runs of whitespace to one space."""
    return " ".join(value.split())


def normalize_name(value: str) -> str:
    """The uniqueness key for a human-entered name: whitespace-collapsed and
    case-folded, so "IIT  Madras" and "iit madras" are the same entry. Must
    stay in step with the SQL used by migration 1a2b3c4d5e6f's backfill
    (`lower(regexp_replace(btrim(name), '\\s+', ' ', 'g'))`)."""
    return collapse_whitespace(value).lower()


def normalize_email_domain(value: str) -> str:
    """`@Example.EDU ` -> `example.edu`."""
    return value.strip().lstrip("@").strip().lower()
