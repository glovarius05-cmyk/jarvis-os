"""Bundled plugin: jarvis-youtube - extract YouTube video metadata and transcript."""

from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET

import httpx


def _extract_video_id(url_or_id: str) -> str:
    raw = (url_or_id or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", raw):
        return raw
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", raw)
    return m.group(1) if m else ""


def register(ctx) -> None:
    async def jarvis_youtube(args: dict, _brain: str) -> str:
        url = str(args.get("url") or args.get("video_id") or "").strip()
        pref_lang = str(args.get("lang") or "ru").strip().lower()
        vid = _extract_video_id(url)
        if not vid:
            return "ERROR: Valid YouTube URL or 11-character video ID is required."

        watch_url = f"https://www.youtube.com/watch?v={vid}"
        try:
            async with httpx.AsyncClient(
                timeout=12.0,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; JarvisOS/1.0)"},
            ) as client:
                resp = await client.get(watch_url)
                if resp.status_code != 200:
                    return f"ERROR: YouTube returned HTTP {resp.status_code}."
                page = resp.text

                title_m = re.search(r"<title>(.*?)</title>", page, re.DOTALL)
                title = html.unescape(title_m.group(1)).replace(" - YouTube", "").strip() if title_m else vid

                cap_m = re.search(r'"captionTracks":(\[.*?\])', page)
                if not cap_m:
                    return f"Видео: **{title}** ({watch_url})\nСубтитры (транскрипт) недоступны для этого видео."

                tracks = json.loads(cap_m.group(1))
                chosen = None
                for lang in (pref_lang, "en", "ru"):
                    for tr in tracks:
                        if str(tr.get("languageCode") or "").lower().startswith(lang):
                            chosen = tr
                            break
                    if chosen:
                        break
                if not chosen and tracks:
                    chosen = tracks[0]

                base_url = chosen.get("baseUrl") if isinstance(chosen, dict) else ""
                if not base_url:
                    return f"Видео: **{title}** ({watch_url})\nНе удалось получить URL субтитров."

                xml_res = await client.get(base_url)
                if xml_res.status_code != 200:
                    return f"ERROR: Failed to fetch captions XML (HTTP {xml_res.status_code})."

            root = ET.fromstring(xml_res.text)
            segments = [html.unescape("".join(el.itertext())).strip() for el in root.findall(".//text")]
            transcript = " ".join(s for s in segments if s)
            lang_code = chosen.get("languageCode", "auto") if isinstance(chosen, dict) else "auto"

            full_text = f"**{title}** ({watch_url}, lang={lang_code})\n\nТранскрипт:\n{transcript}"
            hub = ctx.services.get("hub")
            if hub is not None and len(full_text) > 6000:
                preview, _ = hub.shaper.shape_result(full_text, tool_name="jarvis_youtube")
                return preview
            return full_text
        except Exception as exc:
            return f"ERROR: Failed to extract YouTube transcript: {exc}"

    ctx.register_tool(
        name="jarvis_youtube",
        description="Fetch title and full spoken transcript/captions from any YouTube video URL.",
        handler=jarvis_youtube,
        min_mode="readonly",
        schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "YouTube URL or 11-char video ID"},
                "lang": {"type": "string", "description": "Preferred caption language code (default: ru)", "default": "ru"},
            },
            "required": ["url"],
        },
    )
