"""Hybrid Semantic Search (BM25 + Dense Vector Embeddings via Reciprocal Rank Fusion).

Why this exists: Keyword-only (FTS5) search misses conceptual synonyms (e.g. "отказоустойчивость"
vs "high availability"), while pure vector search often misses exact identifiers or function names.
Hybrid RAG fuses both rankings using Reciprocal Rank Fusion (RRF), delivering fast, private,
and accurate search across the Second Brain (memory, wiki, sources, skills, loops).
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from app.brain.vault import VaultManager, safe_resolve

EMBEDDING_DIM = 384
RRF_K = 60.0


@dataclass
class Chunk:
    path: str
    chunk_index: int
    heading: str
    content: str
    char_count: int


def chunk_markdown(content: str, path: str, max_chunk_chars: int = 1500) -> list[Chunk]:
    """Split markdown into semantic chunks based on headings and paragraph boundaries."""
    raw = (content or "").strip()
    if not raw:
        return []

    lines = raw.splitlines()
    sections: list[tuple[str, list[str]]] = []
    cur_heading = "Overview"
    cur_lines: list[str] = []

    for line in lines:
        if re.match(r"^#{1,4}\s+", line):
            if cur_lines:
                sections.append((cur_heading, cur_lines))
                cur_lines = []
            cur_heading = line.lstrip("#").strip()
            cur_lines.append(line)
        else:
            cur_lines.append(line)

    if cur_lines:
        sections.append((cur_heading, cur_lines))

    chunks: list[Chunk] = []
    chunk_idx = 0

    for heading, sec_lines in sections:
        sec_text = "\n".join(sec_lines).strip()
        if not sec_text:
            continue

        if len(sec_text) <= max_chunk_chars:
            chunks.append(
                Chunk(
                    path=path,
                    chunk_index=chunk_idx,
                    heading=heading,
                    content=sec_text,
                    char_count=len(sec_text),
                )
            )
            chunk_idx += 1
        else:
            # Sub-split long section by paragraphs
            paragraphs = sec_text.split("\n\n")
            buf: list[str] = []
            buf_len = 0
            for p in paragraphs:
                p_clean = p.strip()
                if not p_clean:
                    continue
                if buf_len + len(p_clean) > max_chunk_chars and buf:
                    merged = "\n\n".join(buf)
                    chunks.append(
                        Chunk(
                            path=path,
                            chunk_index=chunk_idx,
                            heading=heading,
                            content=merged,
                            char_count=len(merged),
                        )
                    )
                    chunk_idx += 1
                    buf = [p_clean]
                    buf_len = len(p_clean)
                else:
                    buf.append(p_clean)
                    buf_len += len(p_clean) + 2

            if buf:
                merged = "\n\n".join(buf)
                chunks.append(
                    Chunk(
                        path=path,
                        chunk_index=chunk_idx,
                        heading=heading,
                        content=merged,
                        char_count=len(merged),
                    )
                )
                chunk_idx += 1

    return chunks


class LocalDenseEmbeddingProvider:
    """Fast, deterministic subword and n-gram hash projection into a normalized 384-d vector space.

    Works 100% locally with zero external network or heavy weight downloads. Captures prefixes,
    suffixes, Russian grammatical inflections, and English lemmas via character 3-4 grams + word tokens.
    """

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    def embed(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        clean = (text or "").lower()
        words = re.findall(r"\w+", clean)
        if not words:
            return vec

        for w in words:
            # Word token feature
            h_val = int(hashlib.md5(w.encode("utf-8")).hexdigest(), 16)
            idx = h_val % self.dim
            sign = 1.0 if (h_val >> 16) & 1 else -1.0
            vec[idx] += sign * 1.5

            # Subword character n-grams (3 and 4 characters)
            w_len = len(w)
            for n in (3, 4):
                if w_len >= n:
                    for i in range(w_len - n + 1):
                        ng = w[i : i + n]
                        h_ng = int(hashlib.md5(ng.encode("utf-8")).hexdigest(), 16)
                        idx_ng = h_ng % self.dim
                        sign_ng = 1.0 if (h_ng >> 16) & 1 else -1.0
                        vec[idx_ng] += sign_ng * 0.5

        # Sublinear scaling & L2 normalization
        vec = np.sign(vec) * np.log1p(np.abs(vec))
        norm = float(np.linalg.norm(vec))
        if norm > 0.0:
            vec /= norm
        return vec

    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        return [self.embed(t) for t in texts]


def serialize_vector(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def deserialize_vector(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


class HybridIndexStore:
    """Maintains SQLite FTS5 index and dense vector embeddings for Second Brain notes."""

    def __init__(
        self,
        db_path: Path,
        embedder: LocalDenseEmbeddingProvider | None = None,
    ) -> None:
        self.db_path = db_path
        self.embedder = embedder or LocalDenseEmbeddingProvider()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS file_meta (
                    brain TEXT NOT NULL,
                    path TEXT NOT NULL,
                    mtime REAL NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    PRIMARY KEY (brain, path)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    brain TEXT NOT NULL,
                    path TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    heading TEXT NOT NULL,
                    content TEXT NOT NULL,
                    char_count INTEGER NOT NULL,
                    embedding BLOB NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_brain_path ON chunks (brain, path)"
            )
            # FTS5 Virtual Table for BM25 keyword search
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    content,
                    heading,
                    path UNINDEXED,
                    brain UNINDEXED,
                    tokenize = 'porter unicode61'
                )
                """
            )
            conn.commit()

    def index_file(self, brain: str, rel_path: str, content: str, mtime: float = 0.0) -> int:
        """Chunk, embed, and store a single markdown note into FTS5 and vector tables."""
        chunks = chunk_markdown(content, rel_path)
        with self._get_connection() as conn:
            # Delete old chunks for this file
            old_ids = [
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM chunks WHERE brain = ? AND path = ?", (brain, rel_path)
                ).fetchall()
            ]
            if old_ids:
                conn.execute(
                    "DELETE FROM chunks WHERE brain = ? AND path = ?", (brain, rel_path)
                )
                for cid in old_ids:
                    conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (cid,))

            if not chunks:
                conn.execute(
                    "INSERT OR REPLACE INTO file_meta (brain, path, mtime, chunk_count) VALUES (?, ?, ?, 0)",
                    (brain, rel_path, mtime),
                )
                conn.commit()
                return 0

            # Compute embeddings
            embeddings = self.embedder.embed_batch([c.content for c in chunks])

            for c, emb in zip(chunks, embeddings):
                cur = conn.execute(
                    """
                    INSERT INTO chunks (brain, path, chunk_index, heading, content, char_count, embedding)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        brain,
                        rel_path,
                        c.chunk_index,
                        c.heading,
                        c.content,
                        c.char_count,
                        serialize_vector(emb),
                    ),
                )
                chunk_id = cur.lastrowid
                conn.execute(
                    "INSERT INTO chunks_fts (rowid, content, heading, path, brain) VALUES (?, ?, ?, ?, ?)",
                    (chunk_id, c.content, c.heading, rel_path, brain),
                )

            conn.execute(
                "INSERT OR REPLACE INTO file_meta (brain, path, mtime, chunk_count) VALUES (?, ?, ?, ?)",
                (brain, rel_path, mtime, len(chunks)),
            )
            conn.commit()
            return len(chunks)

    def remove_file(self, brain: str, rel_path: str) -> None:
        """Remove a deleted file and its chunks from FTS5 and vector tables."""
        with self._get_connection() as conn:
            old_ids = [
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM chunks WHERE brain = ? AND path = ?", (brain, rel_path)
                ).fetchall()
            ]
            if old_ids:
                conn.execute(
                    "DELETE FROM chunks WHERE brain = ? AND path = ?", (brain, rel_path)
                )
                for cid in old_ids:
                    conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (cid,))
            conn.execute(
                "DELETE FROM file_meta WHERE brain = ? AND path = ?", (brain, rel_path)
            )
            conn.commit()

    def index_brain(self, vaults: VaultManager, brain: str, force: bool = False) -> dict[str, int]:
        """Incremental re-indexing of all markdown files in the specified brain."""
        root = vaults.brain_path(brain)
        if not root.exists():
            return {"indexed_files": 0, "total_chunks": 0}

        with self._get_connection() as conn:
            known_meta = {
                row["path"]: row["mtime"]
                for row in conn.execute(
                    "SELECT path, mtime FROM file_meta WHERE brain = ?", (brain,)
                ).fetchall()
            }

        indexed_files = 0
        total_chunks = 0
        active_paths: set[str] = set()

        for p in sorted(root.rglob("*.md")):
            if ".git" in p.parts:
                continue
            rel_path = p.relative_to(root).as_posix()
            active_paths.add(rel_path)
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue

            # Skip if file mtime has not changed unless forced
            if not force and rel_path in known_meta and abs(known_meta[rel_path] - mtime) < 1e-4:
                continue

            try:
                content = p.read_text(encoding="utf-8", errors="replace")
                chunks_added = self.index_file(brain, rel_path, content, mtime=mtime)
                indexed_files += 1
                total_chunks += chunks_added
            except Exception:  # noqa: BLE001
                pass

        # Clean up files that were deleted from disk
        for stale_path in set(known_meta.keys()) - active_paths:
            self.remove_file(brain, stale_path)

        return {"indexed_files": indexed_files, "total_chunks": total_chunks}

    def search(
        self,
        brain: str,
        query: str,
        *,
        limit: int = 10,
        alpha: float = 0.5,
        path_filter: str = "",
    ) -> list[dict[str, Any]]:
        """Hybrid RRF Search combining BM25 keyword matching and vector cosine similarity."""
        clean_q = (query or "").strip()
        if not clean_q:
            return []

        # 1. BM25 search via SQLite FTS5
        # Clean query for FTS5 (strip punctuation that breaks MATCH syntax)
        fts_query = re.sub(r'[^\w\sа-яА-ЯёЁ]', ' ', clean_q).strip()
        fts_tokens = [w for w in fts_query.split() if len(w) > 1]
        fts_term = " OR ".join(f'"{t}"*' for t in fts_tokens) if fts_tokens else ""

        fts_ranks: dict[int, int] = {}
        with self._get_connection() as conn:
            if fts_term:
                try:
                    sql = (
                        "SELECT rowid FROM chunks_fts WHERE brain = ? AND chunks_fts MATCH ? "
                        "ORDER BY rank LIMIT 50"
                    )
                    rows = conn.execute(sql, (brain, fts_term)).fetchall()
                    for rank, row in enumerate(rows, 1):
                        fts_ranks[row["rowid"]] = rank
                except sqlite3.OperationalError:
                    pass

            # 2. Vector search via cosine similarity
            q_vec = self.embedder.embed(clean_q)
            q_norm = float(np.linalg.norm(q_vec))

            # Fetch all chunks for this brain
            all_chunks = conn.execute(
                "SELECT id, path, chunk_index, heading, content, embedding FROM chunks WHERE brain = ?",
                (brain,),
            ).fetchall()

        if not all_chunks:
            return []

        # Calculate cosine similarities
        vec_scores: list[tuple[int, float]] = []
        chunk_dict: dict[int, Any] = {}
        for row in all_chunks:
            cid = row["id"]
            path = row["path"]
            if path_filter and not path.lower().startswith(path_filter.lower()):
                continue
            chunk_dict[cid] = row
            if q_norm > 0.0:
                emb = deserialize_vector(row["embedding"])
                dot = float(np.dot(q_vec, emb))
                vec_scores.append((cid, dot))

        vec_scores.sort(key=lambda x: x[1], reverse=True)
        vec_ranks: dict[int, int] = {cid: rank for rank, (cid, _) in enumerate(vec_scores[:50], 1)}

        # 3. Reciprocal Rank Fusion (RRF)
        candidate_ids = set(fts_ranks.keys()) | set(vec_ranks.keys())
        scored_candidates: list[tuple[int, float, str]] = []

        for cid in candidate_ids:
            if cid not in chunk_dict:
                continue
            r_fts = fts_ranks.get(cid)
            r_vec = vec_ranks.get(cid)

            score_fts = (alpha / (RRF_K + r_fts)) if r_fts else 0.0
            score_vec = ((1.0 - alpha) / (RRF_K + r_vec)) if r_vec else 0.0
            combined_score = score_fts + score_vec

            match_type = "hybrid" if (r_fts and r_vec) else ("keyword" if r_fts else "semantic")
            scored_candidates.append((cid, combined_score, match_type))

        scored_candidates.sort(key=lambda x: x[1], reverse=True)

        # 4. Format top results
        results: list[dict[str, Any]] = []
        for cid, score, m_type in scored_candidates[:limit]:
            row = chunk_dict[cid]
            content = row["content"]
            snippet = content[:300].strip() + ("..." if len(content) > 300 else "")
            results.append(
                {
                    "path": row["path"],
                    "heading": row["heading"],
                    "chunk_index": row["chunk_index"],
                    "snippet": snippet,
                    "score": round(score * 100, 2),
                    "match_type": m_type,
                }
            )

        return results
