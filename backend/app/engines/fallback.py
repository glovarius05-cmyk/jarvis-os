"""Resilient automatic provider fallback wrapper (LiteLLM-style cascade).

Why this exists: Background Kanban tasks, scheduled loops, and interactive chats
should not fail when a primary LLM provider returns 429 rate-limit, 503 overload,
timeout, or network errors. FallbackEngine retries seamlessly on the next configured
provider ONLY if no partial text or tool call has been emitted yet.
"""

from __future__ import annotations

import re
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any, AsyncIterator

from app.engines.base import EngineEvent, EngineProtocol, EngineRequest

if TYPE_CHECKING:
    from app.engines.router import EngineRouter

_TRANSIENT_ERR_RE = re.compile(
    r"(429|500|502|503|504|rate.?limit|quota|overloaded|unavailable|timeout|timed out|"
    r"connection|connecterror|readtimeout|service unavailable|bad gateway|temporarily)",
    re.IGNORECASE,
)


def is_retryable_provider_error(message: str) -> bool:
    """Return True if an error message looks like a transient provider failure worth falling back on."""
    return bool(_TRANSIENT_ERR_RE.search(message or ""))


class FallbackEngine:
    """Wraps a primary engine and automatically cascades through fallback providers on transient errors."""

    def __init__(
        self,
        *,
        primary_engine: EngineProtocol,
        primary_provider: str,
        primary_model: str,
        fallback_targets: list[tuple[str, str]],
        router: "EngineRouter",
        brain: str = "",
        run_mode: str = "full",
        telemetry: Any = None,
    ) -> None:
        self._primary_engine = primary_engine
        self._primary_provider = primary_provider
        self._primary_model = primary_model
        self._fallback_targets = list(fallback_targets)
        self._router = router
        self._brain = brain
        self._run_mode = run_mode
        self._telemetry = telemetry

    async def stream(self, req: EngineRequest) -> AsyncIterator[EngineEvent]:
        attempts: list[tuple[str, str, EngineProtocol | None]] = [
            (self._primary_provider, self._primary_model, self._primary_engine),
            *[(p, m, None) for p, m in self._fallback_targets],
        ]

        last_error = ""
        for idx, (prov, mdl, prebuilt) in enumerate(attempts):
            is_fallback = idx > 0
            if req.cancel_event is not None and req.cancel_event.is_set():
                return

            try:
                engine = prebuilt or self._router.build_engine(
                    prov,
                    brain=self._brain,
                    run_mode=self._run_mode,
                    model=mdl,
                    allow_fallback=False,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                if idx + 1 < len(attempts):
                    continue
                yield EngineEvent(type="error", content=last_error)
                return

            if is_fallback and self._telemetry is not None:
                self._telemetry.record_span(
                    name=f"fallback:{self._primary_provider}->{prov}",
                    kind="fallback",
                    status="ok",
                    duration_ms=0.0,
                    attributes={
                        "from_provider": self._primary_provider,
                        "from_model": self._primary_model,
                        "to_provider": prov,
                        "to_model": mdl,
                        "reason": last_error[:300],
                    },
                )

            attempt_req = replace(req, provider=prov, model=mdl)
            emitted_any = False
            failed_before_emit = False
            attempt_error = ""
            t0 = time.perf_counter()

            try:
                async for ev in engine.stream(attempt_req):
                    if ev.type == "error":
                        attempt_error = ev.content
                        if not emitted_any and idx + 1 < len(attempts) and is_retryable_provider_error(ev.content):
                            failed_before_emit = True
                            break
                        yield ev
                        return
                    if ev.type in {"text", "tool_call"}:
                        emitted_any = True
                    if ev.type == "final":
                        usage = dict(ev.usage or {})
                        usage.setdefault("provider", prov)
                        usage.setdefault("model", mdl)
                        if is_fallback:
                            usage["fallback_from"] = f"{self._primary_provider}/{self._primary_model}"
                        yield EngineEvent(type="final", content=ev.content, usage=usage)
                        return
                    yield ev
            except Exception as exc:  # noqa: BLE001
                attempt_error = str(exc)
                if not emitted_any and idx + 1 < len(attempts) and is_retryable_provider_error(attempt_error):
                    failed_before_emit = True
                else:
                    yield EngineEvent(type="error", content=attempt_error)
                    return

            if failed_before_emit:
                last_error = attempt_error
                if self._telemetry is not None:
                    self._telemetry.record_span(
                        name=f"fallback_trigger:{prov}/{mdl}",
                        kind="fallback",
                        status="error",
                        duration_ms=(time.perf_counter() - t0) * 1000.0,
                        attributes={"provider": prov, "model": mdl},
                        error=attempt_error[:300],
                    )
                continue
            return

        if last_error:
            yield EngineEvent(type="error", content=last_error)
