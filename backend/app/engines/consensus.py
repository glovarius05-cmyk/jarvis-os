"""Multi-Model Consensus & Debate Engine (Multi-AI Orchestration).

Inspired by Proxima's `smart_query`, `crew`, and `debate` orchestration:
Queries multiple independent frontier/local models concurrently, evaluates their
points of convergence and disagreement, and synthesizes a high-confidence consensus
or structured debate verdict.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Literal

from app.engines.base import EngineEvent, EngineProtocol, EngineRequest, sanitize_text


async def _collect_stream(
    engine: EngineProtocol,
    request: EngineRequest,
) -> tuple[str, list[dict[str, Any]], dict[str, int], str]:
    """Execute an engine stream and collect accumulated text, tool events, usage, and errors."""
    text_parts: list[str] = []
    tool_events: list[dict[str, Any]] = []
    usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
    error_msg = ""

    try:
        async for ev in engine.stream(request):
            if ev.type == "text":
                text_parts.append(ev.content)
            elif ev.type in {"tool_call", "tool_result"}:
                tool_events.append(ev.to_dict())
            elif ev.type == "error":
                error_msg = ev.content
                break
            elif ev.type == "final":
                if ev.content and not text_parts:
                    text_parts.append(ev.content)
                if ev.usage:
                    usage["input_tokens"] += int(ev.usage.get("input_tokens") or 0)
                    usage["output_tokens"] += int(ev.usage.get("output_tokens") or 0)
    except Exception as exc:  # noqa: BLE001
        error_msg = str(exc)

    return "".join(text_parts).strip(), tool_events, usage, error_msg


class ConsensusEngine:
    """Orchestrates multi-model parallel queries, debate rounds, and arbiter synthesis."""

    def __init__(
        self,
        *,
        primary_provider: str,
        primary_model: str,
        secondary_provider: str,
        secondary_model: str,
        router: Any,
        mode: Literal["consensus", "debate"] = "consensus",
        engine_override: EngineProtocol | None = None,
    ) -> None:
        self._primary_provider = primary_provider
        self._primary_model = primary_model
        self._secondary_provider = secondary_provider
        self._secondary_model = secondary_model
        self._router = router
        self._mode = mode
        self._engine_override = engine_override

    async def stream(self, request: EngineRequest) -> AsyncIterator[EngineEvent]:
        mode_label = "Консенсус" if self._mode == "consensus" else "Дебаты"
        p1, m1 = self._primary_provider, self._primary_model
        p2, m2 = self._secondary_provider, self._secondary_model

        yield EngineEvent(
            type="status",
            content=f"🚀 Multi-AI {mode_label}: параллельный опрос [{p1} / {m1}] и [{p2} / {m2}]...",
            provider="consensus",
            model=f"{m1}+{m2}",
        )

        user_prompt = ""
        for m in reversed(request.messages):
            if m.get("role") == "user":
                user_prompt = str(m.get("content") or "")
                break

        if not user_prompt:
            yield EngineEvent(
                type="error",
                content="[consensus] Пустой запрос пользователя.",
                provider="consensus",
            )
            return

        total_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}

        try:
            engine_1 = self._engine_override or self._router.build_engine(
                p1, brain=request.brain, run_mode="auto"
            )
            engine_2 = self._engine_override or self._router.build_engine(
                p2, brain=request.brain, run_mode="auto"
            )
        except Exception as exc:  # noqa: BLE001
            yield EngineEvent(
                type="error",
                content=f"[consensus] Ошибка инициализации движков: {exc}",
                provider="consensus",
            )
            return

        # Handle Cancellation
        if request.cancel_event and request.cancel_event.is_set():
            return

        if self._mode == "consensus":
            async for ev in self._run_consensus(
                request=request,
                user_prompt=user_prompt,
                engine_1=engine_1,
                engine_2=engine_2,
                p1=p1,
                m1=m1,
                p2=p2,
                m2=m2,
                total_usage=total_usage,
            ):
                yield ev
        else:
            async for ev in self._run_debate(
                request=request,
                user_prompt=user_prompt,
                engine_1=engine_1,
                engine_2=engine_2,
                p1=p1,
                m1=m1,
                p2=p2,
                m2=m2,
                total_usage=total_usage,
            ):
                yield ev

    async def _run_consensus(
        self,
        *,
        request: EngineRequest,
        user_prompt: str,
        engine_1: EngineProtocol,
        engine_2: EngineProtocol,
        p1: str,
        m1: str,
        p2: str,
        m2: str,
        total_usage: dict[str, int],
    ) -> AsyncIterator[EngineEvent]:
        # Step 1: Run both models in parallel
        req_1 = EngineRequest(
            session_id=f"{request.session_id}_c1",
            brain=request.brain,
            system_prompt=request.system_prompt,
            messages=request.messages,
            provider=p1,
            model=m1,
            tools=request.tools,
            tool_executor=request.tool_executor,
            cancel_event=request.cancel_event,
        )
        req_2 = EngineRequest(
            session_id=f"{request.session_id}_c2",
            brain=request.brain,
            system_prompt=request.system_prompt,
            messages=request.messages,
            provider=p2,
            model=m2,
            tools=request.tools,
            tool_executor=request.tool_executor,
            cancel_event=request.cancel_event,
        )

        task_1 = asyncio.create_task(_collect_stream(engine_1, req_1))
        task_2 = asyncio.create_task(_collect_stream(engine_2, req_2))

        (resp_1, tools_1, usage_1, err_1), (resp_2, tools_2, usage_2, err_2) = await asyncio.gather(task_1, task_2)

        total_usage["input_tokens"] += usage_1.get("input_tokens", 0) + usage_2.get("input_tokens", 0)
        total_usage["output_tokens"] += usage_1.get("output_tokens", 0) + usage_2.get("output_tokens", 0)

        # Propagate tool events if any model called tools
        for te in tools_1 + tools_2:
            yield EngineEvent(
                type="tool_call" if te.get("type") == "tool_call" else "tool_result",
                content=te.get("content", ""),
                tool_name=te.get("tool_name", ""),
                tool_args=te.get("tool_args", {}),
                tool_call_id=te.get("tool_call_id", ""),
                provider=te.get("provider", p1),
                model=te.get("model", m1),
            )

        if err_1 and not resp_1 and resp_2:
            yield EngineEvent(
                type="status",
                content=f"⚠️ {p1} вернул ошибку ({err_1}), используется ответ {p2}.",
                provider="consensus",
            )
            yield EngineEvent(type="text", content=resp_2, provider=p2, model=m2)
            yield EngineEvent(type="final", content=resp_2, provider=p2, model=m2, usage=total_usage)
            return

        if err_2 and not resp_2 and resp_1:
            yield EngineEvent(
                type="status",
                content=f"⚠️ {p2} вернул ошибку ({err_2}), используется ответ {p1}.",
                provider="consensus",
            )
            yield EngineEvent(type="text", content=resp_1, provider=p1, model=m1)
            yield EngineEvent(type="final", content=resp_1, provider=p1, model=m1, usage=total_usage)
            return

        if not resp_1 and not resp_2:
            yield EngineEvent(
                type="error",
                content=f"[consensus] Обе модели завершились с ошибкой: {err_1 or 'нет ответа'} | {err_2 or 'нет ответа'}",
                provider="consensus",
            )
            return

        # Step 2: Synthesis & Arbiter Evaluation
        yield EngineEvent(
            type="status",
            content="🔍 Ответы обеих моделей получены. Анализ расхождений и синтез консенсуса...",
            provider="consensus",
            model=f"{m1}+{m2}",
        )

        arbiter_system = (
            "Ты — Главный AI-Арбитр Джарвиса. Твоя задача — проанализировать ответы двух независимых "
            "моделей искусственного интеллекта на запрос пользователя, проверить их на точность и "
            "синтезировать наилучший, выверенный ответ без противоречий.\n"
            "Всегда пиши на чистом русском языке. Не используй длинное тире (—), используй обычный дефис (-)."
        )

        arbiter_prompt = (
            f"Запрос пользователя:\n\"{user_prompt}\"\n\n"
            f"--- ОТВЕТ МОДЕЛИ 1 ({p1} / {m1}) ---\n{resp_1}\n\n"
            f"--- ОТВЕТ МОДЕЛИ 2 ({p2} / {m2}) ---\n{resp_2}\n\n"
            "Инструкция арбитра:\n"
            "1. Сформируй исчерпывающий, согласованный и точный ответ пользователю.\n"
            "2. В конце ответа добавь блок:\n"
            "### 🤝 Матрица консенсуса моделей\n"
            "- **Степень согласия:** [Высокая (100%) | Средняя | Расхождение]\n"
            f"- **Особенности {m1}:** [Что выделила модель 1]\n"
            f"- **Особенности {m2}:** [Что добавила или исправила модель 2]\n"
        )

        arbiter_req = EngineRequest(
            session_id=f"{request.session_id}_arbiter",
            brain=request.brain,
            system_prompt=arbiter_system,
            messages=[{"role": "user", "content": arbiter_prompt}],
            provider=p1,
            model=m1,
            cancel_event=request.cancel_event,
        )

        final_parts: list[str] = []
        async for ev in engine_1.stream(arbiter_req):
            if ev.type == "text":
                final_parts.append(ev.content)
                yield EngineEvent(
                    type="text",
                    content=ev.content,
                    provider="consensus",
                    model=f"{m1}+{m2}",
                )
            elif ev.type == "final":
                if ev.usage:
                    total_usage["input_tokens"] += int(ev.usage.get("input_tokens") or 0)
                    total_usage["output_tokens"] += int(ev.usage.get("output_tokens") or 0)

        full_final = "".join(final_parts).strip()
        yield EngineEvent(
            type="final",
            content=full_final,
            provider="consensus",
            model=f"{m1}+{m2}",
            usage=total_usage,
        )

    async def _run_debate(
        self,
        *,
        request: EngineRequest,
        user_prompt: str,
        engine_1: EngineProtocol,
        engine_2: EngineProtocol,
        p1: str,
        m1: str,
        p2: str,
        m2: str,
        total_usage: dict[str, int],
    ) -> AsyncIterator[EngineEvent]:
        # Round 1: Model 1 proposes initial thesis
        yield EngineEvent(
            type="status",
            content=f"⚔️ Раунд 1: {p1} ({m1}) формирует базовую позицию / решение...",
            provider="debate",
        )
        req_r1 = EngineRequest(
            session_id=f"{request.session_id}_d1",
            brain=request.brain,
            system_prompt="Предложи структурированное решение задачи пользователя.",
            messages=[{"role": "user", "content": user_prompt}],
            provider=p1,
            model=m1,
            tools=request.tools,
            tool_executor=request.tool_executor,
            cancel_event=request.cancel_event,
        )
        r1_text, r1_tools, u1, err1 = await _collect_stream(engine_1, req_r1)
        total_usage["input_tokens"] += u1.get("input_tokens", 0)
        total_usage["output_tokens"] += u1.get("output_tokens", 0)

        for te in r1_tools:
            yield EngineEvent(
                type="tool_call" if te.get("type") == "tool_call" else "tool_result",
                content=te.get("content", ""),
                tool_name=te.get("tool_name", ""),
                tool_args=te.get("tool_args", {}),
                tool_call_id=te.get("tool_call_id", ""),
                provider=p1,
                model=m1,
            )

        if not r1_text:
            yield EngineEvent(type="error", content=f"Ошибка в раунде 1 ({p1}): {err1}", provider="debate")
            return

        # Round 2: Model 2 critiques and challenges
        yield EngineEvent(
            type="status",
            content=f"⚔️ Раунд 2: {p2} ({m2}) критикует решение, ищет скрытые баги и альтернативы...",
            provider="debate",
        )
        critique_prompt = (
            f"Пользователь запросил: \"{user_prompt}\"\n\n"
            f"Модель 1 ({m1}) предложила решение:\n\"\"\"{r1_text}\"\"\"\n\n"
            "Выступи в роли строгого код-ревьюера / оппонента. Проанализируй это решение:\n"
            "1. Какие здесь скрытые баги, краевые случаи или уязвимости?\n"
            "2. Какие допущения неверны?\n"
            "3. Предложи альтернативное, более надежное или оптимальное решение."
        )
        req_r2 = EngineRequest(
            session_id=f"{request.session_id}_d2",
            brain=request.brain,
            system_prompt="Ты строгий технический оппонент и ревьюер.",
            messages=[{"role": "user", "content": critique_prompt}],
            provider=p2,
            model=m2,
            cancel_event=request.cancel_event,
        )
        r2_text, _, u2, _ = await _collect_stream(engine_2, req_r2)
        total_usage["input_tokens"] += u2.get("input_tokens", 0)
        total_usage["output_tokens"] += u2.get("output_tokens", 0)

        # Round 3: Arbiter Verdict & Action Plan
        yield EngineEvent(
            type="status",
            content="⚖️ Раунд 3: Арбитр подводит итоги дебатов и выносит финальный вердикт...",
            provider="debate",
        )

        verdict_system = (
            "Ты — Председатель коллегии AI. Твоя задача — объективно оценить дебаты двух моделей "
            "и сформулировать победную рекомендацию и окончательное решение задачи.\n"
            "Не используй длинное тире (—), используй обычный дефис (-)."
        )
        verdict_prompt = (
            f"Исходный вопрос пользователя: \"{user_prompt}\"\n\n"
            f"Позиция Модели 1 ({m1}):\n\"\"\"{r1_text}\"\"\"\n\n"
            f"Критика и альтернативы Модели 2 ({m2}):\n\"\"\"{r2_text}\"\"\"\n\n"
            "Структура твоего вердикта:\n"
            "## 🏆 Итоговое решение (Вердикт Арбитра)\n"
            "[Синтезированное решение задачи с учетом всех валидных замечаний]\n\n"
            "## ⚔️ Результаты дебатов\n"
            f"- **Оценка позиции {m1}:** [Сильные стороны и слабые места]\n"
            f"- **Оценка критики {m2}:** [Какие замечания были приняты, а какие отклонены]\n"
            "- **Вывод:** [Главный совет пользователю]\n"
        )

        arbiter_req = EngineRequest(
            session_id=f"{request.session_id}_verdict",
            brain=request.brain,
            system_prompt=verdict_system,
            messages=[{"role": "user", "content": verdict_prompt}],
            provider=p1,
            model=m1,
            cancel_event=request.cancel_event,
        )

        final_parts: list[str] = []
        async for ev in engine_1.stream(arbiter_req):
            if ev.type == "text":
                final_parts.append(ev.content)
                yield EngineEvent(
                    type="text",
                    content=ev.content,
                    provider="debate",
                    model=f"{m1} vs {m2}",
                )
            elif ev.type == "final":
                if ev.usage:
                    total_usage["input_tokens"] += int(ev.usage.get("input_tokens") or 0)
                    total_usage["output_tokens"] += int(ev.usage.get("output_tokens") or 0)

        full_final = "".join(final_parts).strip()
        yield EngineEvent(
            type="final",
            content=full_final,
            provider="debate",
            model=f"{m1} vs {m2}",
            usage=total_usage,
        )
