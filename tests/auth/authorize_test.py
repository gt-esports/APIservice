import jwt
import pytest
from fastapi.testclient import TestClient

from apps.auth.main import create_app

SECRET = "test-only-secret-at-least-32-characters"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ENABLE_DEV_AUTH", "true")
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("JWT_ISSUER", "test-issuer")
    monkeypatch.setenv("JWT_AUDIENCE", "test-audience")
    return TestClient(create_app())


def test_login_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_DEV_AUTH", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    response = TestClient(create_app()).post("/login", json={"username": "test-user"})
    assert response.status_code == 404


@pytest.mark.parametrize("secret", ["", "too-short"])
def test_enabled_issuer_requires_secret(monkeypatch, secret):
    monkeypatch.setenv("ENABLE_DEV_AUTH", "true")
    monkeypatch.setenv("JWT_SECRET", secret)
    with pytest.raises(ValueError, match="JWT_SECRET"):
        create_app()


@pytest.mark.parametrize("role", [None, "developer"])
def test_login_issues_verifiable_token(client, role):
    body = {"username": "test-user"}
    if role:
        body["role"] = role
    response = client.post("/login", json=body)
    assert response.status_code == 200
    payload = jwt.decode(
        response.json()["accessToken"],
        SECRET,
        algorithms=["HS256"],
        issuer="test-issuer",
        audience="test-audience",
        options={"require": ["iat", "exp", "sub", "iss", "aud"]},
    )
    assert payload["sub"] == "test-user"
    assert payload["role"] == (role or "user")
    assert payload["exp"] - payload["iat"] == 3600


@pytest.mark.parametrize("body", [{}, {"username": ""}, {"username": "   "}])
def test_username_required(client, body):
    response = client.post("/login", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
