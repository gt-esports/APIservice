import httpx
import pytest
from fastapi.testclient import TestClient

from apps.gateway.main import create_app
from apps.main_site.client import MainSiteClient, get_main_site_client
from apps.main_site.contacts import CONTACT_COLUMNS

USER_ID = "10000000-0000-4000-8000-000000000001"
TOKEN = "synthetic-readiness-token"
KEY = "sb_publishable_synthetic_readiness"
CONTACT = {
    "id": "20000000-0000-4000-8000-000000000001", "email": "private@example.test",
    "name": "Private Contact", "organization": "", "category": "", "notes": "private note",
    "contact_group": "newsletter", "status": "unsubscribed", "last_contacted": None,
    "revision": 1, "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-01T00:00:00Z",
}


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://www.gatechesports.com")
    state = {"role": "admin", "contacts": httpx.Response(200, json=[CONTACT])}
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.headers["apikey"] == KEY
        if request.url.path == "/auth/v1/user":
            return httpx.Response(200, json={"id": USER_ID})
        if request.url.path == "/rest/v1/user_roles":
            return httpx.Response(200, json=[{"user_id": USER_ID, "role": state["role"]}])
        assert request.url.path == "/rest/v1/marketing_contacts"
        assert request.method == "GET"
        assert request.url.params["select"] == CONTACT_COLUMNS
        assert request.url.params["limit"] == "1"
        return state["contacts"]

    async def dependency():
        async with httpx.AsyncClient(base_url="https://synthetic.example", transport=httpx.MockTransport(handler)) as client:
            yield MainSiteClient(client, KEY)

    app = create_app()
    app.dependency_overrides[get_main_site_client] = dependency
    return TestClient(app), state, requests


def test_main_site_readiness_is_private_live_and_read_only(backend):
    client, _, requests = backend
    response = client.get("/v1/main/ready", headers={"Authorization": f"Bearer {TOKEN}", "Origin": "https://www.gatechesports.com"})
    assert response.status_code == 200
    assert response.json() == {"service": "main-site", "status": "ready"}
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Access-Control-Allow-Origin"] == "https://www.gatechesports.com"
    assert len(requests) == 3
    assert all(request.method == "GET" for request in requests)
    assert "private" not in response.text
    assert USER_ID not in response.text


def test_empty_contact_table_is_ready(backend):
    client, state, _ = backend
    state["contacts"] = httpx.Response(200, json=[])
    assert client.get("/v1/main/ready", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_anonymous_request_cannot_probe_main_site(backend):
    client, _, requests = backend
    response = client.get("/v1/main/ready")
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store"
    assert requests == []


def test_revoked_admin_cannot_probe_contact_schema(backend):
    client, state, requests = backend
    state["role"] = "member"
    response = client.get("/v1/main/ready", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 403
    assert response.headers["Cache-Control"] == "no-store"
    assert len(requests) == 2


@pytest.mark.parametrize("upstream", [
    httpx.Response(400, json={"code": "42703", "message": "private schema details"}),
    httpx.Response(200, json={"unexpected": "private"}),
    httpx.Response(200, json=[{"id": "wrong"}]),
    httpx.Response(200, json=[CONTACT, {**CONTACT, "id": "20000000-0000-4000-8000-000000000002"}]),
])
def test_bad_schema_data_or_bounded_query_fails_readiness(backend, upstream):
    client, state, _ = backend
    state["contacts"] = upstream
    response = client.get("/v1/main/ready", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["error"]["code"] == "upstream_unavailable"
    assert "private" not in response.text
