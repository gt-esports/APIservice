import os
from urllib.parse import urlsplit


def cors_allowed_origins() -> list[str]:
    origins = []
    for value in os.getenv("CORS_ALLOWED_ORIGINS", "").split(","):
        value = value.strip()
        if not value:
            continue
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or "*" in value
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("CORS_ALLOWED_ORIGINS must contain explicit HTTP(S) origins")
        origins.append(value.rstrip("/"))
    return origins
