"""Tool Result Shaper, encrypted short-lived result cache, and read-only SQL query tool.

Why this exists: Real MCP connectors (POS orders, CRM lists, web pages) routinely return
100-250 KB JSON payloads. Blind truncation slices JSON mid-record; sending it whole burns
the context window. Instead, large results are saved encrypted (24h TTL, brain-scoped),
the model receives a clean preview of WHOLE records + ref_id, and `jarvis_query` lets it
paginate fields or run one read-only SQL query over all stored records.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.security.secrets import SecretStore

MIN_SHAPE_CHARS = 1_200
INLINE_MAX_CHARS = 8_000
PREVIEW_BUDGET_CHARS = 5_000
TTL_SECONDS = 24 * 3600

_SQL_FORBIDDEN_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|reindex)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ShapedResult:
    text: str
    raw_chars: int
    kind: str  # "small" | "error" | "compact" | "preview" | "text_preview"
    ref_id: str = ""


class ToolResultStore:
    """Encrypted SQLite store for full tool results (24h TTL, brain-scoped)."""

    def __init__(self, state_dir: Path, secrets: SecretStore) -> None:
        self._db_path = state_dir / "tool_results.sqlite3"
        self._secrets = secrets
        self._lock = threading.RLock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stored_results (
                    ref_id TEXT PRIMARY KEY,
                    brain TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    payload_enc TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )

    def put(self, *, brain: str, tool_name: str, payload: str, ttl_seconds: int = TTL_SECONDS) -> str:
        ref_id = f"res_{uuid.uuid4().hex[:12]}"
        now = time.time()
        enc = self._secrets.encrypt(payload)
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM stored_results WHERE expires_at <= ?", (now,))
            conn.execute(
                """
                INSERT INTO stored_results(ref_id, brain, tool_name, payload_enc, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ref_id, brain, tool_name, enc, now, now + ttl_seconds),
            )
        return ref_id

    def get(self, *, brain: str, ref_id: str) -> str | None:
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload_enc, expires_at FROM stored_results
                WHERE ref_id = ? AND brain = ?
                """,
                (ref_id, brain),
            ).fetchone()
            if row is None or float(row["expires_at"]) <= now:
                return None
            return self._secrets.decrypt(str(row["payload_enc"]))


def _extract_records(data: Any) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    """Find the primary list of record objects and any top-level envelope metadata."""
    if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
        return data, {"total_records": len(data)}
    if isinstance(data, dict):
        envelope: dict[str, Any] = {}
        best_list: list[dict[str, Any]] | None = None
        best_key = ""
        for k, v in data.items():
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                if best_list is None or len(v) > len(best_list):
                    best_list = v
                    best_key = k
            elif not isinstance(v, (list, dict)):
                envelope[k] = v
        if best_list is not None:
            envelope["records_key"] = best_key
            envelope["total_records"] = len(best_list)
            return best_list, envelope
    return None, {}


class ToolResultShaper:
    """Shapes tool output for model consumption while preserving complete data in ToolResultStore."""

    def __init__(
        self,
        store: ToolResultStore,
        *,
        inline_max: int = INLINE_MAX_CHARS,
        preview_budget: int = PREVIEW_BUDGET_CHARS,
    ) -> None:
        self._store = store
        self._inline_max = inline_max
        self._preview_budget = preview_budget

    def shape(self, *, brain: str, tool_name: str, raw_text: str) -> ShapedResult:
        raw = raw_text or ""
        raw_len = len(raw)

        # 1. Errors and small results pass word-for-word
        if raw.startswith("ERROR:") or raw_len <= MIN_SHAPE_CHARS:
            return ShapedResult(text=raw, raw_chars=raw_len, kind="error" if raw.startswith("ERROR:") else "small")

        # 2. Try parsing JSON
        parsed: Any = None
        is_json = False
        stripped = raw.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                is_json = True
            except json.JSONDecodeError:
                is_json = False

        if is_json:
            compact = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
            if len(compact) <= self._inline_max:
                return ShapedResult(text=compact, raw_chars=raw_len, kind="compact")

            ref_id = self._store.put(brain=brain, tool_name=tool_name, payload=raw)
            records, envelope = _extract_records(parsed)
            if records is not None:
                fields: list[str] = []
                for rec in records[:20]:
                    for k in rec.keys():
                        if k not in fields:
                            fields.append(k)
                shown: list[dict[str, Any]] = []
                used = 0
                for rec in records:
                    serialized = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
                    if shown and used + len(serialized) > self._preview_budget:
                        break
                    shown.append(rec)
                    used += len(serialized)

                preview_obj = {
                    "shaped": True,
                    "ref_id": ref_id,
                    "envelope": envelope,
                    "fields": fields[:60],
                    "shown_count": len(shown),
                    "total_count": len(records),
                    "records": shown,
                    "hint": f"Full dataset ({len(records)} records, {raw_len} chars) saved as {ref_id}. "
                    f"Use jarvis_query(ref_id='{ref_id}', ...) for paging, field selection, or read-only SQL.",
                }
                return ShapedResult(
                    text=json.dumps(preview_obj, ensure_ascii=False, separators=(",", ":")),
                    raw_chars=raw_len,
                    kind="preview",
                    ref_id=ref_id,
                )

            # Large JSON object without record array
            head = compact[: self._preview_budget]
            msg = (
                f"[Shaped JSON preview: showing first {len(head)} of {len(compact)} chars; "
                f"full copy saved as ref_id={ref_id}. Use jarvis_query(ref_id='{ref_id}') to inspect.]\n{head}"
            )
            return ShapedResult(text=msg, raw_chars=raw_len, kind="preview", ref_id=ref_id)

        # 3. Non-JSON text
        if raw_len <= self._inline_max:
            return ShapedResult(text=raw, raw_chars=raw_len, kind="small")

        ref_id = self._store.put(brain=brain, tool_name=tool_name, payload=raw)
        lines = raw.splitlines()
        head_lines = "\n".join(lines[:40])[: self._preview_budget // 2]
        tail_lines = "\n".join(lines[-20:])[-(self._preview_budget // 3) :]
        msg = (
            f"[Shaped text preview: {raw_len} chars total ({len(lines)} lines); "
            f"full copy saved as ref_id={ref_id}. Use jarvis_query(ref_id='{ref_id}', offset=..., limit=...) to read more.]\n"
            f"{head_lines}\n\n...[omitted middle]...\n\n{tail_lines}"
        )
        return ShapedResult(text=msg, raw_chars=raw_len, kind="text_preview", ref_id=ref_id)


def query_stored_result(
    store: ToolResultStore,
    *,
    brain: str,
    ref_id: str,
    sql: str = "",
    fields: list[str] | None = None,
    offset: int = 0,
    limit: int = 20,
) -> str:
    """Query a stored tool result by paging/fields or by a single read-only SQL SELECT."""
    raw = store.get(brain=brain, ref_id=ref_id)
    if raw is None:
        return f"ERROR: Stored result '{ref_id}' not found or expired for brain '{brain}'."

    safe_offset = max(0, int(offset))
    safe_limit = max(1, min(int(limit), 200))

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        lines = raw.splitlines()
        sliced = lines[safe_offset : safe_offset + safe_limit]
        return json.dumps(
            {
                "ref_id": ref_id,
                "total_lines": len(lines),
                "offset": safe_offset,
                "lines": sliced,
            },
            ensure_ascii=False,
        )

    records, envelope = _extract_records(parsed)
    if sql.strip():
        if records is None:
            return "ERROR: SQL query is only supported on stored results containing a list of record objects."
        return _run_readonly_sql(records, sql.strip(), safe_limit)

    if records is not None:
        sliced_recs = records[safe_offset : safe_offset + safe_limit]
        if fields:
            sliced_recs = [{k: r.get(k) for k in fields} for r in sliced_recs]
        return json.dumps(
            {
                "ref_id": ref_id,
                "envelope": envelope,
                "offset": safe_offset,
                "returned": len(sliced_recs),
                "total": len(records),
                "records": sliced_recs,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    # Generic JSON object slice
    text = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    return text[safe_offset : safe_offset + (safe_limit * 200)]


def _run_readonly_sql(records: list[dict[str, Any]], sql: str, max_rows: int) -> str:
    if not sql.lower().lstrip().startswith(("select ", "with ")):
        return "ERROR: Only read-only SELECT / WITH SQL queries are allowed."
    if ";" in sql.rstrip(";"):
        return "ERROR: Only a single SQL statement is allowed."
    if _SQL_FORBIDDEN_RE.search(sql):
        return "ERROR: Mutating or administrative SQL keywords are forbidden."

    columns: list[str] = []
    for rec in records:
        for key in rec.keys():
            if key not in columns and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(key)):
                columns.append(str(key))
    if not columns:
        return "ERROR: No valid SQL column names found in records."

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        col_defs = ", ".join(f'"{c}"' for c in columns)
        conn.execute(f"CREATE TABLE records ({col_defs})")
        placeholders = ", ".join("?" for _ in columns)
        rows_to_insert = []
        for rec in records:
            row_vals = []
            for c in columns:
                v = rec.get(c)
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, ensure_ascii=False)
                row_vals.append(v)
            rows_to_insert.append(tuple(row_vals))
        conn.executemany(f"INSERT INTO records ({col_defs}) VALUES ({placeholders})", rows_to_insert)

        # Enforce read-only at SQLite engine level before running user query
        conn.execute("PRAGMA query_only = ON")
        cur = conn.execute(sql.rstrip(";"))
        fetched = [dict(r) for r in cur.fetchmany(max_rows)]
        return json.dumps(
            {"columns": list(fetched[0].keys()) if fetched else [], "row_count": len(fetched), "rows": fetched},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except sqlite3.Error as exc:
        return f"ERROR: SQL execution failed: {exc}"
    finally:
        conn.close()
