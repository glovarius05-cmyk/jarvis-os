"""Conversation session persistence backed by SQLite (WAL + FTS5).

Why this exists: Every brain and engine shares a single engine-agnostic session store
so switching from OpenRouter to local Llama or Codex CLI preserves full chat history
and instant full-text search.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SessionRecord:
    id: str
    title: str
    brain: str
    channel: str
    pinned_provider: str
    pinned_model: str
    created_at: float
    updated_at: float
    message_count: int = 0


@dataclass(frozen=True)
class MessageRecord:
    id: int
    session_id: str
    role: str
    content: str
    provider: str
    model: str
    meta: dict[str, Any]
    created_at: float


@dataclass(frozen=True)
class SearchHit:
    session_id: str
    session_title: str
    message_id: int
    role: str
    snippet: str
    created_at: float


class SessionStore:
    """Thread-safe SQLite store with WAL mode and FTS5 message search."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.RLock()
        self._fts_enabled = False
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def fts_enabled(self) -> bool:
        return self._fts_enabled

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=15.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    brain TEXT NOT NULL,
                    channel TEXT NOT NULL DEFAULT 'web',
                    pinned_provider TEXT NOT NULL DEFAULT '',
                    pinned_model TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_brain_updated
                    ON sessions(brain, updated_at DESC);

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    meta_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session_id
                    ON messages(session_id, id ASC);
                """
            )
            try:
                conn.executescript(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                        content,
                        content='messages',
                        content_rowid='id',
                        tokenize='unicode61'
                    );
                    CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                        INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
                    END;
                    CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                        INSERT INTO messages_fts(messages_fts, rowid, content)
                        VALUES('delete', old.id, old.content);
                    END;
                    CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                        INSERT INTO messages_fts(messages_fts, rowid, content)
                        VALUES('delete', old.id, old.content);
                        INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
                    END;
                    """
                )
                self._fts_enabled = True
            except sqlite3.OperationalError:
                self._fts_enabled = False

    def create_session(
        self,
        brain: str,
        title: str = "New chat",
        channel: str = "web",
        pinned_provider: str = "",
        pinned_model: str = "",
        session_id: str | None = None,
    ) -> SessionRecord:
        sid = session_id or uuid.uuid4().hex
        now = time.time()
        clean_title = (title or "New chat").strip()[:120] or "New chat"
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions(id, title, brain, channel, pinned_provider, pinned_model, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sid, clean_title, brain, channel, pinned_provider, pinned_model, now, now),
            )
        return SessionRecord(
            id=sid,
            title=clean_title,
            brain=brain,
            channel=channel,
            pinned_provider=pinned_provider,
            pinned_model=pinned_model,
            created_at=now,
            updated_at=now,
            message_count=0,
        )

    def get_session(self, session_id: str) -> SessionRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT s.*, COUNT(m.id) AS message_count
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                WHERE s.id = ?
                GROUP BY s.id
                """,
                (session_id,),
            ).fetchone()
            return self._row_to_session(row) if row else None

    def list_sessions(self, brain: str | None = None, channel: str | None = None, limit: int = 50) -> list[SessionRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if brain:
            clauses.append("s.brain = ?")
            params.append(brain)
        if channel:
            clauses.append("s.channel = ?")
            params.append(channel)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 200)))

        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT s.*, COUNT(m.id) AS message_count
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                {where}
                GROUP BY s.id
                ORDER BY s.updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [self._row_to_session(r) for r in rows]

    def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        pinned_provider: str | None = None,
        pinned_model: str | None = None,
    ) -> SessionRecord | None:
        fields: list[str] = []
        params: list[Any] = []
        if title is not None:
            fields.append("title = ?")
            params.append(title.strip()[:120] or "New chat")
        if pinned_provider is not None:
            fields.append("pinned_provider = ?")
            params.append(pinned_provider)
        if pinned_model is not None:
            fields.append("pinned_model = ?")
            params.append(pinned_model)
        if not fields:
            return self.get_session(session_id)

        fields.append("updated_at = ?")
        params.append(time.time())
        params.append(session_id)

        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE sessions SET {', '.join(fields)} WHERE id = ?", params)
        return self.get_session(session_id)

    def delete_session(self, session_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return cur.rowcount > 0

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        provider: str = "",
        model: str = "",
        meta: dict[str, Any] | None = None,
    ) -> MessageRecord:
        now = time.time()
        meta_dict = meta or {}
        meta_json = json.dumps(meta_dict, ensure_ascii=False)
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO messages(session_id, role, content, provider, model, meta_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, role, content, provider, model, meta_json, now),
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
            msg_id = int(cur.lastrowid)
        return MessageRecord(
            id=msg_id,
            session_id=session_id,
            role=role,
            content=content,
            provider=provider,
            model=model,
            meta=meta_dict,
            created_at=now,
        )

    def list_messages(self, session_id: str, limit: int = 200) -> list[MessageRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM messages
                WHERE session_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (session_id, max(1, min(limit, 1000))),
            ).fetchall()
            return [self._row_to_message(r) for r in rows]

    def search_messages(self, query: str, brain: str | None = None, limit: int = 20) -> list[SearchHit]:
        clean = (query or "").strip()
        if not clean:
            return []
        cap = max(1, min(limit, 100))

        with self._lock, self._connect() as conn:
            if self._fts_enabled:
                safe_fts = '"' + clean.replace('"', '""') + '"'
                try:
                    if brain:
                        rows = conn.execute(
                            """
                            SELECT m.id AS message_id, m.session_id, s.title AS session_title,
                                   m.role, snippet(messages_fts, 0, '[', ']', '...', 12) AS snippet, m.created_at
                            FROM messages_fts
                            JOIN messages m ON m.id = messages_fts.rowid
                            JOIN sessions s ON s.id = m.session_id
                            WHERE messages_fts MATCH ? AND s.brain = ?
                            ORDER BY bm25(messages_fts)
                            LIMIT ?
                            """,
                            (safe_fts, brain, cap),
                        ).fetchall()
                    else:
                        rows = conn.execute(
                            """
                            SELECT m.id AS message_id, m.session_id, s.title AS session_title,
                                   m.role, snippet(messages_fts, 0, '[', ']', '...', 12) AS snippet, m.created_at
                            FROM messages_fts
                            JOIN messages m ON m.id = messages_fts.rowid
                            JOIN sessions s ON s.id = m.session_id
                            WHERE messages_fts MATCH ?
                            ORDER BY bm25(messages_fts)
                            LIMIT ?
                            """,
                            (safe_fts, cap),
                        ).fetchall()
                    return [self._row_to_hit(r) for r in rows]
                except sqlite3.OperationalError:
                    pass

            # Fallback LIKE query
            like = f"%{clean}%"
            if brain:
                rows = conn.execute(
                    """
                    SELECT m.id AS message_id, m.session_id, s.title AS session_title,
                           m.role, substr(m.content, 1, 160) AS snippet, m.created_at
                    FROM messages m
                    JOIN sessions s ON s.id = m.session_id
                    WHERE m.content LIKE ? AND s.brain = ?
                    ORDER BY m.created_at DESC
                    LIMIT ?
                    """,
                    (like, brain, cap),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT m.id AS message_id, m.session_id, s.title AS session_title,
                           m.role, substr(m.content, 1, 160) AS snippet, m.created_at
                    FROM messages m
                    JOIN sessions s ON s.id = m.session_id
                    WHERE m.content LIKE ?
                    ORDER BY m.created_at DESC
                    LIMIT ?
                    """,
                    (like, cap),
                ).fetchall()
            return [self._row_to_hit(r) for r in rows]

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> SessionRecord:
        return SessionRecord(
            id=row["id"],
            title=row["title"],
            brain=row["brain"],
            channel=row["channel"],
            pinned_provider=row["pinned_provider"],
            pinned_model=row["pinned_model"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            message_count=int(row["message_count"]),
        )

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> MessageRecord:
        try:
            meta = json.loads(row["meta_json"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        return MessageRecord(
            id=int(row["id"]),
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            provider=row["provider"],
            model=row["model"],
            meta=meta if isinstance(meta, dict) else {},
            created_at=float(row["created_at"]),
        )

    @staticmethod
    def _row_to_hit(row: sqlite3.Row) -> SearchHit:
        return SearchHit(
            session_id=row["session_id"],
            session_title=row["session_title"],
            message_id=int(row["message_id"]),
            role=row["role"],
            snippet=str(row["snippet"]),
            created_at=float(row["created_at"]),
        )
