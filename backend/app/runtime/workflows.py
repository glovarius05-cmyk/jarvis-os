"""Multi-step agent WorkflowRunner with per-step verification, retries, and SQLite run history."""

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

from app.brain.agents import AgentDef, AgentStore
from app.engines.base import EngineProtocol, EngineRequest
from app.engines.router import EngineRouter
from app.hub.gateway import MCPHubGateway

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


@dataclass(frozen=True)
class WorkflowRunRecord:
    id: str
    brain: str
    workflow_slug: str
    status: str  # "running" | "done" | "failed"
    input_text: str
    final_output: str
    steps_log: list[dict[str, Any]]
    created_at: float
    finished_at: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "brain": self.brain,
            "workflow_slug": self.workflow_slug,
            "status": self.status,
            "input_text": self.input_text,
            "final_output": self.final_output,
            "steps_log": self.steps_log,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


def _parse_verifier_verdict(text: str) -> tuple[bool, str]:
    match = _JSON_BLOCK_RE.search(text or "")
    if not match:
        return False, f"Verifier did not return JSON verdict: {(text or '')[:200]}"
    try:
        obj = json.loads(match.group(0))
        if isinstance(obj, dict) and "pass" in obj:
            return bool(obj["pass"]), str(obj.get("reason") or "")
    except json.JSONDecodeError:
        pass
    return False, f"Invalid JSON verdict: {(text or '')[:200]}"


