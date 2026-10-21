import base64
import json
import logging
import os
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError

from apps.gamefest.models.schemas import SeasonRecord


logger = logging.getLogger(__name__)
SEASON_LIMIT = 20
SEASON_QUERY = {
    "select": "id,name,slug,players(id,points,users(username,fname,lname))",
    "is_active": "eq.false",
    "order": "created_at.desc,id.asc",
    "limit": str(SEASON_LIMIT),
    "players.order": "points.desc.nullslast,id.asc",
    "players.limit": "3",
}
season_records = TypeAdapter(list[SeasonRecord])


def unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "gamefest_unavailable",
            "message": "GameFest results are temporarily unavailable.",
        },
        headers={"Retry-After": "30"},
    )


def public_key(key: str) -> bool:
    if key.startswith("sb_publishable_"):
        return len(key) > len("sb_publishable_")
    try:
        _, payload, _ = key.split(".")
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return isinstance(claims, dict) and claims.get("role") == "anon"
    except (ValueError, UnicodeError):
        return False


def connection_settings() -> tuple[str, str]:
    url = os.getenv("GAMEFEST_SUPABASE_URL", "").strip().rstrip("/")
    key = os.getenv("GAMEFEST_SUPABASE_ANON_KEY", "").strip()
    try:
        parsed = urlsplit(url)
        valid_url = (
            bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
            and (
                parsed.scheme == "https"
                or (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
            )
        )
        parsed.port
    except ValueError:
        valid_url = False
    if not valid_url or not public_key(key):
        logger.warning("GameFest public data configuration is missing or invalid")
        raise unavailable()
    return url, key


class GameFestRepository:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def list_past_winners(self) -> list[SeasonRecord]:
        try:
            response = await self.client.get("seasons", params=SEASON_QUERY)
            response.raise_for_status()
            records = season_records.validate_json(response.content)
            if len(records) > SEASON_LIMIT or len({row.id for row in records}) != len(records):
                raise ValueError("Invalid season collection")
            if any(len({player.id for player in row.players}) != len(row.players) for row in records):
                raise ValueError("Duplicate player record")
            return records
        except (httpx.HTTPError, ValidationError, ValueError) as exc:
            logger.warning("GameFest public data read failed (%s)", type(exc).__name__)
            raise unavailable() from exc

    async def check_ready(self) -> None:
        await self.list_past_winners()


async def get_gamefest_repository() -> AsyncIterator[GameFestRepository]:
    url, key = connection_settings()
    async with httpx.AsyncClient(
        base_url=f"{url}/rest/v1/",
        headers={"apikey": key, "Accept": "application/json"},
        timeout=httpx.Timeout(5.0),
        follow_redirects=False,
    ) as client:
        yield GameFestRepository(client)
