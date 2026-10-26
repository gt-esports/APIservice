from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from apps.main_site.auth import Principal, require_admin
from apps.main_site.client import MainSiteClient, get_main_site_client
from apps.main_site.contacts import CONTACT_COLUMNS, records, unavailable

router = APIRouter(tags=["Operations"])


class MainSiteReadiness(BaseModel):
    service: Literal["main-site"] = "main-site"
    status: Literal["ready"] = "ready"


@router.get(
    "/v1/main/ready",
    response_model=MainSiteReadiness,
    summary="Admin-only check of live identity, role, and contact schema/read access",
)
async def ready(
    admin: Annotated[Principal, Depends(require_admin)],
    client: Annotated[MainSiteClient, Depends(get_main_site_client)],
) -> MainSiteReadiness:
    response = await client.request(
        "GET",
        "/rest/v1/marketing_contacts",
        params={"select": CONTACT_COLUMNS, "limit": "1"},
        headers={"Authorization": f"Bearer {admin.access_token}"},
    )
    if len(records(response)) > 1:
        raise unavailable()
    return MainSiteReadiness()
