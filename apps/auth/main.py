import os

from fastapi import FastAPI

from apps.auth.config import load_jwt_settings
from apps.auth.routes.auth import create_dev_router
from apps.gateway.middleware.logging_middleware import LoggingMiddleware, configure_logging
from apps.gateway.middleware.request_id_middleware import RequestIDMiddleware
from apps.gateway.utils.response_utils import ErrorResponseMiddleware, register_error_handlers


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Local development token issuer", version="0.1.0")
    register_error_handlers(app)
    if os.getenv("ENABLE_DEV_AUTH", "").lower() == "true":
        app.include_router(create_dev_router(load_jwt_settings()))
    app.add_middleware(ErrorResponseMiddleware)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RequestIDMiddleware)
    return app


app = create_app()
