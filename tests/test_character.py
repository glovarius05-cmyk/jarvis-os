"""Unit tests for Brain Persona and Character Card system (CHARACTER.md)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.brain.character import (
    CHARACTER_TEMPLATES,
    BrainCharacter,
    CharacterManager,
    parse_character_markdown,
    render_character_markdown,
)
from app.brain.prompt_builder import PromptBuilder
from app.brain.vault import VaultManager
from app.container import AppContainer
from app.hub.builtin_tools import build_builtin_tools
from app.hub.shaper import ToolResultStore
from app.main import create_app
from app.security.secrets import SecretStore


def test_character_frontmatter_parse_and_render_roundtrip():
    char = BrainCharacter(
        name="Security Sentinel",
        role="Lead Vulnerability Auditor",
        tone="Rigorous, analytical, direct",
        avatar="🛡️",
        expertise=["Threat Modeling", "SSRF Defense", "Memory Safety"],
        directives=["Never use em-dashes", "Verify input schemas"],
        bio="Specialized AI agent dedicated to security verification.",
    )
    rendered = render_character_markdown(char)
    assert "---" in rendered
    assert "Security Sentinel" in rendered
    assert "🛡️" in rendered

    parsed = parse_character_markdown(rendered)
    assert parsed.name == "Security Sentinel"
    assert parsed.role == "Lead Vulnerability Auditor"
    assert parsed.tone == "Rigorous, analytical, direct"
    assert parsed.avatar == "🛡️"
    assert "SSRF Defense" in parsed.expertise
    assert "Verify input schemas" in parsed.directives
    assert "Specialized AI agent" in parsed.bio


def test_character_manager_scaffolds_default_and_applies_templates(tmp_path: Path):
    brains_dir = tmp_path / "brains"
    vaults = VaultManager(brains_dir)
    brain_name = "CharacterTestBrain"
    vaults.ensure_brain(brain_name, init_git=False)

    mgr = CharacterManager(vaults)

    # 1. First read should auto-scaffold default character card
    char = mgr.get_character(brain_name)
    assert char.name == "Jarvis OS"
    assert (brains_dir / brain_name / "memory" / "CHARACTER.md").exists()

    # 2. Render prompt block
    prompt_block = mgr.render_prompt_block(brain_name)
    assert "# === BRAIN PERSONA & CHARACTER CARD (Jarvis OS) ===" in prompt_block
    assert "- Role:" in prompt_block
    assert "- Communication Style:" in prompt_block

    # 3. Apply 'architect' template
    arch = mgr.apply_template(brain_name, "architect")
    assert arch.name == "Chief Architect & Security Auditor"
    assert "🛡️" in arch.avatar
    assert "Threat Modeling" in str(arch.expertise)

    # Verify persisted on disk
    reloaded = mgr.get_character(brain_name)
    assert reloaded.name == "Chief Architect & Security Auditor"

    # 4. List templates
    templates = mgr.list_templates()
    template_ids = {t["id"] for t in templates}
    assert {"default", "architect", "researcher", "writer", "executive"}.issubset(template_ids)


def test_prompt_builder_injects_character_card(tmp_path: Path):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "kernel.md").write_text("# KERNEL INSTRUCTIONS", encoding="utf-8")

    brains_dir = tmp_path / "brains"
    vaults = VaultManager(brains_dir)
    brain_name = "TestBrain"
    vaults.ensure_brain(brain_name, init_git=False)

    mgr = CharacterManager(vaults)
    mgr.apply_template(brain_name, "researcher")

    builder = PromptBuilder(
        prompts_dir,
        vaults,
        characters=mgr,
    )
    prompt = builder.build(
        brain=brain_name,
        provider="openrouter",
        model="deepseek/deepseek-chat",
    )
    assert "# === BRAIN PERSONA & CHARACTER CARD (Deep Research Analyst) ===" in prompt
    assert "- Role: Autonomous Science & Technology Researcher" in prompt
    assert "- Communication Style: Scholarly, fact-driven, rigorous citation, balanced critique" in prompt


async def test_builtin_character_tools(tmp_path: Path):
    brains_dir = tmp_path / "brains"
    vaults = VaultManager(brains_dir)
    brain_name = "ToolsBrain"
    vaults.ensure_brain(brain_name, init_git=False)

    state_dir = tmp_path / "state"
    secrets = SecretStore(state_dir)
    res_store = ToolResultStore(state_dir, secrets)
    mgr = CharacterManager(vaults)

    tools = build_builtin_tools(
        vaults=vaults,
        result_store=res_store,
        list_connections_fn=lambda: [],
        character_getter=lambda: mgr,
    )
    tool_map = {t.name: t for t in tools}
    assert "jarvis_character_view" in tool_map
    assert "jarvis_character_apply_template" in tool_map

    # 1. View default character
    view_res = await tool_map["jarvis_character_view"].handler({}, brain_name)
    assert "Active Character: Jarvis OS" in view_res

    # 2. Switch to writer template
    apply_res = await tool_map["jarvis_character_apply_template"].handler(
        {"template_id": "writer"}, brain_name
    )
    assert "Applied character template 'writer'" in apply_res
    assert "Content & Strategy Director" in apply_res

    # 3. View updated character
    view_updated = await tool_map["jarvis_character_view"].handler({}, brain_name)
    assert "Active Character: Content & Strategy Director" in view_updated


def test_character_api_endpoints(tmp_path: Path):
    state_dir = tmp_path / "state"
    brains_dir = tmp_path / "brains"
    container = AppContainer.build(
        state_dir=state_dir,
        brains_dir=brains_dir,
        force_login=False,
    )
    app = create_app(container, enable_scheduler=False)
    client = TestClient(app)

    # 1. GET /api/brains/character/templates
    res_tpl = client.get("/api/brains/character/templates")
    assert res_tpl.status_code == 200
    templates = res_tpl.json()["templates"]
    assert len(templates) >= 5

    # 2. GET /api/brains/character
    res_get = client.get("/api/brains/character")
    assert res_get.status_code == 200
    data = res_get.json()
    assert data["character"]["name"] == "Jarvis OS"

    # 3. POST /api/brains/character/template (switch to executive)
    res_post = client.post("/api/brains/character/template", json={"template_id": "executive"})
    assert res_post.status_code == 200
    assert res_post.json()["character"]["name"] == "Executive Chief of Staff"

    # 4. Error on unknown template
    res_err = client.post("/api/brains/character/template", json={"template_id": "unknown_ninja"})
    assert res_err.status_code == 400

    # 5. PUT /api/brains/character (custom markdown edit)
    custom_md = (
        "---\n"
        "name: Custom Bot\n"
        "role: Specialist\n"
        "tone: Calm\n"
        "avatar: 🎯\n"
        "---\n\n"
        "# Identity & Purpose\nCustom purpose.\n"
    )
    res_put = client.put("/api/brains/character", json={"content": custom_md})
    assert res_put.status_code == 200
    assert res_put.json()["character"]["name"] == "Custom Bot"
