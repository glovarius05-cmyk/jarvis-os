"""Autonomous Browser Agent adapter (browser-use -> Playwright -> SSRF-safe HTTP fallback).

Why this exists: Modern SPAs, login-gated dashboards, and multi-step web forms cannot
be automated with static HTML fetching alone. This module integrates `browser-use` when
installed, falls back to Playwright headless Chromium for JS-rendered pages, and safely
degrades to `web_reader` when neither browser runtime is installed.
"""

from __future__ import annotations

import importlib.util
from typing import Any

from app.hub.web_reader import fetch_and_extract_webpage


def detect_browser_backend() -> str:
    """Return the richest available browser automation backend: 'browser_use' | 'playwright' | 'http'."""
    if importlib.util.find_spec("browser_use") is not None:
        return "browser_use"
    if importlib.util.find_spec("playwright") is not None:
        return "playwright"
    return "http"


async def run_browser_task(
    *,
    task: str,
    url: str = "",
    max_steps: int = 10,
    headless: bool = True,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a browser automation or SPA extraction task with SSRF protection."""
    clean_task = (task or "").strip()
    if not clean_task:
        return {"ok": False, "error": "task cannot be empty", "backend": "none"}

    clean_url = (url or "").strip()
    if clean_url:
        from app.hub.builtin_tools import is_safe_public_url

        safe, reason = is_safe_public_url(clean_url)
        if not safe:
            return {"ok": False, "error": f"SSRF block: {reason}", "backend": "ssrf_guard"}

    backend = detect_browser_backend()
    cfg = settings or {}

    if backend == "browser_use":
        try:
            return await _run_with_browser_use(
                task=clean_task,
                url=clean_url,
                max_steps=max(1, min(int(max_steps), 30)),
                headless=headless,
                cfg=cfg,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "backend": "browser_use", "error": str(exc)}

    if backend == "playwright" and clean_url:
        try:
            return await _run_with_playwright(url=clean_url, headless=headless)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "backend": "playwright", "error": str(exc)}

    if clean_url:
        try:
            page = await fetch_and_extract_webpage(clean_url)
            return {
                "ok": True,
                "backend": "http",
                "url": page.url,
                "title": page.title,
                "content": page.content_markdown[:8000],
                "note": (
                    "Executed via static HTTP reader (browser-use/playwright not installed). "
                    "Install `pip install browser-use playwright && playwright install chromium` for full interactive browser control."
                ),
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "backend": "http", "error": str(exc)}

    return {
        "ok": False,
        "backend": backend,
        "error": (
            "Interactive browser agent requires `browser-use` (or a target `url` for HTTP fallback). "
            "Install via: pip install browser-use playwright && playwright install chromium"
        ),
    }


async def _run_with_browser_use(
    *,
    task: str,
    url: str,
    max_steps: int,
    headless: bool,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    from browser_use import Agent, Browser, BrowserConfig  # type: ignore[import-not-found]

    llm = _build_browser_use_llm(cfg)
    full_task = f"Open {url} and then: {task}" if url else task
    browser = Browser(config=BrowserConfig(headless=headless))
    try:
        agent = Agent(task=full_task, llm=llm, browser=browser)
        history = await agent.run(max_steps=max_steps)
        final_result = ""
        if hasattr(history, "final_result"):
            final_result = str(history.final_result() or "")
        else:
            final_result = str(history or "")
        return {
            "ok": True,
            "backend": "browser_use",
            "url": url,
            "result": final_result[:12000],
        }
    finally:
        if hasattr(browser, "close"):
            await browser.close()


def _build_browser_use_llm(cfg: dict[str, Any]) -> Any:
    openai_key = str(cfg.get("openai_api_key") or "").strip()
    gemini_key = str(cfg.get("gemini_api_key") or "").strip()
    if openai_key:
        from langchain_openai import ChatOpenAI  # type: ignore[import-not-found]

        return ChatOpenAI(model="gpt-4o-mini", api_key=openai_key)
    if gemini_key:
        from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore[import-not-found]

        return ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key=gemini_key)
    raise RuntimeError("browser-use requires an OpenAI or Gemini API key configured in Jarvis Settings.")


async def _run_with_playwright(*, url: str, headless: bool) -> dict[str, Any]:
    from playwright.async_api import async_playwright  # type: ignore[import-not-found]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        try:
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            title = await page.title()
            body_text = await page.inner_text("body")
            return {
                "ok": True,
                "backend": "playwright",
                "url": page.url,
                "title": title,
                "content": (body_text or "")[:10000],
            }
        finally:
            await browser.close()
