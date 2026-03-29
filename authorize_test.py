from fastapi.testclient import TestClient
from authorizeLogin import app, createAccessToken, DevSecret, ALGORITHM
import jwt

client = TestClient(app)

def decodeToken(token: str) -> dict:
    return jwt.decode(token, DevSecret, algorithms=[ALGORITHM])

def testLoginReturnsAccessToken():
    response = client.post("/login", json={"username": "testUser", "role": "Software Developer"})
    assert response.status_code == 200
    assert "accessToken" in response.json()

def testWithUserNameOnly():
    response = client.post("/login", json={"username": "testUser"})
    assert response.status_code == 200
    token = response.json()["accessToken"]
    payload = decodeToken(token)
    assert payload["sub"] == "testUser"
    assert payload["role"] == "user"
    assert "iat" in payload
    assert "exp" in payload

def testWithBoth():
    response = client.post("/login", json={"username": "Joey", "role": "Software Developer"})
    assert response.status_code == 200
    token = response.json()["accessToken"]
    payload = decodeToken(token)
    assert payload["sub"] == "Joey"
    assert payload["role"] == "Software Developer"
    assert "iat" in payload
    assert "exp" in payload

def testWithoutUsernameRejection():
    response = client.post("/login", json={})
    assert response.status_code == 422

