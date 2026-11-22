"""Second Brain vault scaffolding, git initialization, and path-jail file safety.

Why this exists: Every user brain is a standalone Obsidian-compatible folder inside
BRAINS_DIR. All file operations performed by Jarvis or its tools must pass through
safe_resolve() so neither path traversal ('..') nor symlinks can escape the active brain.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

BRAIN_DIRECTORIES: tuple[str, ...] = (
    "memory",
    "memory/facts",
    "memory/conversations",
    "wiki",
    "sources",
    "agents",
    "workflows",
    "skills",
    "attachments",
    "Javis",
    "Javis/loops",
)

INITIAL_MEMORY_MD = """# Long-Term Memory Index

<!-- One concise line per durable fact in memory/facts/<slug>.md -->
"""

INITIAL_JAVIS_README = """# Jarvis Operations Folder

- `loops/<slug>.md` - user-editable recurring loop definitions.
- `loop-state.json` - runtime loop state owned by the server (never edit by hand).
- `reminders.json` - scheduled reminders and cron jobs.
"""

INITIAL_VAULT_GITIGNORE = """attachments/
Javis/loop-state.json
.DS_Store
"""

_VALID_BRAIN_NAME_RE = re.compile(r"^[A-Za-z0-9А-Яа-яЁё][A-Za-z0-9А-Яа-яЁё _-]{0,63}$")


class VaultPathError(ValueError):
    """Raised when a requested path attempts to escape the vault root."""


@dataclass(frozen=True)
class BrainInfo:
    name: str
    path: Path
    is_git_repo: bool
    note_count: int


def validate_brain_name(name: str) -> str:
    clean = (name or "").strip()
    if not clean or ".." in clean or "/" in clean or "\\" in clean:
        raise ValueError("Invalid brain name")
    if not _VALID_BRAIN_NAME_RE.match(clean):
        raise ValueError("Brain name may contain letters, numbers, spaces, hyphens, and underscores (1-64 chars)")
    return clean


def safe_resolve(vault_root: Path, rel_path: str) -> Path:
    """Resolve rel_path strictly inside vault_root, blocking traversal and symlink escapes."""
    root = vault_root.resolve()
    raw = (rel_path or "").strip()
    if not raw:
        return root

    candidate = Path(raw)
    if candidate.is_absolute():
        raise VaultPathError(f"Absolute paths are not allowed: {rel_path}")

    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise VaultPathError(f"Path escapes vault root: {rel_path}") from exc

    # Also verify no existing intermediate symlink points outside root
    cur = root
    for part in candidate.parts:
        cur = cur / part
        if cur.is_symlink():
            target = cur.resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise VaultPathError(f"Symlink escapes vault root: {rel_path}") from exc

    return resolved


class VaultManager:
    """Manages multiple user brains inside BRAINS_DIR."""

    def __init__(self, brains_dir: Path) -> None:
        self._brains_dir = brains_dir.resolve()
        self._brains_dir.mkdir(parents=True, exist_ok=True)

    @property
    def brains_dir(self) -> Path:
        return self._brains_dir

    def brain_path(self, name: str) -> Path:
        clean = validate_brain_name(name)
        return safe_resolve(self._brains_dir, clean)

    def ensure_brain(self, name: str, init_git: bool = True) -> BrainInfo:
        """Create standard Second Brain folders and seed files without overwriting user edits."""
        root = self.brain_path(name)
        root.mkdir(parents=True, exist_ok=True)

        for rel in BRAIN_DIRECTORIES:
            (root / rel).mkdir(parents=True, exist_ok=True)

        seed_files = {
            "memory/MEMORY.md": INITIAL_MEMORY_MD,
            "Javis/README.md": INITIAL_JAVIS_README,
            ".gitignore": INITIAL_VAULT_GITIGNORE,
        }
        created_any = False
        for rel, content in seed_files.items():
            target = root / rel
            if not target.exists():
                target.write_text(content, encoding="utf-8")
                created_any = True

        is_git = (root / ".git").exists()
        if init_git and not is_git:
            is_git = self._init_git_repo(root)
        elif init_git and is_git and created_any:
            self._commit_if_dirty(root, "chore: ensure brain scaffold")

        return self._inspect(root)

    def list_brains(self) -> list[BrainInfo]:
        if not self._brains_dir.exists():
            return []
        items: list[BrainInfo] = []
        for entry in sorted(self._brains_dir.iterdir(), key=lambda p: p.name.lower()):
            if entry.is_dir() and not entry.name.startswith("."):
                items.append(self._inspect(entry))
        return items

    def read_text(self, brain_name: str, rel_path: str) -> str:
        root = self.brain_path(brain_name)
        target = safe_resolve(root, rel_path)
        return target.read_text(encoding="utf-8")

    def write_text(self, brain_name: str, rel_path: str, content: str) -> Path:
        root = self.brain_path(brain_name)
        target = safe_resolve(root, rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def write_bytes(self, brain_name: str, rel_path: str, data: bytes) -> Path:
        root = self.brain_path(brain_name)
        target = safe_resolve(root, rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def _inspect(self, root: Path) -> BrainInfo:
        note_count = sum(1 for p in root.rglob("*.md") if ".git" not in p.parts)
        return BrainInfo(
            name=root.name,
            path=root,
            is_git_repo=(root / ".git").exists(),
            note_count=note_count,
        )

    def _init_git_repo(self, root: Path) -> bool:
        try:
            subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Jarvis OS"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "jarvis@local"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-q", "-m", "chore: initialize Second Brain vault"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            return True
        except (OSError, subprocess.CalledProcessError):
            return (root / ".git").exists()

    def _commit_if_dirty(self, root: Path, message: str) -> None:
        try:
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            if not status.stdout.strip():
                return
            subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            pass

    def get_git_remote_status(self, brain_name: str) -> dict[str, Any]:
        """Check the status of the remote origin for the specified brain vault."""
        root = self.brain_path(brain_name)
        if not (root / ".git").exists():
            return {
                "is_git": False,
                "configured": False,
                "remote_url": "",
                "branch": "",
                "ahead": 0,
                "behind": 0,
                "clean": True,
            }

        remote_url = ""
        try:
            res = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            if res.returncode == 0:
                remote_url = res.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass

        branch = "main"
        try:
            res_branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            if res_branch.returncode == 0 and res_branch.stdout.strip():
                branch = res_branch.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass

        is_clean = True
        try:
            res_status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            is_clean = not bool(res_status.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass

        ahead, behind = 0, 0
        if remote_url:
            try:
                res_counts = subprocess.run(
                    ["git", "rev-list", "--left-right", "--count", f"origin/{branch}...HEAD"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                )
                if res_counts.returncode == 0 and res_counts.stdout.strip():
                    parts = res_counts.stdout.strip().split()
                    if len(parts) >= 2:
                        behind = int(parts[0])
                        ahead = int(parts[1])
            except (OSError, subprocess.SubprocessError, ValueError):
                pass

        return {
            "is_git": True,
            "configured": bool(remote_url),
            "remote_url": remote_url,
            "branch": branch,
            "ahead": ahead,
            "behind": behind,
            "clean": is_clean,
        }

    def configure_git_remote(self, brain_name: str, remote_url: str) -> dict[str, Any]:
        """Set or update origin remote URL for the brain vault."""
        root = self.brain_path(brain_name)
        if not (root / ".git").exists():
            self._init_git_repo(root)

        clean_url = (remote_url or "").strip()
        if not clean_url:
            try:
                subprocess.run(["git", "remote", "remove", "origin"], cwd=root, capture_output=True, timeout=5.0)
            except (OSError, subprocess.SubprocessError):
                pass
            return self.get_git_remote_status(brain_name)

        set_res = subprocess.run(
            ["git", "remote", "set-url", "origin", clean_url],
            cwd=root,
            capture_output=True,
            timeout=5.0,
        )
        if set_res.returncode != 0:
            subprocess.run(
                ["git", "remote", "add", "origin", clean_url],
                cwd=root,
                capture_output=True,
                timeout=5.0,
            )

        return self.get_git_remote_status(brain_name)

    def sync_git_remote(self, brain_name: str, action: str = "all") -> dict[str, Any]:
        """Pull and/or push notes to the remote Git origin."""
        root = self.brain_path(brain_name)
        status = self.get_git_remote_status(brain_name)
        if not status["configured"]:
            return {
                "ok": False,
                "error": "No remote origin configured for this brain vault.",
                "status": status,
            }

        branch = status["branch"] or "main"
        self._commit_if_dirty(root, "sync(remote): auto-commit before sync")

        outputs: list[str] = []
        if action in ("pull", "all"):
            try:
                pull_res = subprocess.run(
                    ["git", "pull", "--rebase", "origin", branch],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=30.0,
                )
                if pull_res.stdout.strip():
                    outputs.append(f"Pull: {pull_res.stdout.strip()}")
                if pull_res.returncode != 0:
                    err = pull_res.stderr.strip() or "Pull failed"
                    return {"ok": False, "error": f"Git pull error: {err}", "status": self.get_git_remote_status(brain_name)}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"Git pull failed: {exc}", "status": self.get_git_remote_status(brain_name)}

        if action in ("push", "all"):
            try:
                push_res = subprocess.run(
                    ["git", "push", "-u", "origin", branch],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=30.0,
                )
                if push_res.stdout.strip():
                    outputs.append(f"Push: {push_res.stdout.strip()}")
                if push_res.returncode != 0:
                    err = push_res.stderr.strip() or "Push failed"
                    return {"ok": False, "error": f"Git push error: {err}", "status": self.get_git_remote_status(brain_name)}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"Git push failed: {exc}", "status": self.get_git_remote_status(brain_name)}

        return {
            "ok": True,
            "action": action,
            "message": " | ".join(outputs) or "Sync complete up-to-date",
            "status": self.get_git_remote_status(brain_name),
        }
