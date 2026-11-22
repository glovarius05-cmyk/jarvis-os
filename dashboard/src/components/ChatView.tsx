import { useCallback, useEffect, useRef, useState } from "react";
import {
  Bot,
  FileCode,
  FileSpreadsheet,
  FileText,
  Image as ImageIcon,
  Loader2,
  Mic,
  MicOff,
  Plus,
  Radio,
  Search,
  Send,
  Square,
  Trash2,
  User,
  Volume2,
  Wrench,
  X,
} from "lucide-react";
import {
  api,
  type MessageItem,
  type ProviderStatus,
  type SearchHit,
  type SessionItem,
} from "../api/client";
import type { LiveTurnState } from "../hooks/useChatSocket";
import { useWakeWord } from "../hooks/useWakeWord";
import { MarkdownText } from "./MarkdownText";
import { VoiceCallModal } from "./VoiceCallModal";

export interface AttachedFileItem {
  id: string;
  name: string;
  size: number;
  type: string;
  isImage: boolean;
  isText: boolean;
  previewUrl?: string;
  textContent?: string;
  uploadedPath?: string;
  uploading?: boolean;
  error?: string;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

function getFileIcon(name: string) {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (["jpg", "jpeg", "png", "webp", "gif", "svg", "bmp"].includes(ext)) {
    return <ImageIcon className="h-4 w-4 text-sky-400" />;
  }
  if (["py", "js", "ts", "tsx", "jsx", "html", "css", "json", "yaml", "yml", "sh", "sql", "rs", "go"].includes(ext)) {
    return <FileCode className="h-4 w-4 text-emerald-400" />;
  }
  if (["csv", "tsv", "xlsx", "xls"].includes(ext)) {
    return <FileSpreadsheet className="h-4 w-4 text-amber-400" />;
  }
  return <FileText className="h-4 w-4 text-indigo-400" />;
}

interface ChatViewProps {
  activeBrain: string;
  providers: ProviderStatus[];
  defaultProvider: string;
  defaultModel: string;
  wsConnected: boolean;
  liveTurns: Record<string, LiveTurnState>;
  errors: Record<string, string>;
  onSend: (sessionId: string, content: string, provider: string, model: string, mode: string) => boolean;
  onStop: (sessionId: string) => void;
  onNavigate?: (page: string) => void;
  refreshTrigger: number;
  t: (key: string) => string;
}

export function ChatView({
  activeBrain,
  providers,
  defaultProvider,
  defaultModel,
  wsConnected,
  liveTurns,
  errors,
  onSend,
  onStop,
  onNavigate: _onNavigate,
  refreshTrigger,
  t,
}: ChatViewProps) {
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [draft, setDraft] = useState("");
  const [provider, setProvider] = useState(defaultProvider);
  const [model, setModel] = useState(defaultModel);
  const [runMode, setRunMode] = useState<"suggest" | "auto" | "full" | "consensus" | "debate">("full");
  const [listening, setListening] = useState(false);
  const [playingMessageId, setPlayingMessageId] = useState<number | string | null>(null);
  const [loadingAudioId, setLoadingAudioId] = useState<number | string | null>(null);
  const [autoSpeak, setAutoSpeak] = useState<boolean>(() => {
    try {
      return localStorage.getItem("jarvis_chat_auto_speak") === "true";
    } catch {
      return false;
    }
  });
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const recRef = useRef<unknown>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const prevTurnRef = useRef<LiveTurnState | undefined>(undefined);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchHits, setSearchHits] = useState<SearchHit[]>([]);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const [attachedFiles, setAttachedFiles] = useState<AttachedFileItem[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [voiceCallOpen, setVoiceCallOpen] = useState(false);
  const [initialVoiceUtterance, setInitialVoiceUtterance] = useState("");

  const [wakeWordEnabled, setWakeWordEnabled] = useState<boolean>(() => {
    try {
      return localStorage.getItem("jarvis_wake_word_enabled") === "true";
    } catch {
      return false;
    }
  });

  const { listening: wakeWordListening, supported: wakeWordSupported } = useWakeWord({
    enabled: wakeWordEnabled,
    paused: voiceCallOpen,
    onWake: (trailingUtterance) => {
      setInitialVoiceUtterance(trailingUtterance || "");
      setVoiceCallOpen(true);
    },
  });

  useEffect(() => {
    setProvider(defaultProvider);
    setModel(defaultModel);
  }, [defaultProvider, defaultModel]);

  const loadSessions = useCallback(async () => {
    const res = await api.listSessions(activeBrain);
    setSessions(res.sessions);
    setSelectedId((prev) => {
      if (prev && res.sessions.some((s) => s.id === prev)) return prev;
      return res.sessions[0]?.id ?? "";
    });
  }, [activeBrain]);

  const loadMessages = useCallback(async (sid: string) => {
    if (!sid) {
      setMessages([]);
      return;
    }
    const res = await api.getSession(sid);
    setMessages(res.messages);
  }, []);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions, refreshTrigger]);

  useEffect(() => {
    void loadMessages(selectedId);
  }, [selectedId, loadMessages, refreshTrigger]);

  const activeTurn = selectedId ? liveTurns[selectedId] : undefined;
  const activeError = selectedId ? errors[selectedId] : undefined;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, activeTurn?.text]);

  useEffect(() => {
    const q = searchQuery.trim();
    if (!q) {
      setSearchHits([]);
      return;
    }
    const timer = window.setTimeout(() => {
      void api.searchSessions(q, activeBrain).then((r) => setSearchHits(r.hits));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [searchQuery, activeBrain]);

  const handleNewChat = async () => {
    const created = await api.createSession({ brain: activeBrain, title: t("chat.new") });
    await loadSessions();
    setSelectedId(created.id);
    setMessages([]);
  };

  const handleDelete = async (sid: string) => {
    await api.deleteSession(sid);
    if (sid === selectedId) {
      setSelectedId("");
      setMessages([]);
    }
    await loadSessions();
  };


  const stopSpeech = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      audioRef.current = null;
    }
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
    setPlayingMessageId(null);
    setLoadingAudioId(null);
  }, []);

  const playSpeech = useCallback(
    async (messageId: number | string, text: string) => {
      if (playingMessageId === messageId) {
        stopSpeech();
        return;
      }
      stopSpeech();
      setLoadingAudioId(messageId);
      try {
        const res = await api.synthesizeTTS(text);
        setLoadingAudioId(null);
        if (res.audioUrl) {
          const audio = new Audio(res.audioUrl);
          audioRef.current = audio;
          audio.onended = () => {
            setPlayingMessageId(null);
            audioRef.current = null;
          };
          audio.onerror = () => {
            if ("speechSynthesis" in window) {
              const utter = new SpeechSynthesisUtterance(text);
              utter.lang = /[а-яА-ЯёЁ]/.test(text) ? "ru-RU" : "en-US";
              utter.onend = () => setPlayingMessageId(null);
              utter.onerror = () => setPlayingMessageId(null);
              window.speechSynthesis.speak(utter);
            } else {
              setPlayingMessageId(null);
            }
          };
          setPlayingMessageId(messageId);
          await audio.play();
        } else if (res.fallbackText && "speechSynthesis" in window) {
          setPlayingMessageId(messageId);
          const utter = new SpeechSynthesisUtterance(res.fallbackText);
          utter.lang = /[а-яА-ЯёЁ]/.test(res.fallbackText) ? "ru-RU" : "en-US";
          utter.onend = () => setPlayingMessageId(null);
          utter.onerror = () => setPlayingMessageId(null);
          window.speechSynthesis.speak(utter);
        }
      } catch (err) {
        console.error("TTS playback error", err);
        stopSpeech();
      }
    },
    [playingMessageId, stopSpeech]
  );

  useEffect(() => {
    if (prevTurnRef.current && !activeTurn && autoSpeak) {
      const finishedText = prevTurnRef.current.text;
      if (finishedText && finishedText.trim()) {
        void playSpeech("latest", finishedText);
      }
    }
    prevTurnRef.current = activeTurn;
  }, [activeTurn, autoSpeak, playSpeech]);

  useEffect(() => {
    return () => {
      stopSpeech();
      const rec = recRef.current as { stop?: () => void } | null;
      if (rec?.stop) {
        try {
          rec.stop();
        } catch {}
      }
      if (mediaStreamRef.current) {
        mediaStreamRef.current.getTracks().forEach((t) => t.stop());
      }
    };
  }, [stopSpeech]);

  const startMediaRecorderFallback = async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      setDraft((prev) => prev || "Микрофон заблокирован браузером (незащищенный HTTP). Добавьте URL в chrome://flags/#unsafely-treat-insecure-origin-as-secure или используйте Telegram.");
      setListening(false);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaStreamRef.current = stream;
      const mediaRecorder = new MediaRecorder(stream);
      const chunks: BlobPart[] = [];
      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunks.push(e.data);
      };
      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        mediaStreamRef.current = null;
        setListening(false);
        const blob = new Blob(chunks, { type: "audio/webm" });
        try {
          const res = await api.transcribeAudio(blob, "mic.webm");
          if (res.text?.trim()) {
            setDraft((prev) => (prev ? `${prev} ${res.text.trim()}` : res.text.trim()));
          }
        } catch (err) {
          console.error("STT error", err);
          setDraft((prev) => prev || "Ошибка распознавания аудио на сервере.");
        }
      };
      mediaRecorder.start();
      recRef.current = mediaRecorder;
      setListening(true);
    } catch {
      setDraft((prev) => prev || "Микрофон заблокирован браузером (HTTP). Разрешите доступ в chrome://flags или используйте Telegram-бот.");
      setListening(false);
    }
  };

  const handleMicToggle = async () => {
    if (listening) {
      const rec = recRef.current as { stop?: () => void } | null;
      if (rec?.stop) {
        try {
          rec.stop();
        } catch {}
      }
      if (mediaStreamRef.current) {
        mediaStreamRef.current.getTracks().forEach((t) => t.stop());
        mediaStreamRef.current = null;
      }
      setListening(false);
      return;
    }

    // Check secure context (browsers block mic on http://<ip>:port)
    const isLocal = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
    if (!window.isSecureContext && !isLocal) {
      setDraft((prev) => prev || "Браузер блокирует микрофон по незащищенному HTTP (http://202.71.13.37:7777). Разрешите URL в chrome://flags/#unsafely-treat-insecure-origin-as-secure или используйте Telegram-бот!");
      return;
    }

    const SpeechRec =
      (window as unknown as Record<string, unknown>).SpeechRecognition ||
      (window as unknown as Record<string, unknown>).webkitSpeechRecognition;

    if (typeof SpeechRec === "function") {
      const rec = new (SpeechRec as new () => {
        lang: string;
        continuous: boolean;
        interimResults: boolean;
        onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
        onerror: ((e: { error?: string }) => void) | null;
        onend: (() => void) | null;
        start: () => void;
        stop: () => void;
      })();
      rec.lang = "ru-RU";
      rec.continuous = true;
      rec.interimResults = true;
      recRef.current = rec;
      const initialDraft = draft;

      rec.onresult = (ev) => {
        let transcript = "";
        for (let i = 0; i < ev.results.length; ++i) {
          const item = ev.results[i]?.[0];
          if (item?.transcript) {
            transcript += item.transcript;
          }
        }
        if (transcript) {
          const sep = initialDraft && !initialDraft.endsWith(" ") ? " " : "";
          setDraft(initialDraft ? `${initialDraft}${sep}${transcript.trim()}` : transcript.trim());
        }
      };
      rec.onerror = (ev) => {
        setListening(false);
        if (ev?.error === "not-allowed" || ev?.error === "network" || ev?.error === "service-not-allowed") {
          void startMediaRecorderFallback();
        }
      };
      rec.onend = () => setListening(false);
      try {
        rec.start();
        setListening(true);
      } catch {
        void startMediaRecorderFallback();
      }
      return;
    }

    await startMediaRecorderFallback();
  };

  const handleFilesAdded = useCallback(
    async (files: FileList | File[]) => {
      const fileList = Array.from(files);
      if (fileList.length === 0) return;

      const newItems: AttachedFileItem[] = [];

      for (const file of fileList) {
        const id = Math.random().toString(36).slice(2) + Date.now().toString(36);
        const ext = file.name.split(".").pop()?.toLowerCase() || "";
        const isImage = file.type.startsWith("image/") || ["jpg", "jpeg", "png", "webp", "gif", "svg", "bmp"].includes(ext);
        const isText =
          file.type.startsWith("text/") ||
          [
            "txt", "md", "py", "js", "ts", "tsx", "jsx", "json", "csv", "tsv",
            "html", "css", "yaml", "yml", "xml", "sh", "bash", "sql", "log",
            "env", "toml", "ini", "conf", "rs", "go", "java", "c", "cpp", "h",
          ].includes(ext);

        const item: AttachedFileItem = {
          id,
          name: file.name,
          size: file.size,
          type: file.type || "application/octet-stream",
          isImage,
          isText,
          uploading: true,
        };

        if (isImage) {
          const reader = new FileReader();
          reader.onload = (e) => {
            const dataUrl = e.target?.result as string;
            setAttachedFiles((prev) =>
              prev.map((f) => (f.id === id ? { ...f, previewUrl: dataUrl } : f))
            );
          };
          reader.readAsDataURL(file);
        } else if (isText && file.size < 2 * 1024 * 1024) {
          const reader = new FileReader();
          reader.onload = (e) => {
            const text = e.target?.result as string;
            setAttachedFiles((prev) =>
              prev.map((f) => (f.id === id ? { ...f, textContent: text } : f))
            );
          };
          reader.readAsText(file);
        }

        newItems.push(item);
      }

      setAttachedFiles((prev) => [...prev, ...newItems]);

      // Upload files asynchronously to Second Brain
      for (let i = 0; i < fileList.length; i++) {
        const file = fileList[i];
        const item = newItems[i];
        try {
          const res = await api.uploadChatFile(file, activeBrain);
          setAttachedFiles((prev) =>
            prev.map((f) =>
              f.id === item.id
                ? {
                    ...f,
                    uploadedPath: res.path,
                    uploading: false,
                    textContent: f.textContent || res.text_content || undefined,
                  }
                : f
            )
          );
        } catch (err) {
          console.error("Upload error for", file.name, err);
          setAttachedFiles((prev) =>
            prev.map((f) =>
              f.id === item.id
                ? { ...f, uploading: false, error: err instanceof Error ? err.message : "Upload failed" }
                : f
            )
          );
        }
      }
    },
    [activeBrain]
  );

  const handleRemoveAttachment = (id: string) => {
    setAttachedFiles((prev) => prev.filter((f) => f.id !== id));
  };

  const handleClearAttachments = () => {
    setAttachedFiles([]);
  };

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      void handleFilesAdded(e.target.files);
      e.target.value = "";
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      void handleFilesAdded(e.dataTransfer.files);
    }
  };

  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    if (e.clipboardData.files && e.clipboardData.files.length > 0) {
      e.preventDefault();
      void handleFilesAdded(e.clipboardData.files);
    }
  };

  const handleSubmit = async () => {
    const text = draft.trim();
    if (!text && attachedFiles.length === 0) return;
    let sid = selectedId;
    if (!sid) {
      const created = await api.createSession({ brain: activeBrain, title: t("chat.new") });
      sid = created.id;
      setSelectedId(sid);
      await loadSessions();
    }

    let fullContent = text;
    const fileBlocks: string[] = [];

    for (const f of attachedFiles) {
      const header = `--- Прикрепленный файл: ${f.name} (${formatBytes(f.size)}) ---`;
      if (f.isText && f.textContent) {
        const ext = f.name.split(".").pop() || "";
        fileBlocks.push(`${header}\n\`\`\`${ext}\n${f.textContent}\n\`\`\`\n--------------------------------------------`);
      } else if (f.isImage) {
        const pathInfo = f.uploadedPath ? `[Сохранено в Second Brain: ${f.uploadedPath}]` : `[Изображение прикреплено]`;
        fileBlocks.push(`${header}\n${pathInfo}\n--------------------------------------------`);
      } else {
        const pathInfo = f.uploadedPath ? `[Сохранено в Second Brain: ${f.uploadedPath}]` : `[Файл прикреплен]`;
        fileBlocks.push(`${header}\n${pathInfo}\n--------------------------------------------`);
      }
    }

    if (fileBlocks.length > 0) {
      if (fullContent) {
        fullContent = `${fullContent}\n\n${fileBlocks.join("\n\n")}`;
      } else {
        fullContent = `Пожалуйста, изучи прикрепленные файлы:\n\n${fileBlocks.join("\n\n")}`;
      }
    }

    const ok = onSend(sid, fullContent, provider, model, runMode);
    if (ok) {
      setDraft("");
      setAttachedFiles([]);
      // Optimistically render user turn while waiting for turn_start
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now(),
          session_id: sid,
          role: "user",
          content: fullContent,
          provider,
          model,
          meta: {},
          created_at: Date.now() / 1000,
        },
      ]);
    }
  };

  const activeProviderSpec = providers.find((p) => p.id === provider);
  const selectableProviders = providers;
  const allKnownPairs = providers.flatMap((p) => p.models.map((m) => `${p.id}::${m}`));
  const isKnown = allKnownPairs.includes(`${provider}::${model}`);
  const isCustomModel = !isKnown && model !== "";

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sessions Sidebar */}
      <aside className="flex w-72 flex-col border-r border-zinc-800 bg-zinc-900/40">
        <div className="space-y-2 border-b border-zinc-800 p-3">
          <button
            onClick={handleNewChat}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-amber-500 px-3 py-2 text-sm font-medium text-zinc-950 transition hover:bg-amber-400"
          >
            <Plus className="h-4 w-4" />
            <span>{t("chat.new")}</span>
          </button>
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-zinc-500" />
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t("chat.search")}
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 py-1.5 pl-8 pr-2.5 text-xs text-zinc-200 outline-none focus:border-amber-500/60"
            />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-2">
          {searchQuery.trim() ? (
            <div className="space-y-1">
              {searchHits.map((h) => (
                <button
                  key={`${h.session_id}-${h.message_id}`}
                  onClick={() => {
                    setSelectedId(h.session_id);
                    setSearchQuery("");
                  }}
                  className="w-full rounded-lg border border-zinc-800/80 bg-zinc-900/70 p-2 text-left hover:border-amber-500/40"
                >
                  <p className="truncate text-xs font-medium text-zinc-200">{h.session_title}</p>
                  <p className="mt-0.5 line-clamp-2 text-[11px] text-zinc-400">{h.snippet}</p>
                </button>
              ))}
            </div>
          ) : (
            <div className="space-y-1">
              {sessions.map((s) => {
                const isSelected = s.id === selectedId;
                const isBusy = Boolean(liveTurns[s.id]);
                return (
                  <div
                    key={s.id}
                    className={`group flex items-center justify-between rounded-lg px-2.5 py-2 text-sm transition ${
                      isSelected
                        ? "bg-zinc-800 text-zinc-50"
                        : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
                    }`}
                  >
                    <button
                      onClick={() => setSelectedId(s.id)}
                      className="flex min-w-0 flex-1 items-center gap-2 text-left"
                    >
                      {isBusy && <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-amber-400" />}
                      <span className="truncate text-xs font-medium">{s.title}</span>
                    </button>
                    <button
                      onClick={() => handleDelete(s.id)}
                      className="ml-1 hidden rounded p-1 text-zinc-500 hover:bg-zinc-700 hover:text-rose-400 group-hover:block"
                      title="Delete"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </aside>

      {/* Main Chat Area */}
      <section className="flex flex-1 flex-col bg-zinc-950">
        {/* Top bar with quick brain/model switcher */}
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 px-5 py-3">
          <div className="flex items-center gap-2">
            {/* Unified Model Selector */}
            <select
              value={isCustomModel || model === "" ? `__custom__::${provider}` : `${provider}::${model}`}
              onChange={(e) => {
                const val = e.target.value;
                if (val.startsWith("__custom__")) {
                  const p = val.split("::")[1] || provider;
                  setProvider(p);
                  setModel("");
                } else {
                  const idx = val.indexOf("::");
                  if (idx !== -1) {
                    setProvider(val.slice(0, idx));
                    setModel(val.slice(idx + 2));
                  }
                }
              }}
              title="Выберите модель ИИ"
              className="max-w-[340px] truncate rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-1.5 font-mono text-xs font-medium text-zinc-100 outline-none transition hover:border-zinc-700 focus:border-amber-500"
            >
              {selectableProviders.map((p) => {
                const icon =
                  p.id === "local-llama" ? "🖥️" :
                  p.id === "openrouter" ? "🌐" :
                  p.id === "gemini-api" ? "✨" :
                  p.id === "antigravity-cli" ? "⚡" :
                  p.id === "codex-cli" ? "💻" :
                  p.id === "perplexity" ? "🔍" :
                  p.id === "deepseek" ? "🐋" :
                  p.id === "moonshot" ? "🌙" :
                  p.id === "dashscope" ? "👑" : "🤖";
                const status = p.ready ? "● Готов" : "○ Setup needed";
                const shortProvider =
                  p.id === "antigravity-cli" ? "Gemini (Antigravity)" :
                  p.id === "codex-cli" ? "ChatGPT (Codex)" :
                  p.id === "gemini-api" ? "Google Gemini" :
                  p.id === "local-llama" ? "Local Llama" :
                  p.id === "perplexity" ? "Perplexity" :
                  p.id === "deepseek" ? "DeepSeek" :
                  p.id === "moonshot" ? "Kimi" :
                  p.id === "dashscope" ? "Qwen" :
                  "OpenRouter";
                return (
                  <optgroup key={p.id} label={`${icon} ${p.label} [${status}]`}>
                    {p.models.map((m) => (
                      <option key={`${p.id}::${m}`} value={`${p.id}::${m}`}>
                        {icon} {shortProvider}: {m}
                      </option>
                    ))}
                  </optgroup>
                );
              })}
              <optgroup label="✎ Другое">
                <option value={`__custom__::${provider}`}>✎ Custom model / Ввести вручную...</option>
              </optgroup>
            </select>

            {/* Active Provider Status Badge */}
            <span
              className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium ${
                activeProviderSpec?.ready
                  ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                  : "border-zinc-800 bg-zinc-900 text-zinc-400"
              }`}
              title={activeProviderSpec?.detail || (activeProviderSpec?.ready ? "Ready" : "Setup needed")}
            >
              <span className={`h-1.5 w-1.5 rounded-full ${activeProviderSpec?.ready ? "bg-emerald-400" : "bg-zinc-500"}`} />
              <span className="max-w-[130px] truncate">{activeProviderSpec?.label.split(" ")[0] ?? provider}</span>
            </span>

            {(isCustomModel || model === "") && (
              <div className="flex items-center gap-1.5">
                <input
                  type="text"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder="vendor/model-name:free"
                  className="w-56 rounded-lg border border-amber-500/50 bg-zinc-950 px-2.5 py-1.5 font-mono text-xs text-zinc-100 outline-none focus:border-amber-500"
                />
                <select
                  value={provider}
                  onChange={(e) => setProvider(e.target.value)}
                  className="rounded-lg border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-xs text-zinc-300 outline-none focus:border-amber-500"
                >
                  {selectableProviders.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </div>
            )}

            <select
              value={runMode}
              onChange={(e) => setRunMode(e.target.value as "suggest" | "auto" | "full" | "consensus" | "debate")}
              title="Режим работы и координации моделей"
              className={`rounded-lg border px-2.5 py-1.5 text-xs font-medium outline-none transition ${
                runMode === "consensus"
                  ? "border-emerald-500/50 bg-emerald-950/40 text-emerald-300"
                  : runMode === "debate"
                  ? "border-purple-500/50 bg-purple-950/40 text-purple-300"
                  : "border-zinc-800 bg-zinc-900 text-amber-300 focus:border-amber-500"
              }`}
            >
              <option value="full">Mode: ⚡ full (MCP tools)</option>
              <option value="auto">Mode: 🛡️ auto (safe)</option>
              <option value="suggest">Mode: 👁️ suggest (readonly)</option>
              <option value="consensus">Mode: 🤝 Consensus (Multi-AI)</option>
              <option value="debate">Mode: ⚔️ Debate (Multi-AI)</option>
            </select>
          </div>

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => {
                const next = !autoSpeak;
                setAutoSpeak(next);
                try {
                  localStorage.setItem("jarvis_chat_auto_speak", String(next));
                } catch {}
              }}
              title={autoSpeak ? "Auto-Speak enabled (Jarvis reads replies aloud)" : "Auto-Speak disabled"}
              className={`flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition ${
                autoSpeak
                  ? "border-amber-500/50 bg-amber-500/15 text-amber-300"
                  : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-zinc-200"
              }`}
            >
              <Volume2 className={`h-3.5 w-3.5 ${autoSpeak ? "text-amber-400" : "text-zinc-500"}`} />
              <span>{t("chat.autoSpeak")}</span>
            </button>

            <button
              type="button"
              disabled={!wakeWordSupported}
              onClick={() => {
                const next = !wakeWordEnabled;
                setWakeWordEnabled(next);
                try {
                  localStorage.setItem("jarvis_wake_word_enabled", String(next));
                } catch {}
              }}
              title={
                !wakeWordSupported
                  ? "Браузер не поддерживает распознавание речи"
                  : wakeWordEnabled
                  ? "Ожидание фразы 'Привет Jarvis!' активно (нажмите для выключения)"
                  : "Включить вызов голосовой фразой 'Привет Jarvis!'"
              }
              className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition ${
                wakeWordEnabled
                  ? "border-emerald-500/50 bg-emerald-500/15 text-emerald-300"
                  : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:text-zinc-200"
              } disabled:opacity-50`}
            >
              <span
                className={`h-2 w-2 rounded-full ${
                  wakeWordListening
                    ? "bg-emerald-400 animate-ping"
                    : wakeWordEnabled
                    ? "bg-emerald-400"
                    : "bg-zinc-600"
                }`}
              />
              <span>{wakeWordEnabled ? "👂 'Привет Jarvis!'" : "Команда 'Привет Jarvis!'"}</span>
            </button>

            <button
              type="button"
              onClick={() => {
                setInitialVoiceUtterance("");
                setVoiceCallOpen(true);
              }}
              title="Начать живой голосовой диалог с Джарвисом в реальном времени"
              className="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/50 bg-gradient-to-r from-amber-500/20 via-orange-500/20 to-amber-500/20 px-3 py-1.5 text-xs font-semibold text-amber-300 shadow-sm transition hover:from-amber-500/30 hover:to-orange-500/30 hover:border-amber-400 focus:outline-none"
            >
              <Radio className="h-3.5 w-3.5 text-amber-400 animate-pulse" />
              <span>{t("chat.voiceCall") || "Общение голосом"}</span>
            </button>

            <span className="flex items-center gap-1.5 text-xs text-zinc-400">
              <span className={`h-2 w-2 rounded-full ${wsConnected ? "bg-emerald-400" : "bg-amber-400"}`} />
              {wsConnected ? t("status.wsConnected") : t("status.wsReconnecting")}
            </span>
          </div>
        </header>

        {/* Message List */}
        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className="mx-auto max-w-3xl space-y-5">
            {messages.length === 0 && !activeTurn && (
              <div className="mt-16 rounded-2xl border border-zinc-800/80 bg-zinc-900/40 p-8 text-center">
                <Bot className="mx-auto mb-3 h-10 w-10 text-amber-400" />
                <h3 className="text-lg font-semibold text-zinc-100">{t("chat.emptyTitle")}</h3>
                <p className="mt-2 text-sm text-zinc-400">{t("chat.emptySubtitle")}</p>
              </div>
            )}

            {messages.map((m) => {
              const isUser = m.role === "user";
              return (
                <div key={m.id} className={`flex gap-3 ${isUser ? "justify-end" : "justify-start"}`}>
                  {!isUser && (
                    <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-amber-500/15 text-amber-400">
                      <Bot className="h-4 w-4" />
                    </div>
                  )}
                  <div
                    className={`max-w-[85%] rounded-2xl px-4 py-3 ${
                      isUser
                        ? "bg-amber-500/15 text-zinc-100 border border-amber-500/30"
                        : "bg-zinc-900/80 border border-zinc-800"
                    }`}
                  >
                    {!isUser && (
                      <div className="mb-1.5 flex items-center justify-between gap-2 text-[11px] font-mono text-zinc-500">
                        <span>
                          {m.provider || "jarvis"} · {m.model || "assistant"}
                        </span>
                        <button
                          type="button"
                          onClick={() => void playSpeech(m.id, m.content)}
                          title={playingMessageId === m.id ? t("chat.stopSpeech") : t("chat.playSpeech")}
                          className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] transition ${
                            playingMessageId === m.id
                              ? "bg-amber-500/20 text-amber-300 font-medium"
                              : "text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300"
                          }`}
                        >
                          {loadingAudioId === m.id ? (
                            <Loader2 className="h-3 w-3 animate-spin text-amber-400" />
                          ) : playingMessageId === m.id ? (
                            <>
                              <Square className="h-3 w-3 fill-amber-400 text-amber-400" />
                              <span>{t("chat.stop")}</span>
                            </>
                          ) : (
                            <>
                              <Volume2 className="h-3 w-3" />
                              <span>Голос</span>
                            </>
                          )}
                        </button>
                      </div>
                    )}
                    {isUser ? (
                      <MarkdownText content={m.content} />
                    ) : (
                      <MarkdownText content={m.content} />
                    )}
                  </div>
                  {isUser && (
                    <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-zinc-800 text-zinc-300">
                      <User className="h-4 w-4" />
                    </div>
                  )}
                </div>
              );
            })}

            {/* Live streaming turn */}
            {activeTurn && (
              <div className="flex gap-3">
                <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-amber-500/15 text-amber-400">
                  <Loader2 className="h-4 w-4 animate-spin" />
                </div>
                <div className="max-w-[85%] flex-1 rounded-2xl border border-amber-500/30 bg-zinc-900/80 px-4 py-3">
                  <div className="mb-1.5 flex items-center gap-2 text-[11px] font-mono text-amber-400">
                    <span>
                      {activeTurn.provider} · {activeTurn.model}
                    </span>
                  </div>
                  {activeTurn.tools.length > 0 && (
                    <div className="mb-2 flex flex-wrap gap-1.5">
                      {activeTurn.tools.map((ev, i) => (
                        <span
                          key={i}
                          className="inline-flex items-center gap-1 rounded bg-zinc-800 px-2 py-0.5 font-mono text-[11px] text-zinc-300"
                        >
                          <Wrench className="h-3 w-3 text-amber-400" />
                          {ev.tool_name}
                        </span>
                      ))}
                    </div>
                  )}
                  {activeTurn.text ? (
                    <MarkdownText content={activeTurn.text} />
                  ) : (
                    <p className="text-sm text-zinc-400">{t("chat.thinking")}</p>
                  )}
                </div>
              </div>
            )}

            {activeError && (
              <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3.5 text-sm text-rose-200">
                {activeError}
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        {/* Voice Playback Banner */}
        {playingMessageId && (
          <div className="border-t border-amber-500/30 bg-amber-500/10 px-6 py-2">
            <div className="mx-auto flex max-w-3xl items-center justify-between text-xs text-amber-300">
              <span className="flex items-center gap-2">
                <Volume2 className="h-3.5 w-3.5 animate-pulse text-amber-400" />
                <span>Озвучивание ответа (Edge TTS ru-RU-DmitryNeural)...</span>
              </span>
              <button
                type="button"
                onClick={stopSpeech}
                className="flex items-center gap-1 rounded bg-amber-500/20 px-2.5 py-1 text-xs font-medium text-amber-200 hover:bg-amber-500/30"
              >
                <Square className="h-3 w-3 fill-amber-300 text-amber-300" />
                <span>{t("chat.stopSpeech")}</span>
              </button>
            </div>
          </div>
        )}

        {/* Voice Dictation Banner */}
        {listening && (
          <div className="border-t border-rose-500/30 bg-rose-500/10 px-6 py-2">
            <div className="mx-auto flex max-w-3xl items-center justify-between text-xs text-rose-300">
              <span className="flex items-center gap-2">
                <span className="relative flex h-2.5 w-2.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-rose-400 opacity-75" />
                  <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-rose-500" />
                </span>
                <span className="font-medium">{t("chat.micListening")}</span>
              </span>
              <button
                type="button"
                onClick={handleMicToggle}
                className="rounded bg-rose-500/20 px-2.5 py-1 text-xs font-medium text-rose-200 hover:bg-rose-500/30"
              >
                {t("chat.micStop")}
              </button>
            </div>
          </div>
        )}

        {/* Composer */}
        <div
          className={`border-t border-zinc-800 bg-zinc-950 px-6 py-4 transition-colors ${
            isDragging ? "border-amber-500/60 bg-amber-500/10" : ""
          }`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {/* Drag Overlay Hint */}
          {isDragging && (
            <div className="mx-auto mb-3 max-w-3xl rounded-xl border border-dashed border-amber-500 bg-amber-500/15 py-2.5 text-center text-xs font-medium text-amber-300">
              📂 {t("chat.dropFiles")}
            </div>
          )}

          {/* Attached Files Preview Chips */}
          {attachedFiles.length > 0 && (
            <div className="mx-auto mb-3 flex max-w-3xl flex-wrap items-center gap-2 rounded-xl border border-zinc-800/80 bg-zinc-900/70 p-2.5">
              {attachedFiles.map((att) => (
                <div
                  key={att.id}
                  className={`group relative flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs text-zinc-200 shadow-sm transition ${
                    att.error
                      ? "border-rose-500/50 bg-rose-950/30 text-rose-300"
                      : "border-zinc-700/60 bg-zinc-800/80 hover:border-amber-500/50"
                  }`}
                >
                  {att.isImage && att.previewUrl ? (
                    <img
                      src={att.previewUrl}
                      alt={att.name}
                      className="h-8 w-8 rounded object-cover border border-zinc-700 shrink-0"
                    />
                  ) : (
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded bg-zinc-900 border border-zinc-700/60">
                      {getFileIcon(att.name)}
                    </div>
                  )}
                  <div className="flex flex-col min-w-0 max-w-[170px]">
                    <span className="truncate font-medium text-zinc-100" title={att.name}>
                      {att.name}
                    </span>
                    <span className="text-[10px] text-zinc-400">
                      {formatBytes(att.size)}
                      {att.uploading && " · Загрузка..."}
                      {att.error && " · Ошибка"}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => handleRemoveAttachment(att.id)}
                    className="ml-1 rounded-full p-1 text-zinc-400 hover:bg-zinc-700 hover:text-rose-400 transition"
                    title="Удалить файл"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
              <div className="ml-auto flex items-center gap-2">
                <span className="text-[11px] text-zinc-400">
                  {attachedFiles.length} {t("chat.filesAttached")}
                </span>
                <button
                  type="button"
                  onClick={handleClearAttachments}
                  className="flex items-center gap-1 rounded bg-zinc-800 px-2 py-1 text-[11px] text-zinc-400 hover:bg-zinc-700 hover:text-rose-400 transition"
                  title={t("chat.clearFiles")}
                >
                  <Trash2 className="h-3 w-3" />
                  <span>{t("chat.clearFiles")}</span>
                </button>
              </div>
            </div>
          )}

          <div className="mx-auto flex max-w-3xl items-end gap-2">
            {/* Hidden native file input */}
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.txt,.md,.json,.csv,.tsv,.py,.js,.ts,.tsx,.jsx,.html,.css,.yaml,.yml,.xml,.sh,.sql,.log,.zip"
              className="hidden"
              onChange={handleFileInputChange}
            />

            {/* Plus / Attach Files Button */}
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              title={t("chat.attachFiles")}
              className="relative flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-zinc-800 bg-zinc-900 text-zinc-300 transition hover:border-amber-500 hover:text-amber-400 hover:bg-zinc-800 active:scale-95"
            >
              <Plus className="h-5 w-5" />
              {attachedFiles.length > 0 && (
                <span className="absolute -top-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-amber-500 text-[10px] font-bold text-zinc-950">
                  {attachedFiles.length}
                </span>
              )}
            </button>

            {/* Microphone Button */}
            <button
              type="button"
              onClick={handleMicToggle}
              title={listening ? t("chat.micStop") : t("chat.micStart")}
              className={`relative flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border transition ${
                listening
                  ? "border-rose-500 bg-rose-500/20 text-rose-300 shadow-lg shadow-rose-500/20"
                  : "border-zinc-800 bg-zinc-900 text-zinc-300 hover:border-amber-500"
              }`}
            >
              {listening ? (
                <span className="relative flex h-4 w-4 items-center justify-center">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-rose-400 opacity-75" />
                  <MicOff className="relative h-4 w-4 text-rose-400" />
                </span>
              ) : (
                <Mic className="h-4 w-4" />
              )}
            </button>

            {/* Quick Live Voice Button */}
            <button
              type="button"
              onClick={() => setVoiceCallOpen(true)}
              title="Общение голосом в реальном времени (Live Voice)"
              className="relative flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-amber-500/40 bg-gradient-to-br from-amber-500/15 to-orange-500/15 text-amber-300 hover:border-amber-400 hover:from-amber-500/25 hover:to-orange-500/25 transition shadow-sm"
            >
              <Radio className="h-4 w-4 animate-pulse text-amber-400" />
            </button>

            {/* Input Textarea */}
            <textarea
              rows={2}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onPaste={handlePaste}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void handleSubmit();
                }
              }}
              placeholder={t("chat.placeholder")}
              className="flex-1 resize-none rounded-xl border border-zinc-800 bg-zinc-900 px-3.5 py-2.5 text-sm text-zinc-100 outline-none focus:border-amber-500"
            />

            {/* Send or Stop Button */}
            {activeTurn ? (
              <button
                onClick={() => onStop(selectedId)}
                className="flex h-11 items-center gap-1.5 rounded-xl bg-rose-600 px-4 text-sm font-medium text-white hover:bg-rose-500"
              >
                <Square className="h-4 w-4" />
                <span>{t("chat.stop")}</span>
              </button>
            ) : (
              <button
                onClick={() => void handleSubmit()}
                disabled={!draft.trim() && attachedFiles.length === 0}
                className="flex h-11 items-center gap-1.5 rounded-xl bg-amber-500 px-4 text-sm font-medium text-zinc-950 transition hover:bg-amber-400 disabled:opacity-40"
              >
                <Send className="h-4 w-4" />
                <span>{t("chat.send")}</span>
              </button>
            )}
          </div>
        </div>
      </section>

      {/* Interactive Voice Call Dialog */}
      <VoiceCallModal
        isOpen={voiceCallOpen}
        onClose={() => {
          setVoiceCallOpen(false);
          setInitialVoiceUtterance("");
        }}
        activeBrain={activeBrain}
        provider={provider}
        model={model}
        runMode={runMode}
        sessionId={selectedId}
        initialUtterance={initialVoiceUtterance}
        onSend={onSend}
        activeTurn={activeTurn}
        onStopTurn={onStop}
        messages={messages}
        t={t}
      />
    </div>
  );
}
