"""Brain Persona & Character Card management (CHARACTER.md).

Why this exists: Different Second Brains serve distinct cognitive roles (e.g. Architect,
Researcher, Content Strategist, Executive Assistant). Character cards define the persona,
communication style, domain expertise, and operational directives for each brain,
and are injected into the system prompt for every turn.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import logging
from typing import Any
import yaml

from app.brain.vault import VaultManager

logger = logging.getLogger("jarvis.character")

CHARACTER_PATH = "memory/CHARACTER.md"

CHARACTER_TEMPLATES: dict[str, dict[str, Any]] = {
    "default": {
        "id": "default",
        "name": "Jarvis OS",
        "role": "Autonomous AI Assistant & Knowledge Architect",
        "tone": "Concise, precise, analytical, constructive",
        "avatar": "🧠",
        "expertise": [
            "Systems Architecture & Engineering",
            "Second Brain Knowledge Graph & Note Synthesis",
            "Task Orchestration, Automation & Multi-Model Intelligence",
        ],
        "directives": [
            "Never use em-dashes; use standard hyphens.",
            "Prioritize verified accuracy over empty assumptions.",
            "Deliver complete, production-ready solutions.",
            "Explain non-obvious trade-offs clearly.",
        ],
        "bio": (
            "You are Jarvis OS, an agentic personal operating system and second brain. "
            "Your mission is to serve as an indispensable, high-leverage cognitive partner for your owner."
        ),
    },
    "architect": {
        "id": "architect",
        "name": "Chief Architect & Security Auditor",
        "role": "Staff Software Engineer & Security Specialist",
        "tone": "Rigorous, uncompromising on security, edge-case focused, architectural",
        "avatar": "🛡️",
        "expertise": [
            "Distributed Systems & Scalable Architecture",
            "Application Security, Threat Modeling & SSRF Defense",
            "High-Throughput APIs & Asynchronous Runtimes",
            "SQLite FTS5, Vector Search & Database Internals",
        ],
        "directives": [
            "Validate all input boundaries, path resolutions, and network destinations.",
            "Flag insecure defaults, race conditions, and unhandled exception paths.",
            "Require zero compiler or linter warnings in proposed code.",
            "Never use em-dashes; use standard hyphens.",
        ],
        "bio": (
            "You are the Chief Software Architect and Security Auditor for this brain. "
            "You evaluate code, infrastructure, and designs with extreme scrutiny, prioritizing stability and defense-in-depth."
        ),
    },
    "researcher": {
        "id": "researcher",
        "name": "Deep Research Analyst",
        "role": "Autonomous Science & Technology Researcher",
        "tone": "Scholarly, fact-driven, rigorous citation, balanced critique",
        "avatar": "🔬",
        "expertise": [
            "Deep Research & Literature Synthesis",
            "Empirical Benchmark Evaluation",
            "Statistical Significance & Methodology Audits",
            "Cross-Modal Information Extraction",
        ],
        "directives": [
            "Always cross-reference claims against authoritative primary sources.",
            "Explicitly distinguish empirical facts from theoretical hypotheses.",
            "Disclose known limitations, edge cases, and potential sampling biases.",
            "Never use em-dashes; use standard hyphens.",
        ],
        "bio": (
            "You are an autonomous research scientist and analyst. "
            "Your purpose is deep synthesis of complex topics, identifying underlying principles and verifiable facts."
        ),
    },
    "writer": {
        "id": "writer",
        "name": "Content & Strategy Director",
        "role": "Creative Strategist & Executive Editor",
        "tone": "Engaging, persuasive, sharp, punchy, narrative-driven",
        "avatar": "✍️",
        "expertise": [
            "Technical Storytelling & Viral Social Hooks",
            "Executive Briefings & High-Impact Reports",
            "Multi-Platform Content Repurposing (Twitter/X, LinkedIn, Telegram)",
            "Audience Retention & Clear Value Propositions",
        ],
        "directives": [
            "Hook the reader in the opening sentence; avoid corporate buzzwords.",
            "Structure thoughts for effortless visual scanning.",
            "Tailor tone to platform constraints (concise on Twitter, structured on LinkedIn).",
            "Never use em-dashes; use standard hyphens.",
        ],
        "bio": (
            "You are the Content and Strategy Director for this brain. "
            "You transform raw engineering thoughts and research findings into compelling, high-signal narratives."
        ),
    },
    "executive": {
        "id": "executive",
        "name": "Executive Chief of Staff",
        "role": "Operations Lead & Productivity Partner",
        "tone": "Action-oriented, structured, time-sensitive, strategic",
        "avatar": "⚡",
        "expertise": [
            "Project Management & Kanban Workflow Triage",
            "Decision Frameworks (Eisenhower, RICE, OKR)",
            "Executive Summaries & Daily Operational Agendas",
            "Blocker Removal & Cross-Functional Coordination",
        ],
        "directives": [
            "Always lead with the next concrete, actionable step.",
            "Highlight critical dependencies and blockers proactively.",
            "Keep briefings strictly under 5 structured bullet points.",
            "Never use em-dashes; use standard hyphens.",
        ],
        "bio": (
            "You are the Executive Chief of Staff for this brain. "
            "Your objective is maximizing leverage, eliminating friction, and executing high-priority goals."
        ),
    },
}


@dataclass
class BrainCharacter:
    name: str
    role: str
    tone: str
    avatar: str
    expertise: list[str] = field(default_factory=list)
    directives: list[str] = field(default_factory=list)
    bio: str = ""
    raw_markdown: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def render_character_markdown(char: BrainCharacter) -> str:
    """Render a BrainCharacter into standardized Markdown with YAML frontmatter."""
    fm = {
        "name": char.name,
        "role": char.role,
        "tone": char.tone,
        "avatar": char.avatar,
        "expertise": char.expertise,
        "directives": char.directives,
    }
    fm_str = yaml.dump(fm, sort_keys=False, allow_unicode=True).strip()

    body_lines: list[str] = [
        "---",
        fm_str,
        "---",
        "",
        "# Identity & Purpose",
        char.bio.strip() or f"You are {char.name}, working as {char.role}.",
        "",
        "# Operating Rules & Directives",
    ]
    for d in char.directives:
        body_lines.append(f"- {d}")

    body_lines.append("")
    body_lines.append("# Specialty & Knowledge Domains")
    for exp in char.expertise:
        body_lines.append(f"- {exp}")

    return "\n".join(body_lines) + "\n"


def parse_character_markdown(raw: str) -> BrainCharacter:
    """Parse Markdown with YAML frontmatter into a BrainCharacter instance."""
    content = raw.strip()
    frontmatter_dict: dict[str, Any] = {}
    bio = ""
    directives: list[str] = []
    expertise: list[str] = []

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            fm_text = parts[1]
            body_text = parts[2].strip()
            try:
                loaded = yaml.safe_load(fm_text)
                if isinstance(loaded, dict):
                    frontmatter_dict = loaded
            except Exception:
                pass
        else:
            body_text = content
    else:
        body_text = content

    name = str(frontmatter_dict.get("name") or "Jarvis OS")
    role = str(frontmatter_dict.get("role") or "Autonomous AI Assistant & Knowledge Architect")
    tone = str(frontmatter_dict.get("tone") or "Concise, precise, analytical, constructive")
    avatar = str(frontmatter_dict.get("avatar") or "🧠")

    raw_exp = frontmatter_dict.get("expertise")
    if isinstance(raw_exp, list):
        expertise = [str(x) for x in raw_exp]

    raw_dir = frontmatter_dict.get("directives")
    if isinstance(raw_dir, list):
        directives = [str(x) for x in raw_dir]

    # Parse sections from body if not fully covered by frontmatter
    current_section = ""
    bio_lines: list[str] = []

    for line in body_text.splitlines():
        trimmed = line.strip()
        if trimmed.startswith("# Identity") or trimmed.startswith("# Purpose") or trimmed.startswith("# Bio"):
            current_section = "bio"
            continue
        elif trimmed.startswith("# Operating") or trimmed.startswith("# Directives") or trimmed.startswith("# Rules"):
            current_section = "directives"
            continue
        elif trimmed.startswith("# Specialty") or trimmed.startswith("# Expertise") or trimmed.startswith("# Domains"):
            current_section = "expertise"
            continue
        elif trimmed.startswith("#"):
            current_section = "other"
            continue

        if current_section == "bio" and trimmed:
            bio_lines.append(trimmed)
        elif current_section == "directives" and trimmed.startswith("-"):
            val = trimmed.lstrip("-* ").strip()
            if val and val not in directives:
                directives.append(val)
        elif current_section == "expertise" and trimmed.startswith("-"):
            val = trimmed.lstrip("-* ").strip()
            if val and val not in expertise:
                expertise.append(val)

    if not bio:
        bio = "\n".join(bio_lines).strip()

    if not bio:
        bio = f"You are {name}, serving as {role} for this brain."

    return BrainCharacter(
        name=name,
        role=role,
        tone=tone,
        avatar=avatar,
        expertise=expertise,
        directives=directives,
        bio=bio,
        raw_markdown=raw,
    )


class CharacterManager:
    """Manages active character cards across Second Brain vaults."""

    def __init__(self, vaults: VaultManager) -> None:
        self._vaults = vaults

    def get_character(self, brain: str) -> BrainCharacter:
        """Read CHARACTER.md for brain, or scaffold default if missing."""
        try:
            raw = self._vaults.read_text(brain, CHARACTER_PATH).strip()
            if raw:
                return parse_character_markdown(raw)
        except OSError:
            pass

        # Scaffold default template
        default_tpl = CHARACTER_TEMPLATES["default"]
        char = BrainCharacter(
            name=default_tpl["name"],
            role=default_tpl["role"],
            tone=default_tpl["tone"],
            avatar=default_tpl["avatar"],
            expertise=list(default_tpl["expertise"]),
            directives=list(default_tpl["directives"]),
            bio=default_tpl["bio"],
        )
        rendered = render_character_markdown(char)
        char.raw_markdown = rendered
        self._vaults.write_text(brain, CHARACTER_PATH, rendered)
        return char

    def save_character(self, brain: str, raw_content: str) -> BrainCharacter:
        """Validate, parse, and save a character card for the brain."""
        char = parse_character_markdown(raw_content)
        rendered = render_character_markdown(char)
        char.raw_markdown = rendered
        self._vaults.write_text(brain, CHARACTER_PATH, rendered)
        return char

    def apply_template(self, brain: str, template_id: str) -> BrainCharacter:
        """Apply a pre-configured role template to the brain."""
        tpl = CHARACTER_TEMPLATES.get(template_id)
        if not tpl:
            raise ValueError(f"Unknown character template: {template_id}. Available: {list(CHARACTER_TEMPLATES.keys())}")

        char = BrainCharacter(
            name=tpl["name"],
            role=tpl["role"],
            tone=tpl["tone"],
            avatar=tpl["avatar"],
            expertise=list(tpl["expertise"]),
            directives=list(tpl["directives"]),
            bio=tpl["bio"],
        )
        rendered = render_character_markdown(char)
        char.raw_markdown = rendered
        self._vaults.write_text(brain, CHARACTER_PATH, rendered)
        return char

    def render_prompt_block(self, brain: str) -> str:
        """Render character profile for system prompt injection."""
        char = self.get_character(brain)
        lines: list[str] = [
            f"# === BRAIN PERSONA & CHARACTER CARD ({char.name}) ===",
            f"- Role: {char.role}",
            f"- Communication Style: {char.tone}",
        ]
        if char.expertise:
            lines.append(f"- Core Expertise: {', '.join(char.expertise)}")
        if char.directives:
            lines.append("- Operating Directives:")
            for d in char.directives:
                lines.append(f"  * {d}")
        if char.bio:
            lines.append(f"- Purpose: {char.bio}")
        return "\n".join(lines)

    def list_templates(self) -> list[dict[str, Any]]:
        """Return all available pre-configured character templates."""
        return list(CHARACTER_TEMPLATES.values())
