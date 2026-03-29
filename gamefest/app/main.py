from fastapi import FastAPI
from datetime import datetime, UTC

app = FastAPI(
    title="Gamefest Service",
    version="0.1.0"
)

@app.get("/hello")
def hello():
    return {
        "service" : "gamefest-service",
        "version" : "0.1.0",
        "timestamp" : datetime.now(UTC).isoformat()
    }
