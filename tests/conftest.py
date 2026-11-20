"""Shared pytest fixtures for isolated state and brain directories."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.container import AppContainer
from app.main import create_app


@pytest.fixture()
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture()
def container(tmp_path: Path, project_root: Path) -> AppContainer:
    return AppContainer.build(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        host="127.0.0.1",
        port=7777,
        force_login=False,
    )


@pytest.fixture()
def client(container: AppContainer) -> TestClient:
    app = create_app(container)
    with TestClient(app) as tc:
        yield tc
