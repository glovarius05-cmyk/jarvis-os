"""Autonomous Kanban task queue and dispatcher backed by SQLite.

Lifecycle:
  triage/ready -> (atomic claim + lease) -> running -> specifier -> worker -> acceptance -> done | review | blocked
- Uses `aux_engine` (e.g. local Llama / free OpenRouter) so 24/7 work never drains main subscription plans.
- Detects `[[NEEDS_INPUT]]` strictly at the start of a line to avoid false blocks.
- Auto-orchestration is OFF by default per brain until explicitly enabled on the Tasks page.
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
from typing import TYPE_CHECKING, Any

from app.engines.base import EngineProtocol, EngineRequest
from app.engines.router import EngineRouter
from app.hub.gateway import MCPHubGateway
from app.runtime.inbox import InboxStore
from app.runtime.workflows import WorkflowRunner

if TYPE_CHECKING:
    from app.brain.vault import VaultManager

VALID_STATUSES = frozenset({"triage", "ready", "running", "review", "blocked", "done", "cancelled"})
LEASE_SECONDS = 120.0
_NEEDS_INPUT_LINE_RE = re.compile(r"^\s*(?:[-*>]+\s*)?\[\[NEEDS_INPUT\]\]\s*(.*)$", re.MULTILINE)
_JSON_OBJ_RE = re.compile(r"\{[\s\S]*\}")


@dataclass(frozen=True)
class KanbanTask:
    id: str
    brain: str
    title: str
    description: str
    spec: str
    status: str
    mode: str
    route: str
    owner_chat: str
    result: str
    block_reason: str
    qa_log: list[dict[str, str]]
    attempts: int
    created_at: float
    updated_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "brain": self.brain,
            "title": self.title,
            "description": self.description,
            "spec": self.spec,
            "status": self.status,
            "mode": self.mode,
            "route": self.route,
            "owner_chat": self.owner_chat,
            "result": self.result,
            "block_reason": self.block_reason,
            "qa_log": list(self.qa_log),
            "attempts": self.attempts,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def extract_needs_input(text: str) -> str | None:
    """Match [[NEEDS_INPUT]] ONLY at line start (ignoring inline mentions in prose)."""
    m = _NEEDS_INPUT_LINE_RE.search(text or "")
    if not m:
        return None
    tail = (m.group(1) or "").strip()
    return tail or "Clarification required from owner."


class KanbanRuntime:
    """SQLite-backed task queue with atomic claims, AI specifier, worker, and read-only acceptance."""

    def __init__(
        self,
        *,
        state_dir: Path,
        router: EngineRouter,
        hub: MCPHubGateway,
        workflows: WorkflowRunner,
        inbox: InboxStore,
        vaults: VaultManager | None = None,
        engine_override: EngineProtocol | None = None,
    ) -> None:
        self._db_path = state_dir / "kanban.sqlite3"
        self._cfg_path = state_dir / "kanban_config.json"
        self._router = router
        self._hub = hub
        self._workflows = workflows
        self._inbox = inbox
        self._vaults = vaults
        self._engine_override = engine_override
        self._lock = threading.RLock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=15.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    brain TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    spec TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'ready',
                    mode TEXT NOT NULL DEFAULT 'auto',
                    route TEXT NOT NULL DEFAULT 'auto',
                    owner_chat TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL DEFAULT '',
                    block_reason TEXT NOT NULL DEFAULT '',
                    qa_log_json TEXT NOT NULL DEFAULT '[]',
                    lease_until REAL NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def _load_auto_cfg(self) -> dict[str, bool]:
        if not self._cfg_path.exists():
            return {}
        try:
            data = json.loads(self._cfg_path.read_text(encoding="utf-8"))
            return {str(k): bool(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def is_auto_enabled(self, brain: str) -> bool:
        with self._lock:
            return self._load_auto_cfg().get(brain, False)

    def set_auto_enabled(self, brain: str, enabled: bool) -> bool:
        with self._lock:
            cfg = self._load_auto_cfg()
            cfg[brain] = bool(enabled)
            tmp = self._cfg_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, self._cfg_path)
            return cfg[brain]

    def create_task(
        self,
        *,
        brain: str,
        title: str,
        description: str = "",
        mode: str = "auto",
        route: str = "auto",
        owner_chat: str = "",
    ) -> KanbanTask:
        clean_title = (title or "").strip()
        if not clean_title:
            raise ValueError("Task title is required")
        norm_mode = mode if mode in {"suggest", "auto", "full"} else "auto"
        tid = f"t_{uuid.uuid4().hex[:10]}"
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks(id, brain, title, description, status, mode, route, owner_chat, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'ready', ?, ?, ?, ?, ?)
                """,
                (tid, brain, clean_title, description.strip(), norm_mode, route.strip() or "auto", owner_chat.strip(), now, now),
            )
        task = self.get_task(tid)
        assert task is not None
        return task

    def get_task(self, task_id: str) -> KanbanTask | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return self._row_to_task(row) if row else None

    def list_tasks(self, brain: str | None = None, limit: int = 100) -> list[KanbanTask]:
        with self._lock, self._connect() as conn:
            if brain:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE brain = ? ORDER BY updated_at DESC LIMIT ?",
                    (brain, max(1, min(limit, 200))),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?",
                    (max(1, min(limit, 200))),
                ).fetchall()
            return [self._row_to_task(r) for r in rows]

    def answer_task(self, task_id: str, answer: str) -> KanbanTask:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"Task '{task_id}' not found")
        if task.status != "blocked":
            raise ValueError(f"Task '{task_id}' is not blocked (status={task.status})")
        qa = list(task.qa_log)
        qa.append({"question": task.block_reason, "answer": answer.strip()})
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE tasks SET status='ready', block_reason='', qa_log_json=?, updated_at=?
                WHERE id = ?
                """,
                (json.dumps(qa, ensure_ascii=False), time.time(), task_id),
            )
        return self.get_task(task_id)  # type: ignore[return-value]

    def accept_task(self, task_id: str) -> KanbanTask:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"Task '{task_id}' not found")
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET status='done', block_reason='', updated_at=? WHERE id = ?",
                (time.time(), task_id),
            )
        updated = self.get_task(task_id)
        if updated is not None and (updated.result or "").strip():
            self._save_task_to_vault(updated)
        return updated  # type: ignore[return-value]

    def cancel_task(self, task_id: str, reason: str = "") -> KanbanTask:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"Task '{task_id}' not found")
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET status='cancelled', block_reason=?, updated_at=? WHERE id = ?",
                (reason.strip(), time.time(), task_id),
            )
        return self.get_task(task_id)  # type: ignore[return-value]

    def claim_next(self, brain: str) -> KanbanTask | None:
        """Atomically claim one ready task (or expired running lease) for a brain."""
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT id FROM tasks
                WHERE brain = ?
                  AND (status = 'ready' OR (status = 'running' AND lease_until > 0 AND lease_until < ?))
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (brain, now),
            ).fetchone()
            if row is None:
                conn.rollback()
                return None
            tid = str(row["id"])
            conn.execute(
                """
                UPDATE tasks
                SET status = 'running', lease_until = ?, attempts = attempts + 1, updated_at = ?
                WHERE id = ?
                """,
                (now + LEASE_SECONDS, now, tid),
            )
            conn.commit()
        return self.get_task(tid)

    def _emit_ws_update(self, task_id: str, status: str) -> None:
        pub = getattr(self._inbox, "_ws_publisher", None)
        if pub is not None:
            try:
                pub({"type": "kanban_updated", "task_id": task_id, "status": status})
            except Exception:  # noqa: BLE001
                pass

    async def _run_aux(
        self,
        *,
        brain: str,
        system_prompt: str,
        prompt: str,
        run_mode: str,
        session_id: str,
        override_provider: str = "",
        override_model: str = "",
    ) -> str:
        async def _invoke_role(role: str) -> str:
            provider, model = self._router.resolve_target(
                override_provider=override_provider,
                override_model=override_model,
                role=role,
            )
            engine = self._engine_override or self._router.build_engine(provider, brain=brain, run_mode=run_mode)
            tools = await self._hub.discover_schemas(brain=brain, run_mode=run_mode)
            executor = self._hub.make_executor(brain=brain, run_mode=run_mode, session_id=session_id)

            req = EngineRequest(
                session_id=session_id,
                brain=brain,
                system_prompt=system_prompt,
                messages=[{"role": "user", "content": prompt}],
                provider=provider,
                model=model,
                tools=tools,
                tool_executor=executor,
            )
            parts: list[str] = []
            final = ""
            async for ev in engine.stream(req):
                if ev.type == "text":
                    parts.append(ev.content)
                elif ev.type == "error":
                    raise RuntimeError(ev.content)
                elif ev.type == "final":
                    final = ev.content
            return (final or "".join(parts)).strip()

        try:
            return await _invoke_role("aux")
        except Exception as exc:  # noqa: BLE001
            # Fallback to main brain if aux (e.g. local-llama) is offline/unreachable
            msg = str(exc).lower()
            if self._engine_override is None and any(
                k in msg for k in ("connection", "connecterror", "refused", "unreachable", "timed out", "not running")
            ):
                return await _invoke_role("main")
            raise

    async def execute_task(self, task_id: str) -> KanbanTask:
        """Run a task through Specifier -> Worker (or Workflow) -> Acceptance -> Inbox delivery."""
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"Task '{task_id}' not found")

        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET status='running', lease_until=?, updated_at=? WHERE id=?",
                (now + LEASE_SECONDS, now, task_id),
            )
        task = self.get_task(task_id)
        assert task is not None
        self._emit_ws_update(task_id, "running")

        try:
            # 1. Specifier stage (read-only) if no spec yet
            spec = task.spec
            if not spec:
                spec = await self._run_aux(
                    brain=task.brain,
                    system_prompt="You are a concise task specifier. Output 2-4 bullet acceptance criteria.",
                    prompt=f"Title: {task.title}\nDescription: {task.description}",
                    run_mode="suggest",
                    session_id=f"kanban:spec:{task.id}",
                )
                with self._lock, self._connect() as conn:
                    conn.execute("UPDATE tasks SET spec=?, updated_at=? WHERE id=?", (spec, time.time(), task.id))

            # 2. Worker stage (either workflow route or aux worker)
            qa_context = ""
            if task.qa_log:
                qa_lines = [f"Q: {x['question']}\nA: {x['answer']}" for x in task.qa_log]
                qa_context = "\n\nOwner clarifications:\n" + "\n".join(qa_lines)

            if task.route.startswith("wf:"):
                wf_slug = task.route[3:].strip()
                wf_run = await self._workflows.run(
                    brain=task.brain,
                    workflow_slug=wf_slug,
                    input_text=f"{task.title}\n{task.description}{qa_context}",
                    run_mode=task.mode,
                )
                worker_out = wf_run.final_output
            else:
                worker_prompt = (
                    f"Goal: {task.title}\nDescription: {task.description}\n\n"
                    f"Acceptance criteria:\n{spec}{qa_context}\n\n"
                    "If you genuinely cannot proceed without an answer from the owner, "
                    "start a line with `[[NEEDS_INPUT]] <your concise question>`."
                )
                worker_out = await self._run_aux(
                    brain=task.brain,
                    system_prompt="You are Jarvis autonomous Kanban worker. Execute the goal accurately and concisely.",
                    prompt=worker_prompt,
                    run_mode=task.mode,
                    session_id=f"kanban:work:{task.id}",
                )

            # 3. Check for [[NEEDS_INPUT]]
            question = extract_needs_input(worker_out)
            if question is not None:
                with self._lock, self._connect() as conn:
                    conn.execute(
                        "UPDATE tasks SET status='blocked', block_reason=?, result=?, lease_until=0, updated_at=? WHERE id=?",
                        (question, worker_out, time.time(), task.id),
                    )
                await self._inbox.deliver(
                    brain=task.brain,
                    source="kanban",
                    title=f"Needs input: {task.title}",
                    body=f"Task `{task.id}` paused and needs your answer:\n\n{question}",
                    owner_chat=task.owner_chat,
                )
                return self.get_task(task.id)  # type: ignore[return-value]

            # 4. Read-only acceptance check
            verdict_raw = await self._run_aux(
                brain=task.brain,
                system_prompt='You are a strict read-only QA verifier. Reply ONLY with JSON: {"pass": true|false, "reason": "..."}',
                prompt=f"Goal: {task.title}\nCriteria:\n{spec}\n\nWorker output:\n{worker_out}",
                run_mode="suggest",
                session_id=f"kanban:qa:{task.id}",
            )
            passed, reason = self._parse_acceptance(verdict_raw)
            final_status = "done" if passed else "review"
            block_reason = "" if passed else (reason or "Acceptance check failed")

            with self._lock, self._connect() as conn:
                conn.execute(
                    "UPDATE tasks SET status=?, result=?, block_reason=?, lease_until=0, updated_at=? WHERE id=?",
                    (final_status, worker_out, block_reason, time.time(), task.id),
                )

            saved_rel_path = ""
            if passed and worker_out.strip():
                updated_task = self.get_task(task.id)
                if updated_task is not None:
                    saved_rel_path = self._save_task_to_vault(updated_task)

            summary_body = worker_out if passed else f"Needs review ({block_reason}):\n\n{worker_out}"
            if saved_rel_path:
                summary_body = f"{summary_body}\n\n---\nSaved to Second Brain: `{saved_rel_path}`"
            await self._inbox.deliver(
                brain=task.brain,
                source="kanban",
                title=f"{'Done' if passed else 'Review'}: {task.title}",
                body=summary_body,
                owner_chat=task.owner_chat,
            )
            self._emit_ws_update(task.id, final_status)
            return self.get_task(task.id)  # type: ignore[return-value]
        except Exception as exc:  # noqa: BLE001
            with self._lock, self._connect() as conn:
                conn.execute(
                    "UPDATE tasks SET status='blocked', block_reason=?, lease_until=0, updated_at=? WHERE id=?",
                    (str(exc), time.time(), task.id),
                )
            await self._inbox.deliver(
                brain=task.brain,
                source="kanban",
                title=f"Failed: {task.title}",
                body=f"Task `{task.id}` encountered an error: {exc}",
                owner_chat=task.owner_chat,
            )
            return self.get_task(task.id)  # type: ignore[return-value]

    async def tick(self, brains: list[str]) -> list[str]:
        """Claim and execute at most one ready task per auto-enabled brain."""
        executed: list[str] = []
        for b in brains:
            if not self.is_auto_enabled(b):
                continue
            claimed = self.claim_next(b)
            if claimed is not None:
                await self.execute_task(claimed.id)
                executed.append(claimed.id)
        return executed

    @staticmethod
    def _task_vault_rel_path(task: KanbanTask) -> str:
        """Build a deterministic wiki/tasks/<id>-<slug>.md path inside the brain."""
        raw_slug = re.sub(r"[^\w\s-]", "", task.title.lower(), flags=re.UNICODE)
        slug = re.sub(r"[\s_-]+", "-", raw_slug).strip("-")[:60]
        filename = f"{task.id}-{slug}.md" if slug else f"{task.id}.md"
        return f"wiki/tasks/{filename}"

    def _save_task_to_vault(self, task: KanbanTask) -> str:
        """Persist a completed Kanban task report into Second Brain (`wiki/tasks/`)."""
        if self._vaults is None or not (task.result or "").strip():
            return ""
        rel_path = self._task_vault_rel_path(task)
        try:
            completed_iso = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(task.updated_at or time.time()))
            lines = [
                f"# {task.title}",
                "",
                f"- **Task ID:** `{task.id}`",
                f"- **Status:** `{task.status}`",
                f"- **Completed:** {completed_iso}",
            ]
            if task.description.strip():
                lines.extend(["", "## Description", "", task.description.strip()])
            lines.extend(["", "## Result", "", task.result.strip(), ""])
            self._vaults.write_text(task.brain, rel_path, "\n".join(lines))
            return rel_path
        except Exception:  # noqa: BLE001
            return ""

    def sync_done_tasks_to_vault(self, brain: str | None = None) -> list[str]:
        """Backfill already-completed Kanban tasks that are not yet saved in `wiki/tasks/`."""
        if self._vaults is None:
            return []
        saved: list[str] = []
        for task in self.list_tasks(brain=brain, limit=200):
            if task.status != "done" or not (task.result or "").strip():
                continue
            rel_path = self._task_vault_rel_path(task)
            try:
                root = self._vaults.brain_path(task.brain)
                tasks_dir = root / "wiki" / "tasks"
                already_exists = tasks_dir.exists() and any(tasks_dir.glob(f"{task.id}*.md"))
                if already_exists:
                    continue
            except Exception:  # noqa: BLE001
                continue
            written = self._save_task_to_vault(task)
            if written:
                saved.append(written)
        return saved

    @staticmethod
    def _parse_acceptance(raw: str) -> tuple[bool, str]:
        m = _JSON_OBJ_RE.search(raw or "")
        if not m:
            return True, ""
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict) and "pass" in obj:
                return bool(obj["pass"]), str(obj.get("reason") or "")
        except json.JSONDecodeError:
            pass
        return True, ""

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> KanbanTask:
        try:
            qa = json.loads(row["qa_log_json"] or "[]")
        except json.JSONDecodeError:
            qa = []
        return KanbanTask(
            id=row["id"],
            brain=row["brain"],
            title=row["title"],
            description=row["description"],
            spec=row["spec"],
            status=row["status"],
            mode=row["mode"],
            route=row["route"],
            owner_chat=row["owner_chat"],
            result=row["result"],
            block_reason=row["block_reason"],
            qa_log=qa if isinstance(qa, list) else [],
            attempts=int(row["attempts"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )
