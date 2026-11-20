"""Unit tests for Bi-directional Second Brain note watcher (Obsidian Sync)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.brain.hybrid_search import HybridIndexStore
from app.brain.vault import VaultManager
from app.brain.watcher import VaultWatcherService, is_watchable_note
from app.container import AppContainer
from app.main import create_app


def test_is_watchable_note_filters_temporary_and_system_files():
    assert is_watchable_note(Path("wiki/arch.md")) is True
    assert is_watchable_note(Path("sources/deepseek.md")) is True
    assert is_watchable_note(Path("memory/facts/user.md")) is True

    # Ignored system and temporary files
    assert is_watchable_note(Path(".git/config")) is False
    assert is_watchable_note(Path(".obsidian/workspace.json")) is False
    assert is_watchable_note(Path(".DS_Store")) is False
    assert is_watchable_note(Path("wiki/.DS_Store")) is False
    assert is_watchable_note(Path("Javis/loop-state.json")) is False
    assert is_watchable_note(Path("Javis/reminders.json")) is False
    assert is_watchable_note(Path("wiki/draft.md.tmp")) is False
    assert is_watchable_note(Path("wiki/draft.md.swp")) is False
    assert is_watchable_note(Path("attachments/diagram.png")) is False


@pytest.mark.asyncio
async def test_watcher_indexes_new_note_and_handles_deletion(tmp_path: Path):
    brains_dir = tmp_path / "brains"
    vaults = VaultManager(brains_dir)
    brain_name = "WatcherTestBrain"
    vaults.ensure_brain(brain_name, init_git=False)

    index_store = HybridIndexStore(tmp_path / "state" / "index.sqlite3")
    watcher = VaultWatcherService(
        vaults=vaults,
        hybrid_index=index_store,
        brains_dir=brains_dir,
        debounce_seconds=0.05,
    )

    # 1. Create a new note and trigger watcher change handler
    rel_path = "wiki/quantum-computing.md"
    note_content = (
        "# Quantum Computing\n\n"
        "Superposition and quantum entanglement enable exponential speedups for specific algorithms."
    )
    vaults.write_text(brain_name, rel_path, note_content)

    await watcher.handle_change(brain_name, rel_path, "added")
    # Wait for debounce
    await asyncio.sleep(0.15)

    # Verify note is indexed in hybrid search
    hits = index_store.search(brain_name, "entanglement")
    assert len(hits) >= 1
    assert hits[0]["path"] == rel_path

    # Verify watcher metrics
    status = watcher.status()
    assert status["total_events_processed"] == 1
    assert status["last_synced_path"] == rel_path

    # 2. Modify the note
    updated_content = (
        "# Quantum Computing\n\n"
        "Qubits can be in superposition states. Shor algorithm factors large primes in polynomial time."
    )
    vaults.write_text(brain_name, rel_path, updated_content)
    await watcher.handle_change(brain_name, rel_path, "modified")
    await asyncio.sleep(0.15)

    hits_shor = index_store.search(brain_name, "Shor algorithm polynomial")
    assert len(hits_shor) >= 1
    assert hits_shor[0]["path"] == rel_path

    # 3. Delete the note
    (brains_dir / brain_name / rel_path).unlink()
    await watcher.handle_change(brain_name, rel_path, "deleted")
    await asyncio.sleep(0.15)

    # Verify note is removed from hybrid search index
    hits_after_delete = index_store.search(brain_name, "entanglement")
    assert len(hits_after_delete) == 0


@pytest.mark.asyncio
async def test_watcher_trigger_full_sync(tmp_path: Path):
    brains_dir = tmp_path / "brains"
    vaults = VaultManager(brains_dir)
    brain_name = "SyncAllBrain"
    vaults.ensure_brain(brain_name, init_git=False)

    vaults.write_text(brain_name, "wiki/note1.md", "# Note One\nFirst topic content.")
    vaults.write_text(brain_name, "wiki/note2.md", "# Note Two\nSecond topic content.")

    index_store = HybridIndexStore(tmp_path / "state" / "index.sqlite3")
    watcher = VaultWatcherService(
        vaults=vaults,
        hybrid_index=index_store,
        brains_dir=brains_dir,
    )

    result = await watcher.trigger_full_sync(brain=brain_name)
    assert result["ok"] is True
    assert brain_name in result["brains_synced"]
    assert result["indexed_files"] >= 2

    # Check search hits
    hits = index_store.search(brain_name, "First topic")
    assert len(hits) >= 1


def test_watcher_api_endpoints(tmp_path: Path):
    state_dir = tmp_path / "state"
    brains_dir = tmp_path / "brains"
    container = AppContainer.build(
        state_dir=state_dir,
        brains_dir=brains_dir,
        force_login=False,
    )
    app = create_app(container, enable_scheduler=False)
    client = TestClient(app)

    # Test GET /api/brains/sync/status
    res = client.get("/api/brains/sync/status")
    assert res.status_code == 200
    data = res.json()
    assert data["enabled"] is True
    assert "running" in data
    assert "total_events_processed" in data

    # Test POST /api/brains/sync/trigger
    res_trigger = client.post("/api/brains/sync/trigger", json={})
    assert res_trigger.status_code == 200
    trigger_data = res_trigger.json()
    assert trigger_data["ok"] is True
    assert len(trigger_data["brains_synced"]) >= 1
