"""API engines with streaming and multi-round tool-calling loops.

Supports:
- OpenAICompatEngine: OpenRouter (including :free), Local Llama (Ollama/LM Studio), OpenAI API.
- GeminiApiEngine: Google Gemini REST SSE API (generativelanguage.googleapis.com).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, AsyncIterator

import httpx

from app.engines.base import EngineEvent, EngineRequest, sanitize_text


def _trim_tool_history(messages: list[dict[str, Any]], budget_chars: int) -> list[dict[str, Any]]:
    """Trim older tool outputs when accumulated tool payloads exceed budget_chars."""
    if budget_chars <= 0:
        return messages
    total_tool_chars = sum(len(str(m.get("content") or "")) for m in messages if m.get("role") == "tool")
    if total_tool_chars <= budget_chars:
        return messages

    trimmed: list[dict[str, Any]] = []
    remaining_to_cut = total_tool_chars - budget_chars
    for msg in messages:
        if msg.get("role") == "tool" and remaining_to_cut > 0:
            raw = str(msg.get("content") or "")
            if len(raw) > 400:
                short = raw[:300] + "\n...[trimmed older tool output]..."
                remaining_to_cut -= len(raw) - len(short)
                trimmed.append({**msg, "content": short})
                continue
        trimmed.append(msg)
    return trimmed


class OpenAICompatEngine:
    """Streaming chat + tool loop for OpenRouter, Local Llama (Ollama), and OpenAI-compatible APIs."""

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        api_key: str = "",
        extra_headers: dict[str, str] | None = None,
        timeout_seconds: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
        num_ctx: int = 16384,
    ) -> None:
        self._provider_id = provider_id
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._extra_headers = extra_headers or {}
        self._timeout = timeout_seconds
        self._transport = transport
        self._num_ctx = num_ctx

    async def stream(self, request: EngineRequest) -> AsyncIterator[EngineEvent]:
        headers = {"Content-Type": "application/json", **self._extra_headers}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        conversation: list[dict[str, Any]] = [{"role": "system", "content": request.system_prompt}]
        for msg in request.messages:
            conversation.append({"role": msg["role"], "content": msg["content"]})

        url = f"{self._base_url}/chat/completions"
        total_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        final_text_parts: list[str] = []

        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            max_rounds = max(1, request.max_tool_rounds)
            for _round in range(max_rounds):
                if request.cancel_event and request.cancel_event.is_set():
                    yield EngineEvent(
                        type="final",
                        content="".join(final_text_parts),
                        provider=self._provider_id,
                        model=request.model,
                        usage=total_usage,
                    )
                    return

                model_name = request.model
                if self._provider_id == "openai":
                    aliases = {
                        "6.1-solo": "gpt-6.1-sol",
                        "6.1-sol": "gpt-6.1-sol",
                        "solo": "gpt-6.1-sol",
                        "sol": "gpt-6.1-sol",
                        "6.1-terra": "gpt-6.1-terra",
                        "terra": "gpt-6.1-terra",
                        "6.1-luna": "gpt-6.1-luna",
                        "luna": "gpt-6.1-luna",
                    }
                    model_name = aliases.get(model_name.lower().strip(), model_name)

                payload: dict[str, Any] = {
                    "model": model_name,
                    "messages": _trim_tool_history(conversation, request.tool_budget_chars),
                    "stream": True,
                }
                if self._provider_id == "local-llama":
                    payload["options"] = {"num_ctx": self._num_ctx}

                effective_tools = request.tools
                if self._provider_id == "local-llama" and effective_tools:
                    # Filter out heavy external remote MCP schemas for local models to keep latency fast
                    # and prevent prompt context overflow.
                    effective_tools = [t for t in effective_tools if not t.get("name", "").startswith("mcp__")]
                elif any(kw in request.model.lower() for kw in ("reasoner", "reasoning", "r1")) and effective_tools:
                    # Pure reasoning models (DeepSeek-R1, Sonar-Reasoning) reject the 'tools' parameter
                    effective_tools = []

                if effective_tools:
                    payload["tools"] = [
                        {
                            "type": "function",
                            "function": {
                                "name": t["name"],
                                "description": t.get("description", ""),
                                "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                            },
                        }
                        for t in effective_tools
                    ]

                round_text_parts: list[str] = []
                tool_calls_acc: dict[int, dict[str, Any]] = {}

                try:
                    async with client.stream("POST", url, headers=headers, json=payload) as resp:
                        if resp.status_code >= 400:
                            body = (await resp.aread()).decode("utf-8", errors="replace")[:500]
                            yield EngineEvent(
                                type="error",
                                content=f"[{self._provider_id}] HTTP {resp.status_code}: {body}",
                                provider=self._provider_id,
                                model=request.model,
                            )
                            return

                        async for line in resp.aiter_lines():
                            if request.cancel_event and request.cancel_event.is_set():
                                yield EngineEvent(
                                    type="final",
                                    content="".join(final_text_parts + round_text_parts),
                                    provider=self._provider_id,
                                    model=request.model,
                                    usage=total_usage,
                                )
                                return

                            if not line.startswith("data:"):
                                continue
                            data_str = line[5:].strip()
                            if not data_str or data_str == "[DONE]":
                                continue
                            try:
                                chunk = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue

                            usage = chunk.get("usage")
                            if isinstance(usage, dict):
                                total_usage["input_tokens"] += int(usage.get("prompt_tokens") or 0)
                                total_usage["output_tokens"] += int(usage.get("completion_tokens") or 0)

                            choices = chunk.get("choices") or []
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}

                            content_delta = delta.get("content")
                            reasoning_delta = delta.get("reasoning_content")
                            chunk_text = content_delta if (content_delta is not None and content_delta != "") else reasoning_delta
                            if isinstance(chunk_text, str) and chunk_text:
                                clean = sanitize_text(chunk_text)
                                if clean:
                                    round_text_parts.append(clean)
                                    yield EngineEvent(
                                        type="text",
                                        content=clean,
                                        provider=self._provider_id,
                                        model=request.model,
                                    )

                            for tc in delta.get("tool_calls") or []:
                                idx = int(tc.get("index", 0))
                                slot = tool_calls_acc.setdefault(
                                    idx,
                                    {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                                )
                                if tc.get("id"):
                                    slot["id"] = str(tc["id"])
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    slot["function"]["name"] += str(fn["name"])
                                if fn.get("arguments"):
                                    slot["function"]["arguments"] += str(fn["arguments"])
                except httpx.ConnectError:
                    yield EngineEvent(
                        type="error",
                        content=f"[{self._provider_id}] Connection failed to {self._base_url}. Is the service running?",
                        provider=self._provider_id,
                        model=request.model,
                    )
                    return
                except httpx.TimeoutException:
                    yield EngineEvent(
                        type="error",
                        content=f"[{self._provider_id}] Request timed out after {self._timeout:.0f}s.",
                        provider=self._provider_id,
                        model=request.model,
                    )
                    return
                except Exception as exc:  # noqa: BLE001
                    yield EngineEvent(
                        type="error",
                        content=f"[{self._provider_id}] Stream error: {exc}",
                        provider=self._provider_id,
                        model=request.model,
                    )
                    return

                round_text = "".join(round_text_parts)
                final_text_parts.extend(round_text_parts)

                ordered_calls = [tool_calls_acc[k] for k in sorted(tool_calls_acc)]
                if not ordered_calls or request.tool_executor is None:
                    yield EngineEvent(
                        type="final",
                        content="".join(final_text_parts),
                        provider=self._provider_id,
                        model=request.model,
                        usage=total_usage,
                    )
                    return

                conversation.append(
                    {
                        "role": "assistant",
                        "content": round_text or None,
                        "tool_calls": ordered_calls,
                    }
                )

                parsed_calls = []
                for idx, tc in enumerate(ordered_calls):
                    call_id = tc.get("id") or f"call_{_round}_{idx}"
                    tool_name = tc["function"]["name"]
                    raw_args = tc["function"]["arguments"] or "{}"
                    try:
                        parsed_args = json.loads(raw_args)
                        if not isinstance(parsed_args, dict):
                            parsed_args = {"value": parsed_args}
                    except json.JSONDecodeError:
                        parsed_args = {"_raw": raw_args}
                    parsed_calls.append((idx, call_id, tool_name, parsed_args))
                    yield EngineEvent(
                        type="tool_call",
                        tool_name=tool_name,
                        tool_args=parsed_args,
                        tool_call_id=call_id,
                        provider=self._provider_id,
                        model=request.model,
                    )

                if len(parsed_calls) == 1:
                    _, call_id, tool_name, parsed_args = parsed_calls[0]
                    try:
                        result_text = await request.tool_executor(tool_name, parsed_args)
                    except Exception as exc:  # noqa: BLE001
                        result_text = f"ERROR: {exc}"
                    yield EngineEvent(
                        type="tool_result",
                        content=str(result_text)[:1000],
                        tool_name=tool_name,
                        tool_call_id=call_id,
                        provider=self._provider_id,
                        model=request.model,
                    )
                    conversation.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": str(result_text),
                        }
                    )
                else:
                    # Hermes-style concurrent tool execution (up to 8 parallel workers)
                    concurrency_sem = asyncio.Semaphore(8)

                    async def _exec_call(c_idx: int, c_id: str, c_name: str, c_args: dict[str, Any]) -> tuple[int, str, str, str]:
                        async with concurrency_sem:
                            try:
                                res = await request.tool_executor(c_name, c_args)
                            except Exception as exc:  # noqa: BLE001
                                res = f"ERROR: {exc}"
                        return c_idx, c_id, c_name, str(res)

                    exec_tasks = [
                        asyncio.create_task(_exec_call(idx, cid, tname, targs))
                        for idx, cid, tname, targs in parsed_calls
                    ]
                    ordered_results: list[tuple[str, str, str] | None] = [None] * len(parsed_calls)

                    for done in asyncio.as_completed(exec_tasks):
                        c_idx, c_id, c_name, res = await done
                        ordered_results[c_idx] = (c_id, c_name, res)
                        yield EngineEvent(
                            type="tool_result",
                            content=res[:1000],
                            tool_name=c_name,
                            tool_call_id=c_id,
                            provider=self._provider_id,
                            model=request.model,
                        )

                    for item in ordered_results:
                        if item is not None:
                            c_id, c_name, res = item
                            conversation.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": c_id,
                                    "content": res,
                                }
                            )

        yield EngineEvent(
            type="final",
            content="".join(final_text_parts),
            provider=self._provider_id,
            model=request.model,
            usage=total_usage,
        )


def _clean_gemini_name(name: str) -> str:
    """Sanitize function name to match Gemini API requirements (alphanumeric/underscore, max 64 chars)."""
    clean = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    if not clean or (not clean[0].isalpha() and clean[0] != "_"):
        clean = f"fn_{clean}"
    return clean[:64]


def _clean_gemini_schema(schema: Any) -> Any:
    """Recursively strip fields unsupported by Gemini API's protobuf Schema definition."""
    if not isinstance(schema, dict):
        return schema

    cleaned: dict[str, Any] = {}

    # 1. Type
    raw_type = schema.get("type")
    if isinstance(raw_type, list):
        non_null = [t for t in raw_type if t != "null"]
        cleaned["type"] = str(non_null[0]).lower() if non_null else "string"
        cleaned["nullable"] = "null" in raw_type
    elif isinstance(raw_type, str):
        cleaned["type"] = raw_type.lower()
    elif "properties" in schema:
        cleaned["type"] = "object"
    elif "items" in schema:
        cleaned["type"] = "array"
    else:
        for alt_key in ("anyOf", "oneOf", "allOf"):
            alts = schema.get(alt_key)
            if isinstance(alts, list) and alts:
                branch = _clean_gemini_schema(alts[0])
                if isinstance(branch, dict):
                    return branch
        cleaned["type"] = "object" if "properties" in schema else "string"

    # 2. Format
    if "format" in schema and isinstance(schema["format"], str):
        cleaned["format"] = schema["format"]

    # 3. Description
    if "description" in schema and isinstance(schema["description"], str):
        cleaned["description"] = schema["description"]

    # 4. Nullable
    if "nullable" in schema:
        cleaned["nullable"] = bool(schema["nullable"])

    # 5. Enum
    if "enum" in schema and isinstance(schema["enum"], list):
        cleaned["enum"] = [str(e) for e in schema["enum"]]

    # 6. Properties (recursive)
    if "properties" in schema and isinstance(schema["properties"], dict):
        props: dict[str, Any] = {}
        for k, v in schema["properties"].items():
            if isinstance(k, str):
                props[k] = _clean_gemini_schema(v)
        cleaned["properties"] = props

    # 7. Items (recursive)
    if "items" in schema:
        if isinstance(schema["items"], dict):
            cleaned["items"] = _clean_gemini_schema(schema["items"])
        elif isinstance(schema["items"], list) and schema["items"]:
            cleaned["items"] = _clean_gemini_schema(schema["items"][0])

    # 8. Required (only properties that actually exist)
    if "required" in schema and isinstance(schema["required"], list):
        props = cleaned.get("properties")
        if isinstance(props, dict):
            req = [str(r) for r in schema["required"] if str(r) in props]
            if req:
                cleaned["required"] = req
        else:
            cleaned["required"] = [str(r) for r in schema["required"]]

    # Ensure object type has properties map
    if cleaned.get("type") == "object" and "properties" not in cleaned:
        cleaned["properties"] = {}

    return cleaned


