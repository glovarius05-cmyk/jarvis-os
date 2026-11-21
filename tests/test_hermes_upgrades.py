"""Tests for Hermes Agent upgrades:
1. Concurrent Tool Dispatcher
2. OpenAI Compatible /v1 API
3. Autonomous Skill Synthesizer
4. Dialectic User Profiling (Honcho-style)
5. Multi-Environment Execution (Docker / SSH parameterization in shell tool)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.brain.core_memory import CoreMemoryManager
from app.brain.skills import SkillStore
from app.brain.vault import VaultManager
from app.container import AppContainer
from app.engines.base import EngineEvent, EngineProtocol, EngineRequest
from app.main import create_app
from app.runtime.learn import SelfLearningEngine


def test_autonomous_skill_synthesizer(tmp_path: Path):
    brains_dir = tmp_path / "brains"
    state_dir = tmp_path / "state"
    code_dir = tmp_path / "code"
    (code_dir / "system" / "skills").mkdir(parents=True, exist_ok=True)

    vaults = VaultManager(brains_dir)
    vaults.ensure_brain("Brain Default")

    learner = SelfLearningEngine(
        state_dir=state_dir,
        vaults=vaults,
        sessions=AsyncMock(),
        router=AsyncMock(),
    )

    # 1. Synthesize valid skill
    res = learner.synthesize_skill(
        brain="Brain Default",
        name="CSV Data Cleaner",
        slug="csv-cleaner",
        description="Clean and normalize CSV columns for analytics",
        instructions="# CSV Cleaner\n\n1. Read CSV\n2. Strip whitespace\n3. Export clean CSV.",
        group="Data Engineering",
    )
    assert res["ok"] is True
    assert res["slug"] == "csv-cleaner"
    assert (brains_dir / "Brain Default" / "skills" / "csv-cleaner" / "SKILL.md").is_file()

    # 2. Reject skill with secrets
    res_bad = learner.synthesize_skill(
        brain="Brain Default",
        name="Bad Leaker",
        slug="bad-leaker",
        description="A bad skill",
        instructions="My secret is sk-ant-api03-1234567890abcdef1234",
    )
    assert res_bad["ok"] is False
    assert "secrets" in res_bad["error"]

    # 3. Verify SkillStore lists the newly synthesized skill
    skill_store = SkillStore(code_dir, vaults)
    skills = skill_store.list_skills("Brain Default")
    slugs = [s.slug for s in skills]
    assert "csv-cleaner" in slugs


def test_dialectic_user_profiling(tmp_path: Path):
    brains_dir = tmp_path / "brains"
    vaults = VaultManager(brains_dir)
    vaults.ensure_brain("Brain Default")

    cm = CoreMemoryManager(vaults)
    raw_before = cm.get_raw("Brain Default")
    assert "Language: Russian" in raw_before

    # Proactively profile user
    results = cm.profile_user(
        "Brain Default",
        [
            {"fact": "Preferred IDE: Cursor with MCP bridge", "section": "user"},
            {"fact": "Current Objective: Launch Jarvis OS v1.0.0", "section": "context"},
        ],
    )
    assert len(results) == 2

    raw_after = cm.get_raw("Brain Default")
    assert "Preferred IDE: Cursor with MCP bridge" in raw_after
    assert "Current Objective: Launch Jarvis OS v1.0.0" in raw_after


@pytest.mark.asyncio
async def test_builtin_hermes_tools_execution(tmp_path: Path):
    container = AppContainer.build(
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        force_login=False,
    )
    hub = container.hub

    # 1. jarvis_create_skill
    create_res = await hub.call_tool(
        name="jarvis_create_skill",
        args={
            "name": "Auto Unit Tester",
            "slug": "auto-unit-tester",
            "description": "Generate pytest unit tests for backend functions",
            "instructions": "Inspect function, create test_*.py, run pytest.",
        },
        brain="Brain Default",
    )
    assert "Successfully created skill 'Auto Unit Tester'" in create_res

    # 2. jarvis_profile_user
    profile_res = await hub.call_tool(
        name="jarvis_profile_user",
        args={
            "facts": [
                {"fact": "Coding Style: Strict typing and no em-dashes", "section": "user"}
            ]
        },
        brain="Brain Default",
    )
    assert "Dialectic user profiling updated" in profile_res
    cm_view = container.core_memory.get_raw("Brain Default")
    assert "Coding Style: Strict typing and no em-dashes" in cm_view


@pytest.mark.asyncio
async def test_run_command_target_env_docker_and_ssh(tmp_path: Path):
    container = AppContainer.build(
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        force_login=False,
    )
    hub = container.hub

    # Missing container in docker env -> error
    res_err1 = await hub.call_tool(
        name="run_command",
        args={"command": "ls", "target_env": "docker"},
        brain="Brain Default",
    )
    assert "ERROR: 'container' name or ID is required" in res_err1

    # Missing ssh_host in ssh env -> error
    res_err2 = await hub.call_tool(
        name="run_command",
        args={"command": "ls", "target_env": "ssh"},
        brain="Brain Default",
    )
    assert "ERROR: 'ssh_host'" in res_err2
