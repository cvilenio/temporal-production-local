"""Thin entrypoint — Orders REST API.

The FastAPI app lives in the shared kernel (orders_kernel.api); this app is a
shallow definition on top of it. Run with: uvicorn main:app
"""

from orders_kernel.api import app

__all__ = ["app"]
