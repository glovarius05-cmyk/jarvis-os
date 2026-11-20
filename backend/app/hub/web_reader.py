"""Agentic Web Reader: converts HTML into clean, readable Markdown and extracts metadata.

Why this exists: Raw HTML from modern websites contains navbars, cookie dialogs,
footer menus, scripts, and clutter that overflows LLM context windows and confuses agents.
This reader extracts the core article/documentation body, transforms it into clean GitHub-flavored
Markdown, extracts metadata (title, author, description), and can save it to the Second Brain vault.
"""

from __future__ import annotations

import datetime
import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.brain.vault import VaultManager, safe_resolve
from app.hub.builtin_tools import is_safe_public_url

_NOISE_TAGS = {
    "script",
    "style",
    "noscript",
    "svg",
    "iframe",
    "canvas",
    "nav",
    "footer",
    "header",
    "aside",
    "form",
    "button",
    "input",
    "select",
    "textarea",
    "dialog",
}

_NOISE_ATTR_RE = re.compile(
    r"(cookie|consent|banner|popup|advertisement|sidebar|social-share|share-btn|widget-ad|comments|disqus|newsletter)",
    re.IGNORECASE,
)

_MULTIPLE_NL_RE = re.compile(r"\n{3,}")
_MULTIPLE_SPACES_RE = re.compile(r"[ \t]+")


