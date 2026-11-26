import asyncio
from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient

from app.container import AppContainer
from app.hub.approval import ApprovalManager
from app.hub.browser_agent import run_browser_task
from app.hub.permissions import PermLevel
from app.main import create_app
from app.runtime.terminal import build_docker_sandbox_argv, run_fenced_command


@pytest.mark.asyncio
async def test_approval_manager_approve_flow():
    mgr = ApprovalManager(default_timeout_s=5.0)
    notified = []
    mgr.add_notifier(lambda ev: notified.append(ev))

    async def _request():
        return await mgr.request_approval(
            session_id="s1",
            brain="Brain Default",
            tool_name="tool_shell_execute",
            args={"cmd": "ls"},
            reason="Testing approval",
        )

    task = asyncio.create_task(_request())
    await asyncio.sleep(0.05)

    pending = mgr.list_pending("s1")
    assert len(pending) == 1
    assert pending[0]["tool_name"] == "tool_shell_execute"
    assert pending[0]["status"] == "pending"

    # Resolve approval
    aid = pending[0]["approval_id"]
    resolved = await mgr.resolve(aid, approved=True)
    assert resolved is not None
    assert resolved["status"] == "approved"

    approved, reason = await task
    assert approved is True
    assert "approved" in reason
    assert len(mgr.list_pending()) == 0
    assert any(ev.get("type") == "approval_request" for ev in notified)
    assert any(ev.get("type") == "approval_resolved" and ev.get("status") == "approved" for ev in notified)


@pytest.mark.asyncio
async def test_approval_manager_deny_flow():
    mgr = ApprovalManager(default_timeout_s=5.0)

    async def _request():
        return await mgr.request_approval(
            session_id="s2",
            brain="Brain Default",
            tool_name="delete_database",
            args={},
        )

    task = asyncio.create_task(_request())
    await asyncio.sleep(0.05)

    pending = mgr.list_pending("s2")
    assert len(pending) == 1
    aid = pending[0]["approval_id"]

    await mgr.resolve(aid, approved=False)
    approved, reason = await task
    assert approved is False
    assert "denied" in reason


@pytest.mark.asyncio
async def test_approval_manager_timeout_flow():
    mgr = ApprovalManager(default_timeout_s=0.1)
    approved, reason = await mgr.request_approval(
        session_id="s3",
        brain="Brain Default",
        tool_name="slow_tool",
        args={},
        timeout_s=0.1,
    )
    assert approved is False
    assert "timed out" in reason.lower()
    assert len(mgr.list_pending()) == 0


