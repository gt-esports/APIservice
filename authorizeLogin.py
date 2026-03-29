from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime, timedelta
import jwt
app = FastAPI()

DevSecret = "herro123"
ALGORITHM = "HS256"

class LoginRequest(BaseModel):
    username: str
    role: str | None = None

def createAccessToken(username: str, role: str | None) -> str:
    time = datetime.now()
    payload = {
        "sub": username,
        "role": role or "user",
        "iat": int(time.timestamp()),
        "exp": int((time + timedelta(hours=1)).timestamp())

    }
    return jwt.encode(payload, DevSecret, algorithm = ALGORITHM)


@app.post("/login")
def login(loginRequest: LoginRequest):
    accessToken = createAccessToken(loginRequest.username, loginRequest.role)
    return {"accessToken": accessToken}
