"""Tests for MCP Hub hard permission matrix, audit logging, Tool Result Shaper, and jarvis_query."""

from __future__ import annotations

import json
from pathlib import Path

from app.hub.permissions import PermLevel, effective_level, is_tool_allowed
from app.hub.shaper import ToolResultShaper, ToolResultStore, query_stored_result
from app.security.secrets import SecretStore


def test_permission_matrix_applies_stricter_level() -> None:
    # suggest (readonly) blocks safe and full even if connection is full
    assert effective_level("full", "suggest") == PermLevel.READONLY
    allowed, _ = is_tool_allowed(required="safe", connection_perm="full", run_mode="suggest")
    assert allowed is False
    allowed, _ = is_tool_allowed(required="readonly", connection_perm="full", run_mode="suggest")
    assert allowed is True

    # auto (safe) allows safe, blocks full
    assert effective_level("full", "auto") == PermLevel.SAFE
    assert is_tool_allowed(required="safe", connection_perm="full", run_mode="auto")[0] is True
    assert is_tool_allowed(required="full", connection_perm="full", run_mode="auto")[0] is False

    # full mode still capped by connection's own perm (e.g. readonly connection cannot write!)
    assert effective_level("readonly", "full") == PermLevel.READONLY
    assert is_tool_allowed(required="full", connection_perm="readonly", run_mode="full")[0] is False
    assert is_tool_allowed(required="full", connection_perm="full", run_mode="full")[0] is True


def test_shaper_and_jarvis_query_paging_and_readonly_sql(tmp_path: Path) -> None:
    secrets = SecretStore(tmp_path)
    store = ToolResultStore(tmp_path, secrets)
    shaper = ToolResultShaper(store, inline_max=2000, preview_budget=900)

    # 1. Small result passes verbatim
    small = shaper.shape(brain="Brain Default", tool_name="t", raw_text='{"ok": true}')
    assert small.kind == "small"
    assert small.text == '{"ok": true}'

    # 2. Large JSON (~25 KB of orders) gets stored encrypted + shaped into whole records with ref_id
    orders = [
        {"id": i, "customer": f"User {i}", "total": i * 100, "notes": "x" * 120}
        for i in range(1, 151)
    ]
    raw_big = json.dumps({"shop": "Demo", "orders": orders}, indent=2)
    assert len(raw_big) > 20_000

    shaped = shaper.shape(brain="Brain Default", tool_name="pos_orders", raw_text=raw_big)
    assert shaped.kind == "preview"
    assert shaped.ref_id.startswith("res_")
    preview = json.loads(shaped.text)
    assert preview["total_count"] == 150
    assert 0 < preview["shown_count"] < 150

    # 3. Brain isolation: another brain cannot read this ref_id
    denied = query_stored_result(store, brain="Other Brain", ref_id=shaped.ref_id)
    assert denied.startswith("ERROR:")

    # 4. Paging + field selection via jarvis_query
    page = json.loads(
        query_stored_result(
            store,
            brain="Brain Default",
            ref_id=shaped.ref_id,
            fields=["id", "total"],
            offset=10,
            limit=3,
        )
    )
    assert [r["id"] for r in page["records"]] == [11, 12, 13]
    assert set(page["records"][0].keys()) == {"id", "total"}

    # 5. Read-only SQL aggregation over all 150 stored records
    sql_res = json.loads(
        query_stored_result(
            store,
            brain="Brain Default",
            ref_id=shaped.ref_id,
            sql="SELECT COUNT(*) AS cnt, SUM(total) AS revenue FROM records WHERE id <= 10",
        )
    )
    assert sql_res["rows"][0]["cnt"] == 10
    assert sql_res["rows"][0]["revenue"] == sum(i * 100 for i in range(1, 11))

    # 6. Mutating SQL strictly blocked
    drop_res = query_stored_result(
        store,
        brain="Brain Default",
        ref_id=shaped.ref_id,
        sql="DROP TABLE records",
    )
    assert drop_res.startswith("ERROR:")
