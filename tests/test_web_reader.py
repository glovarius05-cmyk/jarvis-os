"""Tests for Agentic Web Reader and jarvis_browse_url tool."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.brain.vault import VaultManager
from app.hub.builtin_tools import build_builtin_tools
from app.hub.shaper import ToolResultStore
from app.hub.web_reader import (
    ExtractedWebpage,
    fetch_and_extract_webpage,
    parse_html_to_markdown,
    save_webpage_to_vault,
    slugify,
)
from app.security.secrets import SecretStore

SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Understanding Modern Transformers</title>
    <meta name="description" content="A comprehensive breakdown of transformer attention mechanisms.">
    <meta name="author" content="Vaswani et al.">
    <meta property="og:site_name" content="AI Research Lab">
</head>
<body>
    <header>
        <nav><a href="/">Home</a><a href="/archive">Archive</a></nav>
    </header>
    <div class="cookie-banner-popup">Please accept cookies to continue.</div>
    <div id="advertisement-sidebar">Ad: Buy GPU credits now!</div>

    <article>
        <h1>Understanding Modern Transformers</h1>
        <p>Transformers have revolutionized <strong>natural language processing</strong> and <em>computer vision</em>.</p>
        
        <h2>Core Architecture</h2>
        <p>The key innovation is the multi-head attention mechanism:</p>
        <ul>
            <li>Scaled Dot-Product Attention</li>
            <li>Multi-Head Projections</li>
            <li>Feed-Forward Networks</li>
        </ul>

        <h2>Code Implementation</h2>
        <pre><code class="language-python">import numpy as np

def scaled_dot_product(q, k, v):
    d_k = q.shape[-1]
    scores = (q @ k.T) / np.sqrt(d_k)
    return scores @ v
</code></pre>

        <h2>Performance Benchmark</h2>
        <table>
            <tr><th>Model</th><th>Params</th><th>Accuracy</th></tr>
            <tr><td>Mini</td><td>10M</td><td>78.4%</td></tr>
            <tr><td>Base</td><td>110M</td><td>88.9%</td></tr>
        </table>

        <p>For more details, visit <a href="/docs/attention.pdf">Paper PDF</a> or external <a href="https://arxiv.org/abs/1706.03762">arXiv paper</a>.</p>
    </article>

    <footer>
        <p>&copy; 2026 AI Research Lab. All rights reserved.</p>
    </footer>
</body>
</html>
"""


def test_parse_html_to_markdown() -> None:
    page = parse_html_to_markdown(SAMPLE_HTML, base_url="https://example.com/blog/transformers")

    assert page.title == "Understanding Modern Transformers"
    assert "transformer attention mechanisms" in page.description
    assert page.author == "Vaswani et al."
    assert page.is_article is True

    md = page.content_markdown
    # Header check
    assert "# Understanding Modern Transformers" in md
    assert "## Core Architecture" in md
    assert "## Code Implementation" in md

    # Formatting check
    assert "**natural language processing**" in md
    assert "*computer vision*" in md
    assert "- Scaled Dot-Product Attention" in md

    # Code block with language tag
    assert "```python" in md
    assert "def scaled_dot_product(q, k, v):" in md

    # Table check
    assert "| Model | Params | Accuracy |" in md
    assert "| Mini | 10M | 78.4% |" in md

    # URL resolution check
    assert "[Paper PDF](https://example.com/docs/attention.pdf)" in md
    assert "[arXiv paper](https://arxiv.org/abs/1706.03762)" in md

    # Noise stripping check (nav, cookie banner, ads, footer should NOT appear)
    assert "Please accept cookies" not in md
    assert "Buy GPU credits" not in md
    assert "All rights reserved" not in md


def test_slugify() -> None:
    assert slugify("Understanding Modern Transformers") == "understanding-modern-transformers"
    assert slugify("Hello, World! 2026 @ AI") == "hello-world-2026-ai"


def test_save_webpage_to_vault(tmp_path: Path) -> None:
    vaults = VaultManager(tmp_path / "brains")
    vaults.ensure_brain("Brain Default", init_git=False)

    page = ExtractedWebpage(
        url="https://example.com/guide",
        title="Quickstart Guide",
        description="Getting started with the platform.",
        author="Tech Team",
        content_markdown="## Installation\n\nRun `pip install my-tool`.",
        text_length=42,
    )

    rel_path = save_webpage_to_vault(vaults, "Brain Default", page)
    assert rel_path == "sources/quickstart-guide.md"

    saved_text = vaults.read_text("Brain Default", rel_path)
    assert "title: \"Quickstart Guide\"" in saved_text
    assert "source_url: \"https://example.com/guide\"" in saved_text
    assert "author: \"Tech Team\"" in saved_text
    assert "## Installation" in saved_text
    assert "Run `pip install my-tool`." in saved_text


@pytest.mark.asyncio
async def test_ssrf_protection_in_fetch() -> None:
    # Localhost / private IP must be blocked
    with pytest.raises(ValueError, match="SSRF block"):
        await fetch_and_extract_webpage("http://127.0.0.1:8000/admin")

    with pytest.raises(ValueError, match="SSRF block"):
        await fetch_and_extract_webpage("http://localhost:5000/secrets")

    with pytest.raises(ValueError, match="SSRF block"):
        await fetch_and_extract_webpage("http://192.168.1.1/router")


@pytest.mark.asyncio
async def test_jarvis_browse_url_tool_integration(tmp_path: Path) -> None:
    vaults = VaultManager(tmp_path / "brains")
    vaults.ensure_brain("Brain Default", init_git=False)
    res_store = ToolResultStore(tmp_path / "state", SecretStore(tmp_path / "secrets"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SAMPLE_HTML, headers={"content-type": "text/html; charset=utf-8"})

    mock_transport = httpx.MockTransport(handler)

    tools = build_builtin_tools(
        vaults=vaults,
        result_store=res_store,
        list_connections_fn=lambda: [],
        http_transport=mock_transport,
    )
    tool_map = {t.name: t for t in tools}
    assert "jarvis_browse_url" in tool_map

    browse_handler = tool_map["jarvis_browse_url"].handler

    # 1. Browse and verify output
    result = await browse_handler(
        {"url": "https://example.com/transformers", "save_to_vault": True},
        "Brain Default",
    )

    assert "# Understanding Modern Transformers" in result
    assert "Source: https://example.com/transformers" in result
    assert "Author: Vaswani et al." in result
    assert "Saved to Second Brain vault: `sources/understanding-modern-transformers.md`" in result
    assert "## Core Architecture" in result
    assert "```python" in result

    # 2. Check vault file was created
    saved_text = vaults.read_text("Brain Default", "sources/understanding-modern-transformers.md")
    assert "source_url: \"https://example.com/transformers\"" in saved_text
