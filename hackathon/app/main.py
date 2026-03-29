from fastapi import FastAPI
from datetime import datetime

app = FastAPI(
    title="Hackathon Service",
    version="0.1.0"
)

@app.get("/hello")
def hello():
    return {
        "service": "hackathon-service",
        "version": "0.1.0",
        "timestamp": datetime.utcnow().isoformat()
    }