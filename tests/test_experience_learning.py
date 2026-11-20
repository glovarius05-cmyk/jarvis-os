"""Tests for Experience Learning & Self-Healing Memory (Proxima-style memory.db)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from starlette.testclient import TestClient

from app.container import AppContainer
from app.hub.builtin_tools import build_builtin_tools
from app.hub.shaper import ToolResultStore
from app.runtime.experience import ExperienceStore, normalize_error
from app.security.secrets import SecretStore


def test_normalize_error_signatures() -> None:
    # 1. Import error
    raw_1 = """Traceback (most recent call last):
  File "/Users/assassin05/project/main.py", line 42, in <module>
    import unknown_lib
ModuleNotFoundError: No module named 'unknown_lib'"""
    sig_1, type_1 = normalize_error(raw_1)
    assert type_1 == "import_error"
    assert "ModuleNotFoundError: No module named 'unknown_lib'" in sig_1

    # 2. Test failure
    raw_2 = "FAILED tests/test_abc.py::test_fn - AssertionError: assert 'foo' == 'bar'"
    sig_2, type_2 = normalize_error(raw_2)
    assert type_2 == "test_failure"
    assert "AssertionError" in sig_2

    # 3. Path and line normalization
    raw_3 = 'File "/private/var/folders/32/xyz/test.py", line 99, in run: TypeError: object not callable'
    sig_3, type_3 = normalize_error(raw_3)
    assert type_3 == "runtime_type"
    assert "/private/var" not in sig_3
    assert ":<line>:" in sig_3 or "line" not in sig_3


def test_experience_store_crud_and_ema(tmp_path: Path) -> None:
    store = ExperienceStore(tmp_path)

    # 1. Record fix
    res1 = store.record_fix(
        error_text="ModuleNotFoundError: No module named 'yaml'",
        solution_patch="pip install pyyaml\nimport yaml",
        failing_cmd="python script.py",
    )
    assert res1["id"]
    assert res1["error_type"] == "import_error"
    assert res1["confidence_score"] == 1.0
    assert res1["success_count"] == 1

    # 2. Reinforce fix with second record_fix
    res2 = store.record_fix(
        error_text="ModuleNotFoundError: No module named 'yaml'",
        solution_patch="pip install pyyaml",
    )
    assert res2["id"] == res1["id"]
    assert res2["success_count"] == 2
    assert res2["confidence_score"] == 1.0

    # 3. Find solutions (exact signature)
    matches = store.find_solutions("ModuleNotFoundError: No module named 'yaml'")
    assert len(matches) >= 1
    assert matches[0]["id"] == res1["id"]

    # 4. Find solutions (FTS5 token query)
    fts_matches = store.find_solutions("Cannot find module named pyyaml in runtime")
    assert len(fts_matches) >= 1

    # 5. Record negative feedback (solution failed) -> score drops
    store.record_feedback(res1["id"], success=False)
    updated = store.get_solution(res1["id"])
    assert updated is not None
    assert updated["confidence_score"] < 1.0
    assert updated["failure_count"] == 1

    # 6. Record positive feedback -> score recovers
    old_score = updated["confidence_score"]
    store.record_feedback(res1["id"], success=True)
    recovered = store.get_solution(res1["id"])
    assert recovered is not None
    assert recovered["confidence_score"] > old_score
    assert recovered["success_count"] == 3

    # 7. Stats
    stats = store.stats()
    assert stats["total_solutions"] == 1
    assert "import_error" in stats["by_type"]

    # 8. Delete
    deleted = store.delete_solution(res1["id"])
    assert deleted is True
    assert store.get_solution(res1["id"]) is None


@pytest.mark.asyncio
async def test_builtin_experience_tools(tmp_path: Path) -> None:
    from app.brain.vault import VaultManager

    vaults = VaultManager(tmp_path / "brains")
    vaults.ensure_brain("Brain Default", init_git=False)
    secret_store = SecretStore(tmp_path)
    result_store = ToolResultStore(tmp_path, secret_store)
    exp_store = ExperienceStore(tmp_path)

    tools = build_builtin_tools(
        vaults=vaults,
        result_store=result_store,
        list_connections_fn=lambda: [],
        experience_getter=lambda: exp_store,
    )

    tool_map = {t.name: t for t in tools}
    assert "experience_search" in tool_map
    assert "experience_record" in tool_map
    assert "experience_stats" in tool_map

    # Record fix via tool
    res_rec = await tool_map["experience_record"].handler(
        {
            "error_text": "FileNotFoundError: [Errno 2] No such file: 'config.json'",
            "solution_patch": "Create default config.json with {}",
            "failing_command": "python app.py",
        },
        "Brain Default",
    )
    assert "Recorded in Self-Healing Memory" in res_rec

    # Search fix via tool
    res_search = await tool_map["experience_search"].handler(
        {"query": "No such file config.json"},
        "Brain Default",
    )
    assert "Create default config.json" in res_search

    # Stats via tool
    res_stats = await tool_map["experience_stats"].handler({}, "Brain Default")
    assert "Total remembered solutions: 1" in res_stats


def test_experience_api_endpoints(tmp_path: Path) -> None:
    from app.main import app

    container = AppContainer.build(
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
    )
    app.state.container = container

    client = TestClient(app, base_url="http://127.0.0.1:7777")

    # 1. Record via API
    rec_res = client.post(
        "/api/experience/record",
        json={
            "error_text": "TypeError: unsupported operand type(s) for +: 'int' and 'str'",
            "solution_patch": "str(number) + string_val",
            "failing_command": "python test.py",
        },
    )
    assert rec_res.status_code == 200
    sol = rec_res.json()["solution"]
    sol_id = sol["id"]

    # 2. Search via API
    search_res = client.post(
        "/api/experience/search",
        json={"query": "TypeError: unsupported operand type"},
    )
    assert search_res.status_code == 200
    assert search_res.json()["count"] >= 1

    # 3. Feedback via API
    fb_res = client.post(
        "/api/experience/feedback",
        json={"solution_id": sol_id, "success": True},
    )
    assert fb_res.status_code == 200
    assert fb_res.json()["solution"]["success_count"] == 2

    # 4. List via API
    list_res = client.get("/api/experience/list")
    assert list_res.status_code == 200
    assert list_res.json()["count"] >= 1

    # 5. Delete via API
    del_res = client.delete(f"/api/experience/{sol_id}")
    assert del_res.status_code == 200
    assert del_res.json()["ok"] is True
