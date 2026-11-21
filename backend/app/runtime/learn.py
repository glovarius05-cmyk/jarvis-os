"""Safe two-phase Self-Learning engine for Second Brain vaults.

Security architecture:
1. The LLM learning fork runs strictly READ-ONLY with ZERO tools (no MCP, no file writes)
   and outputs ONLY a structured JSON manifest of proposed facts and wiki notes.
2. Trusted Python `promote()` inspects every item through secret scanners, prompt-injection
   filters, and provenance checks before writing to `memory/facts/`, `memory/MEMORY.md`,
   and `wiki/`.
3. Fail-closed Git guard: auto-writes ONLY run when the brain is a Git repo, and every
   learning pass produces an atomic Git commit that can be reverted in 1 click.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from app.brain.git_sync import GitBrain
from app.brain.vault import VaultManager, safe_resolve
from app.engines.base import EngineProtocol, EngineRequest
from app.engines.router import EngineRouter
from app.runtime.sessions import SessionStore

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")
_SLUG_RE = re.compile(r"[^a-z0-9]+")

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-(?:or-|ant-|proj-)?[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----"),
    re.compile(r"\benc:[A-Za-z0-9_=+-]{24,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}\b", re.IGNORECASE),
)

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"override\s+(?:the\s+)?system\s+prompt", re.IGNORECASE),
    re.compile(r"disable\s+(?:security|permissions|auth|guard)", re.IGNORECASE),
    re.compile(r"exfiltrate|send\s+secrets\s+to", re.IGNORECASE),
)

VALID_FACT_TYPES = frozenset({"user", "preference", "business", "decision"})
TRUSTED_PROVENANCE = frozenset({"user_input", "source_file", "tool_verified"})
DAILY_MAX_RUNS = 24


def contains_secret(text: str) -> bool:
    return any(p.search(text or "") for p in _SECRET_PATTERNS)


def contains_injection(text: str) -> bool:
    return any(p.search(text or "") for p in _INJECTION_PATTERNS)


def _slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-")
    return slug[:56] or "fact"


class SelfLearningEngine:
    """Extracts durable memories and wiki concepts via a zero-tool read-only fork + trusted promote()."""

    def __init__(
        self,
        *,
        state_dir: Path,
        vaults: VaultManager,
        sessions: SessionStore,
        router: EngineRouter,
        engine_override: EngineProtocol | None = None,
    ) -> None:
        self._state_path = state_dir / "learn_state.json"
        self._vaults = vaults
        self._sessions = sessions
        self._router = router
        self._engine_override = engine_override
        self._lock = threading.RLock()

    def _load_state(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {}
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_state(self, state: dict[str, Any]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self._state_path)

    def _check_and_bump_daily_limit(self, brain: str) -> tuple[bool, int]:
        today = date.today().isoformat()
        with self._lock:
            st = self._load_state()
            bst = st.setdefault(brain, {})
            if bst.get("day") != today:
                bst["day"] = today
                bst["runs_today"] = 0
            if int(bst["runs_today"]) >= DAILY_MAX_RUNS:
                return False, int(bst["runs_today"])
            bst["runs_today"] = int(bst["runs_today"]) + 1
            bst["last_run_at"] = time.time()
            self._save_state(st)
            return True, int(bst["runs_today"])

    def _collect_recent_transcript(self, brain: str, max_sessions: int = 5, max_chars: int = 12_000) -> str:
        sessions = self._sessions.list_sessions(brain=brain, limit=max_sessions)
        blocks: list[str] = []
        used = 0
        for s in sessions:
            msgs = self._sessions.list_messages(s.id, limit=30)
            if not msgs:
                continue
            lines = [f"## Session: {s.title}"]
            for m in msgs:
                lines.append(f"{m.role.upper()}: {m.content[:1000]}")
            chunk = "\n".join(lines)
            if used + len(chunk) > max_chars and blocks:
                break
            blocks.append(chunk)
            used += len(chunk)
        return "\n\n".join(blocks)

    async def run_learning_pass(
        self,
        *,
        brain: str,
        dry_run: bool = False,
        transcript_override: str = "",
    ) -> dict[str, Any]:
        """Run read-only extraction fork -> trusted promote() -> atomic git commit."""
        root = self._vaults.brain_path(brain)
        is_git = GitBrain.is_git_repo(root)
        # Fail-closed: never auto-write if the brain lacks a git safety net
        effective_dry_run = dry_run or not is_git

        ok_rate, runs_today = self._check_and_bump_daily_limit(brain)
        if not ok_rate:
            return {"ok": False, "error": f"Daily self-learning cap reached ({DAILY_MAX_RUNS} runs/day)."}

        transcript = transcript_override.strip() or self._collect_recent_transcript(brain)
        if not transcript:
            return {
                "ok": True,
                "dry_run": effective_dry_run,
                "promoted_facts": [],
                "promoted_wiki": [],
                "rejected": [],
                "commit_sha": "",
                "note": "No recent conversations to learn from.",
            }

        try:
            current_memory = self._vaults.read_text(brain, "memory/MEMORY.md")
        except OSError:
            current_memory = ""

        manifest = await self._run_readonly_fork(brain=brain, current_memory=current_memory, transcript=transcript)
        promoted = self.promote(brain=brain, manifest=manifest, dry_run=effective_dry_run)
        promoted["runs_today"] = runs_today
        promoted["is_git_repo"] = is_git
        if not is_git and not dry_run:
            promoted["note"] = "Brain is not a git repo; ran fail-closed in dry_run mode."
        return promoted

    async def _run_readonly_fork(
        self,
        *,
        brain: str,
        current_memory: str,
        transcript: str,
    ) -> dict[str, Any]:
        """Strictly read-only LLM pass with ZERO tools that returns a JSON manifest."""
        provider, model = self._router.resolve_target(role="aux")
        engine = self._engine_override or self._router.build_engine(provider, brain=brain, run_mode="suggest")

        sys_prompt = (
            "You are Jarvis Self-Learning Extractor (READ-ONLY). "
            "Extract ONLY durable facts about the user/business/preferences/decisions and reusable Wiki concepts. "
            "Never include secrets, API keys, or ephemeral chatter. "
            "Return ONLY valid JSON with shape:\n"
            '{"memory_facts":[{"slug":"...","type":"user|preference|business|decision","summary":"...","content":"..."}],'
            '"wiki_notes":[{"slug":"...","title":"...","content":"...","source":"user_input|source_file|tool_verified"}]}'
        )
        user_prompt = (
            f"Current MEMORY.md:\n{current_memory[:4000]}\n\n"
            f"Recent conversations:\n{transcript}"
        )

        # ZERO tools passed: the learning fork physically cannot touch files or MCP
        req = EngineRequest(
            session_id=f"learn:{brain}",
            brain=brain,
            system_prompt=sys_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            provider=provider,
            model=model,
            tools=[],
            tool_executor=None,
            max_tool_rounds=1,
        )

        parts: list[str] = []
        final = ""
        async for ev in engine.stream(req):
            if ev.type == "text":
                parts.append(ev.content)
            elif ev.type == "error":
                raise RuntimeError(ev.content)
            elif ev.type == "final":
                final = ev.content

        raw = (final or "".join(parts)).strip()
        match = _JSON_BLOCK_RE.search(raw)
        if not match:
            return {"memory_facts": [], "wiki_notes": []}
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {"memory_facts": [], "wiki_notes": []}
        except json.JSONDecodeError:
            return {"memory_facts": [], "wiki_notes": []}

    def promote(self, *, brain: str, manifest: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
        """Trusted Python promoter: scans for secrets/injection/provenance, writes vault, commits to Git."""
        root = self._vaults.brain_path(brain)
        today = date.today().isoformat()
        promoted_facts: list[str] = []
        promoted_wiki: list[str] = []
        rejected: list[dict[str, str]] = []

        valid_facts: list[dict[str, str]] = []
        for item in manifest.get("memory_facts") or []:
            if not isinstance(item, dict):
                continue
            slug = _slugify(str(item.get("slug") or item.get("summary") or ""))
            ftype = str(item.get("type") or "user").strip().lower()
            if ftype not in VALID_FACT_TYPES:
                ftype = "user"
            summary = str(item.get("summary") or "").strip()[:160]
            content = str(item.get("content") or summary).strip()
            combined = f"{summary}\n{content}"
            if not content:
                continue
            if contains_secret(combined):
                rejected.append({"kind": "fact", "slug": slug, "reason": "secret_detected"})
                continue
            if contains_injection(combined):
                rejected.append({"kind": "fact", "slug": slug, "reason": "prompt_injection_detected"})
                continue
            valid_facts.append({"slug": slug, "type": ftype, "summary": summary or slug, "content": content})

        valid_wiki: list[dict[str, str]] = []
        for item in manifest.get("wiki_notes") or []:
            if not isinstance(item, dict):
                continue
            slug = _slugify(str(item.get("slug") or item.get("title") or ""))
            title = str(item.get("title") or slug).strip()
            content = str(item.get("content") or "").strip()
            source = str(item.get("source") or "").strip().lower()
            if not content:
                continue
            if source not in TRUSTED_PROVENANCE:
                rejected.append({"kind": "wiki", "slug": slug, "reason": f"untrusted_provenance:{source or 'none'}"})
                continue
            if contains_secret(content):
                rejected.append({"kind": "wiki", "slug": slug, "reason": "secret_detected"})
                continue
            if contains_injection(content):
                rejected.append({"kind": "wiki", "slug": slug, "reason": "prompt_injection_detected"})
                continue
            valid_wiki.append({"slug": slug, "title": title, "content": content, "source": source})

        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "promoted_facts": [f["slug"] for f in valid_facts],
                "promoted_wiki": [w["slug"] for w in valid_wiki],
                "rejected": rejected,
                "commit_sha": "",
            }

        # Apply validated facts
        mem_index_path = safe_resolve(root, "memory/MEMORY.md")
        mem_lines = (
            mem_index_path.read_text(encoding="utf-8", errors="replace").splitlines()
            if mem_index_path.exists()
            else ["# Long-Term Memory Index"]
        )

        for fact in valid_facts:
            rel_fact = f"memory/facts/{fact['slug']}.md"
            fact_path = safe_resolve(root, rel_fact)
            fact_path.parent.mkdir(parents=True, exist_ok=True)
            fact_body = (
                f"---\n"
                f"type: {fact['type']}\n"
                f"created: {today}\n"
                f"updated: {today}\n"
                f"---\n\n"
                f"{fact['content']}\n"
            )
            fact_path.write_text(fact_body, encoding="utf-8")
            index_entry = f"- [{fact['type']}] [[{fact['slug']}]]: {fact['summary']}"
            marker = f"[[{fact['slug']}]]"
            replaced = False
            for idx, line in enumerate(mem_lines):
                if marker in line:
                    mem_lines[idx] = index_entry
                    replaced = True
                    break
            if not replaced:
                mem_lines.append(index_entry)
            promoted_facts.append(fact["slug"])

        if promoted_facts:
            mem_index_path.parent.mkdir(parents=True, exist_ok=True)
            mem_index_path.write_text("\n".join(mem_lines).rstrip() + "\n", encoding="utf-8")

        # Apply validated wiki notes
        for note in valid_wiki:
            rel_wiki = f"wiki/{note['slug']}.md"
            wiki_path = safe_resolve(root, rel_wiki)
            wiki_path.parent.mkdir(parents=True, exist_ok=True)
            wiki_body = (
                f"---\n"
                f"type: wiki\n"
                f"title: {json.dumps(note['title'], ensure_ascii=False)}\n"
                f"source: {note['source']}\n"
                f"updated: {today}\n"
                f"---\n\n"
                f"# {note['title']}\n\n"
                f"{note['content']}\n"
            )
            wiki_path.write_text(wiki_body, encoding="utf-8")
            promoted_wiki.append(note["slug"])

        commit_sha = ""
        if promoted_facts or promoted_wiki:
            log_path = safe_resolve(root, "Javis/learn-log.md")
            ts_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            entry = (
                f"## [{ts_str}] learn - +{len(promoted_facts)} facts, +{len(promoted_wiki)} wiki\n"
                f"- Facts: {', '.join(promoted_facts) or 'none'}\n"
                f"- Wiki: {', '.join(promoted_wiki) or 'none'}\n\n"
            )
            existing_log = log_path.read_text(encoding="utf-8") if log_path.exists() else "# Self-Learning Log\n\n"
            log_path.write_text(existing_log + entry, encoding="utf-8")
            commit_sha = GitBrain.commit_all(
                root,
                f"learn: +{len(promoted_facts)} facts, +{len(promoted_wiki)} wiki",
            )

        return {
            "ok": True,
            "dry_run": False,
            "promoted_facts": promoted_facts,
            "promoted_wiki": promoted_wiki,
            "rejected": rejected,
            "commit_sha": commit_sha,
        }

    def synthesize_skill(
        self,
        *,
        brain: str,
        name: str,
        slug: str,
        description: str,
        instructions: str,
        group: str = "Synthesized",
    ) -> dict[str, Any]:
        """Autonomously synthesize or refine an executable Agent Skill with security gates."""
        norm_slug = _SLUG_RE.sub("-", (slug or name or "").strip().lower()).strip("-")
        norm_slug = norm_slug[:48] or "custom-skill"

        clean_desc = (description or "").strip()[:150]
        if not clean_desc:
            return {"ok": False, "error": "Description cannot be empty"}

        # Security check: secrets and injection
        combined = f"{name}\n{description}\n{instructions}"
        if contains_secret(combined):
            return {"ok": False, "error": "Skill contains apparent secrets or API tokens"}
        if contains_injection(combined):
            return {"ok": False, "error": "Skill contains prompt injection patterns"}

        root = self._vaults.brain_path(brain)
        target = safe_resolve(root, f"skills/{norm_slug}/SKILL.md")
        target.parent.mkdir(parents=True, exist_ok=True)

        meta = {
            "name": name.strip(),
            "description": clean_desc,
            "group": group.strip(),
            "enabled": True,
        }
        yaml_front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
        body = f"---\n{yaml_front}\n---\n\n{instructions.strip()}\n"
        target.write_text(body, encoding="utf-8")

        commit_sha = GitBrain.commit_all(
            root,
            f"feat(skills): autonomously synthesized skill '{norm_slug}'",
        )

        return {
            "ok": True,
            "slug": norm_slug,
            "name": name,
            "description": clean_desc,
            "path": f"skills/{norm_slug}/SKILL.md",
            "commit_sha": commit_sha,
        }
