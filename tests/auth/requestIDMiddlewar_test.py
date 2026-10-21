import uuid

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.gateway.middleware.request_id_middleware import RequestIDMiddleware


def test_generated_request_id_matches_state_and_is_unique():
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/test")
    async def example(request: Request):
        return {"request_id": request.state.request_id}

    client = TestClient(app)
    first = client.get("/test", headers={"X-Request-ID": "untrusted-client-value"})
    second = client.get("/test")
    first_id = first.headers["X-Request-ID"]
    assert str(uuid.UUID(first_id)) == first_id
    assert first.json()["request_id"] == first_id
    assert first_id != second.headers["X-Request-ID"]
    assert first_id != "untrusted-client-value"
