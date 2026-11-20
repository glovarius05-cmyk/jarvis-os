"""Token and cost usage ledger split by provider, model, day, and user vs background work."""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Conservative default USD per 1M tokens (input, output). Local Llama & :free models are $0.
_DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "local-llama": (0.0, 0.0),
    "codex-cli": (0.0, 0.0),
    "antigravity-cli": (0.0, 0.0),
    "gemini-api": (0.15, 0.60),
    "openrouter": (0.50, 1.50),
}


def estimate_cost_usd(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    if provider in {"local-llama", "codex-cli", "antigravity-cli"} or ":free" in (model or "").lower():
        return 0.0
    in_rate, out_rate = _DEFAULT_PRICING.get(provider, (0.50, 1.50))
    return round((input_tokens / 1_000_000.0) * in_rate + (output_tokens / 1_000_000.0) * out_rate, 6)


class UsageStore:
    """SQLite ledger tracking tokens and estimated cost across chat and background jobs."""

    def __init__(self, state_dir: Path) -> None:
        self._db_path = state_dir / "usage.sqlite3"
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
                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    day TEXT NOT NULL,
                    brain TEXT NOT NULL,
                    source TEXT NOT NULL,
                    category TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0.0
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_day ON usage_events(day DESC)")

    def record(
        self,
        *,
        brain: str,
        source: str,
        provider: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float | None = None,
    ) -> None:
        now = time.time()
        day = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        tin = max(0, int(input_tokens or 0))
        tout = max(0, int(output_tokens or 0))
        cost = (
            float(cost_usd)
            if cost_usd is not None
            else estimate_cost_usd(provider, model, tin, tout)
        )
        category = "user" if source in {"chat", "telegram", "voice", "cli"} else "background"
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO usage_events(ts, day, brain, source, category, provider, model, input_tokens, output_tokens, cost_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (now, day, brain, source, category, provider, model, tin, tout, cost),
            )

    def summary(self, *, days: int = 30, brain: str | None = None) -> dict[str, Any]:
        cutoff = time.time() - max(1, days) * 86400.0
        where = "WHERE ts >= ?" + (" AND brain = ?" if brain else "")
        params: list[Any] = [cutoff] + ([brain] if brain else [])

        with self._lock, self._connect() as conn:
            totals_row = conn.execute(
                f"""
                SELECT COUNT(*) AS calls,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(cost_usd), 0.0) AS cost_usd
                FROM usage_events {where}
                """,
                params,
            ).fetchone()

            by_category = [
                dict(r)
                for r in conn.execute(
                    f"""
                    SELECT category, COUNT(*) AS calls,
                           COALESCE(SUM(input_tokens), 0) AS input_tokens,
                           COALESCE(SUM(output_tokens), 0) AS output_tokens,
                           COALESCE(SUM(cost_usd), 0.0) AS cost_usd
                    FROM usage_events {where}
                    GROUP BY category
                    """,
                    params,
                ).fetchall()
            ]

            by_provider = [
                dict(r)
                for r in conn.execute(
                    f"""
                    SELECT provider, model, COUNT(*) AS calls,
                           COALESCE(SUM(input_tokens), 0) AS input_tokens,
                           COALESCE(SUM(output_tokens), 0) AS output_tokens,
                           COALESCE(SUM(cost_usd), 0.0) AS cost_usd
                    FROM usage_events {where}
                    GROUP BY provider, model
                    ORDER BY calls DESC
                    """,
                    params,
                ).fetchall()
            ]

        return {
            "days": days,
            "totals": dict(totals_row) if totals_row else {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
            "by_category": by_category,
            "by_provider": by_provider,
        }
