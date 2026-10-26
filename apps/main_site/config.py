import base64
import json
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from apps.gateway.utils.response_utils import APIError


@dataclass(frozen=True)
class MainSiteSettings:
    url: str
    publishable_key: str = field(repr=False)


def is_public_key(key: str) -> bool:
    if re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]+", key):
        return True
    try:
        header, payload, signature = key.split(".")
        if not header or not signature:
            return False
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return isinstance(claims, dict) and claims.get("role") == "anon"
    except (ValueError, UnicodeError):
        return False


def load_settings() -> MainSiteSettings:
    url = os.getenv("MAIN_SITE_SUPABASE_URL", "").strip().rstrip("/")
    key = os.getenv("MAIN_SITE_SUPABASE_PUBLISHABLE_KEY", "").strip()
    try:
        parsed = urlsplit(url)
        valid_url = (
            bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
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
    if not valid_url or not is_public_key(key):
        raise APIError(503, "upstream_unavailable", "Main site access is temporarily unavailable.")
    return MainSiteSettings(url=url, publishable_key=key)
