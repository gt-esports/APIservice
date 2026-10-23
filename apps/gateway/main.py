from fastapi import Depends, FastAPI
from starlette.middleware.cors import CORSMiddleware

from apps.gamefest.db.session import get_gamefest_repository
from apps.gamefest.routes.gamefest import router as gamefest_router
from apps.gateway.config import cors_allowed_origins
from apps.gateway.middleware.logging_middleware import LoggingMiddleware, configure_logging
from apps.gateway.middleware.request_id_middleware import RequestIDMiddleware
from apps.gateway.utils.response_utils import ErrorResponseMiddleware, register_error_handlers
from apps.main_site.identity import router as identity_router
from apps.main_site.middleware import PrivateResponseMiddleware
from apps.main_site.body_limit import ContactBodyLimitMiddleware
from apps.main_site.contacts import router as contacts_router


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="GT Esports API", version="0.1.0")
    register_error_handlers(app)
    app.include_router(gamefest_router, prefix="/v1/gamefest")
    app.include_router(identity_router, prefix="/v1")
    app.include_router(contacts_router)

    @app.get("/health", tags=["Operations"])
    async def health():
        return {"service": "gateway", "status": "ok"}

    @app.get("/ready", tags=["Operations"])
    async def ready(repository=Depends(get_gamefest_repository)):
        await repository.check_ready()
        return {"service": "gateway", "status": "ready"}

    app.add_middleware(ContactBodyLimitMiddleware)
    app.add_middleware(ErrorResponseMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allowed_origins(),
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(PrivateResponseMiddleware)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RequestIDMiddleware)
    return app


app = create_app()
