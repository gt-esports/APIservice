import asyncio
import base64
import json
import logging
from typing import Annotated

import httpx
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from apps.gateway.main import create_app
from apps.gateway.utils.response_utils import APIError
from apps.main_site.auth import Principal, require_admin
from apps.main_site.client import MainSiteClient, get_main_site_client
from apps.main_site.config import is_public_key, load_settings

USER_ID = "10000000-0000-4000-8000-000000000001"
OTHER_ID = "10000000-0000-4000-8000-000000000002"
TOKEN = "test-caller-access-token"
KEY = "sb_publishable_test_key"
AUTH_HEADERS = {"Authorization": f"Bearer {TOKEN}"}
PROFILE = {"id": USER_ID, "full_name": "Test User", "created_at": "2026-10-01T12:00:00Z"}


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    app = create_app()
    state = {
        "auth": {"id": USER_ID, "email": "user@example.test", "user_metadata": {"role": "admin"}},
        "roles": [{"user_id": USER_ID, "role": "admin"}],
        "profiles": [PROFILE],
    }
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.headers["apikey"] == KEY
        resource = {
            "/auth/v1/user": "auth",
            "/rest/v1/user_roles": "roles",
            "/rest/v1/profiles": "profiles",
        }[request.url.path]
        value = state[resource]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, json=value)

    async def mock_client():
        async with httpx.AsyncClient(
            base_url="https://supabase.example", transport=httpx.MockTransport(handler)
        ) as client:
            yield MainSiteClient(client, KEY)

    app.dependency_overrides[get_main_site_client] = mock_client

    @app.get("/v1/main/admin-check")
    async def admin_check(principal: Annotated[Principal, Depends(require_admin)]):
        return {"id": principal.id}

    return app, TestClient(app), state, requests


