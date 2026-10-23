"""The existing main-site contact contract, validated at the API boundary."""

import re
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


Revision = Annotated[int, Field(strict=True, ge=1, le=2147483647)]


class ContactInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    email: Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)]
    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""
    organization: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""
    category: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    contact_group: Literal["newsletter", "alumni", "both"] = "newsletter"
    status: Literal["active", "unsubscribed", "do_not_contact"] = "active"
    last_contacted: str | None = None
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=5000)] = ""

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Invalid email")
        return value

    @field_validator("last_contacted")
    @classmethod
    def valid_date(cls, value: str | None) -> str | None:
        if value is not None:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("Expected YYYY-MM-DD")
            date.fromisoformat(value)
        return value


class ContactUpdate(ContactInput):
    # The editor submits a full editable record. Require every field so an
    # incomplete update cannot silently reset opt-outs or clear existing notes.
    revision: Revision

    @model_validator(mode="before")
    @classmethod
    def complete_record(cls, value):
        if isinstance(value, dict) and not set(ContactInput.model_fields).issubset(value):
            raise ValueError("Provide all editable contact fields")
        return value


class MarketingContact(ContactInput):
    # Only expose the documented columns if Supabase adds more in the future.
    model_config = ConfigDict(extra="ignore", strict=True)

    id: str
    revision: Revision
    created_at: str
    updated_at: str

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        return str(UUID(value))

    @field_validator("created_at", "updated_at")
    @classmethod
    def valid_timestamp(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Expected timezone-aware timestamp")
        return value


class ContactPage(BaseModel):
    contacts: list[MarketingContact]
    next_offset: int | None


class ContactImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contacts: list[ContactInput] = Field(min_length=1, max_length=500)


class ImportResult(BaseModel):
    imported: int
    skipped: int