class GeminiApiEngine:
    """Streaming chat + function-calling loop for Google Gemini API (AI Studio)."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout_seconds: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport

    async def stream(self, request: EngineRequest) -> AsyncIterator[EngineEvent]:
        if not self._api_key:
            yield EngineEvent(
                type="error",
                content="[gemini-api] Gemini API key is not configured in Settings.",
                provider="gemini-api",
                model=request.model,
            )
            return

        name_map: dict[str, str] = {}
        if request.tools:
            for t in request.tools:
                orig = t["name"]
                clean = _clean_gemini_name(orig)
                name_map[clean] = orig

        contents: list[dict[str, Any]] = []
        for msg in request.messages:
            role = "model" if msg["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": str(msg["content"])}]})

        model_name = request.model.removeprefix("models/").strip()
        url = f"{self._base_url}/models/{model_name}:streamGenerateContent"
        params = {"alt": "sse", "key": self._api_key}
        total_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        final_text_parts: list[str] = []

        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            for _round in range(max(1, request.max_tool_rounds)):
                if request.cancel_event and request.cancel_event.is_set():
                    yield EngineEvent(
                        type="final",
                        content="".join(final_text_parts),
                        provider="gemini-api",
                        model=request.model,
                        usage=total_usage,
                    )
                    return

                payload: dict[str, Any] = {
                    "systemInstruction": {"parts": [{"text": request.system_prompt}]},
                    "contents": contents,
                }
                if request.tools:
                    decls = []
                    for t in request.tools:
                        orig = t["name"]
                        clean = _clean_gemini_name(orig)
                        raw_params = t.get("parameters") or {"type": "object", "properties": {}}
                        clean_params = _clean_gemini_schema(raw_params)
                        decls.append(
                            {
                                "name": clean,
                                "description": t.get("description", ""),
                                "parameters": clean_params,
                            }
                        )
                    payload["tools"] = [{"functionDeclarations": decls}]

                round_parts: list[dict[str, Any]] = []
                function_calls: list[dict[str, Any]] = []
                stream_success = False

                models_to_try = [model_name]
                for candidate in (
                    "gemini-3.8-flash",
                    "gemini-2.5-flash",
                    "gemini-2.0-flash",
                    "gemini-3.5-flash-lite",
                    "gemini-1.5-flash",
                    "gemini-3-flash-preview",
                    "gemini-flash-lite-latest",
                ):
                    if candidate not in models_to_try:
                        models_to_try.append(candidate)

                for cur_model in models_to_try:
                    cur_url = f"{self._base_url}/models/{cur_model}:streamGenerateContent"
                    model_attempts = 2 if cur_model != models_to_try[-1] else 3

                    for attempt in range(model_attempts):
                        round_parts.clear()
                        function_calls.clear()
                        try:
                            async with client.stream("POST", cur_url, params=params, json=payload) as resp:
                                if resp.status_code == 404:
                                    if cur_model != models_to_try[-1]:
                                        next_m = models_to_try[models_to_try.index(cur_model) + 1]
                                        yield EngineEvent(
                                            type="status",
                                            content=f"Модель {cur_model} недоступна или устарела (HTTP 404). Автоматически переключаюсь на стабильную {next_m}...",
                                            provider="gemini-api",
                                            model=next_m,
                                        )
                                        break
                                elif resp.status_code in {503, 429, 502, 504}:
                                    if attempt < model_attempts - 1:
                                        wait_s = 2.0 * (attempt + 1)
                                        yield EngineEvent(
                                            type="status",
                                            content=f"Google Gemini высокая нагрузка на {cur_model} (HTTP {resp.status_code}), повтор через {wait_s:.0f}с...",
                                            provider="gemini-api",
                                            model=cur_model,
                                        )
                                        await asyncio.sleep(wait_s)
                                        continue
                                    elif cur_model != models_to_try[-1]:
                                        next_m = models_to_try[models_to_try.index(cur_model) + 1]
                                        yield EngineEvent(
                                            type="status",
                                            content=f"Модель {cur_model} временно перегружена (HTTP {resp.status_code}). Автоматически переключаюсь на стабильную {next_m}...",
                                            provider="gemini-api",
                                            model=next_m,
                                        )
                                        break

                                if resp.status_code >= 400:
                                    body = (await resp.aread()).decode("utf-8", errors="replace")[:500]
                                    yield EngineEvent(
                                        type="error",
                                        content=f"[gemini-api] HTTP {resp.status_code}: {body}",
                                        provider="gemini-api",
                                        model=cur_model,
                                    )
                                    return

                                async for line in resp.aiter_lines():
                                    if request.cancel_event and request.cancel_event.is_set():
                                        yield EngineEvent(
                                            type="final",
                                            content="".join(final_text_parts),
                                            provider="gemini-api",
                                            model=cur_model,
                                            usage=total_usage,
                                        )
                                        return

                                    if not line.startswith("data:"):
                                        continue
                                    data_str = line[5:].strip()
                                    if not data_str:
                                        continue
                                    try:
                                        chunk = json.loads(data_str)
                                    except json.JSONDecodeError:
                                        continue

                                    meta = chunk.get("usageMetadata")
                                    if isinstance(meta, dict):
                                        total_usage["input_tokens"] = int(meta.get("promptTokenCount") or 0)
                                        total_usage["output_tokens"] = int(meta.get("candidatesTokenCount") or 0)

                                    for cand in chunk.get("candidates") or []:
                                        content = cand.get("content") or {}
                                        for part in content.get("parts") or []:
                                            if "text" in part and isinstance(part["text"], str):
                                                clean = sanitize_text(part["text"])
                                                if clean:
                                                    final_text_parts.append(clean)
                                                    clean_part = dict(part)
                                                    clean_part["text"] = clean
                                                    round_parts.append(clean_part)
                                                    yield EngineEvent(
                                                        type="text",
                                                        content=clean,
                                                        provider="gemini-api",
                                                        model=cur_model,
                                                    )
                                            elif "functionCall" in part:
                                                fc = part["functionCall"]
                                                function_calls.append(fc)
                                                # Preserve complete part including thoughtSignature and id
                                                round_parts.append(dict(part))
                                            elif "thoughtSignature" in part or "thought" in part:
                                                round_parts.append(dict(part))
                                stream_success = True
                                break
                        except (httpx.ConnectError, httpx.TimeoutException) as exc:
                            if attempt < model_attempts - 1:
                                await asyncio.sleep(1.0 * (attempt + 1))
                                continue
                            if cur_model != models_to_try[-1]:
                                next_m = models_to_try[models_to_try.index(cur_model) + 1]
                                yield EngineEvent(
                                    type="status",
                                    content=f"Таймаут соединения с {cur_model} ({exc}). Переключаюсь на {next_m}...",
                                    provider="gemini-api",
                                    model=next_m,
                                )
                                break
                            yield EngineEvent(
                                type="error",
                                content=f"[gemini-api] Connection error: {exc}",
                                provider="gemini-api",
                                model=cur_model,
                            )
                            return
                        except Exception as exc:  # noqa: BLE001
                            yield EngineEvent(
                                type="error",
                                content=f"[gemini-api] Stream error: {exc}",
                                provider="gemini-api",
                                model=cur_model,
                            )
                            return
                    if stream_success:
                        break

                if not stream_success:
                    return

                if not function_calls or request.tool_executor is None:
                    yield EngineEvent(
                        type="final",
                        content="".join(final_text_parts),
                        provider="gemini-api",
                        model=request.model,
                        usage=total_usage,
                    )
                    return

                contents.append({"role": "model", "parts": round_parts})
                parsed_gemini_calls = []
                for idx, fc in enumerate(function_calls):
                    call_name = str(fc.get("name") or "")
                    orig_name = name_map.get(call_name, call_name)
                    args = fc.get("args") if isinstance(fc.get("args"), dict) else {}
                    call_id = f"gemini_{_round}_{idx}"
                    parsed_gemini_calls.append((idx, call_name, orig_name, args, call_id, fc.get("id")))
                    yield EngineEvent(
                        type="tool_call",
                        tool_name=orig_name,
                        tool_args=args,
                        tool_call_id=call_id,
                        provider="gemini-api",
                        model=request.model,
                    )

                if len(parsed_gemini_calls) == 1:
                    _, call_name, orig_name, args, call_id, fc_id = parsed_gemini_calls[0]
                    try:
                        res = await request.tool_executor(orig_name, args)
                    except Exception as exc:  # noqa: BLE001
                        res = f"ERROR: {exc}"
                    yield EngineEvent(
                        type="tool_result",
                        content=str(res)[:1000],
                        tool_name=orig_name,
                        tool_call_id=call_id,
                        provider="gemini-api",
                        model=request.model,
                    )
                    resp_data: dict[str, Any] = {"name": call_name, "response": {"result": str(res)}}
                    if fc_id:
                        resp_data["id"] = fc_id
                    contents.append({"role": "user", "parts": [{"functionResponse": resp_data}]})
                else:
                    # Hermes-style concurrent execution for Gemini function calls
                    gemini_sem = asyncio.Semaphore(8)

                    async def _exec_gemini_call(g_idx: int, g_call_name: str, g_orig: str, g_args: dict[str, Any], g_cid: str, g_fc_id: Any) -> tuple[int, str, str, str, Any]:
                        async with gemini_sem:
                            try:
                                g_res = await request.tool_executor(g_orig, g_args)
                            except Exception as exc:  # noqa: BLE001
                                g_res = f"ERROR: {exc}"
                        return g_idx, g_call_name, g_orig, str(g_res), g_cid, g_fc_id

                    gemini_tasks = [
                        asyncio.create_task(_exec_gemini_call(idx, cname, oname, gargs, cid, fcid))
                        for idx, cname, oname, gargs, cid, fcid in parsed_gemini_calls
                    ]
                    ordered_gemini_res: list[tuple[str, str, Any] | None] = [None] * len(parsed_gemini_calls)

                    for done in asyncio.as_completed(gemini_tasks):
                        g_idx, g_call_name, g_orig, g_res, g_cid, g_fc_id = await done
                        ordered_gemini_res[g_idx] = (g_call_name, g_res, g_fc_id)
                        yield EngineEvent(
                            type="tool_result",
                            content=g_res[:1000],
                            tool_name=g_orig,
                            tool_call_id=g_cid,
                            provider="gemini-api",
                            model=request.model,
                        )

                    response_parts: list[dict[str, Any]] = []
                    for item in ordered_gemini_res:
                        if item is not None:
                            g_call_name, g_res, g_fc_id = item
                            resp_data = {"name": g_call_name, "response": {"result": g_res}}
                            if g_fc_id:
                                resp_data["id"] = g_fc_id
                            response_parts.append({"functionResponse": resp_data})
                    contents.append({"role": "user", "parts": response_parts})

        yield EngineEvent(
            type="final",
            content="".join(final_text_parts),
            provider="gemini-api",
            model=request.model,
            usage=total_usage,
        )