@dataclass
class ExtractedWebpage:
    url: str
    title: str
    description: str
    author: str
    content_markdown: str
    text_length: int
    is_article: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class _HTMLToMarkdownParser(HTMLParser):
    def __init__(self, base_url: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self.description = ""
        self.author = ""
        self.site_name = ""

        self._in_title = False
        self._title_chunks: list[str] = []

        self._ignore_depth = 0
        self._current_link_href = ""
        self._current_link_text: list[str] = []

        self._in_pre = False
        self._pre_lang = ""
        self._in_code = False
        self._in_table = False
        self._table_rows: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell: list[str] = []
        self._is_header_row = False

        self._list_stack: list[str] = []  # 'ul' or 'ol'
        self._ol_counter: list[int] = []

        self._article_depth = 0
        self._has_article = False

        # Output text buffers
        self._main_chunks: list[str] = []
        self._article_chunks: list[str] = []

    @property
    def _active_chunks(self) -> list[str]:
        if self._article_depth > 0:
            return self._article_chunks
        return self._main_chunks

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        attr_dict = {k.lower(): (v or "") for k, v in attrs}

        # Check metadata
        if tag_lower == "title":
            self._in_title = True
            return

        if tag_lower == "meta":
            prop = attr_dict.get("property", "").lower() or attr_dict.get("name", "").lower()
            content = attr_dict.get("content", "").strip()
            if not content:
                return
            if prop in {"og:title", "twitter:title"} and not self.title:
                self.title = content
            elif prop in {"description", "og:description", "twitter:description"} and not self.description:
                self.description = content
            elif prop in {"author", "article:author"} and not self.author:
                self.author = content
            elif prop in {"og:site_name", "site_name"} and not self.site_name:
                self.site_name = content
            return

        # Check if tag is noisy
        if tag_lower in _NOISE_TAGS:
            self._ignore_depth += 1
            return

        # Check class / id / role noise
        class_val = attr_dict.get("class", "")
        id_val = attr_dict.get("id", "")
        role_val = attr_dict.get("role", "")
        if (
            _NOISE_ATTR_RE.search(class_val)
            or _NOISE_ATTR_RE.search(id_val)
            or role_val in {"navigation", "banner", "contentinfo", "complementary"}
        ):
            self._ignore_depth += 1
            return

        if self._ignore_depth > 0:
            self._ignore_depth += 1
            return

        # Article container tracking
        if tag_lower in {"article", "main"} or role_val == "main":
            self._article_depth += 1
            self._has_article = True

        # Markdown element translation
        if tag_lower in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(tag_lower[1])
            self._active_chunks.append("\n\n" + ("#" * level) + " ")
        elif tag_lower == "p":
            self._active_chunks.append("\n\n")
        elif tag_lower == "br":
            self._active_chunks.append("\n")
        elif tag_lower == "hr":
            self._active_chunks.append("\n\n---\n\n")
        elif tag_lower in {"strong", "b"}:
            self._active_chunks.append("**")
        elif tag_lower in {"em", "i"}:
            self._active_chunks.append("*")
        elif tag_lower == "blockquote":
            self._active_chunks.append("\n\n> ")
        elif tag_lower == "pre":
            self._in_pre = True
            self._pre_lang = ""
            self._active_chunks.append("\n\n```")
        elif tag_lower == "code":
            self._in_code = True
            if self._in_pre:
                cls = attr_dict.get("class", "")
                m = re.search(r"language-(\w+)", cls)
                if m:
                    self._pre_lang = m.group(1)
                    self._active_chunks.append(self._pre_lang)
                self._active_chunks.append("\n")
            else:
                self._active_chunks.append("`")
        elif tag_lower == "a":
            href = attr_dict.get("href", "").strip()
            if href and not href.startswith(("javascript:", "#")):
                if self.base_url:
                    href = urljoin(self.base_url, href)
                self._current_link_href = href
                self._current_link_text = []
        elif tag_lower == "ul":
            self._list_stack.append("ul")
            self._active_chunks.append("\n")
        elif tag_lower == "ol":
            self._list_stack.append("ol")
            self._ol_counter.append(1)
            self._active_chunks.append("\n")
        elif tag_lower == "li":
            indent = "  " * max(0, len(self._list_stack) - 1)
            if self._list_stack and self._list_stack[-1] == "ol" and self._ol_counter:
                num = self._ol_counter[-1]
                self._ol_counter[-1] += 1
                self._active_chunks.append(f"\n{indent}{num}. ")
            else:
                self._active_chunks.append(f"\n{indent}- ")
        elif tag_lower == "table":
            self._in_table = True
            self._table_rows = []
        elif tag_lower == "tr":
            self._current_row = []
            self._is_header_row = False
        elif tag_lower in {"th", "td"}:
            if tag_lower == "th":
                self._is_header_row = True
            self._current_cell = []
        elif tag_lower == "img":
            src = attr_dict.get("src", "").strip()
            alt = attr_dict.get("alt", "").strip()
            if src and not src.startswith("data:"):
                if self.base_url:
                    src = urljoin(self.base_url, src)
                self._active_chunks.append(f"![{alt or 'image'}]({src})")

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()

        if tag_lower == "title":
            self._in_title = False
            full_title = "".join(self._title_chunks).strip()
            if full_title and not self.title:
                self.title = full_title
            return

        if self._ignore_depth > 0:
            self._ignore_depth -= 1
            return

        if tag_lower in {"article", "main"}:
            if self._article_depth > 0:
                self._article_depth -= 1

        if tag_lower in {"h1", "h2", "h3", "h4", "h5", "h6", "p"}:
            self._active_chunks.append("\n\n")
        elif tag_lower in {"strong", "b"}:
            self._active_chunks.append("**")
        elif tag_lower in {"em", "i"}:
            self._active_chunks.append("*")
        elif tag_lower == "pre":
            self._in_pre = False
            self._active_chunks.append("\n```\n\n")
        elif tag_lower == "code":
            self._in_code = False
            if not self._in_pre:
                self._active_chunks.append("`")
        elif tag_lower == "a":
            if self._current_link_href:
                anchor_text = "".join(self._current_link_text).strip()
                if anchor_text:
                    self._active_chunks.append(f"[{anchor_text}]({self._current_link_href})")
                else:
                    self._active_chunks.append(self._current_link_href)
                self._current_link_href = ""
                self._current_link_text = []
        elif tag_lower == "ul":
            if self._list_stack and self._list_stack[-1] == "ul":
                self._list_stack.pop()
            self._active_chunks.append("\n")
        elif tag_lower == "ol":
            if self._list_stack and self._list_stack[-1] == "ol":
                self._list_stack.pop()
            if self._ol_counter:
                self._ol_counter.pop()
            self._active_chunks.append("\n")
        elif tag_lower in {"th", "td"}:
            cell_text = "".join(self._current_cell).strip().replace("|", "\\|")
            self._current_row.append(cell_text)
            self._current_cell = []
        elif tag_lower == "tr":
            if self._current_row:
                self._table_rows.append(self._current_row)
            self._current_row = []
        elif tag_lower == "table":
            self._in_table = False
            if self._table_rows:
                # Render markdown table
                cols = max(len(r) for r in self._table_rows)
                if cols > 0:
                    first_row = self._table_rows[0]
                    padded_first = first_row + [""] * (cols - len(first_row))
                    lines = [
                        "\n\n| " + " | ".join(padded_first) + " |",
                        "| " + " | ".join(["---"] * cols) + " |",
                    ]
                    for row in self._table_rows[1:]:
                        padded = row + [""] * (cols - len(row))
                        lines.append("| " + " | ".join(padded) + " |")
                    lines.append("\n\n")
                    self._active_chunks.append("\n".join(lines))
            self._table_rows = []

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_chunks.append(data)
            return

        if self._ignore_depth > 0:
            return

        if self._in_table and self._current_row is not None:
            self._current_cell.append(data)
            return

        if self._current_link_href:
            self._current_link_text.append(data)
            return

        if self._in_pre:
            self._active_chunks.append(data)
            return

        # Normal text handling
        cleaned = _MULTIPLE_SPACES_RE.sub(" ", data)
        self._active_chunks.append(cleaned)


def parse_html_to_markdown(html_text: str, base_url: str = "") -> ExtractedWebpage:
    """Parse HTML string into an ExtractedWebpage with Markdown body."""
    parser = _HTMLToMarkdownParser(base_url=base_url)
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:  # noqa: BLE001
        pass

    # Prefer article content if detected
    article_text = "".join(parser._article_chunks).strip()
    main_text = "".join(parser._main_chunks).strip()
    if article_text:
        raw_md = article_text
        is_article = True
    else:
        raw_md = main_text
        is_article = False

    cleaned_md = _MULTIPLE_NL_RE.sub("\n\n", raw_md).strip()

    title = parser.title.strip()
    if not title:
        # Fallback to domain or first heading
        first_h = re.search(r"^#\s+(.+)$", cleaned_md, re.MULTILINE)
        if first_h:
            title = first_h.group(1).strip()
        elif base_url:
            title = urlparse(base_url).netloc

    return ExtractedWebpage(
        url=base_url,
        title=title,
        description=parser.description.strip(),
        author=parser.author.strip(),
        content_markdown=cleaned_md,
        text_length=len(cleaned_md),
        is_article=is_article,
        metadata={
            "site_name": parser.site_name,
        },
    )


async def fetch_and_extract_webpage(
    url: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = 15.0,
    max_redirects: int = 5,
) -> ExtractedWebpage:
    """Safely fetch a public webpage (SSRF-guarded) and extract its Markdown body and metadata."""
    safe, reason = is_safe_public_url(url)
    if not safe:
        raise ValueError(f"SSRF block: {reason}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    }

    current_url = url
    async with httpx.AsyncClient(
        transport=transport,
        timeout=timeout,
        headers=headers,
        follow_redirects=False,
    ) as client:
        for _ in range(max_redirects):
            resp = await client.get(current_url)
            if resp.status_code in {301, 302, 303, 307, 308}:
                loc = resp.headers.get("location")
                if not loc:
                    break
                next_url = urljoin(current_url, loc)
                safe_redir, redir_reason = is_safe_public_url(next_url)
                if not safe_redir:
                    raise ValueError(f"SSRF block on redirect to {next_url}: {redir_reason}")
                current_url = next_url
                continue

            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code} error when fetching {current_url}")

            content_type = resp.headers.get("content-type", "").lower()
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                # If plain text or json
                text = resp.text
                return ExtractedWebpage(
                    url=current_url,
                    title=urlparse(current_url).path or current_url,
                    description="",
                    author="",
                    content_markdown=text,
                    text_length=len(text),
                    is_article=False,
                )

            return parse_html_to_markdown(resp.text, base_url=current_url)

    raise RuntimeError(f"Too many redirects ({max_redirects}) fetching {url}")


def slugify(text: str) -> str:
    """Turn a title or URL into a safe filename slug."""
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:64] or "article"


