import uuid
from fastapi import FastAPI
from fastapi.testclient import TestClient
from requestIDMiddleware import RequestIDMiddleware


app = FastAPI()
app.add_middleware(RequestIDMiddleware)

@app.get("/test")
def example():
    return {"message": "ok"}


client = TestClient(app)


def testRequestIDHeaderIsMade():
    response = client.get("/test")

    assert response.status_code == 200
    assert "X-Request-ID" in response.headers


def testRequestIDIsValid():
    response = client.get("/test")
    request_id = response.headers["X-Request-ID"]

    uuid.UUID(request_id)


def testRequestIDIsUnique():
    response1 = client.get("/test")
    response2 = client.get("/test")

    ID1 = response1.headers["X-Request-ID"]
    ID2 = response2.headers["X-Request-ID"]

    assert ID1 != ID2


def testRequestIDInHeader():
    response = client.get("/test")

    assert response.headers["X-Request-ID"] is not None