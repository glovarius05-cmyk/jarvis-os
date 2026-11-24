"""Owner Telegram Bot channel using pure httpx long-polling (zero heavy bot SDK deps).

Security: enforces a strict owner chat_id whitelist so strangers who find the bot
username cannot drive Jarvis or access the user's Second Brain.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.channels.base import TurnShell
from app.config import SettingsStore

TG_API_BASE = "https://api.telegram.org"
TG_MAX_MESSAGE_LEN = 4000


def chunk_telegram_text(text: str, limit: int = TG_MAX_MESSAGE_LEN) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    if len(raw) <= limit:
        return [raw]
    chunks: list[str] = []
    remaining = raw
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 3:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    return chunks


class TelegramBotService:
    """Long-polling Telegram bot for the owner + background Inbox notification delivery."""

    def __init__(
        self,
        *,
        settings: SettingsStore,
        turn_shell: TurnShell,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._shell = turn_shell
        self._transport = http_transport
        self._poll_task: asyncio.Task[None] | None = None
        self._offset = 0
        self._running = False
        self._last_error = ""
        self._bot_username = ""

    def _token(self) -> str:
        return str(self._settings.load().get("telegram_bot_token") or "").strip()

    def _allowed_chat_ids(self) -> set[str]:
        tg_cfg = self._settings.load().get("telegram") or {}
        ids = tg_cfg.get("allowed_chat_ids") or []
        return {str(x).strip() for x in ids if str(x).strip()}

    def is_enabled(self) -> bool:
        tg_cfg = self._settings.load().get("telegram") or {}
        return bool(tg_cfg.get("enabled", False)) and bool(self._token())

    def status(self) -> dict[str, Any]:
        return {
            "configured": bool(self._token()),
            "enabled": self.is_enabled(),
            "polling": self._running and self._poll_task is not None and not self._poll_task.done(),
            "allowed_chat_ids": sorted(self._allowed_chat_ids()),
            "bot_username": self._bot_username,
            "last_error": self._last_error,
        }

    async def _api_call(self, method: str, payload: dict[str, Any] | None = None, timeout: float = 35.0) -> dict[str, Any]:
        token = self._token()
        if not token:
            raise RuntimeError("Telegram bot token is not configured")
        url = f"{TG_API_BASE}/bot{token}/{method}"
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            resp = await client.post(url, json=payload or {})
            data = resp.json()
            if resp.status_code >= 400 or not data.get("ok"):
                raise RuntimeError(f"Telegram API {method} failed: {data.get('description') or resp.status_code}")
            return data

    async def test_connection(self) -> dict[str, Any]:
        try:
            data = await self._api_call("getMe", {}, timeout=10.0)
            result = data.get("result") or {}
            self._bot_username = str(result.get("username") or "")
            self._last_error = ""
            return {"ok": True, "username": self._bot_username, "first_name": result.get("first_name", "")}
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            return {"ok": False, "error": str(exc)}

    async def send_message(self, chat_id: str, text: str) -> bool:
        chunks = chunk_telegram_text(text)
        if not chunks:
            return False
        for part in chunks:
            await self._api_call("sendMessage", {"chat_id": chat_id, "text": part}, timeout=15.0)
        return True

    async def send_voice(self, chat_id: str, audio_bytes: bytes, caption: str = "") -> bool:
        """Send a native playable voice note to Telegram chat using Edge TTS synthesized audio."""
        if not self._token() or not audio_bytes:
            return False
        url = f"{TG_API_BASE}/bot{self._token()}/sendVoice"
        async with httpx.AsyncClient(timeout=35.0, transport=self._transport) as client:
            files = {"voice": ("voice.mp3", audio_bytes, "audio/mpeg")}
            data: dict[str, Any] = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption[:1024]
            resp = await client.post(url, data=data, files=files)
            return resp.status_code == 200 and bool(resp.json().get("ok"))

    async def notify_from_inbox(self, owner_chat: str, title: str, body: str) -> None:
        """InboxStore callback: push background reports to tg:<chat_id> (or default whitelisted chat)."""
        if not self._token():
            return
        target_chat = ""
        if owner_chat.startswith("tg:"):
            target_chat = owner_chat[3:].strip()
        elif not owner_chat:
            allowed = sorted(self._allowed_chat_ids())
            if allowed:
                target_chat = allowed[0]
        if not target_chat:
            return
        try:
            await self.send_message(target_chat, f"[{title}]\n\n{body}")
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)

    async def handle_update(self, update: dict[str, Any]) -> bool:
        """Process one Telegram Update object (text or voice); returns True if handled."""
        msg = update.get("message")
        if not isinstance(msg, dict):
            return False
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id") or "").strip()
        text = str(msg.get("text") or "").strip()
        voice = msg.get("voice") or msg.get("audio")

        if not chat_id:
            return False

        allowed = self._allowed_chat_ids()
        if chat_id not in allowed:
            if text or (voice and isinstance(voice, dict) and voice.get("file_id")):
                await self.send_message(
                    chat_id,
                    f"Access denied. Add your chat_id `{chat_id}` to Telegram Allowed Chat IDs in Jarvis OS Channels settings.",
                )
            return False

        # Handle inbound voice note / audio (only after owner whitelist check)
        is_voice = False
        if not text and voice and isinstance(voice, dict) and voice.get("file_id"):
            file_id = str(voice["file_id"])
            try:
                file_info = await self._api_call("getFile", {"file_id": file_id})
                file_path = file_info.get("result", {}).get("file_path")
                if file_path:
                    token = self._token()
                    dl_url = f"{TG_API_BASE}/file/bot{token}/{file_path}"
                    async with httpx.AsyncClient(timeout=30.0, transport=self._transport) as client:
                        resp = await client.get(dl_url)
                        if resp.status_code == 200:
                            from app.voice.stt import transcribe_audio_bytes

                            cfg = self._settings.load()
                            try:
                                text = await transcribe_audio_bytes(
                                    resp.content,
                                    suffix=".ogg",
                                    language="ru",
                                    openai_key=str(cfg.get("openai_api_key") or ""),
                                    groq_key=str(cfg.get("groq_api_key") or ""),
                                )
                            except TypeError:
                                text = await transcribe_audio_bytes(resp.content, suffix=".ogg", language="ru")
                            is_voice = True
            except Exception as exc:  # noqa: BLE001
                self._last_error = f"Voice transcription failed: {exc}"

        if not text:
            if is_voice:
                await self.send_message(
                    chat_id,
                    "Не удалось распознать голосовое сообщение. Попробуйте сказать чуть четче или ближе к микрофону.",
                )
            return False

        res = await self._shell.handle_message(channel="telegram", peer_id=chat_id, text=text, run_mode="full")
        reply = str(res.get("reply") or "").strip()
        if reply:
            await self.send_message(chat_id, reply)

            # Send voice message response when user sent voice or if tg_voice_reply is 'always'
            voice_cfg = self._settings.load().get("voice") or {}
            reply_mode = str(voice_cfg.get("tg_voice_reply") or "on_voice")
            if (is_voice and reply_mode != "never") or reply_mode == "always":
                try:
                    from app.voice.tts import synthesize_edge_tts

                    tts_voice = str(voice_cfg.get("tts_voice") or "ru-RU-DmitryNeural")
                    rate = str(voice_cfg.get("tts_rate") or "+5%")
                    audio, _ = await synthesize_edge_tts(reply, voice=tts_voice, rate=rate)
                    if audio:
                        await self.send_voice(chat_id, audio)
                except Exception as exc:  # noqa: BLE001
                    self._last_error = f"Voice reply failed: {exc}"

        return True

    async def poll_once(self) -> int:
        data = await self._api_call(
            "getUpdates",
            {"offset": self._offset, "timeout": 20, "allowed_updates": ["message"]},
            timeout=30.0,
        )
        updates = data.get("result") or []
        handled = 0
        for upd in updates:
            uid = int(upd.get("update_id") or 0)
            if uid >= self._offset:
                self._offset = uid + 1
            if await self.handle_update(upd):
                handled += 1
        return handled

    def start(self) -> bool:
        if not self.is_enabled():
            return False
        if self._poll_task is not None and not self._poll_task.done():
            return True
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        return True

    async def stop(self) -> None:
        self._running = False
        if self._poll_task is not None and not self._poll_task.done():
            self._poll_task.cancel()
            await asyncio.gather(self._poll_task, return_exceptions=True)
        self._poll_task = None

    async def _poll_loop(self) -> None:
        while self._running and self.is_enabled():
            try:
                await self.poll_once()
                self._last_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                await asyncio.sleep(5.0)
