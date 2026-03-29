import time
import uuid
import json
import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("gateway")
logging.basicConfig(level=logging.INFO)

class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        start_time = time.time()

        user_id = None

        if hasattr(request.state, "user_id"):
            user_id = request.state.user_id

        response = await call_next(request)

        latency = (time.time() - start_time) * 1000

        log_data = {
            # add "user_id" from JWT once auth middleware exists
            "request_id": request_id,
            "route": request.url.path,
            "method": request.method,
            "status_code": response.status_code,
            "latency_ms": round(latency, 2),
            "user_id": user_id
        }

        logger.info(json.dumps(log_data))

        response.headers["X-Request-ID"] = request_id

        return response