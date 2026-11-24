"""Deterministic system prompt assembly for every engine and channel.

Order matches the Jarvis architecture contract:
  1. Prompt kernel (prompts/kernel.md)
  2. Active runtime identity (engine + model + active brain)
  3. Long-term memory index (<brain>/memory/MEMORY.md)
  4. Channel context block (web / telegram / cli / background)
  5. Language directive block
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from app.brain.vault import VaultManager

if TYPE_CHECKING:
    from app.brain.character import CharacterManager
    from app.brain.core_memory import CoreMemoryManager
    from app.brain.skills import SkillStore
    from app.config import SettingsStore

MEMORY_MAX_CHARS = 8_000

_CHANNEL_INSTRUCTIONS: dict[str, str] = {
    "web": (
        "Channel: Web Dashboard. Markdown headings, concise bullets, code blocks, and comparison tables "
        "are supported. Remember that session_id is available for routing background results back here."
    ),
    "telegram": (
        "Channel: Telegram (plain text / lightweight formatting). Avoid wide Markdown tables; keep "
        "messages compact and scannable on a phone screen."
    ),
    "cli": "Channel: Terminal CLI. Keep output concise, structured, and easy to read in a terminal.",
    "background": (
        "Channel: Autonomous Background Worker. No human is watching live; do not ask conversational "
        "questions unless genuinely blocked (use [[NEEDS_INPUT]] at line start)."
    ),
}


class PromptBuilder:
    """Assembles the system prompt from the kernel and the active Second Brain vault."""

    def __init__(
        self,
        prompts_dir: Path,
        vaults: VaultManager,
        skills: "SkillStore | None" = None,
        core_memory: "CoreMemoryManager | None" = None,
        characters: "CharacterManager | None" = None,
        settings: "SettingsStore | None" = None,
    ) -> None:
        self._kernel_path = prompts_dir / "kernel.md"
        self._vaults = vaults
        self._skills = skills
        self._core_memory = core_memory
        self._characters = characters
        self._settings = settings

    def build(
        self,
        *,
        brain: str,
        provider: str,
        model: str,
        channel: str = "web",
        session_id: str = "",
        pinned_lang: str = "",
    ) -> str:
        sections: list[str] = []

        # 1. Kernel
        kernel = self._kernel_path.read_text(encoding="utf-8").strip()
        sections.append(kernel)

        # 2. Active runtime badge
        sections.append(
            "# === RUNTIME CONTEXT ===\n"
            f"- Active engine: `{provider}`\n"
            f"- Active model: `{model}`\n"
            f"- Active Second Brain: `{brain}`\n"
            + (f"- Current session id: `{session_id}`\n" if session_id else "")
        )

        # 2b. Core Memory (Always-in-context self-editing state in style of MemGPT/Letta)
        core_mem = ""
        if self._core_memory is not None:
            core_mem = self._core_memory.get_raw(brain).strip()
        else:
            try:
                core_mem = self._vaults.read_text(brain, "memory/CORE_MEMORY.md").strip()
            except OSError:
                core_mem = ""
        if core_mem:
            sections.append(
                "# === CORE MEMORY (Self-Editing Persistent State) ===\n"
                "The following Core Memory blocks represent persistent, verified knowledge about the owner and your operational persona. "
                "You have native self-editing capabilities: when the user mentions personal preferences, habits, technical stacks, or rules, "
                "call the `jarvis_remember` or `jarvis_forget` tool to keep this memory up-to-date across sessions.\n\n"
                f"{core_mem}"
            )

        # 2c. Brain Persona & Character Card (CHARACTER.md)
        char_block = ""
        if self._characters is not None:
            char_block = self._characters.render_prompt_block(brain).strip()
        else:
            try:
                char_text = self._vaults.read_text(brain, "memory/CHARACTER.md").strip()
                if char_text:
                    from app.brain.character import parse_character_markdown
                    char_obj = parse_character_markdown(char_text)
                    c_lines = [
                        f"# === BRAIN PERSONA & CHARACTER CARD ({char_obj.name}) ===",
                        f"- Role: {char_obj.role}",
                        f"- Communication Style: {char_obj.tone}",
                    ]
                    if char_obj.expertise:
                        c_lines.append(f"- Core Expertise: {', '.join(char_obj.expertise)}")
                    if char_obj.directives:
                        c_lines.append("- Operating Directives:")
                        for d in char_obj.directives:
                            c_lines.append(f"  * {d}")
                    if char_obj.bio:
                        c_lines.append(f"- Purpose: {char_obj.bio}")
                    char_block = "\n".join(c_lines)
            except OSError:
                char_block = ""
        if char_block:
            sections.append(char_block)

        # 3. Long-term memory index (MEMORY.md)
        try:
            memory_text = self._vaults.read_text(brain, "memory/MEMORY.md").strip()
        except OSError:
            memory_text = ""
        if memory_text:
            if len(memory_text) > MEMORY_MAX_CHARS:
                memory_text = memory_text[:MEMORY_MAX_CHARS] + "\n...[MEMORY.md truncated]..."
            sections.append(f"# === LONG-TERM MEMORY (memory/MEMORY.md) ===\n{memory_text}")

        # 3b. Compact Skill Index (each description <= 150 chars, single-line for local models)
        if self._skills is not None:
            skill_idx = self._skills.build_prompt_index(brain, compact=(provider == "local-llama"))
            if skill_idx:
                sections.append(f"# === AVAILABLE SKILLS ===\n{skill_idx}")

        # 3c. Connected user integrations & accounts (Gmail, Calendar, etc.)
        if self._settings is not None:
            try:
                cfg = self._settings.load()
                account_emails = cfg.get("composio_account_emails") or {}
                connected_apps = cfg.get("composio_connected_apps") or []
                if account_emails or connected_apps:
                    lines = ["# === CONNECTED USER ACCOUNTS & INTEGRATIONS ==="]
                    lines.append("The owner has connected and authorized the following external accounts via Composio:")
                    all_apps = set(connected_apps) | set(account_emails.keys())
                    for app in sorted(all_apps):
                        em = account_emails.get(app)
                        if em:
                            lines.append(f"- {app.upper()}: connected (user email / identifier: `{em}`)")
                        else:
                            lines.append(f"- {app.upper()}: connected")
                    lines.append("You can directly search emails, read messages, manage calendar, or perform actions for these accounts using available tools.")
                    sections.append("\n".join(lines))
            except Exception:
                pass

        # 4. Channel block
        ch_desc = _CHANNEL_INSTRUCTIONS.get(channel, _CHANNEL_INSTRUCTIONS["web"])
        sections.append(f"# === CURRENT CHANNEL ===\n{ch_desc}")

        # 5. Language block
        if pinned_lang:
            lang_block = f"Always reply in `{pinned_lang}` unless the user explicitly asks to switch."
        else:
            lang_block = (
                "Reply in the exact language the user wrote their latest message in. "
                "Keep code, file paths, and tool names untranslated."
            )
        sections.append(f"# === LANGUAGE ===\n{lang_block}")

        return "\n\n".join(sections)
