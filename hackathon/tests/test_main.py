from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_hello_endpoint_returns_200():
    response = client.get("/hello")
    assert response.status_code == 200

def test_hello_response_structure():
    response = client.get("/hello")
    data = response.json()

    assert "service" in data
    assert "version" in data
    assert "timestamp" in data

def test_service_name_correct():
    response = client.get("/hello")
    assert response.json()["service"] == "hackathon-service"