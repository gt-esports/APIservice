from dataclasses import dataclass, field
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import Depends
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from apps.gateway.utils.response_utils import APIError
from apps.main_site.client import MainSiteClient, get_bearer_token, get_main_site_client, unauthorized


@dataclass(frozen=True)
class Principal:
    id: str
    email: str | None
    role: Literal["member", "admin"]
    access_token: str = field(repr=False)


class AuthUser(BaseModel):
    model_config = ConfigDict(strict=True)

    id: UUID
    email: str | None = None


class RoleRecord(BaseModel):
    model_config = ConfigDict(strict=True)

    user_id: UUID
    role: Literal["member", "admin"]


def check_response(response: httpx.Response) -> None:
    if response.status_code == 401:
        raise unauthorized()
    if response.status_code == 403:
        raise APIError(403, "forbidden", "You do not have access to this resource.")
    if response.status_code == 504:
        raise APIError(504, "upstream_timeout", "Main site access timed out. Please try again.")
    if response.status_code != 200:
        raise APIError(503, "upstream_unavailable", "Main site access is temporarily unavailable.")


def invalid_upstream_response() -> APIError:
    return APIError(503, "upstream_unavailable", "Main site access is temporarily unavailable.")


async def require_user(
    access_token: Annotated[str, Depends(get_bearer_token)],
    client: Annotated[MainSiteClient, Depends(get_main_site_client)],
) -> Principal:
    headers = {"Authorization": f"Bearer {access_token}"}
    response = await client.request("GET", "/auth/v1/user", headers=headers)
    if response.status_code in {401, 403}:
        raise unauthorized()
    check_response(response)
    try:
        user = AuthUser.model_validate_json(response.content)
    except ValidationError:
        raise invalid_upstream_response() from None

    role_response = await client.request(
        "GET",
        "/rest/v1/user_roles",
        params={"select": "user_id,role", "user_id": f"eq.{user.id}", "limit": "2"},
        headers=headers,
    )
    check_response(role_response)
    try:
        records = TypeAdapter(list[RoleRecord]).validate_json(role_response.content)
        if len(records) > 1:
            raise ValueError()
        role: Literal["member", "admin"] = "member"
        if records:
            record = records[0]
            if record.user_id != user.id:
                raise ValueError()
            role = record.role
    except (ValueError, ValidationError):
        raise invalid_upstream_response() from None
    return Principal(id=str(user.id), email=user.email, role=role, access_token=access_token)


async def require_admin(principal: Annotated[Principal, Depends(require_user)]) -> Principal:
    if principal.role != "admin":
        raise APIError(403, "forbidden", "Administrator access is required.")
    return principal
