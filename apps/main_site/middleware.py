from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class PrivateResponseMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        path = scope.get("path", "")
        private = path in {"/v1/me", "/v1/main"} or path.startswith(("/v1/me/", "/v1/main/"))
        if scope["type"] != "http" or not private:
            await self.app(scope, receive, send)
            return

        async def no_store(message: Message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, no_store)
