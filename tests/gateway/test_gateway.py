import json
import logging

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from apps.gamefest.db.session import get_gamefest_repository
from apps.gateway.main import create_app


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    return create_app()


def assert_error(response, status, code):
    assert response.status_code == status
    error = response.json()["error"]
    assert set(error) == {"code", "message", "request_id"}
    assert error["code"] == code
    assert error["request_id"] == response.headers["X-Request-ID"]


def test_health_does_not_need_database_configuration(app, monkeypatch):
    monkeypatch.delenv("GAMEFEST_SUPABASE_URL", raising=False)
    monkeypatch.delenv("GAMEFEST_SUPABASE_ANON_KEY", raising=False)
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"service": "gateway", "status": "ok"}
    assert "X-Request-ID" in response.headers


def test_ready_checks_repository(app):
    class Repository:
        checked = False

        async def check_ready(self):
            self.checked = True

    repository = Repository()
    app.dependency_overrides[get_gamefest_repository] = lambda: repository
    response = TestClient(app).get("/ready")
    assert response.status_code == 200
    assert repository.checked
    assert response.json()["status"] == "ready"


def test_ready_reports_unavailable_dependency(app):
    class Repository:
        async def check_ready(self):
            raise HTTPException(503, detail={"code": "upstream_unavailable", "message": "Data unavailable"})

    app.dependency_overrides[get_gamefest_repository] = Repository
    response = TestClient(app).get("/ready")
    assert_error(response, 503, "upstream_unavailable")


def test_ready_reports_missing_configuration(app, monkeypatch):
    monkeypatch.delenv("GAMEFEST_SUPABASE_URL", raising=False)
    monkeypatch.delenv("GAMEFEST_SUPABASE_ANON_KEY", raising=False)
    assert_error(TestClient(app).get("/ready"), 503, "gamefest_unavailable")


@pytest.mark.parametrize("method,path,status,code", [
    ("GET", "/missing", 404, "not_found"),
    ("POST", "/health", 405, "method_not_allowed"),
    ("POST", "/login", 404, "not_found"),
])
def test_consistent_http_errors(app, method, path, status, code):
    assert_error(TestClient(app).request(method, path), status, code)


def test_validation_error_omits_input_and_uses_request_id(app):
    @app.get("/integer")
    async def integer(value: int):
        return {"value": value}

    response = TestClient(app).get("/integer", params={"value": "private-value"})
    assert_error(response, 422, "validation_error")
    assert "private-value" not in response.text


def test_request_state_header_and_log_use_one_id(app, caplog):
    @app.get("/state")
    async def state(request: Request):
        return {"request_id": request.state.request_id}

    with caplog.at_level(logging.INFO, logger="gateway"):
        response = TestClient(app).get("/state")
    record = json.loads(next(record.message for record in caplog.records if record.name == "gateway"))
    assert record["request_id"] == response.headers["X-Request-ID"] == response.json()["request_id"]
    assert record["status_code"] == 200


def test_unexpected_error_logged_and_sanitized(app, caplog):
    @app.get("/crash")
    async def crash():
        raise RuntimeError("sensitive internal connection details")

    with caplog.at_level(logging.INFO, logger="gateway"):
        response = TestClient(app).get("/crash")
    assert_error(response, 500, "internal_error")
    assert "sensitive" not in response.text
    record = json.loads(next(record.message for record in caplog.records if record.name == "gateway"))
    assert record["request_id"] == response.headers["X-Request-ID"]
    assert record["status_code"] == 500
    assert record["error_type"] == "RuntimeError"


def test_cors_allows_only_configured_origins_and_exposes_id(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://gamefest.example,http://localhost:5173")
    app = create_app()

    @app.get("/crash")
    async def crash():
        raise RuntimeError("internal")

    client = TestClient(app)
    for path in ["/health", "/missing", "/crash"]:
        response = client.get(path, headers={"Origin": "https://gamefest.example"})
        assert response.headers["access-control-allow-origin"] == "https://gamefest.example"
        assert response.headers["access-control-expose-headers"] == "X-Request-ID"
    response = client.get("/health", headers={"Origin": "https://other.example"})
    assert "access-control-allow-origin" not in response.headers
    response = client.options("/health", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_no_cors_origins_by_default(app):
    response = TestClient(app).get("/health", headers={"Origin": "https://gamefest.example"})
    assert "access-control-allow-origin" not in response.headers


def test_cors_preflight_is_logged_with_request_id(app, caplog):
    with caplog.at_level(logging.INFO, logger="gateway"):
        response = TestClient(app).options("/health", headers={
            "Origin": "https://unconfigured.example", "Access-Control-Request-Method": "GET"
        })
    assert response.status_code == 400
    record = json.loads(next(record.message for record in caplog.records if record.name == "gateway"))
    assert record["request_id"] == response.headers["X-Request-ID"]
    assert record["status_code"] == 400


@pytest.mark.parametrize("origin", ["*", "https://*.example", "https://example.com/path", "null"])
def test_invalid_cors_configuration_fails_at_startup(monkeypatch, origin):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", origin)
    with pytest.raises(ValueError, match="explicit HTTP"):
        create_app()


def test_openapi_lists_versioned_public_api(app):
    response = TestClient(app).get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/v1/gamefest/past-winners" in paths
    assert "/login" not in paths
