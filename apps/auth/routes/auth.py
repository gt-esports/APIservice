from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from apps.auth.config import JWTSettings
from apps.auth.services.auth_service import create_access_token


class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    username: str = Field(min_length=1, max_length=100)
    role: str = Field(default="user", min_length=1, max_length=100)


def create_dev_router(settings: JWTSettings) -> APIRouter:
    router = APIRouter()

    @router.post("/login", summary="Development-only token issuer; no identity verification")
    async def login(body: LoginRequest):
        return {"accessToken": create_access_token(body.username, body.role, settings)}

    return router
