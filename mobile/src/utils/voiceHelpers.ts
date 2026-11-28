import type { ProviderStatus } from "../types/api";

const FEMALE_VOICE_IDS = new Set([
  "aoede",
  "kore",
  "leda",
  "zephyr",
  "despina",
  "autonoe",
  "erinome",
  "callirrhoe",
  "ru-ru-svetlananeural",
  "ru-ru-dariyaneural",
]);

export function isFemaleVoice(voiceId: string): boolean {
  const key = (voiceId || "").trim().toLowerCase();
  if (FEMALE_VOICE_IDS.has(key)) return true;
  return key.includes("svetlana") || key.includes("dariya") || key.includes("female");
}

export function filterProvidersForPicker(providers: ProviderStatus[]): ProviderStatus[] {
  return providers.filter((p) => p.id !== "local-llama" && p.id !== "perplexity");
}

export function getVisibleModelsForProvider(provider: ProviderStatus): string[] {
  if (provider.id === "openrouter") {
    return provider.models.filter((m) => m.endsWith(":free"));
  }
  return provider.models;
}

export function shortProviderLabel(id: string): string {
  switch (id) {
    case "antigravity-cli":
      return "Gemini (Antigravity)";
    case "codex-cli":
      return "ChatGPT (Codex)";
    case "gemini-api":
      return "Google Gemini";
    case "deepseek":
      return "DeepSeek";
    case "moonshot":
      return "Kimi";
    case "dashscope":
      return "Qwen";
    default:
      return "OpenRouter";
  }
}

export function formatRelativeTime(unixSec: number): string {
  if (!unixSec) return "";
  const diff = Math.max(0, Math.floor(Date.now() / 1000 - unixSec));
  if (diff < 60) return "только что";
  if (diff < 3600) return `${Math.floor(diff / 60)} мин назад`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} ч назад`;
  return new Date(unixSec * 1000).toLocaleDateString("ru-RU");
}
