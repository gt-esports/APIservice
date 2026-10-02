from datetime import datetime, timedelta, timezone

import jwt

from apps.auth.config import JWTSettings


def create_access_token(username: str, role: str, settings: JWTSettings) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": username,
            "role": role,
            "iat": now,
            "exp": now + timedelta(hours=1),
            "iss": settings.issuer,
            "aud": settings.audience,
        },
        settings.secret,
        algorithm=settings.algorithm,
    )
