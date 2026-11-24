"""OpenTelemetry-compatible local trace & span observability store (SQLite WAL).

Why this exists: Agentic turns combine LLM streaming, tool calls, fallbacks, and
compaction. Recording every turn and span locally in SQLite (while also emitting
standard OpenTelemetry spans via opentelemetry-api) gives instant, zero-dependency
observability with full compatibility for Langfuse / OTLP collectors.
"""

from __future__ import annotations

import contextvars
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

try:
    from opentelemetry import trace as otel_trace
except ImportError:  # pragma: no cover
    otel_trace = None  # type: ignore[assignment]

_ACTIVE_TRACE_ID: contextvars.ContextVar[str] = contextvars.ContextVar("jarvis_active_trace_id", default="")
_ACTIVE_SPAN_ID: contextvars.ContextVar[str] = contextvars.ContextVar("jarvis_active_span_id", default="")


def current_trace_id() -> str:
    return _ACTIVE_TRACE_ID.get()


def current_span_id() -> str:
    return _ACTIVE_SPAN_ID.get()


@dataclass(frozen=True)
class TraceHandle:
    trace_id: str
    root_span_id: str
    started_at: float


class TraceStore:
    """Thread-safe SQLite trace & span store with OpenTelemetry API bridge."""

    def __init__(self, state_dir: Path, *, max_traces: int = 2000) -> None:
        self._db_path = state_dir / "traces.sqlite3"
        self._max_traces = max(100, int(max_traces))
        self._lock = threading.RLock()
        self._tracer = otel_trace.get_tracer("jarvis-os") if otel_trace is not None else None
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL DEFAULT '',
                    brain TEXT NOT NULL DEFAULT '',
                    channel TEXT NOT NULL DEFAULT 'web',
                    provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    run_mode TEXT NOT NULL DEFAULT 'full',
                    status TEXT NOT NULL DEFAULT 'running',
                    started_at REAL NOT NULL,
                    ended_at REAL,
                    duration_ms REAL NOT NULL DEFAULT 0.0,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    meta_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_traces_started ON traces(started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_traces_session ON traces(session_id, started_at DESC);

                CREATE TABLE IF NOT EXISTS spans (
                    span_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL REFERENCES traces(trace_id) ON DELETE CASCADE,
                    parent_span_id TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'internal',
                    status TEXT NOT NULL DEFAULT 'ok',
                    started_at REAL NOT NULL,
                    ended_at REAL,
                    duration_ms REAL NOT NULL DEFAULT 0.0,
                    attributes_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id, started_at ASC);
                """
            )

    def start_trace(
        self,
        *,
        session_id: str = "",
        brain: str = "",
        channel: str = "web",
        provider: str = "",
        model: str = "",
        run_mode: str = "full",
        meta: dict[str, Any] | None = None,
    ) -> TraceHandle:
        trace_id = uuid.uuid4().hex
        root_span_id = uuid.uuid4().hex[:16]
        now = time.time()
        meta_json = json.dumps(meta or {}, ensure_ascii=False)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO traces(
                    trace_id, session_id, brain, channel, provider, model, run_mode,
                    status, started_at, meta_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (trace_id, session_id, brain, channel, provider, model, run_mode, now, meta_json),
            )
            # Prune oldest traces beyond retention cap
            conn.execute(
                """
                DELETE FROM traces WHERE trace_id IN (
                    SELECT trace_id FROM traces ORDER BY started_at DESC LIMIT -1 OFFSET ?
                )
                """,
                (self._max_traces,),
            )
        return TraceHandle(trace_id=trace_id, root_span_id=root_span_id, started_at=now)

    def finish_trace(
        self,
        handle: TraceHandle,
        *,
        status: str = "ok",
        provider: str | None = None,
        model: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        error: str = "",
        meta: dict[str, Any] | None = None,
    ) -> None:
        ended_at = time.time()
        duration_ms = max(0.0, (ended_at - handle.started_at) * 1000.0)
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT provider, model, meta_json FROM traces WHERE trace_id = ?", (handle.trace_id,)).fetchone()
            if row is None:
                return
            merged_meta: dict[str, Any] = {}
            try:
                loaded = json.loads(row["meta_json"] or "{}")
                if isinstance(loaded, dict):
                    merged_meta.update(loaded)
            except json.JSONDecodeError:
                pass
            if meta:
                merged_meta.update(meta)
            conn.execute(
                """
                UPDATE traces
                SET status = ?,
                    provider = ?,
                    model = ?,
                    ended_at = ?,
                    duration_ms = ?,
                    input_tokens = ?,
                    output_tokens = ?,
                    error = ?,
                    meta_json = ?
                WHERE trace_id = ?
                """,
                (
                    status,
                    provider if provider is not None else row["provider"],
                    model if model is not None else row["model"],
                    ended_at,
                    round(duration_ms, 2),
                    max(0, int(input_tokens or 0)),
                    max(0, int(output_tokens or 0)),
                    (error or "")[:2000],
                    json.dumps(merged_meta, ensure_ascii=False),
                    handle.trace_id,
                ),
            )

    def record_span(
        self,
        *,
        name: str,
        kind: str = "internal",
        trace_id: str = "",
        parent_span_id: str = "",
        status: str = "ok",
        duration_ms: float = 0.0,
        started_at: float | None = None,
        attributes: dict[str, Any] | None = None,
        error: str = "",
    ) -> str | None:
        tid = trace_id or current_trace_id()
        if not tid:
            return None
        pid = parent_span_id or current_span_id()
        span_id = uuid.uuid4().hex[:16]
        now = time.time()
        start = started_at if started_at is not None else max(0.0, now - (duration_ms / 1000.0))
        attrs_json = json.dumps(attributes or {}, ensure_ascii=False, default=str)

        if self._tracer is not None:
            try:
                with self._tracer.start_as_current_span(name) as otel_span:
                    otel_span.set_attribute("jarvis.trace_id", tid)
                    otel_span.set_attribute("jarvis.kind", kind)
                    otel_span.set_attribute("jarvis.status", status)
                    for k, v in (attributes or {}).items():
                        if isinstance(v, (str, int, float, bool)):
                            otel_span.set_attribute(f"jarvis.{k}", v)
            except Exception:  # noqa: BLE001
                pass

        with self._lock, self._connect() as conn:
            exists = conn.execute("SELECT 1 FROM traces WHERE trace_id = ?", (tid,)).fetchone()
            if exists is None:
                return None
            conn.execute(
                """
                INSERT INTO spans(
                    span_id, trace_id, parent_span_id, name, kind, status,
                    started_at, ended_at, duration_ms, attributes_json, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    span_id,
                    tid,
                    pid,
                    name,
                    kind,
                    status,
                    start,
                    now,
                    round(max(0.0, float(duration_ms)), 2),
                    attrs_json,
                    (error or "")[:2000],
                ),
            )
        return span_id

    @contextmanager
    def bind_trace(self, handle: TraceHandle) -> Iterator[TraceHandle]:
        """Bind trace_id / root_span_id into contextvars for nested tool & engine calls."""
        t_tok = _ACTIVE_TRACE_ID.set(handle.trace_id)
        s_tok = _ACTIVE_SPAN_ID.set(handle.root_span_id)
        try:
            yield handle
        finally:
            _ACTIVE_TRACE_ID.reset(t_tok)
            _ACTIVE_SPAN_ID.reset(s_tok)

    def list_traces(
        self,
        *,
        brain: str | None = None,
        session_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if brain:
            clauses.append("t.brain = ?")
            params.append(brain)
        if session_id:
            clauses.append("t.session_id = ?")
            params.append(session_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 200)))

        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT t.*, COUNT(s.span_id) AS span_count
                FROM traces t
                LEFT JOIN spans s ON s.trace_id = t.trace_id
                {where}
                GROUP BY t.trace_id
                ORDER BY t.started_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [self._row_to_trace(r) for r in rows]

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            trow = conn.execute(
                """
                SELECT t.*, COUNT(s.span_id) AS span_count
                FROM traces t
                LEFT JOIN spans s ON s.trace_id = t.trace_id
                WHERE t.trace_id = ?
                GROUP BY t.trace_id
                """,
                (trace_id,),
            ).fetchone()
            if trow is None:
                return None
            srows = conn.execute(
                """
                SELECT * FROM spans
                WHERE trace_id = ?
                ORDER BY started_at ASC
                """,
                (trace_id,),
            ).fetchall()
            trace_dict = self._row_to_trace(trow)
            trace_dict["spans"] = [self._row_to_span(r) for r in srows]
            return trace_dict

    def summary(self, *, days: int = 7, brain: str | None = None) -> dict[str, Any]:
        cutoff = time.time() - max(1, int(days)) * 86400.0
        where = "WHERE started_at >= ?" + (" AND brain = ?" if brain else "")
        params: list[Any] = [cutoff] + ([brain] if brain else [])

        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT status, duration_ms, input_tokens, output_tokens FROM traces {where}",
                params,
            ).fetchall()
            tool_spans = conn.execute(
                f"""
                SELECT COUNT(*) AS cnt,
                       SUM(CASE WHEN s.status != 'ok' THEN 1 ELSE 0 END) AS err_cnt
                FROM spans s
                JOIN traces t ON t.trace_id = s.trace_id
                {where.replace('started_at', 't.started_at').replace('brain', 't.brain')}
                AND s.kind = 'tool'
                """,
                params,
            ).fetchone()
            fallback_spans = conn.execute(
                f"""
                SELECT COUNT(*) AS cnt
                FROM spans s
                JOIN traces t ON t.trace_id = s.trace_id
                {where.replace('started_at', 't.started_at').replace('brain', 't.brain')}
                AND s.kind = 'fallback'
                """,
                params,
            ).fetchone()

        total = len(rows)
        errors = sum(1 for r in rows if r["status"] == "error")
        durations = sorted(float(r["duration_ms"] or 0.0) for r in rows)
        p50 = durations[len(durations) // 2] if durations else 0.0
        p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else 0.0
        return {
            "days": days,
            "total_traces": total,
            "error_traces": errors,
            "error_rate": round(errors / total, 4) if total else 0.0,
            "latency_p50_ms": round(p50, 2),
            "latency_p95_ms": round(p95, 2),
            "total_input_tokens": sum(int(r["input_tokens"] or 0) for r in rows),
            "total_output_tokens": sum(int(r["output_tokens"] or 0) for r in rows),
            "tool_calls": int(tool_spans["cnt"] or 0) if tool_spans else 0,
            "tool_errors": int(tool_spans["err_cnt"] or 0) if tool_spans else 0,
            "fallback_events": int(fallback_spans["cnt"] or 0) if fallback_spans else 0,
        }

    @staticmethod
    def _row_to_trace(row: sqlite3.Row) -> dict[str, Any]:
        try:
            meta = json.loads(row["meta_json"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        return {
            "trace_id": row["trace_id"],
            "session_id": row["session_id"],
            "brain": row["brain"],
            "channel": row["channel"],
            "provider": row["provider"],
            "model": row["model"],
            "run_mode": row["run_mode"],
            "status": row["status"],
            "started_at": float(row["started_at"]),
            "ended_at": float(row["ended_at"]) if row["ended_at"] is not None else None,
            "duration_ms": float(row["duration_ms"] or 0.0),
            "input_tokens": int(row["input_tokens"] or 0),
            "output_tokens": int(row["output_tokens"] or 0),
            "error": row["error"],
            "meta": meta if isinstance(meta, dict) else {},
            "span_count": int(row["span_count"]) if "span_count" in row.keys() else 0,
        }

    @staticmethod
    def _row_to_span(row: sqlite3.Row) -> dict[str, Any]:
        try:
            attrs = json.loads(row["attributes_json"] or "{}")
        except json.JSONDecodeError:
            attrs = {}
        return {
            "span_id": row["span_id"],
            "trace_id": row["trace_id"],
            "parent_span_id": row["parent_span_id"],
            "name": row["name"],
            "kind": row["kind"],
            "status": row["status"],
            "started_at": float(row["started_at"]),
            "ended_at": float(row["ended_at"]) if row["ended_at"] is not None else None,
            "duration_ms": float(row["duration_ms"] or 0.0),
            "attributes": attrs if isinstance(attrs, dict) else {},
            "error": row["error"],
        }
