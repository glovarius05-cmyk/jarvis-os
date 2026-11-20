"""Guardrails on the system prompt kernel: bounded token budget and TTS-safe punctuation."""

from __future__ import annotations

from pathlib import Path

KERNEL_MAX_CHARS = 14_000


def test_prompt_kernel_budget_and_no_em_dash() -> None:
    root = Path(__file__).resolve().parents[1]
    kernel = (root / "prompts" / "kernel.md").read_text(encoding="utf-8")
    voice_kernel = (root / "prompts" / "voice_kernel.md").read_text(encoding="utf-8")

    assert 500 < len(kernel) <= KERNEL_MAX_CHARS
    assert "\u2014" not in kernel
    assert "\u2014" not in voice_kernel
