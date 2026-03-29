from fastapi import FastAPI
from app.middleware.logging_middleware import LoggingMiddleware

app = FastAPI(
    title="Gateway Service",
    version="0.1.0"
)

app.add_middleware(LoggingMiddleware)

@app.get("/health")
def health():
    return {"service": "gateway", "status": "ok"}