class WorkflowRunner:
    """Executes chains of specialist agents with optional verify_agent retries and SQLite checkpoints."""

    def __init__(
        self,
        *,
        state_dir: Path,
        agents: AgentStore,
        router: EngineRouter,
        hub: MCPHubGateway,
        engine_override: EngineProtocol | None = None,
    ) -> None:
        self._db_path = state_dir / "workflows.sqlite3"
        self._agents = agents
        self._router = router
        self._hub = hub
        self._engine_override = engine_override
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
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    id TEXT PRIMARY KEY,
                    brain TEXT NOT NULL,
                    workflow_slug TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_text TEXT NOT NULL,
                    final_output TEXT NOT NULL DEFAULT '',
                    steps_json TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    finished_at REAL
                )
                """
            )

    async def _run_agent_once(
        self,
        *,
        brain: str,
        agent: AgentDef,
        prompt: str,
        run_mode: str,
        session_id: str,
    ) -> str:
        provider, model = self._router.resolve_target(
            override_provider=agent.model_provider,
            override_model=agent.model,
            role="main",
        )
        engine = self._engine_override or self._router.build_engine(provider, brain=brain, run_mode=run_mode)
        tools = await self._hub.discover_schemas(brain=brain, run_mode=run_mode)
        executor = self._hub.make_executor(brain=brain, run_mode=run_mode, session_id=session_id)

        sys_prompt = (
            f"You are specialist agent **{agent.name}** ({agent.role}).\n\n"
            f"{agent.prompt}"
        )
        req = EngineRequest(
            session_id=session_id,
            brain=brain,
            system_prompt=sys_prompt,
            messages=[{"role": "user", "content": prompt}],
            provider=provider,
            model=model,
            tools=tools,
            tool_executor=executor,
        )

        chunks: list[str] = []
        final_text = ""
        async for ev in engine.stream(req):
            if ev.type == "text":
                chunks.append(ev.content)
            elif ev.type == "error":
                raise RuntimeError(ev.content)
            elif ev.type == "final":
                final_text = ev.content
        return (final_text or "".join(chunks)).strip()

    async def run(
        self,
        *,
        brain: str,
        workflow_slug: str,
        input_text: str,
        run_mode: str = "auto",
    ) -> WorkflowRunRecord:
        wf = self._agents.get_workflow(brain, workflow_slug)
        if wf is None:
            raise KeyError(f"Workflow '{workflow_slug}' not found in brain '{brain}'")

        run_id = f"wfr_{uuid.uuid4().hex[:12]}"
        created_at = time.time()
        steps_log: list[dict[str, Any]] = []
        self._save_run(run_id, brain, workflow_slug, "running", input_text, "", steps_log, created_at, None)

        prev_output = ""
        try:
            for idx, step in enumerate(wf.steps, start=1):
                worker = self._agents.get_agent(brain, step.agent)
                if worker is None:
                    raise KeyError(f"Step {idx} references missing agent '{step.agent}'")

                rendered_task = (
                    step.task.replace("{{input}}", input_text).replace("{{prev}}", prev_output)
                )

                step_output = ""
                verifier_passed = True
                verifier_reason = ""
                attempts = 0
                feedback = ""

                for attempt in range(step.max_retries + 1):
                    attempts = attempt + 1
                    task_with_feedback = (
                        rendered_task
                        if not feedback
                        else f"{rendered_task}\n\n[Previous attempt failed verification: {feedback}. Fix and try again.]"
                    )
                    step_output = await self._run_agent_once(
                        brain=brain,
                        agent=worker,
                        prompt=task_with_feedback,
                        run_mode=run_mode,
                        session_id=f"{run_id}:step{idx}",
                    )

                    if not step.verify_agent:
                        verifier_passed = True
                        break

                    verifier = self._agents.get_agent(brain, step.verify_agent)
                    if verifier is None:
                        raise KeyError(f"Step {idx} references missing verify_agent '{step.verify_agent}'")

                    verify_prompt = (
                        f"Task:\n{rendered_task}\n\nCandidate output:\n{step_output}\n\n"
                        'Return ONLY valid JSON: {"pass": true|false, "reason": "..."}'
                    )
                    verdict_raw = await self._run_agent_once(
                        brain=brain,
                        agent=verifier,
                        prompt=verify_prompt,
                        run_mode="suggest",  # Verifier is ALWAYS read-only
                        session_id=f"{run_id}:verify{idx}",
                    )
                    verifier_passed, verifier_reason = _parse_verifier_verdict(verdict_raw)
                    if verifier_passed:
                        break
                    feedback = verifier_reason or "Did not meet criteria"

                steps_log.append(
                    {
                        "step": idx,
                        "agent": step.agent,
                        "verify_agent": step.verify_agent,
                        "attempts": attempts,
                        "passed": verifier_passed,
                        "verifier_reason": verifier_reason,
                        "output": step_output,
                    }
                )
                self._save_run(run_id, brain, workflow_slug, "running", input_text, step_output, steps_log, created_at, None)

                if not verifier_passed:
                    finished = time.time()
                    self._save_run(
                        run_id,
                        brain,
                        workflow_slug,
                        "failed",
                        input_text,
                        f"Step {idx} ({step.agent}) failed verification after {attempts} attempt(s): {verifier_reason}",
                        steps_log,
                        created_at,
                        finished,
                    )
                    return self.get_run(run_id)  # type: ignore[return-value]

                prev_output = step_output

            finished = time.time()
            self._save_run(run_id, brain, workflow_slug, "done", input_text, prev_output, steps_log, created_at, finished)
            return self.get_run(run_id)  # type: ignore[return-value]
        except Exception as exc:  # noqa: BLE001
            finished = time.time()
            self._save_run(run_id, brain, workflow_slug, "failed", input_text, f"ERROR: {exc}", steps_log, created_at, finished)
            return self.get_run(run_id)  # type: ignore[return-value]

    def _save_run(
        self,
        run_id: str,
        brain: str,
        workflow_slug: str,
        status: str,
        input_text: str,
        final_output: str,
        steps_log: list[dict[str, Any]],
        created_at: float,
        finished_at: float | None,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workflow_runs(id, brain, workflow_slug, status, input_text, final_output, steps_json, created_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    final_output=excluded.final_output,
                    steps_json=excluded.steps_json,
                    finished_at=excluded.finished_at
                """,
                (
                    run_id,
                    brain,
                    workflow_slug,
                    status,
                    input_text,
                    final_output,
                    json.dumps(steps_log, ensure_ascii=False),
                    created_at,
                    finished_at,
                ),
            )

    def get_run(self, run_id: str) -> WorkflowRunRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM workflow_runs WHERE id = ?", (run_id,)).fetchone()
            return self._row_to_run(row) if row else None

    def list_runs(self, brain: str | None = None, limit: int = 30) -> list[WorkflowRunRecord]:
        with self._lock, self._connect() as conn:
            if brain:
                rows = conn.execute(
                    "SELECT * FROM workflow_runs WHERE brain = ? ORDER BY created_at DESC LIMIT ?",
                    (brain, max(1, min(limit, 100))),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM workflow_runs ORDER BY created_at DESC LIMIT ?",
                    (max(1, min(limit, 100))),
                ).fetchall()
            return [self._row_to_run(r) for r in rows]

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> WorkflowRunRecord:
        try:
            steps = json.loads(row["steps_json"] or "[]")
        except json.JSONDecodeError:
            steps = []
        return WorkflowRunRecord(
            id=row["id"],
            brain=row["brain"],
            workflow_slug=row["workflow_slug"],
            status=row["status"],
            input_text=row["input_text"],
            final_output=row["final_output"],
            steps_log=steps if isinstance(steps, list) else [],
            created_at=float(row["created_at"]),
            finished_at=float(row["finished_at"]) if row["finished_at"] is not None else None,
        )
