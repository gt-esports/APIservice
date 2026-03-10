from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "gateway"

def test_request_id_header_exists():
    response = client.get("/health")
    assert "X-Request-ID" in response.headers