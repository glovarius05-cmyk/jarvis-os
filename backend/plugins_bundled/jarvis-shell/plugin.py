"""Bundled plugin: run_command - fenced shell tool available strictly in Full permission mode."""

from __future__ import annotations

import shlex
from app.runtime.terminal import run_fenced_command


def register(ctx) -> None:
    async def run_command(args: dict, brain: str) -> str:
        cmd = str(args.get("command") or "").strip()
        if not cmd:
            return "ERROR: command is required."
        timeout = float(args.get("timeout_s") or 30.0)
        target_env = str(args.get("target_env") or "local").lower().strip()

        effective_cmd = cmd
        if target_env == "docker":
            container = str(args.get("container") or "").strip()
            if not container:
                return "ERROR: 'container' name or ID is required when target_env is 'docker'."
            effective_cmd = f"docker exec {container} sh -c {shlex.quote(cmd)}"
        elif target_env == "ssh":
            ssh_host = str(args.get("ssh_host") or "").strip()
            if not ssh_host:
                return "ERROR: 'ssh_host' (e.g. user@hostname) is required when target_env is 'ssh'."
            effective_cmd = f"ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new {ssh_host} {shlex.quote(cmd)}"

        vaults = ctx.services.get("vaults")
        hub = ctx.services.get("hub")
        settings_store = ctx.services.get("settings")
        if vaults is None:
            return "ERROR: VaultManager unavailable."
        cwd = vaults.brain_path(brain)
        extra = [hub.hub_token()] if hub is not None else []

        sandbox_cfg: dict = {}
        if settings_store is not None:
            try:
                sandbox_cfg = settings_store.load().get("sandbox", {}) if hasattr(settings_store, "load") else {}
            except Exception:
                sandbox_cfg = {}

        mode = "docker" if target_env == "docker" and not args.get("container") else str(sandbox_cfg.get("mode") or "local").lower()
        res = await run_fenced_command(
            effective_cmd,
            cwd=cwd,
            timeout_s=timeout,
            extra_secrets=extra,
            sandbox_mode=mode if target_env != "ssh" else "local",
            docker_image=str(sandbox_cfg.get("docker_image") or "python:3.12-slim"),
            docker_network=str(sandbox_cfg.get("network") or "none"),
            memory_limit=str(sandbox_cfg.get("memory_limit") or "512m"),
            cpus=str(sandbox_cfg.get("cpus") or "1.0"),
        )
        status = f"exit_code={res.exit_code}" + (" (TIMED OUT)" if res.timed_out else "")
        prefix = f"[{target_env.upper()}] " if target_env != "local" else (f"[DOCKER-SANDBOX] " if res.sandbox == "docker" else "")
        out = f"{prefix}{status}\n{res.stdout}"
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
        description="Execute a shell command locally, in a Docker container, or over SSH (Full access only, secrets redacted).",
        handler=run_command,
        min_mode="full",
        schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"},
                "timeout_s": {"type": "number", "default": 30},
                "target_env": {
                    "type": "string",
                    "enum": ["local", "docker", "ssh"],
                    "default": "local",
                    "description": "Execution environment: 'local', 'docker', or 'ssh'",
                },
                "container": {"type": "string", "description": "Docker container name or ID when target_env is docker"},
                "ssh_host": {"type": "string", "description": "Remote SSH destination (user@host) when target_env is ssh"},
            },
            "required": ["command"],
        },
    )
