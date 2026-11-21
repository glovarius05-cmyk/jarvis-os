"""Hybrid engine router: OpenRouter, Local Llama (Ollama), Gemini API, Codex CLI, Antigravity CLI.

Resolves which provider/model executes a chat turn or background job, and discovers
available local Llama models from Ollama / OpenAI-compatible localhost servers.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from app.brain.vault import VaultManager
from app.config import SettingsStore
from app.engines.api_engine import GeminiApiEngine, OpenAICompatEngine
from app.engines.base import EngineProtocol
from app.engines.cli_adapters import AntigravityCliEngine, CodexCliEngine, find_cli_binary

if TYPE_CHECKING:
    from app.hub.gateway import MCPHubGateway


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    kind: str  # "api" | "local" | "cli"
    default_model: str
    suggested_models: tuple[str, ...]
    key_field: str = ""
    cli_binary: str = ""
    phase2_cli: bool = False


PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="openrouter",
        label="OpenRouter (API + Free models)",
        kind="api",
        default_model="baidu/ernie-5.5-flash-preview:free",
        suggested_models=(
            "baidu/ernie-5.5-flash-preview:free",
            "deepseek/deepseek-r1:free",
            "deepseek/deepseek-chat",
            "deepseek/deepseek-r1",
            "qwen/qwen-2.5-72b-instruct",
            "qwen/qwen-2.5-coder-32b-instruct",
            "qwen/qwen-2.5-7b-instruct:free",
            "perplexity/sonar",
            "perplexity/sonar-reasoning",
            "moonshotai/moonshot-v1-32k",
            "zhipu/glm-4-9b-chat",
            "minimax/minimax-01",
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "nvidia/nemotron-3.5-lightning:free",
            "google/gemma-4-31b-it:free",
            "google/gemma-4-26b-a4b-it:free",
            "cohere/north-mini-code:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "anthropic/claude-3.7-sonnet",
            "openrouter/auto",
        ),
        key_field="openrouter_api_key",
    ),
    ProviderSpec(
        id="local-llama",
        label="Local Llama (Ollama / LM Studio)",
        kind="local",
        default_model="hf.co/gguf/Qwen3.5-2B-GGUF:Q4_K_M",
        suggested_models=(
            "hf.co/gguf/Qwen3.5-2B-GGUF:Q4_K_M",
            "qwen2.5:7b",
            "qwen2.5:14b",
            "qwen2.5-coder:7b",
            "qwen2.5-coder:14b",
            "deepseek-r1:7b",
            "deepseek-r1:8b",
            "deepseek-r1:14b",
            "llama3.1:8b",
            "llama3.2:3b",
        ),
    ),
    ProviderSpec(
        id="gemini-api",
        label="Google Gemini (API)",
        kind="api",
        default_model="gemini-3-flash-preview",
        suggested_models=(
            "gemini-3-flash-preview",
            "gemini-3.8-flash",
            "gemini-3.5-flash-lite",
            "gemini-flash-lite-latest",
        ),
        key_field="gemini_api_key",
    ),
    ProviderSpec(
        id="perplexity",
        label="Perplexity AI (Sonar / Web Search)",
        kind="api",
        default_model="sonar",
        suggested_models=(
            "sonar",
            "sonar-pro",
            "sonar-reasoning",
            "sonar-reasoning-pro",
        ),
        key_field="perplexity_api_key",
    ),
    ProviderSpec(
        id="deepseek",
        label="DeepSeek (V3 / R1 Reasoner)",
        kind="api",
        default_model="deepseek-chat",
        suggested_models=(
            "deepseek-chat",
            "deepseek-reasoner",
        ),
        key_field="deepseek_api_key",
    ),
    ProviderSpec(
        id="moonshot",
        label="Kimi / Moonshot AI",
        kind="api",
        default_model="moonshot-v1-auto",
        suggested_models=(
            "moonshot-v1-auto",
            "moonshot-v1-32k",
            "moonshot-v1-128k",
            "moonshot-v1-8k",
            "kimi-latest",
        ),
        key_field="moonshot_api_key",
    ),
    ProviderSpec(
        id="dashscope",
        label="Qwen / Alibaba DashScope",
        kind="api",
        default_model="qwen-plus",
        suggested_models=(
            "qwen-plus",
            "qwen-max",
            "qwen-turbo",
            "qwen2.5-72b-instruct",
            "qwen2.5-coder-32b-instruct",
            "qwen2.5-coder-7b-instruct",
        ),
        key_field="dashscope_api_key",
    ),
    ProviderSpec(
        id="codex-cli",
        label="ChatGPT via Codex CLI (Subscription)",
        kind="cli",
        default_model="codex-default",
        suggested_models=("codex-default",),
        cli_binary="codex",
        phase2_cli=False,
    ),
    ProviderSpec(
        id="antigravity-cli",
        label="Gemini via Antigravity CLI (Subscription)",
        kind="cli",
        default_model="gemini-3.8-flash-high",
        suggested_models=(
            "gemini-3.8-flash-high",
            "gemini-3.8-flash-medium",
            "gemini-3.8-flash-low",
            "claude-sonnet-4-6",
            "claude-opus-4-6-thinking",
            "gpt-oss-120b-medium",
            "agy-default",
        ),
        cli_binary="agy",
        phase2_cli=False,
    ),
)

PROVIDER_MAP: dict[str, ProviderSpec] = {p.id: p for p in PROVIDER_SPECS}


class EngineRouter:
    """Selects and instantiates the right engine for a chat session or background task."""

    def __init__(
        self,
        settings: SettingsStore,
        vaults: VaultManager | None = None,
        hub: "MCPHubGateway | None" = None,
    ) -> None:
        self._settings = settings
        self._vaults = vaults
        self._hub = hub

    def resolve_target(
        self,
        *,
        override_provider: str = "",
        override_model: str = "",
        pinned_provider: str = "",
        pinned_model: str = "",
        role: str = "main",
    ) -> tuple[str, str]:
        cfg = self._settings.load()
        model_cfg = cfg.get("model") or {}

        if role == "aux":
            default_provider = str(model_cfg.get("aux_provider") or "local-llama")
            default_model = str(model_cfg.get("aux_model") or "llama3.1:8b")
        else:
            default_provider = str(model_cfg.get("main_provider") or "openrouter")
            default_model = str(model_cfg.get("main_model") or "openrouter/auto")

        provider = (override_provider or pinned_provider or default_provider).strip()
        if provider not in PROVIDER_MAP:
            provider = "openrouter"

        if override_model:
            model = override_model.strip()
        elif override_provider and override_provider != default_provider:
            model = self._default_model_for(provider, cfg)
        elif pinned_model:
            model = pinned_model.strip()
        elif pinned_provider and pinned_provider != default_provider:
            model = self._default_model_for(provider, cfg)
        else:
            model = default_model or self._default_model_for(provider, cfg)

        return provider, model

    @property
    def settings(self) -> SettingsStore:
        return self._settings

    def resolve_secondary_target(self, primary_provider: str, cfg: dict[str, Any] | None = None) -> tuple[str, str]:
        c = cfg or self._settings.load()
        if primary_provider == "gemini-api":
            if c.get("openrouter_api_key"):
                return "openrouter", self._default_model_for("openrouter", c)
            if c.get("local_llama"):
                return "local-llama", self._default_model_for("local-llama", c)
            return "gemini-api", "gemini-flash-lite-latest"
        elif primary_provider == "openrouter":
            if c.get("gemini_api_key"):
                return "gemini-api", self._default_model_for("gemini-api", c)
            if c.get("local_llama"):
                return "local-llama", self._default_model_for("local-llama", c)
            return "openrouter", "google/gemma-4-31b-it:free"
        elif primary_provider == "local-llama":
            if c.get("gemini_api_key"):
                return "gemini-api", self._default_model_for("gemini-api", c)
            if c.get("openrouter_api_key"):
                return "openrouter", self._default_model_for("openrouter", c)
            return "local-llama", self._default_model_for("local-llama", c)
        else:
            if c.get("gemini_api_key"):
                return "gemini-api", self._default_model_for("gemini-api", c)
            return "openrouter", self._default_model_for("openrouter", c)

    def _default_model_for(self, provider: str, cfg: dict[str, Any]) -> str:
        if provider == "local-llama":
            return str((cfg.get("local_llama") or {}).get("model") or "llama3.1:8b")
        return PROVIDER_MAP[provider].default_model

    def build_engine(
        self,
        provider: str,
        *,
        brain: str = "",
        run_mode: str = "full",
        model: str = "",
    ) -> EngineProtocol:
        if getattr(self, "_engine_override", None) is not None:
            return self._engine_override
        cfg = self._settings.load()

        if run_mode in {"consensus", "debate"}:
            from app.engines.consensus import ConsensusEngine
            sec_p, sec_m = self.resolve_secondary_target(provider, cfg)
            prim_m = model or self._default_model_for(provider, cfg)
            return ConsensusEngine(
                primary_provider=provider,
                primary_model=prim_m,
                secondary_provider=sec_p,
                secondary_model=sec_m,
                router=self,
                mode=run_mode,
            )

        if provider == "openrouter":
            key = str(cfg.get("openrouter_api_key") or "").strip()
            if not key:
                raise ValueError("OpenRouter API key is not configured yet. Add it on the Models page.")
            return OpenAICompatEngine(
                provider_id="openrouter",
                base_url="https://openrouter.ai/api/v1",
                api_key=key,
                extra_headers={
                    "HTTP-Referer": "http://localhost:7777",
                    "X-Title": "Jarvis OS",
                },
            )

        if provider == "local-llama":
            local_cfg = cfg.get("local_llama") or {}
            base_url = str(local_cfg.get("base_url") or "http://127.0.0.1:11434/v1").strip()
            num_ctx = int(local_cfg.get("num_ctx") or 16384)
            return OpenAICompatEngine(
                provider_id="local-llama",
                base_url=base_url,
                api_key="local-llama",
                num_ctx=num_ctx,
            )

        if provider == "gemini-api":
            key = str(cfg.get("gemini_api_key") or "").strip()
            if not key:
                raise ValueError("Google Gemini API key is not configured yet. Add it on the Models page.")
            return GeminiApiEngine(api_key=key)

        if provider == "perplexity":
            key = str(cfg.get("perplexity_api_key") or "").strip()
            if not key:
                raise ValueError("Perplexity API key is not configured yet. Add it on the Models page.")
            return OpenAICompatEngine(
                provider_id="perplexity",
                base_url="https://api.perplexity.ai",
                api_key=key,
            )

        if provider == "deepseek":
            key = str(cfg.get("deepseek_api_key") or "").strip()
            if not key:
                raise ValueError("DeepSeek API key is not configured yet. Add it on the Models page.")
            return OpenAICompatEngine(
                provider_id="deepseek",
                base_url="https://api.deepseek.com",
                api_key=key,
            )

        if provider == "moonshot":
            key = str(cfg.get("moonshot_api_key") or "").strip()
            if not key:
                raise ValueError("Moonshot (Kimi) API key is not configured yet. Add it on the Models page.")
            return OpenAICompatEngine(
                provider_id="moonshot",
                base_url="https://api.moonshot.cn/v1",
                api_key=key,
            )

        if provider == "dashscope":
            key = str(cfg.get("dashscope_api_key") or "").strip()
            if not key:
                raise ValueError("Alibaba DashScope (Qwen) API key is not configured yet. Add it on the Models page.")
            return OpenAICompatEngine(
                provider_id="dashscope",
                base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                api_key=key,
            )

        if provider in {"codex-cli", "antigravity-cli"}:
            if self._hub is None or self._vaults is None:
                raise ValueError(f"CLI engine '{provider}' requires MCP Hub and VaultManager.")
            active_brain = brain or str(cfg.get("active_brain") or "Brain Default")
            cwd = self._vaults.brain_path(active_brain)
            cls = CodexCliEngine if provider == "codex-cli" else AntigravityCliEngine
            binary = "codex" if provider == "codex-cli" else "agy"
            return cls(
                binary=binary,
                hub_url=self._hub.hub_url(),
                hub_token=self._hub.hub_token(),
                cwd=cwd,
                run_mode=run_mode,
                redact_fn=self._hub.hide_hub_token,
            )

        raise ValueError(f"Unsupported provider: {provider}")

    async def discover_local_models(self, base_url: str | None = None) -> tuple[bool, list[str], str]:
        """Probe Ollama / OpenAI-compatible localhost server for installed models."""
        cfg = self._settings.load()
        target = (base_url or (cfg.get("local_llama") or {}).get("base_url") or "http://127.0.0.1:11434/v1").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{target}/models")
                if resp.status_code == 200:
                    data = resp.json()
                    ids = [str(m.get("id")) for m in (data.get("data") or []) if m.get("id")]
                    if ids:
                        return True, ids, ""
                # Fallback to Ollama native /api/tags
                ollama_root = target[:-3] if target.endswith("/v1") else target
                resp2 = await client.get(f"{ollama_root}/api/tags")
                if resp2.status_code == 200:
                    data2 = resp2.json()
                    ids2 = [str(m.get("name")) for m in (data2.get("models") or []) if m.get("name")]
                    return True, ids2, ""
                return False, [], f"HTTP {resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            return False, [], str(exc)

    async def list_providers_status(self) -> list[dict[str, Any]]:
        cfg = self._settings.load()
        local_ok, local_models, local_err = await self.discover_local_models()
        result: list[dict[str, Any]] = []
        for spec in PROVIDER_SPECS:
            ready = False
            detail = ""
            models = list(spec.suggested_models)

            if spec.id == "local-llama":
                ready = local_ok
                detail = "Connected" if local_ok else (local_err or "Offline")
                all_local = list(local_models) if local_models else list(spec.suggested_models)
                configured_model = str((cfg.get("local_llama") or {}).get("model") or "").strip()
                aux_model = str((cfg.get("model") or {}).get("aux_model") or "").strip()
                for cm in (aux_model, configured_model):
                    if cm and cm not in all_local:
                        all_local.insert(0, cm)
                models = all_local
            elif spec.id == "openrouter":
                ready = bool(cfg.get(spec.key_field))
                detail = "API key configured" if ready else "API key missing"
                all_openrouter = list(spec.suggested_models)
                main_model = str((cfg.get("model") or {}).get("main_model") or "").strip()
                if main_model and main_model not in all_openrouter:
                    all_openrouter.insert(0, main_model)
                models = all_openrouter
            elif spec.kind == "api":
                ready = bool(cfg.get(spec.key_field))
                detail = "API key configured" if ready else "API key missing"
            elif spec.kind == "cli":
                binary_path = find_cli_binary(spec.cli_binary) if spec.cli_binary else None
                ready = bool(binary_path)
                detail = (
                    f"CLI ready ({binary_path})"
                    if binary_path
                    else f"Install '{spec.cli_binary}' CLI and sign in to enable"
                )

            result.append(
                {
                    "id": spec.id,
                    "label": spec.label,
                    "kind": spec.kind,
                    "default_model": models[0] if models else spec.default_model,
                    "models": models,
                    "ready": ready,
                    "detail": detail,
                    "phase2_cli": spec.phase2_cli,
                }
            )
        return result
