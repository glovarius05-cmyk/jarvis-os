"""Tests for Skills sync/truncation and PluginHost safety, hooks, and jarvis_plugin."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.brain.skills import SKILL_DESC_MAX
from app.container import AppContainer


def test_system_skills_sync_preserves_user_edits_and_caps_description(
    tmp_path: Path, project_root: Path
) -> None:
    ctr = AppContainer.build(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
    )
    skills = ctr.skills.list_skills("Brain Default")
    slugs = {s.slug for s in skills}
    assert {"jarvis-builder", "ingest-source", "query-wiki"} <= slugs

    # Edit a synced system skill; subsequent sync must NOT overwrite the user's edit
    custom_body = "---\nname: Custom Builder\ndescription: User edited version\ngroup: AI\n---\n\nKeep me!\n"
    ctr.vaults.write_text("Brain Default", "skills/jarvis-builder/SKILL.md", custom_body)
    ctr.skills.sync_system_skills("Brain Default")
    assert "Keep me!" in (ctr.skills.read_skill_body("Brain Default", "jarvis-builder") or "")

    # Long descriptions (>150 chars) are truncated in the prompt index
    long_desc = "A" * 220
    ctr.vaults.write_text(
        "Brain Default",
        "skills/long-skill/SKILL.md",
        f"---\nname: Long\ndescription: {long_desc}\ngroup: Test\n---\n\nBody\n",
    )
    prompt = ctr.prompt_builder.build(brain="Brain Default", provider="openrouter", model="openrouter/auto")
    assert "# === AVAILABLE SKILLS ===" in prompt
    assert ("A" * SKILL_DESC_MAX) in prompt
    assert ("A" * (SKILL_DESC_MAX + 5)) not in prompt


@pytest.mark.asyncio
async def test_bundled_plugins_user_plugin_safety_and_hooks(
    tmp_path: Path, project_root: Path
) -> None:
    ctr = AppContainer.build(
        code_dir=project_root,
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
    )

    # Bundled tools work right out of the box through MCP Hub
    now_res = await ctr.hub.call_tool(name="jarvis_now", args={"timezone": "UTC"}, brain="Brain Default", run_mode="suggest")
    assert "UTC" in now_res

    skill_res = await ctr.hub.call_tool(
        name="use_skill",
        args={"slug": "jarvis-builder"},
        brain="Brain Default",
        run_mode="suggest",
    )
    assert "Jarvis Builder" in skill_res

    # jarvis_plugin rejects bad Python syntax
    bad_syntax = await ctr.hub.call_tool(
        name="jarvis_plugin",
        args={
            "op": "write",
            "slug": "bad-plug",
            "files": {
                "plugin.yaml": "name: Bad\nslug: bad-plug\nenabled: true\nmin_mode: readonly\n",
                "plugin.py": "def register(ctx:\n  pass",
            },
        },
        brain="Brain Default",
        run_mode="auto",
    )
    assert "syntax error" in bad_syntax.lower()

    # jarvis_plugin blocks overwriting bundled system plugin
    sys_clone = await ctr.hub.call_tool(
        name="jarvis_plugin",
        args={
            "op": "write",
            "slug": "datetime-now",
            "files": {"plugin.yaml": "name: Clone\n", "plugin.py": "def register(ctx): pass\n"},
        },
        brain="Brain Default",
        run_mode="auto",
    )
    assert "Cannot overwrite or clone bundled system plugin" in sys_clone

    # Valid user plugin with hook + tool MUST be saved with enabled: false even if caller passed enabled: true!
    write_ok = await ctr.hub.call_tool(
        name="jarvis_plugin",
        args={
            "op": "write",
            "slug": "math-hook",
            "scope": "global",
            "files": {
                "plugin.yaml": "name: Math Hook\nslug: math-hook\nenabled: true\nmin_mode: readonly\n",
                "plugin.py": (
                    "def register(ctx):\n"
                    "    def triple(args, brain):\n"
                    "        return str(int(args.get('n', 0)) * 3)\n"
                    "    ctx.register_tool(name='math_triple', description='Multiply by 3', handler=triple, min_mode='readonly')\n"
                    "    def on_post(tool_name='', **_):\n"
                    "        (ctx.data_dir / 'hook.log').write_text(tool_name, encoding='utf-8')\n"
                    "    ctx.register_hook('post_tool_call', on_post)\n"
                ),
            },
        },
        brain="Brain Default",
        run_mode="auto",
    )
    assert "enabled=false" in write_ok

    # Before human enables it, tool MUST NOT be callable
    not_active = await ctr.hub.call_tool(name="math_triple", args={"n": 7}, brain="Brain Default", run_mode="full")
    assert "Unknown tool" in not_active

    # Human enables plugin -> tool & post_tool_call hook both fire cleanly
    toggled = ctr.plugins.toggle_plugin("math-hook", True, brain="Brain Default")
    assert toggled is not None and toggled.enabled is True

    tripled = await ctr.hub.call_tool(name="math_triple", args={"n": 7}, brain="Brain Default", run_mode="suggest")
    assert tripled == "21"
    hook_file = tmp_path / "state" / "plugin-data" / "math-hook" / "hook.log"
    assert hook_file.read_text(encoding="utf-8") == "math_triple"
