"""Git safety net for Second Brain vaults: atomic commits, history, 1-click revert, and remote sync."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


class GitBrain:
    """Wrapper around local git operations inside a user's Second Brain vault."""

    @staticmethod
    def is_git_repo(vault_root: Path) -> bool:
        return (vault_root / ".git").exists()

    @staticmethod
    def commit_all(vault_root: Path, message: str) -> str:
        """Stage all vault changes and commit; returns new commit SHA or '' if clean."""
        if not GitBrain.is_git_repo(vault_root):
            return ""
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=vault_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if status.returncode != 0 or not status.stdout.strip():
            return ""
        subprocess.run(["git", "add", "-A"], cwd=vault_root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", message], cwd=vault_root, check=True, capture_output=True)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=vault_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return head.stdout.strip()

    @staticmethod
    def recent_commits(vault_root: Path, limit: int = 20) -> list[dict[str, Any]]:
        if not GitBrain.is_git_repo(vault_root):
            return []
        res = subprocess.run(
            ["git", "log", f"-n{max(1, min(limit, 100))}", "--pretty=format:%H|%h|%cI|%s"],
            cwd=vault_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            return []
        commits: list[dict[str, Any]] = []
        for line in res.stdout.splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append(
                    {
                        "sha": parts[0],
                        "short_sha": parts[1],
                        "date": parts[2],
                        "message": parts[3],
                        "is_learning": parts[3].startswith("learn:"),
                    }
                )
        return commits

    @staticmethod
    def rollback_commit(vault_root: Path, sha: str) -> tuple[bool, str]:
        """Non-destructively revert a specific commit via `git revert --no-edit`."""
        clean_sha = (sha or "").strip()
        if not clean_sha or not all(c in "0123456789abcdefABCDEF" for c in clean_sha):
            return False, "Invalid commit SHA"
        if not GitBrain.is_git_repo(vault_root):
            return False, "Brain is not a git repository"

        res = subprocess.run(
            ["git", "revert", "--no-edit", clean_sha],
            cwd=vault_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            subprocess.run(["git", "revert", "--abort"], cwd=vault_root, capture_output=True, check=False)
            return False, f"Git revert failed: {(res.stderr or res.stdout).strip()[:300]}"
        return True, f"Reverted commit {clean_sha[:8]}"
