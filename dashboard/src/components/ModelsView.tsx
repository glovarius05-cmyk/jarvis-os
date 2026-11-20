import { useState } from "react";
import { CheckCircle2, Cpu, KeyRound, RefreshCw, Server, Terminal } from "lucide-react";
import { api, type ProvidersResponse } from "../api/client";

interface ModelsViewProps {
  data: ProvidersResponse | null;
  onUpdated: () => Promise<void>;
  t: (key: string) => string;
}

export function ModelsView({ data, onUpdated, t }: ModelsViewProps) {
  const [openrouterKey, setOpenrouterKey] = useState("");
  const [geminiKey, setGeminiKey] = useState("");
  const [perplexityKey, setPerplexityKey] = useState("");
  const [deepseekKey, setDeepseekKey] = useState("");
  const [moonshotKey, setMoonshotKey] = useState("");
  const [dashscopeKey, setDashscopeKey] = useState("");
  const [localUrl, setLocalUrl] = useState(data?.local_llama.base_url ?? "http://127.0.0.1:11434/v1");
  const [mainProvider, setMainProvider] = useState(data?.model.main_provider ?? "openrouter");
  const [mainModel, setMainModel] = useState(data?.model.main_model ?? "openrouter/auto");
  const [auxProvider, setAuxProvider] = useState(data?.model.aux_provider ?? "local-llama");
  const [auxModel, setAuxModel] = useState(data?.model.aux_model ?? "llama3.1:8b");
  const [localProbe, setLocalProbe] = useState<string>("");
  const [savedNotice, setSavedNotice] = useState(false);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    const updates: Record<string, unknown> = {
      model: {
        main_provider: mainProvider,
        main_model: mainModel,
        aux_provider: auxProvider,
        aux_model: auxModel,
      },
      local_llama: {
        base_url: localUrl,
        model: auxProvider === "local-llama" ? auxModel : data?.local_llama.model ?? "llama3.1:8b",
        num_ctx: data?.local_llama.num_ctx ?? 16384,
      },
    };
    if (openrouterKey.trim()) updates.openrouter_api_key = openrouterKey.trim();
    if (geminiKey.trim()) updates.gemini_api_key = geminiKey.trim();
    if (perplexityKey.trim()) updates.perplexity_api_key = perplexityKey.trim();
    if (deepseekKey.trim()) updates.deepseek_api_key = deepseekKey.trim();
    if (moonshotKey.trim()) updates.moonshot_api_key = moonshotKey.trim();
    if (dashscopeKey.trim()) updates.dashscope_api_key = dashscopeKey.trim();

    await api.patchSettings(updates);
    setOpenrouterKey("");
    setGeminiKey("");
    setPerplexityKey("");
    setDeepseekKey("");
    setMoonshotKey("");
    setDashscopeKey("");
    setSavedNotice(true);
    window.setTimeout(() => setSavedNotice(false), 2000);
    await onUpdated();
  };

  const handleProbeLocal = async () => {
    setLocalProbe("...");
    const res = await api.testLocalLlama(localUrl);
    if (res.ok) {
      setLocalProbe(`OK (${res.models.length} models): ${res.models.slice(0, 4).join(", ")}`);
      if (res.models[0]) setAuxModel(res.models[0]);
    } else {
      setLocalProbe(`Offline: ${res.error}`);
    }
  };

  const providers = data?.providers ?? [];
  const mainProviderModels = providers.find((p) => p.id === mainProvider)?.models ?? [];
  const isCustomMain = mainModel !== "" && !mainProviderModels.includes(mainModel);

  const auxProviderModels = providers.find((p) => p.id === auxProvider)?.models ?? [];
  const isCustomAux = auxModel !== "" && !auxProviderModels.includes(auxModel);

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-8">
      <header className="border-b border-zinc-800 pb-4">
        <h2 className="text-2xl font-semibold tracking-tight">{t("models.title")}</h2>
        <p className="mt-1 text-sm text-zinc-400">{t("models.subtitle")}</p>
      </header>

      {/* Provider readiness grid */}
      <section className="grid gap-3 md:grid-cols-2">
        {providers.map((p) => (
          <div
            key={p.id}
            className="flex flex-col justify-between rounded-xl border border-zinc-800 bg-zinc-900/50 p-4"
          >
            <div className="flex items-start justify-between gap-2">
              <div>
                <h3 className="text-sm font-semibold text-zinc-100">{p.label}</h3>
                <p className="mt-0.5 text-xs text-zinc-400">{p.detail}</p>
              </div>
              {p.kind === "cli" ? (
                <Terminal className="h-4 w-4 text-zinc-400" />
              ) : p.kind === "local" ? (
                <Server className="h-4 w-4 text-emerald-400" />
              ) : (
                <Cpu className="h-4 w-4 text-amber-400" />
              )}
            </div>
            <div className="mt-3 flex items-center justify-between text-xs">
              <span
                className={`rounded-full px-2 py-0.5 font-medium ${
                  p.ready
                    ? "bg-emerald-500/15 text-emerald-300"
                    : "bg-zinc-800 text-zinc-400"
                }`}
              >
                {p.ready ? "Ready" : p.phase2_cli ? "Phase 2" : "Setup needed"}
              </span>
              <span className="truncate font-mono text-[11px] text-zinc-500">{p.default_model}</span>
            </div>
          </div>
        ))}
      </section>

      {/* Main / Aux + Keys Form */}
      <form onSubmit={handleSave} className="space-y-5 rounded-xl border border-zinc-800 bg-zinc-900/50 p-6">
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <label className="text-xs font-semibold uppercase tracking-wider text-zinc-400">
              {t("models.main")}
            </label>
            <select
              value={mainProvider}
              onChange={(e) => setMainProvider(e.target.value)}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100"
            >
              {providers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
            <select
              value={isCustomMain ? "__custom__" : mainModel}
              onChange={(e) => {
                if (e.target.value === "__custom__") {
                  setMainModel("");
                } else {
                  setMainModel(e.target.value);
                }
              }}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-xs text-zinc-100 outline-none focus:border-amber-500"
            >
              {mainProviderModels.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
              <option value="__custom__">✎ Custom model / Ввести вручную...</option>
            </select>
            {(isCustomMain || mainModel === "") && (
              <input
                value={mainModel}
                onChange={(e) => setMainModel(e.target.value)}
                placeholder="vendor/model-name:free"
                className="w-full rounded-lg border border-amber-500/50 bg-zinc-950 px-3 py-2 font-mono text-xs text-zinc-100 outline-none focus:border-amber-500"
              />
            )}
          </div>

          <div className="space-y-2">
            <label className="text-xs font-semibold uppercase tracking-wider text-zinc-400">
              {t("models.aux")}
            </label>
            <select
              value={auxProvider}
              onChange={(e) => {
                const next = e.target.value;
                setAuxProvider(next);
                const spec = providers.find((p) => p.id === next);
                if (spec?.default_model) setAuxModel(spec.default_model);
              }}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100"
            >
              {providers.filter((p) => !p.phase2_cli).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
            <select
              value={isCustomAux ? "__custom__" : auxModel}
              onChange={(e) => {
                if (e.target.value === "__custom__") {
                  setAuxModel("");
                } else {
                  setAuxModel(e.target.value);
                }
              }}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-xs text-zinc-100 outline-none focus:border-amber-500"
            >
              {auxProviderModels.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
              <option value="__custom__">✎ Custom model / Ввести вручную...</option>
            </select>
            {(isCustomAux || auxModel === "") && (
              <input
                value={auxModel}
                onChange={(e) => setAuxModel(e.target.value)}
                placeholder="vendor/model-name"
                className="w-full rounded-lg border border-amber-500/50 bg-zinc-950 px-3 py-2 font-mono text-xs text-zinc-100 outline-none focus:border-amber-500"
              />
            )}
          </div>
        </div>

        <div className="space-y-3 border-t border-zinc-800 pt-4">
          <div className="space-y-1.5">
            <label className="flex items-center justify-between text-xs font-medium text-zinc-300">
              <span className="flex items-center gap-1.5">
                <KeyRound className="h-3.5 w-3.5 text-amber-400" /> OpenRouter API Key
              </span>
              <span className="text-zinc-400">
                {data?.secrets_configured.openrouter_api_key ? t("models.keyConfigured") : t("models.keyMissing")}
              </span>
            </label>
            <input
              type="password"
              value={openrouterKey}
              onChange={(e) => setOpenrouterKey(e.target.value)}
              placeholder={t("models.apiKeyPlaceholder")}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-xs text-zinc-100"
            />
          </div>

          <div className="space-y-1.5">
            <label className="flex items-center justify-between text-xs font-medium text-zinc-300">
              <span className="flex items-center gap-1.5">
                <KeyRound className="h-3.5 w-3.5 text-sky-400" /> Google Gemini API Key
              </span>
              <span className="text-zinc-400">
                {data?.secrets_configured.gemini_api_key ? t("models.keyConfigured") : t("models.keyMissing")}
              </span>
            </label>
            <input
              type="password"
              value={geminiKey}
              onChange={(e) => setGeminiKey(e.target.value)}
              placeholder={t("models.apiKeyPlaceholder")}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-xs text-zinc-100"
            />
          </div>

          <div className="space-y-1.5">
            <label className="flex items-center justify-between text-xs font-medium text-zinc-300">
              <span className="flex items-center gap-1.5">
                <KeyRound className="h-3.5 w-3.5 text-teal-400" /> Perplexity AI API Key (Sonar)
              </span>
              <span className="text-zinc-400">
                {data?.secrets_configured.perplexity_api_key ? t("models.keyConfigured") : t("models.keyMissing")}
              </span>
            </label>
            <input
              type="password"
              value={perplexityKey}
              onChange={(e) => setPerplexityKey(e.target.value)}
              placeholder="pplx-..."
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-xs text-zinc-100"
            />
          </div>

          <div className="space-y-1.5">
            <label className="flex items-center justify-between text-xs font-medium text-zinc-300">
              <span className="flex items-center gap-1.5">
                <KeyRound className="h-3.5 w-3.5 text-blue-400" /> DeepSeek API Key (V3 &amp; R1)
              </span>
              <span className="text-zinc-400">
                {data?.secrets_configured.deepseek_api_key ? t("models.keyConfigured") : t("models.keyMissing")}
              </span>
            </label>
            <input
              type="password"
              value={deepseekKey}
              onChange={(e) => setDeepseekKey(e.target.value)}
              placeholder="sk-..."
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-xs text-zinc-100"
            />
          </div>

          <div className="space-y-1.5">
            <label className="flex items-center justify-between text-xs font-medium text-zinc-300">
              <span className="flex items-center gap-1.5">
                <KeyRound className="h-3.5 w-3.5 text-indigo-400" /> Kimi / Moonshot AI API Key
              </span>
              <span className="text-zinc-400">
                {data?.secrets_configured.moonshot_api_key ? t("models.keyConfigured") : t("models.keyMissing")}
              </span>
            </label>
            <input
              type="password"
              value={moonshotKey}
              onChange={(e) => setMoonshotKey(e.target.value)}
              placeholder="sk-..."
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-xs text-zinc-100"
            />
          </div>

          <div className="space-y-1.5">
            <label className="flex items-center justify-between text-xs font-medium text-zinc-300">
              <span className="flex items-center gap-1.5">
                <KeyRound className="h-3.5 w-3.5 text-orange-400" /> Qwen / Alibaba DashScope API Key
              </span>
              <span className="text-zinc-400">
                {data?.secrets_configured.dashscope_api_key ? t("models.keyConfigured") : t("models.keyMissing")}
              </span>
            </label>
            <input
              type="password"
              value={dashscopeKey}
              onChange={(e) => setDashscopeKey(e.target.value)}
              placeholder="sk-..."
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-xs text-zinc-100"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-medium text-zinc-300">{t("models.localUrl")}</label>
            <div className="flex gap-2">
              <input
                value={localUrl}
                onChange={(e) => setLocalUrl(e.target.value)}
                className="flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-xs text-zinc-100"
              />
              <button
                type="button"
                onClick={() => void handleProbeLocal()}
                className="flex items-center gap-1.5 rounded-lg border border-zinc-700 px-3 py-2 text-xs font-medium text-zinc-200 hover:border-zinc-500"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                <span>{t("models.testLocal")}</span>
              </button>
            </div>
            {localProbe && <p className="text-xs text-zinc-400">{localProbe}</p>}
          </div>
        </div>

        <div className="flex items-center gap-3 pt-2">
          <button
            type="submit"
            className="rounded-lg bg-amber-500 px-4 py-2 text-sm font-medium text-zinc-950 hover:bg-amber-400"
          >
            {t("models.save")}
          </button>
          {savedNotice && (
            <span className="flex items-center gap-1 text-xs font-medium text-emerald-400">
              <CheckCircle2 className="h-4 w-4" /> {t("models.saved")}
            </span>
          )}
        </div>
      </form>
    </div>
  );
}
