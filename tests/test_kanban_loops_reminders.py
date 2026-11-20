"""Tests for Kanban dispatcher, Multi-Loop circuit breaker, Reminders preconditions, and work plugins."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app.container import AppContainer
from app.engines.base import EngineEvent, EngineRequest
from app.main import create_app
from app.runtime.kanban import extract_needs_input


class _KanbanFakeEngine:
    """Scripts Specifier -> Worker (NEEDS_INPUT on 1st try, success after answer) -> QA pass."""

    async def stream(self, request: EngineRequest) -> AsyncIterator[EngineEvent]:
        sys_p = request.system_prompt
        user_p = request.messages[-1]["content"]
        if "task specifier" in sys_p:
            out = "- Produce concise summary\n- Include target budget"
        elif "QA verifier" in sys_p:
            out = '{"pass": true, "reason": "All criteria met"}'
        elif "Owner clarifications:" in user_p:
            out = "Completed plan with budget $500. (No [[NEEDS_INPUT]] needed now.)"
        else:
            out = "[[NEEDS_INPUT]] What is your target budget?"
        yield EngineEvent(type="text", content=out, provider=request.provider, model=request.model)
        yield EngineEvent(type="final", content=out, provider=request.provider, model=request.model)


class _FailingLoopEngine:
    async def stream(self, request: EngineRequest) -> AsyncIterator[EngineEvent]:
        yield EngineEvent(type="error", content="Simulated provider outage", provider=request.provider, model=request.model)


def test_extract_needs_input_only_matches_line_start() -> None:
    assert extract_needs_input("The marker [[NEEDS_INPUT]] was not needed here.") is None
    assert extract_needs_input("Hello\n[[NEEDS_INPUT]] Which shop?") == "Which shop?"


@pytest.mark.asyncio
async def test_kanban_needs_input_answer_acceptance_and_chat_delivery(
    tmp_path: Path, project_root: Path
) -> None:
    ctr = AppContainer.build(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        engine_override=_KanbanFakeEngine(),
    )
    sess = ctr.sessions.create_session(brain="Brain Default", title="Owner chat")

    task = ctr.kanban.create_task(
        brain="Brain Default",
        title="Prepare ad plan",
        description="Draft Q4 budget",
        owner_chat=f"web:{sess.id}",
    )

    # First run hits [[NEEDS_INPUT]] -> blocked + question delivered to web chat & inbox
    t1 = await ctr.kanban.execute_task(task.id)
    assert t1.status == "blocked"
    assert "budget" in t1.block_reason.lower()
    assert ctr.inbox.unread_count(brain="Brain Default") == 1

    # Owner answers -> moves back to ready -> second run completes and passes QA
    t_ready = ctr.kanban.answer_task(task.id, "$500")
    assert t_ready.status == "ready"

    t2 = await ctr.kanban.execute_task(task.id)
    assert t2.status == "done"
    assert "$500" in t2.result

    # Both messages landed in the originating web chat session!
    chat_msgs = ctr.sessions.list_messages(sess.id)
    assert len(chat_msgs) == 2
    assert "Completed plan with budget $500" in chat_msgs[-1].content


@pytest.mark.asyncio
async def test_multi_loop_circuit_breaker_and_reminders_preconditions(
    tmp_path: Path, project_root: Path
) -> None:
    ctr = AppContainer.build(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        engine_override=_FailingLoopEngine(),
    )

    # Loop fails 3 times -> circuit breaker auto-pauses in loop-state.json (without mutating .md)
    ctr.loops.create_loop(
        brain="Brain Default",
        slug="nightly-check",
        name="Nightly Check",
        prompt="Check metrics",
        interval_min=30,
        enabled=True,
    )
    for _ in range(3):
        res = await ctr.loops.run_loop("Brain Default", "nightly-check")
        assert res["ok"] is False

    loops = ctr.loops.list_loops("Brain Default")
    assert loops[0]["state"]["fail_streak"] == 3
    assert "Auto-paused" in loops[0]["state"]["auto_paused_reason"]
    # .md file stays untouched (still enabled=True), state holds the pause
    assert loops[0]["enabled"] is True

    # Reminders precondition check blocks missing MCP connection unless allow_missing=True
    blocked = ctr.reminders.create_reminder(
        brain="Brain Default",
        name="Morning POS report",
        prompt="Summarize sales",
        cron="0 9 * * *",
        notify_only=True,
        required_connections=["pos"],
    )
    assert blocked["created"] is False
    assert blocked["can_force"] is True
    assert any("pos" in m for m in blocked["missing_preconditions"])

    # Force create and tick at 09:00 -> fires and delivers to Inbox
    created = ctr.reminders.create_reminder(
        brain="Brain Default",
        name="Morning ping",
        prompt="Time to review budget!",
        cron="0 9 * * *",
        notify_only=True,
        allow_missing=True,
    )
    assert created["created"] is True
    fired = await ctr.reminders.tick(["Brain Default"], now_dt=datetime(2026, 11, 18, 9, 0))
    assert len(fired) == 1
    assert any("Morning ping" in i.title for i in ctr.inbox.list_items(brain="Brain Default"))


@pytest.mark.asyncio
async def test_bundled_task_and_schedule_plugins_and_work_api(
    tmp_path: Path, project_root: Path
) -> None:
    ctr = AppContainer.build(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        engine_override=_KanbanFakeEngine(),
    )

    # jarvis_task warns honestly that auto-orchestration is OFF by default
    add_res = json.loads(
        await ctr.hub.call_tool(
            name="jarvis_task",
            args={"op": "add", "title": "Audit wiki links"},
            brain="Brain Default",
            run_mode="auto",
        )
    )
    assert add_res["ok"] is True
    assert "OFF" in add_res["note"]

    # jarvis_schedule creates loops strictly disabled
    loop_res = json.loads(
        await ctr.hub.call_tool(
            name="jarvis_schedule",
            args={"op": "create_loop", "name": "Hourly Sweep", "prompt": "Tidy notes", "interval_min": 60},
            brain="Brain Default",
            run_mode="auto",
        )
    )
    assert loop_res["ok"] is True
    assert loop_res["loop"]["enabled"] is False

    app = create_app(ctr, enable_scheduler=False)
    with TestClient(app) as tc:
        assert tc.get("/api/work/tasks").status_code == 200
        assert tc.get("/api/work/loops").status_code == 200
        assert tc.get("/api/work/reminders").status_code == 200
        assert tc.get("/api/work/inbox").status_code == 200
