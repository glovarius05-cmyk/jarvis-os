"""Bi-directional Second Brain note watcher for Obsidian, VS Code, and external editors.

Monitors user brains on disk using Rust-backed watchfiles (with an async polling fallback),
automatically synchronizing modified notes into the Hybrid Index (FTS5 + dense vector embeddings)
and auto-committing external changes to git when the vault is version-controlled.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path
import re
from typing import Any

from app.brain.hybrid_search import HybridIndexStore
from app.brain.vault import VaultManager

logger = logging.getLogger("jarvis.watcher")

# Ignored folder segments and file patterns
_IGNORED_PARTS = {
    ".git",
    ".obsidian",
    ".trash",
    ".smart-env",
    "attachments",
    ".DS_Store",
    "__pycache__",
}
_IGNORED_FILES = {
    ".DS_Store",
    "loop-state.json",
    "reminders.json",
}
_TEMP_PATTERN = re.compile(r"(\.tmp|\.swp|~|\.bak|\.crdownload)$", re.IGNORECASE)


def is_watchable_note(path: Path) -> bool:
    """Return True if path points to a user-editable Markdown note inside a brain."""
    if path.suffix.lower() != ".md":
        return False
    if path.name.startswith("."):
        return False
    if path.name in _IGNORED_FILES:
        return False
    if _TEMP_PATTERN.search(path.name):
        return False
    parts = set(path.parts)
    if parts.intersection(_IGNORED_PARTS):
        return False
    return True


class VaultWatcherService:
    """Background service that monitors the Second Brain folder and keeps indexes in sync."""

    def __init__(
        self,
        vaults: VaultManager,
        hybrid_index: HybridIndexStore,
        brains_dir: Path | None = None,
        debounce_seconds: float = 0.5,
    ) -> None:
        self.vaults = vaults
        self.hybrid_index = hybrid_index
        self.brains_dir = (brains_dir or vaults.brains_dir).resolve()
        self.debounce_seconds = debounce_seconds

        self._running = False
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._pending_changes: dict[tuple[str, str], str] = {}  # (brain, rel_path) -> 'modify' | 'delete'
        self._debounce_handle: asyncio.TimerHandle | None = None
        self._lock = asyncio.Lock()

        # Operational metrics
        self.total_events_processed: int = 0
        self.last_event_type: str | None = None
        self.last_synced_path: str | None = None
        self.last_synced_brain: str | None = None
        self.last_sync_timestamp: str | None = None
        self.error_count: int = 0
        self.last_error: str | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    def status(self) -> dict[str, Any]:
        """Return diagnostic health and status metrics for the watcher."""
        return {
            "enabled": True,
            "running": self._running,
            "watched_dir": str(self.brains_dir),
            "total_events_processed": self.total_events_processed,
            "last_event_type": self.last_event_type,
            "last_synced_path": self.last_synced_path,
            "last_synced_brain": self.last_synced_brain,
            "last_sync_timestamp": self.last_sync_timestamp,
            "error_count": self.error_count,
            "last_error": self.last_error,
        }

    async def start(self) -> None:
        """Start the background file watching loop."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="vault-watcher")
        logger.info("VaultWatcherService started for %s", self.brains_dir)

    async def stop(self) -> None:
        """Signal the watcher to stop and wait for completion."""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("VaultWatcherService stopped")

    def _resolve_brain_and_rel(self, full_path: Path) -> tuple[str, str] | None:
        """Extract (brain_name, relative_path) from an absolute file path."""
        try:
            rel = full_path.resolve().relative_to(self.brains_dir)
        except ValueError:
            return None
        parts = rel.parts
        if len(parts) < 2:
            return None
        brain_name = parts[0]
        if brain_name.startswith("."):
            return None
        if any(p.startswith(".") for p in parts[1:]):
            return None
        note_rel_path = "/".join(parts[1:])
        return brain_name, note_rel_path

    async def handle_change(self, brain: str, rel_path: str, change_type: str) -> None:
        """Queue and debounce note change events."""
        async with self._lock:
            self._pending_changes[(brain, rel_path)] = change_type

        # Debounce execution by debounce_seconds
        loop = asyncio.get_running_loop()
        if self._debounce_handle is not None:
            self._debounce_handle.cancel()
        self._debounce_handle = loop.call_later(
            self.debounce_seconds,
            lambda: asyncio.create_task(self._flush_pending()),
        )

    async def _flush_pending(self) -> None:
        """Apply all debounced file additions, updates, and deletions."""
        async with self._lock:
            changes = dict(self._pending_changes)
            self._pending_changes.clear()

        if not changes:
            return

        for (brain, rel_path), change_type in changes.items():
            try:
                await self._apply_single_change(brain, rel_path, change_type)
            except Exception as exc:  # noqa: BLE001
                self.error_count += 1
                self.last_error = str(exc)
                logger.warning("Error syncing note %s/%s: %s", brain, rel_path, exc)

    async def _apply_single_change(self, brain: str, rel_path: str, change_type: str) -> None:
        """Process a single file event: update hybrid index and git repository."""
        brain_root = self.vaults.brain_path(brain)
        file_path = brain_root / rel_path

        now_iso = datetime.now(timezone.utc).isoformat()

        if change_type in ("added", "modified"):
            if not file_path.exists() or not file_path.is_file():
                return
            content = file_path.read_text(encoding="utf-8", errors="replace")
            mtime = file_path.stat().st_mtime

            # Update hybrid index in a worker thread so event loop remains responsive
            await asyncio.to_thread(
                self.hybrid_index.index_file,
                brain,
                rel_path,
                content,
                mtime,
            )

            # Auto-commit to vault git repo if dirty
            commit_msg = f"sync(obsidian): update {rel_path}"
            await asyncio.to_thread(self.vaults._commit_if_dirty, brain_root, commit_msg)

        elif change_type == "deleted":
            # Remove from hybrid search index
            await asyncio.to_thread(self.hybrid_index.remove_file, brain, rel_path)

            # Commit deletion if brain is git repo
            commit_msg = f"sync(obsidian): delete {rel_path}"
            await asyncio.to_thread(self.vaults._commit_if_dirty, brain_root, commit_msg)

        self.total_events_processed += 1
        self.last_event_type = change_type
        self.last_synced_brain = brain
        self.last_synced_path = rel_path
        self.last_sync_timestamp = now_iso
        logger.info("Synced %s on %s/%s", change_type, brain, rel_path)

    async def trigger_full_sync(self, brain: str | None = None, force: bool = False) -> dict[str, Any]:
        """Manually trigger a full re-indexing and git commit sweep."""
        brains = [brain] if brain else [b.name for b in self.vaults.list_brains()]
        total_indexed = 0
        total_chunks = 0

        for b in brains:
            root = self.vaults.brain_path(b)
            res = await asyncio.to_thread(self.hybrid_index.index_brain, self.vaults, b, force)
            total_indexed += res.get("indexed_files", 0)
            total_chunks += res.get("total_chunks", 0)
            await asyncio.to_thread(self.vaults._commit_if_dirty, root, "sync(obsidian): manual full sync")

        now_iso = datetime.now(timezone.utc).isoformat()
        self.last_sync_timestamp = now_iso
        self.last_event_type = "manual_full_sync"
        return {
            "ok": True,
            "brains_synced": brains,
            "indexed_files": total_indexed,
            "total_chunks": total_chunks,
            "timestamp": now_iso,
        }

    async def _run_loop(self) -> None:
        """Main watching loop prioritizing watchfiles with polling fallback."""
        try:
            from watchfiles import Change, awatch

            logger.info("Using Rust-backed watchfiles for vault monitoring at %s", self.brains_dir)
            async for changes in awatch(str(self.brains_dir), stop_event=self._stop_event):
                for change_code, raw_path in changes:
                    p = Path(raw_path)
                    if not is_watchable_note(p):
                        continue
                    resolved = self._resolve_brain_and_rel(p)
                    if not resolved:
                        continue
                    brain_name, note_rel = resolved

                    if change_code == Change.added:
                        c_type = "added"
                    elif change_code == Change.modified:
                        c_type = "modified"
                    elif change_code == Change.deleted:
                        c_type = "deleted"
                    else:
                        c_type = "modified"

                    await self.handle_change(brain_name, note_rel, c_type)

        except (ImportError, Exception) as exc:
            logger.warning(
                "Watchfiles loop stopped or unavailable (%s), falling back to polling scanner", exc
            )
            await self._run_polling_fallback()

    async def _run_polling_fallback(self) -> None:
        """Periodic mtime polling scanner used as resilient fallback."""
        known_mtimes: dict[tuple[str, str], float] = {}

        while not self._stop_event.is_set():
            try:
                current_files: set[tuple[str, str]] = set()
                brains = self.vaults.list_brains()

                for b in brains:
                    root = self.vaults.brain_path(b.name)
                    if not root.exists():
                        continue
                    for p in root.rglob("*.md"):
                        if not is_watchable_note(p):
                            continue
                        rel_path = p.relative_to(root).as_posix()
                        key = (b.name, rel_path)
                        current_files.add(key)
                        try:
                            mtime = p.stat().st_mtime
                        except OSError:
                            continue

                        if key not in known_mtimes:
                            known_mtimes[key] = mtime
                            # Initial discovery or new note
                            await self.handle_change(b.name, rel_path, "added")
                        elif abs(known_mtimes[key] - mtime) > 1e-4:
                            known_mtimes[key] = mtime
                            await self.handle_change(b.name, rel_path, "modified")

                # Detect deletions
                deleted_keys = set(known_mtimes.keys()) - current_files
                for b_name, r_path in deleted_keys:
                    del known_mtimes[(b_name, r_path)]
                    await self.handle_change(b_name, r_path, "deleted")

            except Exception as exc:  # noqa: BLE001
                self.error_count += 1
                self.last_error = str(exc)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=2.0)
                break
            except asyncio.TimeoutError:
                pass
