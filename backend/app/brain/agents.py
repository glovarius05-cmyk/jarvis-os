"""Flat-folder storage for specialist Agents (<brain>/agents/<slug>.md) and Workflows (<brain>/workflows/<slug>.md)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.brain.skills import dump_markdown_frontmatter, parse_markdown_frontmatter
from app.brain.vault import VaultManager, safe_resolve

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class AgentDef:
    slug: str
    name: str
    role: str
    tags: list[str]
    skills: list[str]
    model: str
    model_provider: str
    prompt: str
    path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "role": self.role,
            "tags": list(self.tags),
            "skills": list(self.skills),
            "model": self.model,
            "model_provider": self.model_provider,
            "prompt": self.prompt,
            "path": self.path,
        }


@dataclass(frozen=True)
class WorkflowStepDef:
    agent: str
    task: str
    verify_agent: str = ""
    max_retries: int = 1


@dataclass(frozen=True)
class WorkflowDef:
    slug: str
    name: str
    description: str
    group: str
    status: str  # "on" | "off"
    steps: list[WorkflowStepDef] = field(default_factory=list)
    body: str = ""
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "group": self.group,
            "status": self.status,
            "steps": [
                {
                    "agent": s.agent,
                    "task": s.task,
                    "verify_agent": s.verify_agent,
                    "max_retries": s.max_retries,
                }
                for s in self.steps
            ],
            "body": self.body,
            "path": self.path,
        }


class AgentStore:
    """CRUD over flat Markdown files in <brain>/agents/ and <brain>/workflows/."""

    def __init__(self, vaults: VaultManager) -> None:
        self._vaults = vaults

    def list_agents(self, brain: str) -> list[AgentDef]:
        root = self._vaults.brain_path(brain)
        folder = root / "agents"
        if not folder.exists():
            return []
        out: list[AgentDef] = []
        for p in sorted(folder.glob("*.md"), key=lambda x: x.name.lower()):
            raw = p.read_text(encoding="utf-8", errors="replace")
            meta, body = parse_markdown_frontmatter(raw)
            slug = str(meta.get("slug") or p.stem)
            out.append(
                AgentDef(
                    slug=slug,
                    name=str(meta.get("name") or slug),
                    role=str(meta.get("role") or ""),
                    tags=[str(x) for x in (meta.get("tags") or [])],
                    skills=[str(x) for x in (meta.get("skills") or [])],
                    model=str(meta.get("model") or ""),
                    model_provider=str(meta.get("model_provider") or ""),
                    prompt=body,
                    path=f"agents/{p.name}",
                )
            )
        return out

    def get_agent(self, brain: str, slug: str) -> AgentDef | None:
        for a in self.list_agents(brain):
            if a.slug == slug:
                return a
        return None

    def save_agent(
        self,
        brain: str,
        *,
        slug: str,
        name: str,
        role: str,
        prompt: str,
        tags: list[str] | None = None,
        skills: list[str] | None = None,
        model: str = "",
        model_provider: str = "",
    ) -> AgentDef:
        clean_slug = slug.strip().lower()
        if not _SLUG_RE.match(clean_slug):
            raise ValueError("Agent slug must be lowercase ASCII alphanumeric/hyphen/underscore")
        root = self._vaults.brain_path(brain)
        target = safe_resolve(root, f"agents/{clean_slug}.md")
        meta = {
            "type": "agent",
            "name": name.strip() or clean_slug,
            "slug": clean_slug,
            "role": role.strip(),
            "tags": tags or ["General"],
            "skills": skills or [],
            "model": model.strip(),
            "model_provider": model_provider.strip(),
            "updated": date.today().isoformat(),
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dump_markdown_frontmatter(meta, prompt), encoding="utf-8")
        agent = self.get_agent(brain, clean_slug)
        assert agent is not None
        return agent

    def list_workflows(self, brain: str) -> list[WorkflowDef]:
        root = self._vaults.brain_path(brain)
        folder = root / "workflows"
        if not folder.exists():
            return []
        out: list[WorkflowDef] = []
        for p in sorted(folder.glob("*.md"), key=lambda x: x.name.lower()):
            raw = p.read_text(encoding="utf-8", errors="replace")
            meta, body = parse_markdown_frontmatter(raw)
            slug = str(meta.get("slug") or p.stem)
            raw_status = meta.get("status", "on")
            status = "on" if str(raw_status).lower() in {"on", "true", "enabled"} else "off"
            steps: list[WorkflowStepDef] = []
            for s in meta.get("steps") or []:
                if isinstance(s, dict) and s.get("agent") and s.get("task"):
                    steps.append(
                        WorkflowStepDef(
                            agent=str(s["agent"]),
                            task=str(s["task"]),
                            verify_agent=str(s.get("verify_agent") or ""),
                            max_retries=max(0, min(int(s.get("max_retries", 1)), 3)),
                        )
                    )
            out.append(
                WorkflowDef(
                    slug=slug,
                    name=str(meta.get("name") or slug),
                    description=str(meta.get("description") or ""),
                    group=str(meta.get("group") or "General"),
                    status=status,
                    steps=steps,
                    body=body,
                    path=f"workflows/{p.name}",
                )
            )
        return out

    def get_workflow(self, brain: str, slug: str) -> WorkflowDef | None:
        for wf in self.list_workflows(brain):
            if wf.slug == slug:
                return wf
        return None

    def save_workflow(
        self,
        brain: str,
        *,
        slug: str,
        name: str,
        description: str,
        steps: list[dict[str, Any]],
        group: str = "General",
        status: str = "on",
        body: str = "",
    ) -> WorkflowDef:
        clean_slug = slug.strip().lower()
        if not _SLUG_RE.match(clean_slug):
            raise ValueError("Workflow slug must be lowercase ASCII alphanumeric/hyphen/underscore")
        if not steps:
            raise ValueError("Workflow must have at least one step")

        root = self._vaults.brain_path(brain)
        target = safe_resolve(root, f"workflows/{clean_slug}.md")
        meta = {
            "type": "workflow",
            "name": name.strip() or clean_slug,
            "slug": clean_slug,
            "status": "on" if status == "on" else "off",
            "group": group.strip() or "General",
            "description": description.strip(),
            "steps": steps,
            "updated": date.today().isoformat(),
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dump_markdown_frontmatter(meta, body or description), encoding="utf-8")
        wf = self.get_workflow(brain, clean_slug)
        assert wf is not None
        return wf
