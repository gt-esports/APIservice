import base64
import copy
import json
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.gamefest.db.session import GameFestRepository, get_gamefest_repository
from apps.gateway.main import create_app


SEASON_ID = "9a1f58f7-4f6b-4b4a-94e4-e3944dcf9cc8"
PLAYER_ID = "97c67ab2-aa93-4b84-85e1-29267a079fb2"
ROWS = [{
    "id": SEASON_ID,
    "name": "GameFest 2026",
    "slug": "gamefest-2026",
    "players": [{
        "id": PLAYER_ID,
        "points": 120,
        "users": {"username": "fixture", "fname": "Test", "lname": "Player"},
    }],
}]


@pytest.fixture
def api():
    def make(handler):
        app = create_app()

        async def repository():
            async with httpx.AsyncClient(
                base_url="https://example.supabase.co/rest/v1/",
                headers={"apikey": "sb_publishable_fixture"},
                transport=httpx.MockTransport(handler),
            ) as client:
                yield GameFestRepository(client)

        app.dependency_overrides[get_gamefest_repository] = repository
        return TestClient(app)
    return make


def test_public_api_uses_bounded_anonymous_query_and_projects_response(api):
    rows = copy.deepcopy(ROWS)
    rows[0]["tournament_slug"] = "not-part-of-contract"
    rows[0]["players"][0]["users"]["email"] = "private@example.invalid"
    captured = []

    def upstream(request):
        captured.append(request)
        return httpx.Response(200, json=rows)

    with api(upstream) as client:
        response = client.get(
            "/v1/gamefest/past-winners?select=*",
            headers={"Authorization": "Bearer user-token", "X-User-ID": "untrusted"},
        )
    assert response.status_code == 200
    assert response.json() == {"seasons": [{
        "id": SEASON_ID, "name": "GameFest 2026", "slug": "gamefest-2026",
        "winners": [{"rank": 1, "display_name": "Test Player", "points": 120}],
    }]}
    assert response.headers["Cache-Control"] == "public, max-age=30"
    UUID(response.headers["X-Request-ID"])
    assert len(captured) == 1
    request = captured[0]
    assert request.method == "GET"
    assert request.url.path == "/rest/v1/seasons"
    assert dict(request.url.params) == {
        "select": "id,name,slug,players(id,points,users(username,fname,lname))",
        "is_active": "eq.false", "order": "created_at.desc,id.asc", "limit": "20",
        "players.order": "points.desc.nullslast,id.asc", "players.limit": "3",
    }
    assert "authorization" not in request.headers
    assert "x-user-id" not in request.headers
    assert request.headers["apikey"] == "sb_publishable_fixture"


@pytest.mark.parametrize("rows,expected", [([], {"seasons": []}),
    ([{**ROWS[0], "players": []}], {"seasons": [{
        "id": SEASON_ID, "name": "GameFest 2026", "slug": "gamefest-2026", "winners": [],
    }]}),
])
def test_empty_results_are_successful(api, rows, expected):
    with api(lambda _: httpx.Response(200, json=rows)) as client:
        response = client.get("/v1/gamefest/past-winners")
    assert response.status_code == 200
    assert response.json() == expected


@pytest.mark.parametrize("profile,name", [
    (None, "GameFest Player"), ([], "GameFest Player"),
    ({"username": "  fixture  ", "fname": "  ", "lname": None}, "fixture"),
    ([{"username": "fallback", "fname": " Test ", "lname": " Player "}], "Test Player"),
])
def test_profile_fallback_and_null_points(api, profile, name):
    rows = copy.deepcopy(ROWS)
    rows[0]["players"][0].update(users=profile, points=None)
    with api(lambda _: httpx.Response(200, json=rows)) as client:
        response = client.get("/v1/gamefest/past-winners")
    assert response.status_code == 200
    assert response.json()["seasons"][0]["winners"] == [{"rank": 1, "display_name": name, "points": 0}]


@pytest.mark.parametrize("status", [301, 401, 403, 404, 429, 500])
def test_upstream_errors_are_sanitized_and_not_cached(api, status):
    with api(lambda _: httpx.Response(status, json={"message": "private upstream detail"})) as client:
        response = client.get("/v1/gamefest/past-winners")
    assert_unavailable(response)
    assert "private" not in response.text


def assert_unavailable(response):
    assert response.status_code == 503
    assert response.json() == {"error": {
        "code": "gamefest_unavailable",
        "message": "GameFest results are temporarily unavailable.",
        "request_id": response.headers["X-Request-ID"],
    }}
    assert response.headers["Retry-After"] == "30"
    assert "public" not in response.headers.get("Cache-Control", "")


@pytest.mark.parametrize("exception", [httpx.ReadTimeout, httpx.ConnectError])
def test_network_failure_is_unavailable(api, exception):
    def fail(request):
        raise exception("internal network detail", request=request)
    with api(fail) as client:
        assert_unavailable(client.get("/v1/gamefest/past-winners"))


@pytest.mark.parametrize("mutation", [
    lambda rows: {},
    lambda rows: rows * 21,
    lambda rows: rows * 2,
    lambda rows: [{**rows[0], "name": " "}],
    lambda rows: [{**rows[0], "players": rows[0]["players"] * 4}],
    lambda rows: [{**rows[0], "players": rows[0]["players"] * 2}],
    lambda rows: [{**rows[0], "players": [{**rows[0]["players"][0], "points": -1}]}],
    lambda rows: [{**rows[0], "players": [{**rows[0]["players"][0], "points": True}]}],
    lambda rows: [{**rows[0], "players": [{**rows[0]["players"][0], "id": "bad-id"}]}],
])
def test_invalid_upstream_contract_is_unavailable(api, mutation):
    with api(lambda _: httpx.Response(200, json=mutation(copy.deepcopy(ROWS)))) as client:
        assert_unavailable(client.get("/v1/gamefest/past-winners"))


def test_invalid_json_is_unavailable(api):
    with api(lambda _: httpx.Response(200, content=b"not JSON")) as client:
        assert_unavailable(client.get("/v1/gamefest/past-winners"))


def test_readiness_checks_real_repository_dependency(api):
    with api(lambda _: httpx.Response(200, json=[])) as client:
        assert client.get("/ready").status_code == 200
    with api(lambda _: httpx.Response(503)) as client:
        assert_unavailable(client.get("/ready"))


def test_public_api_has_no_write_route(api):
    def upstream(_):
        pytest.fail("A write request must not reach the data source")
    with api(upstream) as client:
        assert client.post("/v1/gamefest/past-winners", json={}).status_code == 405


@pytest.mark.parametrize("url,key", [
    ("", ""),
    ("http://remote.example", "sb_publishable_fixture"),
    ("https://user:pass@example.com", "sb_publishable_fixture"),
    ("https://example.com/rest/v1", "sb_publishable_fixture"),
    ("https://example.com?query=1", "sb_publishable_fixture"),
    ("https://example.com:bad", "sb_publishable_fixture"),
    ("https://example.com", "sb_secret_do_not_use"),
    ("https://example.com", "not-an-anonymous-key"),
    ("https://example.com", "x." + base64.urlsafe_b64encode(json.dumps({"role": "service_role"}).encode()).decode().rstrip("=") + ".x"),
])
def test_missing_or_privileged_configuration_fails_closed(monkeypatch, url, key):
    monkeypatch.setenv("GAMEFEST_SUPABASE_URL", url)
    monkeypatch.setenv("GAMEFEST_SUPABASE_ANON_KEY", key)
    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200
        assert_unavailable(client.get("/v1/gamefest/past-winners"))
