import json
import logging
from time import perf_counter

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("gateway")


def configure_logging() -> None:
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler())


class LoggingMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        status_code = 500
        error_type = None

        async def record_status(message: Message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, record_status)
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            error_type = error_type or scope.get("state", {}).get("error_type")
            log_data = {
                "request_id": scope.get("state", {}).get("request_id"),
                "route": scope["path"],
                "method": scope["method"],
                "status_code": status_code,
                "latency_ms": round((perf_counter() - started) * 1000, 2),
            }
            if error_type:
                log_data["error_type"] = error_type
            level = logging.ERROR if status_code >= 500 or error_type else logging.INFO
            logger.log(level, json.dumps(log_data))
