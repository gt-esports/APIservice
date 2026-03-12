import pytest
import jwt
import time
from fastapi.testclient import TestClient
from fastapi import Request
from jwtMiddleware import app, DevSecret, ALGORITHM


# Dummy route for testing
@app.get("/test")
async def protected_route(request: Request):
    return {"message": "success", "user": request.state.user}

client = TestClient(app)

# Helpers
def generate_token(payload, secret=DevSecret, algo=ALGORITHM):
    return jwt.encode(payload, secret, algorithm=algo)

# Tests
def test_no_auth_header():
    """Request without header returns 401"""
    response = client.get("/test")
    assert response.status_code == 401
    assert response.json() == {"error": "Missing or invalid Authorization header"}

def test_invalid_authorization_header_scheme():
    """A header that's not bearer gets 404"""
    response = client.get("/test", headers={"Authorization": "Basic user:pass"})
    assert response.status_code == 401
    assert response.json() == {"error": "Missing or invalid Authorization header"}

def test_malformed_token():
    """Non decodable string for token"""
    response = client.get("/test", headers={"Authorization": "Bearer vhjefohjvofd.wss.s"})
    assert response.status_code == 401
    assert response.json() == {"error": "Error Decoding Authorization Token"}

def test_missing_exp_claim():
    """Token without exp"""
    token = generate_token({"sub": "user123"})
    response = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    
    assert response.status_code == 401
    assert "exp" in response.json()["error"]

def test_missing_sub_claim():
    """Token without sub"""
    token = generate_token({"exp": time.time() + 3600})
    response = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    
    assert response.status_code == 401
    assert "sub" in response.json()["error"]

def test_expired_token():
    """Token that expired"""
    payload = {"sub": "user123", "exp": time.time() - 20}
    token = generate_token(payload)
    response = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    
    assert response.status_code == 401
    assert response.json() == {"error": "Expired Authorization Token Signature"}

def test_invalid_signature():
    """Wrong secret key"""
    payload = {"sub": "user123", "exp": time.time() + 3600}

    token = generate_token(payload, secret="invalidsecret")
    response = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    
    assert response.status_code == 401
    assert response.json() == {"error": "Invalid Authorization Token Signature"}

def test_valid_token():
    """Valid token request passes"""
    payload = {"sub": "valid_user", "exp": time.time() + 3600}
    token = generate_token(payload)
    response = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "success"
    assert data["user"]["sub"] == "valid_user"