@pytest.mark.asyncio
async def test_approval_rest_routes(tmp_path: Path):
    ctr = AppContainer.build(
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        force_login=False,
    )
    app = create_app(ctr, enable_scheduler=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://localhost:7777") as client:
        # Trigger an approval request in background
        async def _req():
            return await ctr.approvals.request_approval(
                session_id="sess_api",
                brain="Brain Default",
                tool_name="system_reboot",
                args={},
                timeout_s=5.0,
            )

        task = asyncio.create_task(_req())
        await asyncio.sleep(0.05)

        # GET /api/hub/approvals/pending
        resp = await client.get("/api/hub/approvals/pending")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["pending"]) == 1
        aid = data["pending"][0]["approval_id"]

        # POST /api/hub/approvals/{id}/resolve
        resp2 = await client.post(
            f"/api/hub/approvals/{aid}/resolve",
            json={"decision": "approve"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["ok"] is True

        approved, _ = await task
        assert approved is True


@pytest.mark.asyncio
async def test_hub_tool_execution_approval_gate(tmp_path: Path):
    ctr = AppContainer.build(
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        force_login=False,
    )
    # Enable require_for_full_tools in settings
    ctr.settings.update({
        "approval": {
            "require_for_full_tools": True,
            "timeout_s": 5.0,
            "auto_approve_tools": [],
        }
    })

    # Calling a FULL mode tool should require approval
    async def _call():
        return await ctr.hub.call_tool(
            name="jarvis_browser_task",
            args={"task": "do something sensitive"},
            brain="Brain Default",
            run_mode="full",
            session_id="sess_gate",
        )

    task = asyncio.create_task(_call())
    await asyncio.sleep(0.05)

    pending = ctr.approvals.list_pending("sess_gate")
    assert len(pending) == 1
    aid = pending[0]["approval_id"]

    # Deny it
    await ctr.approvals.resolve(aid, approved=False)
    result = await task
    assert "ERROR: Execution of 'jarvis_browser_task' denied" in result


@pytest.mark.asyncio
async def test_browser_agent_ssrf_safety():
    # Private IP blocked
    res = await run_browser_task(task="inspect router", url="http://192.168.1.1/admin")
    assert res["ok"] is False
    assert "SSRF block" in res["error"]

    # Loopback blocked
    res2 = await run_browser_task(task="read internal", url="http://127.0.0.1:8080/mcp")
    assert res2["ok"] is False
    assert "SSRF block" in res2["error"]

    # Cloud metadata blocked
    res3 = await run_browser_task(task="get keys", url="http://169.254.169.254/latest/meta-data")
    assert res3["ok"] is False
    assert "SSRF block" in res3["error"]


@pytest.mark.asyncio
async def test_browser_agent_builtin_tools_registration(tmp_path: Path):
    ctr = AppContainer.build(
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        force_login=False,
    )
    defs = await ctr.hub.all_tool_definitions("Brain Default")
    assert "jarvis_browser_task" in defs
    assert "jarvis_browser_navigate" in defs

    task_tool = defs["jarvis_browser_task"]
    assert task_tool.required_perm == PermLevel.FULL


def test_docker_sandbox_argv_builder(tmp_path: Path):
    argv = build_docker_sandbox_argv(
        "echo hello",
        cwd=tmp_path,
        image="alpine:latest",
        network="none",
        memory_limit="256m",
        cpus="0.5",
    )
    assert argv[0] == "docker"
    assert argv[1] == "run"
    assert "--rm" in argv
    assert "--network" in argv and "none" in argv
    assert "--memory" in argv and "256m" in argv
    assert "--cpus" in argv and "0.5" in argv
    assert "--cap-drop" in argv and "ALL" in argv
    assert "--security-opt" in argv and "no-new-privileges" in argv
    assert f"{tmp_path.resolve()}:/workspace:rw" in argv
    assert argv[-1] == "echo hello"


@pytest.mark.asyncio
async def test_fenced_command_local_execution(tmp_path: Path):
    res = await run_fenced_command("echo 'hello stage2'", cwd=tmp_path, sandbox_mode="local")
    assert res.exit_code == 0
    assert "hello stage2" in res.stdout
    assert res.sandbox == "local"


@pytest.mark.asyncio
async def test_destructive_composio_action_triggers_approval(tmp_path: Path):
    ctr = AppContainer.build(
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        force_login=False,
    )
    executed = []

    from app.hub.builtin_tools import ToolDefinition

    async def _fake_composio(args: dict, _brain: str) -> str:
        executed.append(args.get("action_name"))
        return '{"ok": true}'

    ctr.hub._builtins.append(
        ToolDefinition(
            name="composio_action",
            description="fake composio action",
            parameters={"type": "object", "properties": {"action_name": {"type": "string"}}},
            required_perm=PermLevel.FULL,
            source="composio",
            handler=_fake_composio,
        )
    )

    # 1. Read-only action should execute immediately without approval even in full mode
    read_res = await ctr.hub.call_tool(
        brain="Brain Default",
        name="composio_action",
        args={"action_name": "GMAIL_FETCH_EMAILS"},
        run_mode="full",
        session_id="s-safe",
    )
    assert '"ok": true' in read_res or "ok" in read_res
    assert executed == ["GMAIL_FETCH_EMAILS"]

    # 2. Destructive action should block for human approval even though require_for_full_tools=False
    task = asyncio.create_task(
        ctr.hub.call_tool(
            brain="Brain Default",
            name="composio_action",
            args={"action_name": "CLOUDFLARE_DELETE_DNS_RECORD"},
            run_mode="full",
            session_id="s-destr",
        )
    )
    await asyncio.sleep(0.05)

    pending = ctr.approvals.list_pending("s-destr")
    assert len(pending) == 1
    assert "Destructive external action" in pending[0]["reason"]
    await ctr.approvals.resolve(pending[0]["approval_id"], approved=False)

    destr_res = await task
    assert "denied" in destr_res.lower()
    assert executed == ["GMAIL_FETCH_EMAILS"]
