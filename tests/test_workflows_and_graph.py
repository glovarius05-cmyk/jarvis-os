"""Tests for Agents, Workflows with verify_agent retry loop, Knowledge Graph, and Studio API."""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app.brain.graph import build_knowledge_graph
from app.container import AppContainer
from app.engines.base import EngineEvent, EngineRequest
from app.main import create_app


class _ScriptedWorkflowEngine:
    """Simulates a worker that improves after verifier feedback + a strict verifier."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def stream(self, request: EngineRequest) -> AsyncIterator[EngineEvent]:
        self.calls.append(request.system_prompt)
        user_msg = request.messages[-1]["content"]
        if "Reviewer" in request.system_prompt:
            if "IMPROVED" in user_msg:
                out = '{"pass": true, "reason": "Looks complete"}'
            else:
                out = '{"pass": false, "reason": "Missing IMPROVED keyword"}'
        elif "Writer" in request.system_prompt:
            if "Missing IMPROVED keyword" in user_msg:
                out = "IMPROVED draft based on feedback"
            else:
                out = "Initial rough draft"
        else:
            out = f"Polished: {user_msg}"
        yield EngineEvent(type="text", content=out, provider=request.provider, model=request.model)
        yield EngineEvent(type="final", content=out, provider=request.provider, model=request.model)


@pytest.mark.asyncio
async def test_workflow_runner_executes_chain_with_verify_retry(
    tmp_path: Path, project_root: Path
) -> None:
    engine = _ScriptedWorkflowEngine()
    ctr = AppContainer.build(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        engine_override=engine,
    )

    ctr.agents.save_agent("Brain Default", slug="writer", name="Writer", role="Drafts text", prompt="Write clearly.")
    ctr.agents.save_agent("Brain Default", slug="reviewer", name="Reviewer", role="Reviews text", prompt="Verify strictly.")
    ctr.agents.save_agent("Brain Default", slug="editor", name="Editor", role="Polishes text", prompt="Polish output.")

    ctr.agents.save_workflow(
        "Brain Default",
        slug="draft-and-polish",
        name="Draft and Polish",
        description="Two-step writing pipeline",
        steps=[
            {"agent": "writer", "task": "Write about {{input}}", "verify_agent": "reviewer", "max_retries": 2},
            {"agent": "editor", "task": "Polish: {{prev}}"},
        ],
    )

    run = await ctr.workflows.run(brain="Brain Default", workflow_slug="draft-and-polish", input_text="Jarvis OS")
    assert run.status == "done"
    assert len(run.steps_log) == 2
    assert run.steps_log[0]["attempts"] == 2
    assert run.steps_log[0]["passed"] is True
    assert "IMPROVED draft based on feedback" in run.final_output

    # Persisted in SQLite history
    runs = ctr.workflows.list_runs(brain="Brain Default")
    assert len(runs) == 1
    assert runs[0].id == run.id


def test_knowledge_graph_and_studio_api(tmp_path: Path, project_root: Path) -> None:
    ctr = AppContainer.build(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        engine_override=_ScriptedWorkflowEngine(),
    )
    ctr.vaults.write_text("Brain Default", "wiki/architecture.md", "# Arch\nSee [[second-brain]] and [[missing-note|Alias]].\n")
    ctr.vaults.write_text("Brain Default", "wiki/second-brain.md", "# Second Brain\nBack to [[architecture]].\n")

    graph = build_knowledge_graph(ctr.vaults.brain_path("Brain Default"))
    node_ids = {n["id"] for n in graph["nodes"]}
    assert "wiki/architecture.md" in node_ids
    assert "wiki/second-brain.md" in node_ids
    assert "unresolved:missing-note" in node_ids
    assert any(e["source"] == "wiki/architecture.md" and e["target"] == "wiki/second-brain.md" for e in graph["edges"])

    app = create_app(ctr)
    with TestClient(app) as tc:
        assert tc.get("/api/studio/skills").status_code == 200
        assert tc.get("/api/studio/plugins").status_code == 200
        g_resp = tc.get("/api/brain/graph")
        assert g_resp.status_code == 200
        assert g_resp.json()["stats"]["total_edges"] >= 2

        # File editor read & write + traversal blocked
        assert tc.put("/api/brain/file", json={"path": "wiki/note.md", "content": "Hello [[architecture]]"}).status_code == 200
        assert tc.get("/api/brain/file?path=wiki/note.md").json()["content"] == "Hello [[architecture]]"
        assert tc.get("/api/brain/file?path=../../etc/passwd").status_code == 400
