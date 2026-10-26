from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from apps.main_site.auth import Principal, check_response, invalid_upstream_response, require_user
from apps.main_site.client import MainSiteClient, get_main_site_client

router = APIRouter(tags=["Main site identity"])


class UserIdentity(BaseModel):
    id: UUID
    email: str | None


class Profile(BaseModel):
    model_config = ConfigDict(strict=True)

    id: UUID
    full_name: str | None
    created_at: datetime | None


class MeResponse(BaseModel):
    user: UserIdentity
    profile: Profile | None
    role: Literal["member", "admin"]


@router.get("/me", response_model=MeResponse, summary="Current Supabase identity and live main-site role")
async def me(
    principal: Annotated[Principal, Depends(require_user)],
    client: Annotated[MainSiteClient, Depends(get_main_site_client)],
) -> MeResponse:
    response = await client.request(
        "GET",
        "/rest/v1/profiles",
        params={"select": "id,full_name,created_at", "id": f"eq.{principal.id}", "limit": "2"},
        headers={"Authorization": f"Bearer {principal.access_token}"},
    )
    check_response(response)
    try:
        profiles = TypeAdapter(list[Profile]).validate_json(response.content)
        if len(profiles) > 1 or (profiles and str(profiles[0].id) != principal.id):
            raise ValueError()
    except (ValidationError, ValueError):
        raise invalid_upstream_response() from None
    return MeResponse(
        user=UserIdentity(id=principal.id, email=principal.email),
        profile=profiles[0] if profiles else None,
        role=principal.role,
    )
