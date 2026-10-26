import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.text import collapse_whitespace, normalize_email_domain
from app.models.institution import InstitutionType

_DOMAIN = re.compile(r"^(?=.{3,255}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _clean_domains(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    cleaned: list[str] = []
    for value in values:
        domain = normalize_email_domain(value)
        if not domain:
            continue
        if not _DOMAIN.match(domain):
            raise ValueError(f'"{value}" is not a valid email domain (e.g. "college.edu").')
        if domain not in cleaned:
            cleaned.append(domain)
    return cleaned


class _InstitutionFields(BaseModel):
    institution_type: InstitutionType | None = None
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    website: str | None = Field(default=None, max_length=500)
    email_domains: list[str] | None = Field(default=None, max_length=20)
    placement_contact_name: str | None = Field(default=None, max_length=255)
    placement_contact_email: EmailStr | None = None
    placement_contact_phone: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=5000)

    @field_validator("email_domains")
    @classmethod
    def _check_domains(cls, value: list[str] | None) -> list[str] | None:
        return _clean_domains(value)

    @field_validator("website")
    @classmethod
    def _check_website(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not re.match(r"^https?://", value, re.IGNORECASE):
            value = f"https://{value}"
        return value


class InstitutionCreateRequest(_InstitutionFields):
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        cleaned = collapse_whitespace(value)
        if not cleaned:
            raise ValueError("Institution name is required.")
        return cleaned


class InstitutionUpdateRequest(_InstitutionFields):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = collapse_whitespace(value)
        if not cleaned:
            raise ValueError("Institution name is required.")
        return cleaned


class InstitutionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    institution_type: InstitutionType | None
    city: str | None
    state: str | None
    country: str | None
    website: str | None
    email_domains: list[str]
    placement_contact_name: str | None
    placement_contact_email: str | None
    placement_contact_phone: str | None
    notes: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    #: Live count of this institution's (non-deleted) campus drives.
    campus_drive_count: int = 0


class InstitutionSummary(BaseModel):
    """The minimal public shape — name and location only, used on the
    candidate-facing program page. Never contacts, domains or notes."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    city: str | None
    state: str | None
