import jwt
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from apps.auth.config import JWTSettings
from apps.gateway.utils.response_utils import error_response


class JWTMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        settings: JWTSettings,
        exempt_paths: tuple[str, ...] = ("/health", "/ready", "/docs", "/openapi.json"),
    ):
        self.app = app
        self.settings = settings
        self.exempt_paths = frozenset(exempt_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope["path"] in self.exempt_paths:
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        try:
            if scheme.lower() != "bearer" or not token:
                raise jwt.InvalidTokenError()
            user = jwt.decode(
                token,
                self.settings.secret,
                algorithms=[self.settings.algorithm],
                issuer=self.settings.issuer,
                audience=self.settings.audience,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
        except jwt.InvalidTokenError:
            response = error_response(
                request,
                401,
                "unauthorized",
                "A valid Bearer token is required",
                {"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        request.state.user = user
        await self.app(scope, receive, send)
