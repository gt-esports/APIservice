import time

import jwt
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.auth.config import JWTSettings
from apps.auth.services.auth_service import create_access_token
from apps.gateway.middleware.jwt_middleware import JWTMiddleware
from apps.gateway.middleware.request_id_middleware import RequestIDMiddleware

SETTINGS = JWTSettings(secret="test-only-secret-at-least-32-characters")


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(JWTMiddleware, settings=SETTINGS)
    app.add_middleware(RequestIDMiddleware)

    @app.get("/protected")
    async def protected(request: Request):
        return {"user": request.state.user}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/health/private")
    async def health_private():
        return {"sensitive": True}

    return TestClient(app)


def valid_claims():
    return {
        "sub": "test-user",
        "exp": int(time.time()) + 3600,
        "iss": SETTINGS.issuer,
        "aud": SETTINGS.audience,
    }


def assert_unauthorized(response):
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {
        "error": {
            "code": "unauthorized",
            "message": "A valid Bearer token is required",
            "request_id": response.headers["X-Request-ID"],
        }
    }


@pytest.mark.parametrize("header", ["", "Basic user:pass", "Bearer", "Bearer bad.token", "Bearer "])
def test_invalid_authorization(client, header):
    assert_unauthorized(client.get("/protected", headers={"Authorization": header}))


@pytest.mark.parametrize("claim", ["exp", "sub", "iss", "aud"])
def test_required_claims(client, claim):
    payload = valid_claims()
    payload.pop(claim)
    token = jwt.encode(payload, SETTINGS.secret, algorithm="HS256")
    assert_unauthorized(client.get("/protected", headers={"Authorization": f"Bearer {token}"}))


@pytest.mark.parametrize(
    "change", [{"exp": 1}, {"iss": "wrong-issuer"}, {"aud": "wrong-audience"}]
)
def test_expiration_issuer_and_audience_enforced(client, change):
    token = jwt.encode(valid_claims() | change, SETTINGS.secret, algorithm="HS256")
    assert_unauthorized(client.get("/protected", headers={"Authorization": f"Bearer {token}"}))


def test_signature_required(client):
    token = jwt.encode(valid_claims(), "another-secret-at-least-32-characters", algorithm="HS256")
    assert_unauthorized(client.get("/protected", headers={"Authorization": f"Bearer {token}"}))


def test_issuer_token_accepted_by_verifier(client):
    token = create_access_token("test-user", "user", SETTINGS)
    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["user"]["sub"] == "test-user"


def test_only_exact_health_path_is_exempt(client):
    assert client.get("/health").status_code == 200
    assert_unauthorized(client.get("/health/private"))
