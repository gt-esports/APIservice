import uuid
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.datastructures import MutableHeaders

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        requestID = str(uuid.uuid4())

        request.state.request_id = requestID

        headers = MutableHeaders(scope=request.scope)
        headers["X-Request-ID"] = requestID

        response = await call_next(request)
        response.headers["X-Request-ID"] = requestID

        return response