def assert_error(response, status, code):
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_me_returns_trusted_identity_and_own_profile(backend):
    _, client, _, requests = backend
    response = client.get("/v1/me", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {
        "user": {"id": USER_ID, "email": "user@example.test"},
        "profile": PROFILE,
        "role": "admin",
    }
    assert requests[1].url.params["user_id"] == f"eq.{USER_ID}"
    assert requests[1].url.params["select"] == "user_id,role"
    assert requests[2].url.params["id"] == f"eq.{USER_ID}"
    assert requests[2].url.params["select"] == "id,full_name,created_at"
    assert TOKEN not in response.text
    assert "user_metadata" not in response.text


def test_missing_profile_role_and_email_are_supported(backend):
    _, client, state, _ = backend
    state["auth"] = {"id": USER_ID}
    state["roles"] = []
    state["profiles"] = []
    response = client.get("/v1/me", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json() == {"user": {"id": USER_ID, "email": None}, "profile": None, "role": "member"}


def test_profile_nullable_fields(backend):
    _, client, state, _ = backend
    state["profiles"] = [{"id": USER_ID, "full_name": None, "created_at": None}]
    response = client.get("/v1/me", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json()["profile"]["created_at"] is None


@pytest.mark.parametrize("authorization", [None, "Basic abc", "Bearer", "Bearer bad token", "Bearer a\tb"])
def test_invalid_header_denied_before_config_or_network(monkeypatch, authorization):
    monkeypatch.delenv("MAIN_SITE_SUPABASE_URL", raising=False)
    monkeypatch.delenv("MAIN_SITE_SUPABASE_PUBLISHABLE_KEY", raising=False)
    client = TestClient(create_app())
    headers = {} if authorization is None else {"Authorization": authorization}
    response = client.get("/v1/me", headers=headers)
    assert_error(response, 401, "unauthorized")
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_duplicate_authorization_header_rejected(backend):
    _, client, _, requests = backend
    response = client.get("/v1/me", headers=[("Authorization", f"Bearer {TOKEN}"), ("Authorization", "Bearer other")])
    assert_error(response, 401, "unauthorized")
    assert not requests


@pytest.mark.parametrize("status", [401, 403])
def test_invalid_or_expired_session_stops_before_role_lookup(backend, status):
    _, client, state, requests = backend
    state["auth"] = httpx.Response(status, json={"message": "private rejected token"})
    response = client.get("/v1/me", headers=AUTH_HEADERS)
    assert_error(response, 401, "unauthorized")
    assert len(requests) == 1
    assert "private" not in response.text


@pytest.mark.parametrize("user", [{}, {"id": "bad-uuid"}, {"id": USER_ID, "email": 42}, []])
def test_malformed_identity_fails_closed(backend, user):
    _, client, state, requests = backend
    state["auth"] = user
    assert_error(client.get("/v1/me", headers=AUTH_HEADERS), 503, "upstream_unavailable")
    assert len(requests) == 1


@pytest.mark.parametrize("roles", [
    None,
    {},
    [{"user_id": USER_ID, "role": "owner"}],
    [{"user_id": USER_ID, "role": None}],
    [{"user_id": OTHER_ID, "role": "admin"}],
    [{"role": "admin"}],
    [{"user_id": USER_ID, "role": "admin"}] * 2,
])
def test_malformed_role_lookup_fails_closed(backend, roles):
    _, client, state, requests = backend
    state["roles"] = roles
    assert_error(client.get("/v1/main/admin-check", headers=AUTH_HEADERS), 503, "upstream_unavailable")
    assert len(requests) == 2


@pytest.mark.parametrize("roles", [[], [{"user_id": USER_ID, "role": "member"}]])
def test_metadata_cannot_grant_admin_access(backend, roles):
    _, client, state, _ = backend
    state["roles"] = roles
    assert_error(client.get("/v1/main/admin-check", headers=AUTH_HEADERS), 403, "forbidden")


def test_role_is_read_again_and_revocation_effective_on_next_request(backend):
    _, client, state, requests = backend
    assert client.get("/v1/main/admin-check", headers=AUTH_HEADERS).status_code == 200
    state["roles"] = [{"user_id": USER_ID, "role": "member"}]
    assert_error(client.get("/v1/main/admin-check", headers=AUTH_HEADERS), 403, "forbidden")
    assert [request.url.path for request in requests] == ["/auth/v1/user", "/rest/v1/user_roles"] * 2


@pytest.mark.parametrize("resource", ["auth", "roles", "profiles"])
@pytest.mark.parametrize("status,expected,code", [
    (500, 503, "upstream_unavailable"),
    (429, 503, "upstream_unavailable"),
    (504, 504, "upstream_timeout"),
    (302, 503, "upstream_unavailable"),
])
def test_upstream_http_failures_are_sanitized(backend, resource, status, expected, code):
    _, client, state, _ = backend
    state[resource] = httpx.Response(status, json={"details": "private database and auth details"})
    response = client.get("/v1/me", headers=AUTH_HEADERS)
    assert_error(response, expected, code)
    assert "private" not in response.text


@pytest.mark.parametrize("resource", ["auth", "roles", "profiles"])
@pytest.mark.parametrize("failure,status,code", [
    (httpx.ReadTimeout("secret upstream connection"), 504, "upstream_timeout"),
    (httpx.ConnectError("secret upstream connection"), 503, "upstream_unavailable"),
])
def test_transport_failures_are_sanitized(backend, resource, failure, status, code, caplog):
    _, client, state, _ = backend
    state[resource] = failure
    with caplog.at_level(logging.INFO, logger="gateway"):
        response = client.get("/v1/me", headers=AUTH_HEADERS)
    assert_error(response, status, code)
    assert "secret upstream" not in response.text
    assert "secret upstream" not in caplog.text
    assert TOKEN not in caplog.text


@pytest.mark.parametrize("profiles", [
    {},
    [{"id": OTHER_ID, "full_name": "Other", "created_at": None}],
    [PROFILE, PROFILE],
    [{"id": USER_ID, "full_name": 123, "created_at": None}],
    [{"id": USER_ID, "full_name": "User", "created_at": "not-a-date"}],
])
def test_bad_or_foreign_profile_fails_closed(backend, profiles):
    _, client, state, _ = backend
    state["profiles"] = profiles
    assert_error(client.get("/v1/me", headers=AUTH_HEADERS), 503, "upstream_unavailable")


def test_protected_validation_unexpected_and_routing_errors_have_no_store(backend):
    app, client, _, _ = backend

    @app.get("/v1/main/validate")
    async def validate(value: int):
        return {"value": value}

    @app.get("/v1/main/crash")
    async def crash():
        raise RuntimeError("private PII and connection details")

    assert_error(client.get("/v1/main/validate?value=private-note"), 422, "validation_error")
    assert_error(client.get("/v1/main/crash"), 500, "internal_error")
    assert_error(client.post("/v1/me"), 405, "method_not_allowed")
    assert_error(client.get("/v1/main/missing"), 404, "not_found")


def test_request_logs_use_route_templates_and_omit_raw_input(backend, caplog):
    app, client, _, _ = backend

    @app.get("/v1/main/probe/{contact_id}")
    async def probe(contact_id: str):
        return {"ok": True}

    with caplog.at_level(logging.INFO, logger="gateway"):
        client.get("/v1/main/probe/private-email@example.test?note=private-note", headers=AUTH_HEADERS)
        client.get("/v1/main/private-unmatched-value")
    records = [json.loads(record.message) for record in caplog.records if record.name == "gateway"]
    assert [record["route"] for record in records] == ["/v1/main/probe/{contact_id}", "<unmatched>"]
    assert "private-email" not in caplog.text
    assert "private-note" not in caplog.text
    assert "private-unmatched" not in caplog.text
    assert TOKEN not in caplog.text


@pytest.mark.parametrize("method", ["GET", "POST", "PATCH", "DELETE", "OPTIONS"])
def test_cors_accepts_explicit_origin_bearer_and_private_methods(monkeypatch, method):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://website.example")
    client = TestClient(create_app())
    headers = {
        "Origin": "https://website.example",
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": "authorization,content-type",
    }
    response = client.options("/v1/main/marketing-contacts", headers=headers)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Access-Control-Allow-Origin"] == "https://website.example"
    headers["Origin"] = "https://attacker.example"
    response = client.options("/v1/main/marketing-contacts", headers=headers)
    assert response.status_code == 400
    assert response.headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in response.headers


def test_public_gamefest_cache_policy_is_unchanged(backend):
    app, client, _, _ = backend
    from apps.gamefest.db.session import get_gamefest_repository

    class EmptyRepository:
        async def list_past_winners(self):
            return []

    app.dependency_overrides[get_gamefest_repository] = EmptyRepository
    response = client.get("/v1/gamefest/past-winners")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "public, max-age=30"


def legacy_key(role):
    payload = base64.urlsafe_b64encode(json.dumps({"role": role}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


@pytest.mark.parametrize("key,valid", [
    (KEY, True),
    ("sb_secret_test", False),
    ("sb_publishable_", False),
    ("invalid-key", False),
    (legacy_key("anon"), True),
    (legacy_key("service_role"), False),
    (legacy_key("authenticated"), False),
])
def test_only_publishable_or_anon_configuration_keys(key, valid):
    assert is_public_key(key) is valid


@pytest.mark.parametrize("url,valid", [
    ("https://project.supabase.co", True),
    ("http://localhost:54321", True),
    ("http://127.0.0.1:54321", True),
    ("http://[::1]:54321", True),
    ("http://project.supabase.co", False),
    ("https://name:password@project.supabase.co", False),
    ("https://project.supabase.co/path", False),
    ("https://project.supabase.co?key=private", False),
    ("https://project.supabase.co:bad", False),
    ("", False),
])
def test_settings_validate_transport_and_origin(monkeypatch, url, valid):
    monkeypatch.setenv("MAIN_SITE_SUPABASE_URL", url)
    monkeypatch.setenv("MAIN_SITE_SUPABASE_PUBLISHABLE_KEY", KEY)
    if valid:
        assert load_settings().url == url
    else:
        with pytest.raises(APIError) as error:
            load_settings()
        assert error.value.status_code == 503


def test_missing_configuration_is_a_private_503(monkeypatch):
    monkeypatch.delenv("MAIN_SITE_SUPABASE_URL", raising=False)
    monkeypatch.delenv("MAIN_SITE_SUPABASE_PUBLISHABLE_KEY", raising=False)
    assert_error(TestClient(create_app()).get("/v1/me", headers=AUTH_HEADERS), 503, "upstream_unavailable")


def test_principal_repr_omits_access_token():
    principal = Principal(USER_ID, None, "member", TOKEN)
    assert TOKEN not in repr(principal)


def test_client_does_not_follow_redirects_or_allow_apikey_override():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://other.example/private"})

    async def exercise():
        async with httpx.AsyncClient(base_url="https://supabase.example", transport=httpx.MockTransport(handler)) as http:
            client = MainSiteClient(http, KEY)
            response = await client.request("GET", "/auth/v1/user", headers=AUTH_HEADERS | {"apikey": "malicious"})
            assert response.status_code == 302
            with pytest.raises(ValueError):
                await client.request("GET", "https://other.example", headers=AUTH_HEADERS)

    asyncio.run(exercise())
    assert len(requests) == 1
    assert requests[0].headers["apikey"] == KEY
