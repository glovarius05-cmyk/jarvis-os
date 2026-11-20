"""Tests for Second Brain vault scaffolding, git safety net, and path-jail."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.brain.vault import BRAIN_DIRECTORIES, VaultManager, VaultPathError, safe_resolve


def test_vault_scaffold_git_init_and_no_overwrite(tmp_path: Path) -> None:
    manager = VaultManager(tmp_path / "brains")
    info = manager.ensure_brain("Brain Default")

    assert info.name == "Brain Default"
    assert info.is_git_repo is True
    for rel in BRAIN_DIRECTORIES:
        assert (info.path / rel).is_dir()
    assert (info.path / "memory" / "MEMORY.md").is_file()

    # Custom user content must never be clobbered on re-ensure
    custom = "# Long-Term Memory Index\n- Prefers Russian UI\n"
    manager.write_text("Brain Default", "memory/MEMORY.md", custom)
    manager.ensure_brain("Brain Default")
    assert manager.read_text("Brain Default", "memory/MEMORY.md") == custom


def test_safe_resolve_blocks_traversal_absolute_and_symlinks(tmp_path: Path) -> None:
    vault_root = tmp_path / "brains" / "Main"
    vault_root.mkdir(parents=True)
    outside = tmp_path / "secret.txt"
    outside.write_text("top-secret", encoding="utf-8")

    with pytest.raises(VaultPathError):
        safe_resolve(vault_root, "../secret.txt")

    with pytest.raises(VaultPathError):
        safe_resolve(vault_root, str(outside))

    escape_link = vault_root / "escape.md"
    escape_link.symlink_to(outside)
    with pytest.raises(VaultPathError):
        safe_resolve(vault_root, "escape.md")

    valid = safe_resolve(vault_root, "wiki/architecture.md")
    assert valid == (vault_root / "wiki" / "architecture.md").resolve()
