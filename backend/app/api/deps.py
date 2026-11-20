"""FastAPI dependency helpers for accessing AppContainer without importing app.main."""

from __future__ import annotations

from fastapi import Request

from app.container import AppContainer


def get_container(request: Request) -> AppContainer:
    return request.app.state.container
