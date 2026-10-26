"""ASGI entrypoint shared by Vercel, Uvicorn, and the container."""

from apps.gateway.main import app

__all__ = ["app"]
