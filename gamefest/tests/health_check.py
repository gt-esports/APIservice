from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_hello_success():
    response = client.get('/hello')
    assert response.status_code == 200

def test_hello_structure():
    res = client.get('/hello').json()
    assert 'service' in res
    assert 'version' in res
    assert 'timestamp' in res

def test_hello_correct_service():
    response = client.get('/hello')
    assert response.json()['service'] == 'gamefest-service'

