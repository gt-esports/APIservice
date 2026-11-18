import re
from collections.abc import AsyncIterator, Mapping
from typing import Annotated, Any

import httpx
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from apps.gateway.utils.response_utils import APIError
from apps.main_site.config import load_settings

bearer_scheme = HTTPBearer(auto_error=False, scheme_name="Supabase access token")


def unauthorized() -> APIError:
    return APIError(
        401,
        "unauthorized",
        "A valid sign-in session is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_bearer_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> str:
    if (
        credentials is None
        or len(request.headers.getlist("authorization")) != 1
        or not re.fullmatch(r"[A-Za-z0-9._~+/-]+={0,2}", credentials.credentials)
        or len(credentials.credentials) > 16384
    ):
        raise unauthorized()
    return credentials.credentials


class MainSiteClient:
    def __init__(self, client: httpx.AsyncClient, publishable_key: str):
        self.client = client
        self._publishable_key = publishable_key

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        if path != "/auth/v1/user" and not re.fullmatch(r"/rest/v1/[a-z_]+", path):
            raise ValueError("Main site requests must use an internal Auth or REST resource path")
        request_headers = httpx.Headers(headers)
        request_headers["apikey"] = self._publishable_key
        request_headers["Accept"] = "application/json"
        try:
            return await self.client.request(
                method, path, params=params, json=json, headers=request_headers, follow_redirects=False
            )
        except httpx.TimeoutException:
            raise APIError(504, "upstream_timeout", "Main site access timed out. Please try again.") from None
        except httpx.HTTPError:
            raise APIError(503, "upstream_unavailable", "Main site access is temporarily unavailable.") from None


async def get_main_site_client(
    access_token: Annotated[str, Depends(get_bearer_token)],
) -> AsyncIterator[MainSiteClient]:
    settings = load_settings()
    async with httpx.AsyncClient(
        base_url=settings.url,
        timeout=httpx.Timeout(5.0, connect=3.0),
        follow_redirects=False,
    ) as client:
        yield MainSiteClient(client, settings.publishable_key)
