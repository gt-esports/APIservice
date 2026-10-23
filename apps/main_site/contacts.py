"""Private contacts use the caller's Supabase identity and existing RLS."""

import re
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Query, Response
from pydantic import TypeAdapter, ValidationError

from apps.gateway.utils.response_utils import APIError
from apps.main_site.auth import Principal, require_admin
from apps.main_site.client import MainSiteClient, get_main_site_client
from apps.main_site.models import (
    ContactImport,
    ContactInput,
    ContactPage,
    ContactUpdate,
    ImportResult,
    MarketingContact,
)


router = APIRouter(prefix="/v1/main/marketing-contacts", tags=["Main-site contacts"])
Admin = Annotated[Principal, Depends(require_admin)]
Client = Annotated[MainSiteClient, Depends(get_main_site_client)]
CONTACT_COLUMNS = (
    "id,email,name,organization,category,contact_group,status,last_contacted,notes,"
    "revision,created_at,updated_at"
)
contact_records = TypeAdapter(list[MarketingContact])


def unavailable() -> APIError:
    return APIError(503, "upstream_unavailable", "Contact storage is temporarily unavailable.")


def revision_conflict() -> APIError:
    return APIError(409, "revision_conflict", "This contact changed or is no longer available. Refresh the list before trying again.")


def checked_response(response: httpx.Response) -> None:
    if response.is_success:
        return
    # Never forward database error messages: uniqueness violations include email.
    try:
        detail = response.json()
        code = detail.get("code") if isinstance(detail, dict) else None
    except ValueError:
        code = None
    if response.status_code == 401:
        raise APIError(401, "unauthorized", "Your session is no longer valid. Sign in again.", headers={"WWW-Authenticate": "Bearer"})
    if response.status_code == 403 or code == "42501":
        raise APIError(403, "forbidden", "Administrator access is required.")
    if code == "23505":
        raise APIError(409, "duplicate_contact", "A contact with this email already exists.")
    if code in {"23514", "23502", "22007", "22008", "22001"}:
        raise APIError(422, "validation_error", "Invalid contact fields.")
    raise unavailable()


def records(response: httpx.Response) -> list[MarketingContact]:
    checked_response(response)
    try:
        rows = contact_records.validate_json(response.content)
        if len({row.id for row in rows}) != len(rows):
            raise ValueError("Duplicate record")
        return rows
    except (ValidationError, ValueError) as exc:
        raise unavailable() from exc


def caller_headers(admin: Principal, prefer: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin.access_token}", "Prefer": prefer}


@router.get("", response_model=ContactPage)
async def list_contacts(
    admin: Admin,
    client: Client,
    offset: Annotated[int, Query(ge=0, le=2147483647)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
) -> ContactPage:
    response = await client.request(
        "GET", "/rest/v1/marketing_contacts",
        params={"select": CONTACT_COLUMNS, "order": "email.asc,id.asc", "offset": str(offset), "limit": str(limit)},
        headers=caller_headers(admin, "count=exact"),
    )
    # PostgREST uses 416 when an offset is past the current end of the list.
    # This can happen when another administrator deletes rows between pages.
    if response.status_code == 416:
        match = re.fullmatch(r"\*/(\d+)", response.headers.get("Content-Range", ""))
        if match and offset >= int(match[1]):
            return ContactPage(contacts=[], next_offset=None)
    rows = records(response)
    try:
        match = re.fullmatch(r"(?:(\d+)-(\d+)|\*)/(\d+)", response.headers.get("Content-Range", ""))
        if match is None or len(rows) > limit:
            raise ValueError("Invalid range")
        total = int(match[3])
        if rows:
            if match[1] is None or int(match[1]) != offset or int(match[2]) != offset + len(rows) - 1 or total < offset + len(rows):
                raise ValueError("Inconsistent range")
        elif total > offset:
            raise ValueError("Truncated page")
        next_offset = offset + len(rows) if offset + len(rows) < total else None
    except ValueError as exc:
        raise unavailable() from exc
    return ContactPage(contacts=rows, next_offset=next_offset)


@router.post("", status_code=201, response_model=MarketingContact)
async def create_contact(values: ContactInput, admin: Admin, client: Client) -> MarketingContact:
    response = await client.request(
        "POST", "/rest/v1/marketing_contacts", params={"select": CONTACT_COLUMNS},
        json=values.model_dump(), headers=caller_headers(admin, "return=representation"),
    )
    rows = records(response)
    if len(rows) != 1:
        raise unavailable()
    return rows[0]


@router.post("/import", response_model=ImportResult)
async def import_contacts(batch: ContactImport, admin: Admin, client: Client) -> ImportResult:
    # Validation of every input row completes before one atomic bulk statement.
    # Keep the first occurrence; never merge into existing opted-out records.
    unique: dict[str, ContactInput] = {}
    for contact in batch.contacts:
        unique.setdefault(contact.email, contact)
    response = await client.request(
        "POST", "/rest/v1/marketing_contacts", params={"on_conflict": "email"},
        json=[contact.model_dump() for contact in unique.values()],
        headers=caller_headers(admin, "resolution=ignore-duplicates,return=minimal,count=exact"),
    )
    checked_response(response)
    # Count the committed rows, without returning private records or depending
    # on PostgREST's maximum representation size.
    match = re.fullmatch(r"\*/(\d+)", response.headers.get("Content-Range", ""))
    if match is None or int(match[1]) > len(unique):
        raise unavailable()
    imported = int(match[1])
    return ImportResult(imported=imported, skipped=len(batch.contacts) - imported)


@router.patch("/{contact_id}", response_model=MarketingContact)
async def update_contact(contact_id: UUID, values: ContactUpdate, admin: Admin, client: Client) -> MarketingContact:
    response = await client.request(
        "PATCH", "/rest/v1/marketing_contacts",
        params={"id": f"eq.{contact_id}", "revision": f"eq.{values.revision}", "select": CONTACT_COLUMNS},
        json=values.model_dump(exclude={"revision"}), headers=caller_headers(admin, "return=representation"),
    )
    rows = records(response)
    if not rows:
        raise revision_conflict()
    if len(rows) != 1 or rows[0].id != str(contact_id) or rows[0].revision != values.revision + 1:
        raise unavailable()
    return rows[0]


@router.delete("/{contact_id}", status_code=204)
async def delete_contact(
    contact_id: UUID, admin: Admin, client: Client,
    revision: Annotated[int, Query(ge=1, le=2147483647)],
) -> Response:
    response = await client.request(
        "DELETE", "/rest/v1/marketing_contacts",
        params={"id": f"eq.{contact_id}", "revision": f"eq.{revision}", "select": "id"},
        headers=caller_headers(admin, "return=representation"),
    )
    checked_response(response)
    try:
        rows = response.json()
        if rows == []:
            raise revision_conflict()
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or rows[0].get("id") != str(contact_id):
            raise ValueError("Invalid deleted record")
    except ValueError as exc:
        raise unavailable() from exc
    return Response(status_code=204)
