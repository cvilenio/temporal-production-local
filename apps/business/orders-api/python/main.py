"""Thin entrypoint — Orders REST API.

The FastAPI app lives in the shared kernel (orders.api); this app is a
shallow definition on top of it. Run with: uvicorn main:app
"""

from orders.api import app

__all__ = ["app"]