def save_webpage_to_vault(
    vaults: VaultManager,
    brain: str,
    page: ExtractedWebpage,
    target_path: str = "",
) -> str:
    """Save an extracted webpage into Second Brain vault with YAML frontmatter."""
    if target_path:
        rel_path = target_path.strip().lstrip("/")
        if not rel_path.endswith(".md"):
            rel_path += ".md"
    else:
        slug = slugify(page.title) or slugify(urlparse(page.url).netloc)
        rel_path = f"sources/{slug}.md"

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    lines = [
        "---",
        f"title: \"{page.title.replace('\"', '')}\"",
        f"source_url: \"{page.url}\"",
        f"ingested_at: \"{now_iso}\"",
    ]
    if page.author:
        lines.append(f"author: \"{page.author.replace('\"', '')}\"")
    if page.description:
        lines.append(f"description: \"{page.description.replace('\"', '')}\"")
    lines.extend([
        "tags:",
        "  - web",
        "  - source",
        "---",
        "",
        f"# {page.title}",
        "",
        f"> Source: [{page.url}]({page.url}) | Ingested: {now_iso[:10]}",
        "",
        page.content_markdown,
    ])
    full_content = "\n".join(lines).strip() + "\n"

    vaults.write_text(brain, rel_path, full_content)
    return rel_path
