import os
from dataclasses import dataclass


@dataclass(frozen=True)
class JWTSettings:
    secret: str
    issuer: str = "gt-esports-dev"
    audience: str = "gt-esports-api"
    algorithm: str = "HS256"


def load_jwt_settings() -> JWTSettings:
    secret = os.getenv("JWT_SECRET", "")
    if len(secret) < 32:
        raise ValueError("JWT_SECRET must contain at least 32 characters")
    return JWTSettings(
        secret=secret,
        issuer=os.getenv("JWT_ISSUER", "gt-esports-dev"),
        audience=os.getenv("JWT_AUDIENCE", "gt-esports-api"),
    )
