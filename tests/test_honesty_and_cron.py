"""Tests for Honesty Guard (Russian + English) and 5-field cron matcher."""

from __future__ import annotations

from datetime import datetime

from app.runtime.honesty_guard import apply_honesty_guard, detect_empty_promise
from app.runtime.reminders import cron_matches, validate_cron


def test_honesty_guard_detects_ru_and_en_empty_promises_and_ignores_code() -> None:
    # Russian empty promise
    out_ru, rule_ru = apply_honesty_guard(
        "Хорошо, я проверю логи и сразу отпишусь тебе.",
        created_background_job=False,
    )
    assert rule_ru == "ru_report_back"
    assert "Проверка честности" in out_ru

    # English empty promise
    out_en, rule_en = apply_honesty_guard(
        "Sure, I will report back once finished.",
        created_background_job=False,
    )
    assert rule_en == "en_report_back"
    assert "Honesty Guard" in out_en

    # Silent when a real background job WAS created
    clean_out, flag = apply_honesty_guard(
        "Queued it! I will report back once finished.",
        created_background_job=True,
    )
    assert flag == ""
    assert clean_out == "Queued it! I will report back once finished."

    # Ignores promises quoted inside fenced or inline code blocks
    code_only = "Do not write `I will report back` or:\n```\nя отпишусь позже\n```"
    assert detect_empty_promise(code_only) == ""


def test_five_field_cron_validation_and_matching() -> None:
    assert validate_cron("30 8 * * 1-5") is True
    assert validate_cron("*/15 * * * *") is True
    assert validate_cron("invalid cron") is False

    # Wednesday 2026-11-18 08:30 -> weekday Mon=1..Wed=3
    wed_830 = datetime(2026, 11, 18, 8, 30)
    assert cron_matches("30 8 * * 1-5", wed_830) is True
    assert cron_matches("*/15 * * * *", wed_830) is True
    assert cron_matches("31 8 * * 1-5", wed_830) is False

    # Sunday 2026-11-22 10:00 -> matches both 0 and 7 in cron
    sun_10 = datetime(2026, 11, 22, 10, 0)
    assert cron_matches("0 10 * * 0", sun_10) is True
    assert cron_matches("0 10 * * 7", sun_10) is True
    assert cron_matches("0 10 * * 1-5", sun_10) is False
