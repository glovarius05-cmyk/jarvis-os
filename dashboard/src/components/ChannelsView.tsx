import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Mic, Send, Volume2 } from "lucide-react";
import { api } from "../api/client";

export function ChannelsView() {
  const [tgEnabled, setTgEnabled] = useState(false);
  const [tgToken, setTgToken] = useState("");
  const [tgChatIds, setTgChatIds] = useState("");
  const [tgStatus, setTgStatus] = useState<{
    configured: boolean;
    polling: boolean;
    bot_username: string;
    last_error: string;
  } | null>(null);
  const [tgTestResult, setTgTestResult] = useState("");

  const [ttsVoice, setTtsVoice] = useState("ru-RU-DmitryNeural");
  const [ttsRate, setTtsRate] = useState("+5%");
  const [voiceInput, setVoiceInput] = useState("");
  const [voiceReply, setVoiceReply] = useState("");
  const [savedNotice, setSavedNotice] = useState(false);

  const refresh = useCallback(async () => {
    const st = await api.getTelegramStatus();
    setTgStatus(st);
    setTgEnabled(st.enabled);
    setTgChatIds(st.allowed_chat_ids.join(", "));
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleSaveTelegram = async (e: React.FormEvent) => {
    e.preventDefault();
    const ids = tgChatIds
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    await api.saveTelegramConfig({
      enabled: tgEnabled,
      bot_token: tgToken.trim(),
      allowed_chat_ids: ids,
    });
    setTgToken("");
    setSavedNotice(true);
    window.setTimeout(() => setSavedNotice(false), 1800);
    await refresh();
  };

  const handleTestTelegram = async () => {
    setTgTestResult("Testing...");
    const res = await api.testTelegram();
    setTgTestResult(res.ok ? `Connected: @${res.username}` : `Error: ${res.error}`);
    await refresh();
  };

  const handleSaveVoice = async (e: React.FormEvent) => {
    e.preventDefault();
    await api.patchSettings({ voice: { tts_voice: ttsVoice, tts_rate: ttsRate } });
    try {
      localStorage.setItem("jarvis_selected_voice", ttsVoice);
    } catch {}
    setSavedNotice(true);
    window.setTimeout(() => setSavedNotice(false), 1800);
  };

  const handleVoiceTest = async () => {
    if (!voiceInput.trim()) return;
    const res = await api.voiceTurn(voiceInput.trim());
    setVoiceReply(JSON.stringify(res, null, 2));
    if (!res.skipped && res.spoken_text) {
      try {
        const audioRes = await api.synthesizeTTS(res.spoken_text, ttsVoice, ttsRate);
        if (audioRes.audioUrl) {
          const audio = new Audio(audioRes.audioUrl);
          await audio.play().catch(() => {});
          return;
        }
      } catch {}
      if ("speechSynthesis" in window) {
        const utter = new SpeechSynthesisUtterance(res.spoken_text);
        utter.lang = /[а-яА-ЯёЁ]/.test(res.spoken_text) ? "ru-RU" : "en-US";
        if (!ttsVoice.includes("Svetlana") && ttsVoice !== "browser-female") {
          utter.pitch = 0.82;
        }
        window.speechSynthesis.speak(utter);
      }
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-8">
      <header className="border-b border-zinc-800 pb-4">
        <h2 className="text-2xl font-semibold tracking-tight">Telegram Bot & Voice V2</h2>
        <p className="text-sm text-zinc-400">
          Connect your private Telegram owner bot and configure two-brain hands-free voice (Fast Voice Brain + TTS).
        </p>
      </header>

      {/* Telegram Bot Configuration */}
      <form onSubmit={handleSaveTelegram} className="space-y-4 rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Send className="h-4 w-4 text-sky-400" />
            <h3 className="text-sm font-semibold">Owner Telegram Bot</h3>
          </div>
          <span
            className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
              tgStatus?.polling ? "bg-emerald-500/15 text-emerald-300" : "bg-zinc-800 text-zinc-400"
            }`}
          >
            {tgStatus?.polling ? "Polling active" : tgStatus?.configured ? "Configured (off)" : "Not configured"}
          </span>
        </div>

        <div className="space-y-2">
          <label className="text-xs text-zinc-400">Bot Token from @BotFather (stored Fernet-encrypted)</label>
          <input
            type="password"
            value={tgToken}
            onChange={(e) => setTgToken(e.target.value)}
            placeholder={tgStatus?.configured ? "Token saved (leave empty to keep)" : "123456789:AA..."}
            className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-xs text-zinc-100"
          />
        </div>

        <div className="space-y-2">
          <label className="text-xs text-zinc-400">Allowed Owner Chat IDs (comma-separated whitelist)</label>
          <input
            value={tgChatIds}
            onChange={(e) => setTgChatIds(e.target.value)}
            placeholder="e.g. 123456789"
            className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-xs text-zinc-100"
          />
        </div>

        <label className="flex items-center gap-2 text-xs text-zinc-300">
          <input
            type="checkbox"
            checked={tgEnabled}
            onChange={(e) => setTgEnabled(e.target.checked)}
            className="rounded border-zinc-700"
          />
          Enable Telegram background long-polling & Inbox report delivery
        </label>

        <div className="flex flex-wrap items-center gap-2 pt-1">
          <button
            type="submit"
            className="rounded-lg bg-amber-500 px-3.5 py-1.5 text-xs font-medium text-zinc-950 hover:bg-amber-400"
          >
            Save Telegram
          </button>
          <button
            type="button"
            onClick={() => void handleTestTelegram()}
            className="rounded-lg border border-zinc-700 px-3.5 py-1.5 text-xs text-zinc-200 hover:border-sky-400"
          >
            Test Bot (`getMe`)
          </button>
          {tgTestResult && <span className="text-xs text-zinc-300">{tgTestResult}</span>}
          {savedNotice && (
            <span className="inline-flex items-center gap-1 text-xs text-emerald-400">
              <CheckCircle2 className="h-3.5 w-3.5" /> Saved
            </span>
          )}
        </div>
        {tgStatus?.last_error && <p className="text-xs text-rose-300">{tgStatus.last_error}</p>}
      </form>

      {/* Voice V2 Settings & Simulator */}
      <div className="space-y-4 rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <div className="flex items-center gap-2">
          <Volume2 className="h-4 w-4 text-amber-400" />
          <h3 className="text-sm font-semibold">Voice V2 (Fast Voice Brain + TTS)</h3>
        </div>

        <form onSubmit={handleSaveVoice} className="grid gap-3 sm:grid-cols-3">
          <select
            value={ttsVoice}
            onChange={(e) => setTtsVoice(e.target.value)}
            className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 font-mono text-xs text-zinc-100"
          >
            <option value="ru-RU-DmitryNeural">ru-RU-DmitryNeural (Мужской - Дмитрий)</option>
            <option value="ru-RU-SvetlanaNeural">ru-RU-SvetlanaNeural (Женский - Светлана)</option>
            <option value="browser-male">browser-male (Мужской баритон, Браузер)</option>
            <option value="browser-female">browser-female (Женский, Браузер)</option>
          </select>
          <input
            value={ttsRate}
            onChange={(e) => setTtsRate(e.target.value)}
            placeholder="Speed (+5%)"
            className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 font-mono text-xs text-zinc-100"
          />
          <button
            type="submit"
            className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:border-amber-500"
          >
            Save Voice Settings
          </button>
        </form>

        <div className="space-y-2 border-t border-zinc-800 pt-3">
          <label className="text-xs text-zinc-400">Test Fast Voice Brain (supports JARVIS_ASK_MAIN / JARVIS_UI / JARVIS_SKIP)</label>
          <div className="flex gap-2">
            <input
              value={voiceInput}
              onChange={(e) => setVoiceInput(e.target.value)}
              placeholder="Say or type a voice utterance..."
              className="flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-xs text-zinc-100"
            />
            <button
              type="button"
              onClick={() => void handleVoiceTest()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-amber-500 px-3.5 py-1.5 text-xs font-medium text-zinc-950 hover:bg-amber-400"
            >
              <Mic className="h-3.5 w-3.5" /> Speak Turn
            </button>
          </div>
          {voiceReply && (
            <pre className="overflow-x-auto rounded-lg bg-zinc-950 p-3 font-mono text-xs text-zinc-300">
              {voiceReply}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}
