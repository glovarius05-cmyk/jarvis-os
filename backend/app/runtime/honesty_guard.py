"""Honesty Guard: detects empty async promises when no real background job was created.

Why this exists: LLMs frequently reply "I'm checking this now and will report back shortly"
and end their turn without queuing any task, leaving the user waiting forever. Rather than
relying solely on prompt instructions, we verify against reality: if the text promises a
follow-up report and zero background jobs were created during that turn, the server appends
a concise factual correction below the answer.
"""

from __future__ import annotations

import re

_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")

_PROMISE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "en_report_back",
        re.compile(
            r"\b(i(?:'ll| will)\s+(?:report back|let you know|get back to you|update you|follow up|summarize once|wait for .* to finish))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "en_working_on_it",
        re.compile(
            r"\b(i(?:'m| am)\s+(?:looking into|checking|investigating|working on) .{0,40}and\s+(?:will|'ll)\s+(?:report|update|notify|tell))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ru_report_back",
        re.compile(
            r"(отпишусь|сообщу\s+как\s+только|дам\s+знать\s+(?:как|когда)|вернусь\s+с\s+(?:результат|отчёт)|дождусь\s+завершени|проверю\s+и\s+(?:сразу\s+)?(?:сообщу|напишу|отпишусь|доложу))",
            re.IGNORECASE,
        ),
    ),
)

_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")


def detect_empty_promise(text: str) -> str:
    """Return matched promise rule name, or '' if the text contains no async promise."""
    if not text:
        return ""
    stripped = _CODE_FENCE_RE.sub(" ", text)
    stripped = _INLINE_CODE_RE.sub(" ", stripped)
    for name, pattern in _PROMISE_PATTERNS:
        if pattern.search(stripped):
            return name
    return ""


def apply_honesty_guard(text: str, *, created_background_job: bool) -> tuple[str, str]:
    """Append a factual note if text promises a follow-up without any real background job."""
    if created_background_job:
        return text, ""
    matched = detect_empty_promise(text)
    if not matched:
        return text, ""

    is_ru = bool(_CYRILLIC_RE.search(text))
    if is_ru:
        note = (
            "\n\n---\n"
            "> **Проверка честности (Jarvis Runtime):** в этом ходе не была создана реальная фоновая "
            "задача (Kanban / напоминание), поэтому автоматический отчёт позже не придёт. "
            "Попроси явно поставить задачу в очередь (`jarvis_task`) или выполнить проверку прямо сейчас."
        )
    else:
        note = (
            "\n\n---\n"
            "> **Honesty Guard (Jarvis Runtime):** no real background job or reminder was queued "
            "during this turn, so nothing will automatically report back later. Ask explicitly to "
            "queue a Kanban task (`jarvis_task`) or run the check immediately."
        )
    return text + note, matched
