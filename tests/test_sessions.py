"""Tests for SQLite session persistence and FTS5 full-text search."""

from __future__ import annotations

from pathlib import Path

from app.runtime.sessions import SessionStore


def test_sessions_crud_and_fts5_search(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "conversations.db")
    assert store.fts_enabled is True

    s1 = store.create_session(brain="Brain Default", title="Планирование Jarvis")
    store.add_message(s1.id, "user", "Настрой локальную Llama через Ollama", provider="local-llama", model="llama3.1:8b")
    store.add_message(s1.id, "assistant", "Готово, локальный движок подключён.", provider="local-llama", model="llama3.1:8b")

    s2 = store.create_session(brain="Work", title="Other brain")
    store.add_message(s2.id, "user", "Unrelated note about Ollama")

    hits_default = store.search_messages("Ollama", brain="Brain Default")
    assert len(hits_default) == 1
    assert hits_default[0].session_id == s1.id

    hits_cyrillic = store.search_messages("локальную", brain="Brain Default")
    assert len(hits_cyrillic) == 1
    assert hits_cyrillic[0].session_id == s1.id

    updated = store.update_session(s1.id, title="Jarvis Setup", pinned_provider="codex")
    assert updated is not None
    assert updated.title == "Jarvis Setup"
    assert updated.pinned_provider == "codex"
    assert updated.message_count == 2

    assert store.delete_session(s1.id) is True
    assert store.get_session(s1.id) is None
    assert store.list_messages(s1.id) == []
