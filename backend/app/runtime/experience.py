"""Self-Healing Experience Memory for Jarvis OS (inspired by Proxima memory.db).

Stores and indexes error signatures, failing commands, and verified solution patches/diffs.
When commands, builds, or tests fail, the agent can query this database to instantly apply
solutions that worked previously instead of hallucinating or looping through trial-and-error.

Features:
- Regex-based error normalization (strips ephemeral timestamps, PIDs, absolute paths).
- Exponential Moving Average (EMA) scoring: confidence drops on failure, rises on success.
- SQLite FTS5 full-text search over error signatures, commands, and patch descriptions.
- Thread-safe with WAL mode.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PATH_RE = re.compile(r"/(?:Users|home|private|var|tmp)/[^\s:\"']+/([^/\s:\"']+)")
_TIMESTAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b")
_LINE_NUM_RE = re.compile(r'(?:line\s+\d+|:\d+:\d+|:\d+:)', re.IGNORECASE)
_HEX_ADDR_RE = re.compile(r"\b0x[0-9a-fA-F]{6,16}\b")


def normalize_error(raw_error: str) -> tuple[str, str]:
    """Clean error text to produce a canonical error signature and classify its type."""
    if not raw_error:
        return "", "general"

    lines = [ln.strip() for ln in raw_error.strip().splitlines() if ln.strip()]
    if not lines:
        return "", "general"

    # Identify primary error line (usually the last or containing an exception class)
    primary_line = lines[-1]
    for ln in reversed(lines):
        if any(kw in ln for kw in (
            "Error:", "Exception:", "FAILED", "fatal:", "panic:", "SyntaxError",
            "TypeError", "ModuleNotFoundError", "ImportError", "AttributeError",
            "KeyError", "IndexError", "ValueError", "RuntimeError", "AssertionError",
            "HTTP 4", "HTTP 5", "command not found", "No such file", "Permission denied"
        )):
            primary_line = ln
            break

    # Determine error type
    err_lower = primary_line.lower()
    if any(k in err_lower for k in ("modulenotfound", "importerror", "cannot import")):
        etype = "import_error"
    elif any(k in err_lower for k in ("syntaxerror", "unexpected eof", "parsing error")):
        etype = "syntax_error"
    elif any(k in err_lower for k in ("typeerror", "attributeerror", "keyerror", "valueerror")):
        etype = "runtime_type"
    elif any(k in err_lower for k in ("assert", "failed [", "test session starts")):
        etype = "test_failure"
    elif any(k in err_lower for k in ("build error", "compilation failed", "tsc", "vite")):
        etype = "build_error"
    elif any(k in err_lower for k in ("http 4", "http 5", "status_code", "connection refused")):
        etype = "network_api"
    elif any(k in err_lower for k in ("permission denied", "eacces", "forbidden")):
        etype = "permission_error"
    else:
        etype = "general"

    # Normalize paths: replace full path with basename
    cleaned = _PATH_RE.sub(r".../\1", primary_line)
    cleaned = _TIMESTAMP_RE.sub("<timestamp>", cleaned)
    cleaned = _HEX_ADDR_RE.sub("<addr>", cleaned)
    cleaned = _LINE_NUM_RE.sub(":<line>:", cleaned)

    return cleaned.strip(), etype


@dataclass(frozen=True)
class ExperienceSolution:
    id: str
    error_signature: str
    error_type: str
    failing_command: str
    solution_patch: str
    brain: str
    success_count: int
    failure_count: int
    confidence_score: float
    created_at: float
    updated_at: float
    last_used_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "error_signature": self.error_signature,
            "error_type": self.error_type,
            "failing_command": self.failing_command,
            "solution_patch": self.solution_patch,
            "brain": self.brain,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "confidence_score": round(self.confidence_score, 3),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_used_at": self.last_used_at,
        }


class ExperienceStore:
    """Persistent SQLite store with FTS5 indexing for self-healing error/fix patterns."""

    def __init__(self, state_dir: Path) -> None:
        self._db_path = state_dir / "experience_memory.sqlite3"
        self._lock = threading.RLock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS experience_solutions (
                    id TEXT PRIMARY KEY,
                    error_signature TEXT NOT NULL,
                    error_type TEXT NOT NULL,
                    failing_command TEXT NOT NULL DEFAULT '',
                    solution_patch TEXT NOT NULL,
                    brain TEXT NOT NULL DEFAULT 'global',
                    success_count INTEGER NOT NULL DEFAULT 1,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    confidence_score REAL NOT NULL DEFAULT 1.0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_used_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_exp_signature ON experience_solutions(error_signature);
                CREATE INDEX IF NOT EXISTS idx_exp_type ON experience_solutions(error_type);
                CREATE INDEX IF NOT EXISTS idx_exp_confidence ON experience_solutions(confidence_score DESC);

                CREATE VIRTUAL TABLE IF NOT EXISTS experience_solutions_fts USING fts5(
                    id UNINDEXED,
                    error_signature,
                    failing_command,
                    solution_patch,
                    tokenize='unicode61'
                );

                CREATE TRIGGER IF NOT EXISTS trg_exp_ai AFTER INSERT ON experience_solutions BEGIN
                    INSERT INTO experience_solutions_fts(id, error_signature, failing_command, solution_patch)
                    VALUES (new.id, new.error_signature, new.failing_command, new.solution_patch);
                END;

                CREATE TRIGGER IF NOT EXISTS trg_exp_ad AFTER DELETE ON experience_solutions BEGIN
                    DELETE FROM experience_solutions_fts WHERE id = old.id;
                END;

                CREATE TRIGGER IF NOT EXISTS trg_exp_au AFTER UPDATE ON experience_solutions BEGIN
                    DELETE FROM experience_solutions_fts WHERE id = old.id;
                    INSERT INTO experience_solutions_fts(id, error_signature, failing_command, solution_patch)
                    VALUES (new.id, new.error_signature, new.failing_command, new.solution_patch);
                END;
                """
            )

    def record_fix(
        self,
        error_text: str,
        solution_patch: str,
        *,
        failing_cmd: str = "",
        brain: str = "global",
    ) -> dict[str, Any]:
        """Record or reinforce a successful fix for an error signature."""
        sig, etype = normalize_error(error_text)
        if not sig:
            sig = error_text.strip()[:200]
        now = time.time()

        with self._lock, self._get_connection() as conn:
            # Check for existing match on normalized signature
            row = conn.execute(
                "SELECT * FROM experience_solutions WHERE error_signature = ? LIMIT 1",
                (sig,),
            ).fetchone()

            if row:
                old_conf = float(row["confidence_score"])
                old_succ = int(row["success_count"])
                # EMA boost: 70% current, 30% 1.0
                new_conf = min(1.0, old_conf * 0.7 + 1.0 * 0.3)
                conn.execute(
                    """
                    UPDATE experience_solutions
                    SET solution_patch = ?,
                        failing_command = CASE WHEN ? != '' THEN ? ELSE failing_command END,
                        success_count = ?,
                        confidence_score = ?,
                        updated_at = ?,
                        last_used_at = ?
                    WHERE id = ?
                    """,
                    (solution_patch.strip(), failing_cmd, failing_cmd, old_succ + 1, new_conf, now, now, row["id"]),
                )
                conn.commit()
                return self.get_solution(row["id"]) or {}

            # Insert new solution
            new_id = uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO experience_solutions(
                    id, error_signature, error_type, failing_command, solution_patch,
                    brain, success_count, failure_count, confidence_score,
                    created_at, updated_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, 0, 1.0, ?, ?, ?)
                """,
                (new_id, sig, etype, failing_cmd.strip(), solution_patch.strip(), brain, now, now, now),
            )
            conn.commit()
            return self.get_solution(new_id) or {}

    def find_solutions(
        self,
        error_text: str,
        *,
        failing_cmd: str = "",
        min_confidence: float = 0.3,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Find matching solutions by exact signature match or FTS5 ranking."""
        sig, _ = normalize_error(error_text)
        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        with self._lock, self._get_connection() as conn:
            # 1. Exact signature match
            if sig:
                rows = conn.execute(
                    """
                    SELECT * FROM experience_solutions
                    WHERE error_signature = ? AND confidence_score >= ?
                    ORDER BY confidence_score DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (sig, min_confidence, limit),
                ).fetchall()
                for r in rows:
                    seen_ids.add(r["id"])
                    results.append(self._row_to_dict(r))

            # 2. FTS5 query with tokens from error_text
            tokens = [re.sub(r"[^\w]", "", w) for w in (error_text + " " + failing_cmd).split()]
            valid_tokens = [t for t in tokens if len(t) > 3][:6]
            if valid_tokens and len(results) < limit:
                fts_query = " OR ".join(f'"{t}"' for t in valid_tokens)
                try:
                    fts_rows = conn.execute(
                        """
                        SELECT s.* FROM experience_solutions_fts f
                        JOIN experience_solutions s ON f.id = s.id
                        WHERE experience_solutions_fts MATCH ? AND s.confidence_score >= ?
                        ORDER BY s.confidence_score DESC, s.updated_at DESC
                        LIMIT ?
                        """,
                        (fts_query, min_confidence, limit * 2),
                    ).fetchall()
                    for r in fts_rows:
                        if r["id"] not in seen_ids:
                            seen_ids.add(r["id"])
                            results.append(self._row_to_dict(r))
                            if len(results) >= limit:
                                break
                except sqlite3.OperationalError:
                    pass

        return results

    def record_feedback(self, solution_id: str, success: bool) -> None:
        """Update confidence score when a remembered solution is tested."""
        now = time.time()
        with self._lock, self._get_connection() as conn:
            row = conn.execute("SELECT * FROM experience_solutions WHERE id = ?", (solution_id,)).fetchone()
            if not row:
                return
            old_conf = float(row["confidence_score"])
            succ = int(row["success_count"])
            fail = int(row["failure_count"])

            target = 1.0 if success else 0.0
            new_conf = max(0.05, min(1.0, old_conf * 0.7 + target * 0.3))

            conn.execute(
                """
                UPDATE experience_solutions
                SET success_count = ?,
                    failure_count = ?,
                    confidence_score = ?,
                    last_used_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (succ + (1 if success else 0), fail + (0 if success else 1), new_conf, now, now, solution_id),
            )
            conn.commit()

    def get_solution(self, solution_id: str) -> dict[str, Any] | None:
        with self._lock, self._get_connection() as conn:
            row = conn.execute("SELECT * FROM experience_solutions WHERE id = ?", (solution_id,)).fetchone()
            return self._row_to_dict(row) if row else None

    def list_solutions(self, limit: int = 50, error_type: str = "") -> list[dict[str, Any]]:
        with self._lock, self._get_connection() as conn:
            if error_type:
                rows = conn.execute(
                    """
                    SELECT * FROM experience_solutions
                    WHERE error_type = ?
                    ORDER BY confidence_score DESC, updated_at DESC LIMIT ?
                    """,
                    (error_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM experience_solutions ORDER BY confidence_score DESC, updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def delete_solution(self, solution_id: str) -> bool:
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM experience_solutions WHERE id = ?", (solution_id,))
            conn.commit()
            return cursor.rowcount > 0

    def stats(self) -> dict[str, Any]:
        with self._lock, self._get_connection() as conn:
            total = conn.execute("SELECT count(*) FROM experience_solutions").fetchone()[0]
            avg_conf = conn.execute("SELECT coalesce(avg(confidence_score), 0.0) FROM experience_solutions").fetchone()[0]
            by_type = dict(
                conn.execute(
                    "SELECT error_type, count(*) FROM experience_solutions GROUP BY error_type"
                ).fetchall()
            )
            return {
                "total_solutions": total,
                "average_confidence": round(float(avg_conf), 3),
                "by_type": by_type,
            }

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "error_signature": row["error_signature"],
            "error_type": row["error_type"],
            "failing_command": row["failing_command"],
            "solution_patch": row["solution_patch"],
            "brain": row["brain"],
            "success_count": int(row["success_count"]),
            "failure_count": int(row["failure_count"]),
            "confidence_score": round(float(row["confidence_score"]), 3),
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
            "last_used_at": float(row["last_used_at"]),
        }
