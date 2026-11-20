"""Bundled plugin: run_command - fenced shell tool available strictly in Full permission mode."""

from __future__ import annotations

from app.runtime.terminal import run_fenced_command


def register(ctx) -> None:
    async def run_command(args: dict, brain: str) -> str:
        cmd = str(args.get("command") or "").strip()
        if not cmd:
            return "ERROR: command is required."
        timeout = float(args.get("timeout_s") or 30.0)
        vaults = ctx.services.get("vaults")
        hub = ctx.services.get("hub")
        if vaults is None:
            return "ERROR: VaultManager unavailable."
        cwd = vaults.brain_path(brain)
        extra = [hub.hub_token()] if hub is not None else []
        res = await run_fenced_command(cmd, cwd=cwd, timeout_s=timeout, extra_secrets=extra)
        status = f"exit_code={res.exit_code}" + (" (TIMED OUT)" if res.timed_out else "")
        out = f"{status}\n{res.stdout}"
        if res.stderr:
            out += f"\n[stderr]\n{res.stderr}"

        # Self-Healing Memory lookup: if command failed, check if we have a known solution!
        if res.exit_code != 0:
            exp = ctx.services.get("experience")
            if exp is not None:
                err_text = (res.stderr or res.stdout or "").strip()
                solutions = exp.find_solutions(err_text, failing_cmd=cmd, limit=2)
                if solutions:
                    sol = solutions[0]
                    conf_pct = int(sol.get("confidence_score", 1.0) * 100)
                    out += (
                        f"\n\n[💡 Jarvis Self-Healing Memory]: Найдено проверенное решение из базы опыта (уверенность {conf_pct}%):\n"
                        f"{sol.get('solution_patch')}"
                    )
        return out.strip()

    ctx.register_tool(
        name="run_command",
        description="Execute one shell command inside the active Second Brain directory (Full access only, secrets auto-redacted).",
        handler=run_command,
        min_mode="full",
        schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"},
                "timeout_s": {"type": "number", "default": 30},
            },
            "required": ["command"],
        },
    )
