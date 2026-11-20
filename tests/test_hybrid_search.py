"""Tests for Hybrid Semantic Search (BM25 + Vector Embeddings via Reciprocal Rank Fusion)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from starlette.testclient import TestClient

from app.brain.hybrid_search import (
    HybridIndexStore,
    LocalDenseEmbeddingProvider,
    chunk_markdown,
)
from app.brain.vault import VaultManager
from app.container import AppContainer
from app.hub.builtin_tools import build_builtin_tools
from app.hub.shaper import ToolResultStore
from app.main import create_app
from app.security.secrets import SecretStore


def test_chunk_markdown_splits_headings_and_paragraphs() -> None:
    text = """# Architecture Overview
This is the main introduction to the system architecture.

## Database Layer
We use PostgreSQL with streaming replication for high availability.
WAL archiving is enabled for disaster recovery.

## API Gateway
FastAPI handles incoming HTTP requests and WebSocket connections.
"""
    chunks = chunk_markdown(text, "wiki/architecture.md", max_chunk_chars=300)
    assert len(chunks) == 3
    assert chunks[0].heading == "Architecture Overview"
    assert chunks[1].heading == "Database Layer"
    assert chunks[2].heading == "API Gateway"
    assert "PostgreSQL with streaming replication" in chunks[1].content


def test_local_dense_embeddings_semantic_similarity() -> None:
    embedder = LocalDenseEmbeddingProvider(dim=384)

    v1 = embedder.embed("отказоустойчивость баз данных postgres")
    v2 = embedder.embed("настройка отказоустойчивости postgresql replication")
    v3 = embedder.embed("рецепт приготовления кофе капучино")

    assert v1.shape == (384,)
    assert abs(float(np.linalg.norm(v1)) - 1.0) < 1e-4

    sim_related = float(np.dot(v1, v2))
    sim_unrelated = float(np.dot(v1, v3))

    assert sim_related > sim_unrelated
    assert sim_related > 0.3


def test_hybrid_index_store_indexing_and_search(tmp_path: Path) -> None:
    vaults = VaultManager(tmp_path / "brains")
    vaults.ensure_brain("Brain Default", init_git=False)

    vaults.write_text(
        "Brain Default",
        "wiki/databases.md",
        "# Database Reliability\nPostgres clustering, streaming replication, and failover automation.",
    )
    vaults.write_text(
        "Brain Default",
        "wiki/coffee.md",
        "# Coffee Brewing\nEspresso extraction, grind size, and milk steaming guide.",
    )

    db_path = tmp_path / "state" / "hybrid_index.sqlite3"
    idx = HybridIndexStore(db_path)

    # Index brain
    res = idx.index_brain(vaults, "Brain Default")
    assert res["indexed_files"] >= 2
    assert res["total_chunks"] >= 2

    # Search for database failover
    hits_db = idx.search("Brain Default", "отказоустойчивость postgres replication")
    assert len(hits_db) > 0
    top_hit = hits_db[0]
    assert "wiki/databases.md" == top_hit["path"]
    assert top_hit["score"] > 0
    assert top_hit["match_type"] in {"hybrid", "semantic", "keyword"}

    # Search for coffee
    hits_coffee = idx.search("Brain Default", "espresso extraction")
    assert len(hits_coffee) > 0
    assert "wiki/coffee.md" == hits_coffee[0]["path"]


@pytest.mark.asyncio
async def test_builtin_jarvis_search_tool(tmp_path: Path) -> None:
    vaults = VaultManager(tmp_path / "brains")
    vaults.ensure_brain("Brain Default", init_git=False)
    vaults.write_text(
        "Brain Default",
        "sources/docker.md",
        "# Docker Containers\nContainer isolation, cgroups, and overlayfs storage drivers.",
    )

    db_path = tmp_path / "state" / "hybrid_index.sqlite3"
    idx = HybridIndexStore(db_path)
    res_store = ToolResultStore(tmp_path / "state", SecretStore(tmp_path / "secrets"))

    tools = build_builtin_tools(
        vaults=vaults,
        result_store=res_store,
        list_connections_fn=lambda: [],
        hybrid_index_getter=lambda: idx,
    )
    tool_map = {t.name: t for t in tools}
    assert "jarvis_search" in tool_map
    assert "jarvis_reindex" in tool_map

    search_handler = tool_map["jarvis_search"].handler
    res = await search_handler({"query": "cgroups container storage"}, "Brain Default")
    assert "sources/docker.md" in res
    assert "Relevance:" in res

    reindex_handler = tool_map["jarvis_reindex"].handler
    reindex_res = await reindex_handler({"force": True}, "Brain Default")
    assert "hybrid indexing complete" in reindex_res


def test_hybrid_search_api_endpoints(tmp_path: Path) -> None:
    container = AppContainer.build(
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        force_login=False,
    )
    container.vaults.ensure_brain("Brain Default", init_git=False)
    container.vaults.write_text(
        "Brain Default",
        "wiki/security.md",
        "# Security Guard\nTOTP multi-factor authentication, Fernet secret encryption, and SSRF prevention.",
    )

    app = create_app(container)
    client = TestClient(app)

    # POST /api/brains/reindex
    r_idx = client.post("/api/brains/reindex", json={"force": True})
    assert r_idx.status_code == 200
    assert r_idx.json()["ok"] is True

    # GET /api/brains/search
    r_search = client.get("/api/brains/search?query=TOTP+multi-factor+authentication")
    assert r_search.status_code == 200
    data = r_search.json()
    assert data["total"] > 0
    assert any("wiki/security.md" in h["path"] for h in data["hits"])
