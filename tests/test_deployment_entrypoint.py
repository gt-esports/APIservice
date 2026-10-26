from fastapi.testclient import TestClient

from app import app
from apps.gateway.main import app as gateway_app


def test_deployment_entrypoint_exports_full_gateway_without_dev_login(monkeypatch):
    monkeypatch.delenv("MAIN_SITE_SUPABASE_URL", raising=False)
    monkeypatch.delenv("MAIN_SITE_SUPABASE_PUBLISHABLE_KEY", raising=False)
    assert app is gateway_app
    client = TestClient(app)
    assert client.get("/health").json() == {"service": "gateway", "status": "ok"}
    paths = client.get("/openapi.json").json()["paths"]
    assert "/v1/main/ready" in paths
    assert "/v1/main/marketing-contacts" in paths
    assert "/v1/gamefest/past-winners" in paths
    assert "/login" not in paths
    assert client.get("/v1/main/ready").status_code == 401
