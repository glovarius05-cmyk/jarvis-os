"""Tests for Core Memory (Letta / MemGPT style self-editing state)."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.brain.core_memory import CoreMemoryManager
from app.brain.prompt_builder import PromptBuilder
from app.brain.vault import VaultManager
from app.container import AppContainer
from app.hub.builtin_tools import build_builtin_tools
from app.hub.shaper import ToolResultStore
from app.main import create_app
from app.security.secrets import SecretStore


def test_core_memory_scaffold_parse_and_render(tmp_path: Path) -> None:
    vaults = VaultManager(tmp_path / "brains")
    vaults.ensure_brain("Brain Default", init_git=False)
    cm = CoreMemoryManager(vaults)

    # First get_raw should auto-scaffold
    raw = cm.get_raw("Brain Default")
    assert "# Core Memory (Always-in-Context)" in raw
    assert "## User Profile" in raw
    assert "## Persona" in raw
    assert "## Context & Goals" in raw

    sections = cm.parse_sections("Brain Default")
    assert len(sections["user"]) > 0
    assert len(sections["persona"]) > 0
    assert len(sections["context"]) > 0


def test_core_memory_remember_and_forget(tmp_path: Path) -> None:
    vaults = VaultManager(tmp_path / "brains")
    vaults.ensure_brain("Brain Default", init_git=False)
    cm = CoreMemoryManager(vaults)

    # 1. Add new preference
    res1 = cm.remember("Brain Default", "Coffee preference: Espresso without sugar", section="user")
    assert "Added to" in res1
    sections = cm.parse_sections("Brain Default")
    assert "Coffee preference: Espresso without sugar" in sections["user"]

    # 2. Update existing key:value preference
    res2 = cm.remember("Brain Default", "Coffee preference: Flat white with oat milk", section="user")
    assert "Updated" in res2
    sections2 = cm.parse_sections("Brain Default")
    assert "Coffee preference: Flat white with oat milk" in sections2["user"]
    assert "Coffee preference: Espresso without sugar" not in sections2["user"]

    # 3. Add to persona
    res3 = cm.remember("Brain Default", "Motto: Precision over hype", section="persona")
    assert "Added to" in res3
    sections3 = cm.parse_sections("Brain Default")
    assert "Motto: Precision over hype" in sections3["persona"]

    # 4. Forget a fact
    res4 = cm.forget("Brain Default", "Flat white", section="user")
    assert "Removed 1 fact(s)" in res4
    sections4 = cm.parse_sections("Brain Default")
    assert not any("Flat white" in f for f in sections4["user"])


def test_prompt_builder_injects_core_memory(tmp_path: Path, project_root: Path) -> None:
    vaults = VaultManager(tmp_path / "brains")
    vaults.ensure_brain("Brain Default", init_git=False)
    cm = CoreMemoryManager(vaults)
    cm.remember("Brain Default", "Owner favorite editor: Neovim", section="user")

    builder = PromptBuilder(project_root / "prompts", vaults, core_memory=cm)
    prompt = builder.build(
        brain="Brain Default",
        provider="openrouter",
        model="openrouter/auto",
        channel="web",
    )

    assert "# === CORE MEMORY (Self-Editing Persistent State) ===" in prompt
    assert "Owner favorite editor: Neovim" in prompt
    assert "# === LONG-TERM MEMORY (memory/MEMORY.md) ===" in prompt


@pytest.mark.asyncio
async def test_builtin_core_memory_tools(tmp_path: Path) -> None:
    vaults = VaultManager(tmp_path / "brains")
    vaults.ensure_brain("Brain Default", init_git=False)
    cm = CoreMemoryManager(vaults)
    res_store = ToolResultStore(tmp_path / "state", SecretStore(tmp_path / "secrets"))

    tools = build_builtin_tools(
        vaults=vaults,
        result_store=res_store,
        list_connections_fn=lambda: [],
        core_memory_getter=lambda: cm,
    )
    tool_map = {t.name: t for t in tools}
    assert "jarvis_remember" in tool_map
    assert "jarvis_forget" in tool_map
    assert "jarvis_core_memory_view" in tool_map

    # Test remember tool
    remember_handler = tool_map["jarvis_remember"].handler
    res = await remember_handler({"fact": "Timezone: Europe/Berlin", "section": "user"}, "Brain Default")
    assert "[Core Memory]" in res
    assert "Timezone: Europe/Berlin" in cm.get_raw("Brain Default")

    # Test view tool
    view_handler = tool_map["jarvis_core_memory_view"].handler
    raw_view = await view_handler({}, "Brain Default")
    assert "Timezone: Europe/Berlin" in raw_view

    # Test forget tool
    forget_handler = tool_map["jarvis_forget"].handler
    f_res = await forget_handler({"query": "Berlin", "section": "user"}, "Brain Default")
    assert "[Core Memory] Removed" in f_res
    assert "Timezone: Europe/Berlin" not in cm.get_raw("Brain Default")


def test_core_memory_api_endpoints(tmp_path: Path) -> None:
    container = AppContainer.build(
        state_dir=tmp_path / "state",
        brains_dir=tmp_path / "brains",
        force_login=False,
    )
    container.vaults.ensure_brain("Brain Default", init_git=False)
    app = create_app(container)
    client = TestClient(app)

    # GET /api/brains/core-memory
    r1 = client.get("/api/brains/core-memory")
    assert r1.status_code == 200
    data1 = r1.json()
    assert "sections" in data1
    assert "user" in data1["sections"]

    # POST /api/brains/core-memory/remember
    r2 = client.post("/api/brains/core-memory/remember", json={"fact": "Pet: Cat named Luna", "section": "user"})
    assert r2.status_code == 200
    assert r2.json()["ok"] is True
    assert "Luna" in r2.json()["view"]["raw"]

    # POST /api/brains/core-memory/forget
    r3 = client.post("/api/brains/core-memory/forget", json={"query": "Luna", "section": "user"})
    assert r3.status_code == 200
    assert "Luna" not in r3.json()["view"]["raw"]
