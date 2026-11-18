"""Bound contact JSON before parsing, including requests without Content-Length."""

from fastapi import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from apps.gateway.utils.response_utils import error_response


MAX_CONTACT_BODY_BYTES = 4 * 1024 * 1024


class ContactBodyLimitMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if (scope["type"] != "http" or scope["method"] not in {"POST", "PATCH"}
                or not scope["path"].startswith("/v1/main/marketing-contacts")):
            await self.app(scope, receive, send)
            return

        async def too_large():
            response = error_response(Request(scope), 413, "payload_too_large", "Contact requests must be at most 4 MiB.")
            await response(scope, receive, send)

        length = dict(scope.get("headers", [])).get(b"content-length", b"")
        if length.isdigit() and int(length) > MAX_CONTACT_BODY_BYTES:
            await too_large()
            return
        chunks: list[Message] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > MAX_CONTACT_BODY_BYTES:
                await too_large()
                return
            chunks.append(message)
            if not message.get("more_body", False):
                break
        iterator = iter(chunks)

        async def replay():
            return next(iterator, None) or await receive()

        await self.app(scope, replay, send)
