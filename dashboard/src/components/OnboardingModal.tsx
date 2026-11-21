import { useState } from "react";
import { Bot, Brain, CheckCircle2, ChevronRight, Cpu, Key, Shield, Sparkles, X } from "lucide-react";
import { api, type CharacterTemplate, type OnboardingStatus } from "../api/client";

interface OnboardingModalProps {
  status: OnboardingStatus;
  onCompleted: () => void;
  onDismiss: () => void;
}

export function OnboardingModal({ status, onCompleted, onDismiss }: OnboardingModalProps) {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [provider, setProvider] = useState<string>(
    status.has_ollama ? "local-llama" : "openrouter"
  );
  const [apiKey, setApiKey] = useState("");
  const [selectedModel, setSelectedModel] = useState(
    status.local_models.length > 0 ? status.local_models[0] : ""
  );
  const [characterTemplate, setCharacterTemplate] = useState("default");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleFinish = async () => {
    setLoading(true);
    setError(null);
    try {
      await api.completeOnboarding({
        provider,
        model: selectedModel,
        api_key: apiKey.trim(),
        character_template: characterTemplate,
      });
      onCompleted();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Onboarding failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-md">
      <div className="w-full max-w-xl rounded-2xl border border-zinc-800 bg-zinc-900 p-6 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-zinc-800 pb-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-500/20 text-amber-400">
              <Brain className="h-6 w-6" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-zinc-100">Jarvis OS Setup Wizard</h2>
              <p className="text-xs text-zinc-400">Step {step} of 3: {step === 1 ? "Select AI Provider" : step === 2 ? "Choose Brain Persona" : "Review & Launch"}</p>
            </div>
          </div>
          <button
            onClick={onDismiss}
            className="rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
            title="Skip wizard"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {error && (
          <div className="mt-3 rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-200">
            {error}
          </div>
        )}

        {/* Step 1: Provider selection */}
        {step === 1 && (
          <div className="mt-5 space-y-4">
            <p className="text-xs text-zinc-300">
              Choose how Jarvis OS will process intelligence and conversational turns:
            </p>

            <div className="grid gap-3 sm:grid-cols-2">
              <button
                type="button"
                onClick={() => setProvider("local-llama")}
                className={`rounded-xl border p-4 text-left transition ${
                  provider === "local-llama"
                    ? "border-emerald-500/60 bg-emerald-500/10"
                    : "border-zinc-800 bg-zinc-950/60 hover:border-zinc-700"
                }`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 font-medium text-xs text-zinc-200">
                    <Cpu className="h-4 w-4 text-emerald-400" />
                    <span>Local Llama / Ollama</span>
                  </div>
                  {status.has_ollama && (
                    <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] text-emerald-300 font-medium">
                      Detected
                    </span>
                  )}
                </div>
                <p className="mt-2 text-[11px] leading-relaxed text-zinc-400">
                  Runs 100% private and offline on your hardware via local Ollama.
                </p>
              </button>

              <button
                type="button"
                onClick={() => setProvider("openrouter")}
                className={`rounded-xl border p-4 text-left transition ${
                  provider === "openrouter"
                    ? "border-amber-500/60 bg-amber-500/10"
                    : "border-zinc-800 bg-zinc-950/60 hover:border-zinc-700"
                }`}
              >
                <div className="flex items-center gap-2 font-medium text-xs text-zinc-200">
                  <Key className="h-4 w-4 text-amber-400" />
                  <span>OpenRouter (Cloud)</span>
                </div>
                <p className="mt-2 text-[11px] leading-relaxed text-zinc-400">
                  Access to 200+ top cloud models, including free community models.
                </p>
              </button>

              <button
                type="button"
                onClick={() => setProvider("gemini")}
                className={`rounded-xl border p-4 text-left transition ${
                  provider === "gemini"
                    ? "border-sky-500/60 bg-sky-500/10"
                    : "border-zinc-800 bg-zinc-950/60 hover:border-zinc-700"
                }`}
              >
                <div className="flex items-center gap-2 font-medium text-xs text-zinc-200">
                  <Sparkles className="h-4 w-4 text-sky-400" />
                  <span>Google Gemini API</span>
                </div>
                <p className="mt-2 text-[11px] leading-relaxed text-zinc-400">
                  Ultra-fast response streaming and 1M+ token context window.
                </p>
              </button>

              <button
                type="button"
                onClick={() => setProvider("perplexity")}
                className={`rounded-xl border p-4 text-left transition ${
                  provider === "perplexity"
                    ? "border-purple-500/60 bg-purple-500/10"
                    : "border-zinc-800 bg-zinc-950/60 hover:border-zinc-700"
                }`}
              >
                <div className="flex items-center gap-2 font-medium text-xs text-zinc-200">
                  <Shield className="h-4 w-4 text-purple-400" />
                  <span>Perplexity Sonar</span>
                </div>
                <p className="mt-2 text-[11px] leading-relaxed text-zinc-400">
                  Real-time web search and verified online citations.
                </p>
              </button>

              <button
                type="button"
                onClick={() => setProvider("openai")}
                className={`rounded-xl border p-4 text-left transition ${
                  provider === "openai"
                    ? "border-emerald-500/60 bg-emerald-500/10"
                    : "border-zinc-800 bg-zinc-950/60 hover:border-zinc-700"
                }`}
              >
                <div className="flex items-center gap-2 font-medium text-xs text-zinc-200">
                  <Bot className="h-4 w-4 text-emerald-400" />
                  <span>OpenAI / Codex API</span>
                </div>
                <p className="mt-2 text-[11px] leading-relaxed text-zinc-400">
                  Direct access to GPT-4o, o1, o3-mini and OpenAI models.
                </p>
              </button>
            </div>

            {/* Provider detail fields */}
            {provider === "local-llama" ? (
              <div className="rounded-xl border border-zinc-800 bg-zinc-950/80 p-3.5 space-y-2">
                <label className="block text-xs font-medium text-zinc-300">
                  Select Installed Ollama Model
                </label>
                {status.local_models.length > 0 ? (
                  <select
                    value={selectedModel}
                    onChange={(e) => setSelectedModel(e.target.value)}
                    className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-100 outline-none"
                  >
                    {status.local_models.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                ) : (
                  <p className="text-xs text-amber-300">
                    Ollama not detected on port 11434. Run `ollama serve` or choose a cloud provider.
                  </p>
                )}
              </div>
            ) : (
              <div className="rounded-xl border border-zinc-800 bg-zinc-950/80 p-3.5 space-y-2">
                <label className="block text-xs font-medium text-zinc-300">
                  {provider.toUpperCase()} API Key (Optional if already in .env)
                </label>
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="sk-..."
                  className="w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-100 outline-none focus:border-amber-400"
                />
              </div>
            )}
          </div>
        )}

        {/* Step 2: Persona template */}
        {step === 2 && (
          <div className="mt-5 space-y-3">
            <p className="text-xs text-zinc-300">
              Select the initial role and communication style for your Second Brain:
            </p>

            <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
              {status.templates.map((tpl: CharacterTemplate) => {
                const isSelected = characterTemplate === tpl.id;
                return (
                  <button
                    key={tpl.id}
                    type="button"
                    onClick={() => setCharacterTemplate(tpl.id)}
                    className={`w-full rounded-xl border p-3 text-left transition flex items-start justify-between gap-3 ${
                      isSelected
                        ? "border-amber-500/60 bg-amber-500/10"
                        : "border-zinc-800 bg-zinc-950/50 hover:border-zinc-700"
                    }`}
                  >
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-base">{tpl.avatar}</span>
                        <h4 className="text-xs font-semibold text-zinc-100">{tpl.name}</h4>
                        <span className="rounded bg-zinc-800 px-1.5 py-0.2 text-[10px] text-zinc-400">
                          {tpl.role}
                        </span>
                      </div>
                      <p className="mt-1 text-[11px] text-zinc-400 leading-snug">{tpl.bio}</p>
                      <p className="mt-1 text-[10px] text-amber-400/90 font-mono">
                        Tone: {tpl.tone}
                      </p>
                    </div>
                    {isSelected && <CheckCircle2 className="h-4 w-4 text-amber-400 shrink-0 mt-1" />}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Step 3: Confirmation */}
        {step === 3 && (
          <div className="mt-5 space-y-4">
            <div className="rounded-xl border border-zinc-800 bg-zinc-950/80 p-4 space-y-2.5 text-xs">
              <h4 className="font-semibold text-zinc-100">Setup Summary</h4>
              <div className="flex justify-between border-b border-zinc-850 pb-1.5">
                <span className="text-zinc-400">Primary Provider:</span>
                <span className="font-medium text-amber-300">{provider}</span>
              </div>
              <div className="flex justify-between border-b border-zinc-850 pb-1.5">
                <span className="text-zinc-400">Persona Role:</span>
                <span className="font-medium text-zinc-200">{characterTemplate}</span>
              </div>
              <div className="flex justify-between border-b border-zinc-850 pb-1.5">
                <span className="text-zinc-400">Second Brain Vault:</span>
                <span className="font-medium text-zinc-200">{status.active_brain}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-zinc-400">Hybrid Search &amp; Obsidian Sync:</span>
                <span className="text-emerald-400 font-medium">Ready</span>
              </div>
            </div>

            <p className="text-[11px] text-zinc-400 leading-relaxed">
              You can adjust model settings, connect additional MCP servers, or change brain characters anytime from the sidebar.
            </p>
          </div>
        )}

        {/* Footer controls */}
        <div className="mt-6 flex items-center justify-between border-t border-zinc-800 pt-4">
          {step > 1 ? (
            <button
              type="button"
              onClick={() => setStep((s) => (s - 1) as any)}
              className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800"
            >
              Back
            </button>
          ) : (
            <div />
          )}

          {step < 3 ? (
            <button
              type="button"
              onClick={() => setStep((s) => (s + 1) as any)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-amber-500 px-4 py-1.5 text-xs font-semibold text-zinc-950 hover:bg-amber-400"
            >
              <span>Next</span>
              <ChevronRight className="h-3.5 w-3.5" />
            </button>
          ) : (
            <button
              type="button"
              disabled={loading}
              onClick={() => void handleFinish()}
              className="inline-flex items-center gap-2 rounded-lg bg-amber-500 px-5 py-2 text-xs font-semibold text-zinc-950 hover:bg-amber-400 disabled:opacity-50"
            >
              <Sparkles className="h-4 w-4" />
              <span>{loading ? "Starting..." : "Launch Jarvis OS"}</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
