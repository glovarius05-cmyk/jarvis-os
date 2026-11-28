import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  AudioLines,
  Bot,
  Brain,
  Calendar,
  Check,
  Copy,
  Download,
  Ear,
  FileCode,
  FileSpreadsheet,
  FileText,
  GitBranch,
  History,
  Image as ImageIcon,
  Loader2,
  Mail,
  Mic,
  MicOff,
  PanelRightOpen,
  Plus,
  Search,
  Send,
  ShieldAlert,
  Sparkles,
  Square,
  Trash2,
  User,
  Volume2,
  X,
} from "lucide-react";
import {
  api,
  type ApprovalItem,
  type MessageItem,
  type ProviderStatus,
  type SearchHit,
  type SessionItem,
} from "../api/client";
import type { LiveTurnState } from "../hooks/useChatSocket";
import { useGeminiLive } from "../hooks/useGeminiLive";
import { getSavedVoice, isFemaleVoice, VOICE_CATALOG } from "../voiceCatalog";
import { MarkdownText } from "./MarkdownText";

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

function extractArtifactTitle(content: string): string {
  const heading = content.match(/^#{1,3}\s+(.+)$/m);
  if (heading?.[1]) return heading[1].trim().slice(0, 60);
  const firstLine = content.split("\n").find((l) => l.trim().length > 0)?.trim() ?? "";
  return firstLine.replace(/[*_`#]/g, "").slice(0, 50) || "Ответ Jarvis";
}

const QUICK_START_PROMPTS = [
  {
    id: "gmail",
    title: "Проверить почту",
    desc: "Сводка новых и непрочитанных писем за сегодня",
    prompt: "Проверь мою почту за сегодня, покажи отправителей, темы и краткое содержание.",
    color: "text-rose-400 bg-rose-500/10 border-rose-500/20",
    icon: Mail,
  },
  {
    id: "calendar",
    title: "Встречи в Календаре",
    desc: "Расписание событий и напоминаний на неделю",
    prompt: "Какие у меня запланированы встречи и события в Google Календаре на эту неделю?",
    color: "text-sky-400 bg-sky-500/10 border-sky-500/20",
    icon: Calendar,
  },
  {
    id: "github",
    title: "Репозитории GitHub",
    desc: "Статус проектов, последние репозитории и issues",
    prompt: "Покажи список моих репозиториев на GitHub и их актуальный статус.",
    color: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
    icon: GitBranch,
  },
  {
    id: "brain",
    title: "Заметки Второго мозга",
    desc: "Обзор базы знаний, задач и ключевых фактов",
    prompt: "Проанализируй мои последние заметки во Втором мозге и напомни главные текущие задачи.",
    color: "text-amber-400 bg-amber-500/10 border-amber-500/20",
    icon: Brain,
  },
];

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
  pendingApprovals?: ApprovalItem[];
  onResolveApproval?: (approvalId: string, decision: "approve" | "deny") => Promise<void>;
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
  pendingApprovals,
  onResolveApproval,
  refreshTrigger,
  t,
}: ChatViewProps) {
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [mobileSessionsOpen, setMobileSessionsOpen] = useState(false);
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [draft, setDraft] = useState("");
  const [provider, setProvider] = useState(defaultProvider);
  const [model, setModel] = useState(defaultModel);
  const [runMode, setRunMode] = useState<"suggest" | "auto" | "full" | "consensus" | "debate">("full");
  const [listening, setListening] = useState(false);
  const [qaMode, setQaMode] = useState(false);
  const [qaStatus, setQaStatus] = useState<"idle" | "listening" | "thinking" | "speaking">("idle");
  const qaModeRef = useRef(false);
  const qaStatusRef = useRef<"idle" | "listening" | "thinking" | "speaking">("idle");
  const startQaListeningRef = useRef<() => void>(() => {});
  const [wakeMode, setWakeMode] = useState<boolean>(() => {
    try {
      return localStorage.getItem("jarvis_wake_enabled") === "true";
    } catch {
      return false;
    }
  });
  const [wakeWord, setWakeWord] = useState<string>(() => {
    try {
      return localStorage.getItem("jarvis_wake_word") || "Джарвис";
    } catch {
      return "Джарвис";
    }
  });
  const [editingWakeWord, setEditingWakeWord] = useState(false);
  const [wakeHeard, setWakeHeard] = useState("");
  const [wakeError, setWakeError] = useState("");
  const [selectedVoice, setSelectedVoice] = useState<string>(() => getSavedVoice());
  const selectedVoiceRef = useRef(selectedVoice);
  selectedVoiceRef.current = selectedVoice;
  const wakeModeRef = useRef(wakeMode);
  const wakeWordRef = useRef(wakeWord);
  wakeWordRef.current = wakeWord;
  const wakeRecRef = useRef<unknown>(null);
  const selectedIdRef = useRef(selectedId);
  selectedIdRef.current = selectedId;

  const geminiLive = useGeminiLive({
    onTranscript: () => {
      const sid = selectedIdRef.current;
      if (sid) {
        void api.getSession(sid).then((d) => setMessages(d.messages)).catch(() => {});
      }
    },
  });
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
  const audioUrlRef = useRef<string | null>(null);
  const recRef = useRef<unknown>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const prevTurnRef = useRef<LiveTurnState | undefined>(undefined);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchHits, setSearchHits] = useState<SearchHit[]>([]);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const [attachedFiles, setAttachedFiles] = useState<AttachedFileItem[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [artifact, setArtifact] = useState<{ id: string | number; title: string; content: string } | null>(null);
  const [artifactTab, setArtifactTab] = useState<"preview" | "raw">("preview");
  const [copiedId, setCopiedId] = useState<string | number | null>(null);
  const [savingBrainId, setSavingBrainId] = useState<string | number | null>(null);
  const [savedBrainMsg, setSavedBrainMsg] = useState<string | null>(null);

  const handleCopyText = useCallback(async (id: string | number, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedId(id);
      window.setTimeout(() => setCopiedId((prev) => (prev === id ? null : prev)), 1800);
    } catch {
      // ignore clipboard errors
    }
  }, []);

  const handleSaveToBrain = useCallback(async (id: string | number, content: string) => {
    setSavingBrainId(id);
    setSavedBrainMsg(null);
    try {
      const title = extractArtifactTitle(content)
        .toLowerCase()
        .replace(/[^\p{L}\p{N}\s-]/gu, "")
        .replace(/\s+/g, "-")
        .slice(0, 40) || `note-${Date.now()}`;
      const path = `wiki/notes/${title}-${Date.now().toString().slice(-4)}.md`;
      await api.writeBrainFile(path, content);
      setSavedBrainMsg(`Сохранено: ${path}`);
      window.setTimeout(() => setSavedBrainMsg(null), 3500);
    } catch {
      setSavedBrainMsg("Ошибка сохранения во Второй мозг");
      window.setTimeout(() => setSavedBrainMsg(null), 3500);
    } finally {
      setSavingBrainId(null);
    }
  }, []);

  const handleQuickPrompt = async (promptText: string) => {
    let sid = selectedId;
    if (!sid) {
      const created = await api.createSession({ brain: activeBrain, title: t("chat.new") });
      sid = created.id;
      setSelectedId(sid);
      await loadSessions();
    }
    const ok = onSend(sid, promptText, provider, model, runMode);
    if (ok) {
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now(),
          session_id: sid,
          role: "user",
          content: promptText,
          provider,
          model,
          meta: {},
          created_at: Date.now() / 1000,
        },
      ]);
    }
  };

  useEffect(() => {
    setProvider(defaultProvider);
    if (defaultProvider === "openrouter" && !defaultModel.endsWith(":free")) {
      setModel("baidu/ernie-5.5-flash-preview:free");
    } else {
      setModel(defaultModel);
    }
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
  const activeApprovals = (pendingApprovals ?? []).filter(
    (a) => !a.session_id || a.session_id === selectedId || a.session_id === "default"
  );

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
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = null;
    }
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
    setPlayingMessageId(null);
    setLoadingAudioId(null);
  }, []);

  const speakBrowserFallback = (text: string, messageId: number | string, onEnded?: () => void) => {
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
      setPlayingMessageId(messageId);
      const utter = new SpeechSynthesisUtterance(text);
      utter.lang = /[а-яА-ЯёЁ]/.test(text) ? "ru-RU" : "en-US";

      const savedVoice = selectedVoiceRef.current || getSavedVoice();
      const isMale = !isFemaleVoice(savedVoice);
      const voices = window.speechSynthesis.getVoices();
      const ruVoices = voices.filter((v) => v.lang.toLowerCase().startsWith("ru"));

      if (isMale) {
        const maleVoice = ruVoices.find((v) =>
          /yuri|pavel|dmitry|александр|максим|павел|юрий|муж|male|man/i.test(v.name)
        );
        if (maleVoice) utter.voice = maleVoice;
        else if (ruVoices.length > 0) utter.voice = ruVoices[0];
        utter.pitch = 0.82;
        utter.rate = 1.0;
      } else {
        const femaleVoice = ruVoices.find((v) =>
          /milena|svetlana|katya|irina|жен|female/i.test(v.name)
        );
        if (femaleVoice) utter.voice = femaleVoice;
        else if (ruVoices.length > 0) utter.voice = ruVoices[0];
        utter.pitch = 1.05;
        utter.rate = 1.02;
      }

      utter.onend = () => {
        setPlayingMessageId(null);
        onEnded?.();
      };
      utter.onerror = () => {
        setPlayingMessageId(null);
        onEnded?.();
      };
      window.speechSynthesis.speak(utter);
    } else {
      setPlayingMessageId(null);
      onEnded?.();
    }
  };

  const playSpeech = useCallback(
    async (messageId: number | string, text: string, onEnded?: () => void) => {
      if (playingMessageId === messageId && !onEnded) {
        stopSpeech();
        return;
      }
      stopSpeech();
      setLoadingAudioId(messageId);
      try {
        const savedVoice = selectedVoiceRef.current || getSavedVoice();
        const res = await api.synthesizeTTS(text, savedVoice);
        setLoadingAudioId(null);
        if (res.audioUrl) {
          const objectUrl = res.audioUrl;
          audioUrlRef.current = objectUrl;
          const audio = new Audio(objectUrl);
          audioRef.current = audio;
          const releaseAudio = () => {
            audioRef.current = null;
            if (audioUrlRef.current === objectUrl) {
              URL.revokeObjectURL(objectUrl);
              audioUrlRef.current = null;
            }
          };
          audio.onended = () => {
            setPlayingMessageId(null);
            releaseAudio();
            onEnded?.();
          };
          audio.onerror = () => {
            releaseAudio();
            speakBrowserFallback(text, messageId, onEnded);
          };
          setPlayingMessageId(messageId);
          await audio.play().catch(() => {
            releaseAudio();
            speakBrowserFallback(text, messageId, onEnded);
          });
        } else {
          speakBrowserFallback(res.fallbackText || text, messageId, onEnded);
        }
      } catch (err) {
        console.error("TTS playback error", err);
        stopSpeech();
        onEnded?.();
      }
    },
    [playingMessageId, stopSpeech]
  );

  useEffect(() => {
    if (prevTurnRef.current && !activeTurn) {
      const finishedText = prevTurnRef.current.text;
      if (qaModeRef.current) {
        if (finishedText && finishedText.trim()) {
          qaStatusRef.current = "speaking";
          setQaStatus("speaking");
          void playSpeech("latest", finishedText, () => {
            if (qaModeRef.current) {
              startQaListeningRef.current();
            }
          });
        } else {
          startQaListeningRef.current();
        }
      } else if (autoSpeak && finishedText && finishedText.trim()) {
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

  const stopQaMode = useCallback(() => {
    qaModeRef.current = false;
    qaStatusRef.current = "idle";
    setQaMode(false);
    setQaStatus("idle");
    const rec = recRef.current as { stop?: () => void } | null;
    if (rec?.stop) {
      try {
        rec.stop();
      } catch {}
    }
    setListening(false);
    stopSpeech();
  }, [stopSpeech]);

  const startQaListening = useCallback(() => {
    if (!qaModeRef.current) return;
    stopSpeech();

    const isLocal = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
    if (!window.isSecureContext && !isLocal) {
      setDraft("Для режима Вопрос-Ответ нужен HTTPS (или localhost), чтобы браузер разрешил микрофон.");
      stopQaMode();
      return;
    }

    const SpeechRec =
      (window as unknown as Record<string, unknown>).SpeechRecognition ||
      (window as unknown as Record<string, unknown>).webkitSpeechRecognition;

    if (typeof SpeechRec !== "function") {
      setDraft("Ваш браузер не поддерживает потоковое распознавание речи (Web Speech API). Используйте Chrome/Safari или микрофон.");
      stopQaMode();
      return;
    }

    const rec = new (SpeechRec as new () => {
      lang: string;
      continuous: boolean;
      interimResults: boolean;
      onresult: ((e: { results: ArrayLike<{ isFinal?: boolean } & ArrayLike<{ transcript: string }>> }) => void) | null;
      onerror: ((e: { error?: string }) => void) | null;
      onend: (() => void) | null;
      start: () => void;
      stop: () => void;
    })();

    rec.lang = "ru-RU";
    rec.continuous = false;
    rec.interimResults = true;
    recRef.current = rec;

    const isVoiceStopPhrase = (raw: string) => {
      const norm = raw
        .toLowerCase()
        .replace(/ё/g, "е")
        .replace(/[^\p{L}\p{N}\s]/gu, " ")
        .replace(/\s+/g, " ")
        .trim();
      return [
        "закончим разговор",
        "заканчиваем разговор",
        "заверши разговор",
        "завершаем разговор",
        "завершить разговор",
        "прекратим разговор",
        "стоп разговор",
        "конец связи",
        "хватит на сегодня",
        "отбой джарвис",
        "пока джарвис",
        "stop conversation",
        "end conversation",
        "goodbye jarvis",
      ].some((p) => norm.includes(p));
    };

    let finalTranscript = "";
    let submitted = false;

    rec.onresult = (ev) => {
      let full = "";
      let hasFinal = false;
      for (let i = 0; i < ev.results.length; ++i) {
        const resItem = ev.results[i];
        const t = resItem?.[0]?.transcript || "";
        full += t;
        if (resItem?.isFinal) hasFinal = true;
      }
      finalTranscript = full.trim();
      setDraft(finalTranscript);
      if (hasFinal && finalTranscript && !submitted) {
        submitted = true;
        if (isVoiceStopPhrase(finalTranscript)) {
          setDraft("");
          stopQaMode();
          return;
        }
        try {
          rec.stop();
        } catch {}
        qaStatusRef.current = "thinking";
        setQaStatus("thinking");
        setListening(false);
        void handleSubmit(finalTranscript);
      }
    };

    rec.onerror = (ev) => {
      setListening(false);
      if (ev?.error === "not-allowed" || ev?.error === "service-not-allowed") {
        setDraft("Доступ к микрофону запрещён в настройках браузера.");
        stopQaMode();
        return;
      }
      if (qaModeRef.current && qaStatusRef.current === "listening" && ev?.error === "no-speech") {
        setTimeout(() => {
          if (qaModeRef.current && qaStatusRef.current === "listening") {
            startQaListeningRef.current();
          }
        }, 250);
      }
    };

    rec.onend = () => {
      setListening(false);
      if (!submitted && finalTranscript && qaModeRef.current) {
        submitted = true;
        if (isVoiceStopPhrase(finalTranscript)) {
          setDraft("");
          stopQaMode();
          return;
        }
        qaStatusRef.current = "thinking";
        setQaStatus("thinking");
        void handleSubmit(finalTranscript);
        return;
      }
      if (!submitted && qaModeRef.current && qaStatusRef.current === "listening") {
        setTimeout(() => {
          if (qaModeRef.current && qaStatusRef.current === "listening") {
            startQaListeningRef.current();
          }
        }, 250);
      }
    };

    try {
      qaStatusRef.current = "listening";
      setQaStatus("listening");
      setListening(true);
      rec.start();
    } catch {
      stopQaMode();
    }
  }, [stopQaMode, stopSpeech]);

  useEffect(() => {
    startQaListeningRef.current = startQaListening;
  }, [startQaListening]);

  const handleQaToggle = async () => {
    if (geminiLive.liveState !== "off") {
      geminiLive.stopLive();
      return;
    }
    if (qaMode) {
      stopQaMode();
      return;
    }
    if (listening) {
      const rec = recRef.current as { stop?: () => void } | null;
      try {
        rec?.stop?.();
      } catch {}
      setListening(false);
    }
    if (geminiLive.configured) {
      let sid = selectedId;
      if (!sid) {
        const created = await api.createSession({ brain: activeBrain, title: t("chat.new") });
        sid = created.id;
        setSelectedId(sid);
        await loadSessions();
      }
      await geminiLive.startLive(sid, selectedVoiceRef.current);
      return;
    }
    qaModeRef.current = true;
    setQaMode(true);
    startQaListening();
  };

  const playActivationChime = useCallback(() => {
    try {
      const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new AudioCtx();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(660, ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(990, ctx.currentTime + 0.11);
      gain.gain.setValueAtTime(0.08, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.16);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.17);
      osc.onended = () => void ctx.close().catch(() => {});
    } catch {}
  }, []);

  const handleQaToggleRef = useRef<() => Promise<void>>(async () => {});
  handleQaToggleRef.current = handleQaToggle;
  const liveStateRef = useRef(geminiLive.liveState);
  liveStateRef.current = geminiLive.liveState;

  const wakeRestartTimerRef = useRef<number | null>(null);
  const wakeTriggeredRef = useRef(false);
  const wasInDialogRef = useRef(false);
  const wakeFallbackStopRef = useRef<(() => void) | null>(null);
  const useWakeFallbackRef = useRef(false);

  const stopWakeRecognition = useCallback(() => {
    const rec = wakeRecRef.current as { abort?: () => void; stop?: () => void } | null;
    if (rec) {
      try {
        if (rec.abort) rec.abort();
        else if (rec.stop) rec.stop();
      } catch {}
    }
    wakeRecRef.current = null;
    if (wakeFallbackStopRef.current) {
      try {
        wakeFallbackStopRef.current();
      } catch {}
      wakeFallbackStopRef.current = null;
    }
  }, []);

  const matchWakePhrase = useCallback((rawText: string): { matched: boolean; norm: string } => {
    const norm = rawText
      .toLowerCase()
      .replace(/ё/g, "е")
      .replace(/[^\p{L}\p{N}\s]/gu, " ")
      .replace(/\s+/g, " ")
      .trim();
    const target = (wakeWordRef.current || "джарвис")
      .toLowerCase()
      .replace(/ё/g, "е")
      .replace(/[^\p{L}\p{N}\s]/gu, " ")
      .replace(/\s+/g, " ")
      .trim();
    const isDefaultJarvis = target === "джарвис" || target === "jarvis";
    const matched =
      Boolean(target && norm.includes(target)) ||
      (isDefaultJarvis &&
        (norm.includes("jarvis") ||
          norm.includes("jarwis") ||
          norm.includes("jervis") ||
          norm.includes("джарвис") ||
          norm.includes("джарвиз") ||
          norm.includes("джарвес") ||
          norm.includes("джарв")));
    return { matched, norm };
  }, []);

  const startMediaRecorderWakeFallback = useCallback(async () => {
    if (!wakeModeRef.current || qaModeRef.current || liveStateRef.current !== "off") return;
    if (wakeFallbackStopRef.current) return;
    if (!navigator.mediaDevices?.getUserMedia) {
      setWakeError("Микрофон недоступен (нужен HTTPS).");
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new AudioCtx();
      const src = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      src.connect(analyser);
      const buf = new Float32Array(analyser.fftSize);

      let stopped = false;
      let recording = false;
      let lastLoudAt = 0;
      let rafId = 0;
      let activeRecorder: MediaRecorder | null = null;

      const cleanup = () => {
        stopped = true;
        if (rafId) cancelAnimationFrame(rafId);
        if (activeRecorder && activeRecorder.state !== "inactive") {
          try {
            activeRecorder.stop();
          } catch {}
        }
        stream.getTracks().forEach((t) => t.stop());
        void ctx.close().catch(() => {});
      };
      wakeFallbackStopRef.current = cleanup;
      setWakeError("");

      const triggerClip = () => {
        if (stopped || recording || !wakeModeRef.current || wakeTriggeredRef.current) return;
        recording = true;
        const chunks: BlobPart[] = [];
        const mr = new MediaRecorder(stream);
        activeRecorder = mr;
        mr.ondataavailable = (e) => {
          if (e.data.size > 0) chunks.push(e.data);
        };
        mr.onstop = async () => {
          recording = false;
          activeRecorder = null;
          if (stopped || !wakeModeRef.current || wakeTriggeredRef.current) return;
          const blob = new Blob(chunks, { type: "audio/webm" });
          if (blob.size < 800) return;
          try {
            const res = await api.transcribeAudio(blob, "wake.webm");
            const txt = (res.text || "").trim();
            if (!txt) return;
            const { matched, norm } = matchWakePhrase(txt);
            const isHallucination = /динамичная музыка|веселая музыка|субтитры|редактор субтитров|продолжение следует|thanks for watching/.test(
              norm
            );
            if (!isHallucination) {
              setWakeHeard(norm.slice(-60));
            }
            if (matched && !wakeTriggeredRef.current) {
              wakeTriggeredRef.current = true;
              cleanup();
              wakeFallbackStopRef.current = null;
              setWakeHeard("");
              playActivationChime();
              void handleQaToggleRef.current();
            }
          } catch {}
        };
        mr.start();
        window.setTimeout(() => {
          if (mr.state !== "inactive") {
            try {
              mr.stop();
            } catch {}
          }
        }, 2200);
      };

      const tick = () => {
        if (stopped) return;
        analyser.getFloatTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
        const rms = Math.sqrt(sum / buf.length);
        const now = performance.now();
        if (rms > 0.045 && !recording && now - lastLoudAt > 350) {
          lastLoudAt = now;
          triggerClip();
        }
        rafId = requestAnimationFrame(tick);
      };
      rafId = requestAnimationFrame(tick);
    } catch (err) {
      setWakeError(err instanceof Error ? err.message : "Нет доступа к микрофону");
    }
  }, [matchWakePhrase, playActivationChime]);

  const startWakeListener = useCallback(() => {
    if (!wakeModeRef.current || qaModeRef.current || liveStateRef.current !== "off") return;
    if (wakeRestartTimerRef.current !== null) {
      window.clearTimeout(wakeRestartTimerRef.current);
      wakeRestartTimerRef.current = null;
    }
    if (wakeRecRef.current || wakeFallbackStopRef.current) return;

    const isLocal = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
    if (!window.isSecureContext && !isLocal) {
      setWakeError("Нужен HTTPS (или localhost) для доступа к микрофону.");
      wakeModeRef.current = false;
      setWakeMode(false);
      return;
    }

    const SpeechRec =
      (window as unknown as Record<string, unknown>).SpeechRecognition ||
      (window as unknown as Record<string, unknown>).webkitSpeechRecognition;

    if (typeof SpeechRec !== "function" || useWakeFallbackRef.current) {
      wakeTriggeredRef.current = false;
      void startMediaRecorderWakeFallback();
      return;
    }

    wakeTriggeredRef.current = false;

    const rec = new (SpeechRec as new () => {
      lang: string;
      continuous: boolean;
      interimResults: boolean;
      onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
      onerror: ((e: { error?: string }) => void) | null;
      onend: (() => void) | null;
      start: () => void;
      stop: () => void;
      abort?: () => void;
    })();

    const phrase = (wakeWordRef.current || "Джарвис").trim();
    rec.lang = /[а-яА-ЯёЁ]/.test(phrase) ? "ru-RU" : "en-US";
    rec.continuous = true;
    rec.interimResults = true;
    wakeRecRef.current = rec;

    rec.onresult = (ev) => {
      if (!wakeModeRef.current || wakeTriggeredRef.current) return;
      setWakeError("");
      let combined = "";
      for (let i = Math.max(0, ev.results.length - 3); i < ev.results.length; i++) {
        const t = ev.results[i]?.[0]?.transcript || "";
        combined += " " + t;
      }
      const { matched, norm } = matchWakePhrase(combined);
      if (norm) {
        setWakeHeard(norm.slice(-60));
      }

      if (matched) {
        wakeTriggeredRef.current = true;
        stopWakeRecognition();
        setWakeHeard("");
        playActivationChime();
        void handleQaToggleRef.current();
      }
    };

    rec.onerror = (ev) => {
      const code = ev?.error || "unknown";
      if (code === "not-allowed" || code === "service-not-allowed") {
        // Try MediaRecorder fallback if browser Speech service is blocked
        if (code === "service-not-allowed") {
          useWakeFallbackRef.current = true;
          stopWakeRecognition();
          void startMediaRecorderWakeFallback();
          return;
        }
        setWakeError("Доступ к микрофону запрещён в настройках браузера.");
        wakeModeRef.current = false;
        setWakeMode(false);
        stopWakeRecognition();
        return;
      }
      if (code === "network" || code === "language-not-supported" || code === "audio-capture") {
        useWakeFallbackRef.current = true;
        stopWakeRecognition();
        void startMediaRecorderWakeFallback();
        return;
      }
      if (code !== "no-speech" && code !== "aborted") {
        setWakeError(`Ошибка распознавания: ${code}`);
      }
    };

    rec.onend = () => {
      wakeRecRef.current = null;
      if (wakeModeRef.current && !wakeTriggeredRef.current && !qaModeRef.current && liveStateRef.current === "off") {
        wakeRestartTimerRef.current = window.setTimeout(() => {
          startWakeListener();
        }, 300);
      }
    };

    try {
      rec.start();
    } catch {
      wakeRecRef.current = null;
      useWakeFallbackRef.current = true;
      void startMediaRecorderWakeFallback();
    }
  }, [matchWakePhrase, playActivationChime, startMediaRecorderWakeFallback, stopWakeRecognition]);

  useEffect(() => {
    const inDialog = qaMode || geminiLive.liveState !== "off";
    if (inDialog) {
      wasInDialogRef.current = true;
    } else if (wasInDialogRef.current) {
      wasInDialogRef.current = false;
      wakeTriggeredRef.current = false;
    }

    const shouldListenWake = wakeMode && !qaMode && geminiLive.liveState === "off" && !listening;
    if (!shouldListenWake) {
      if (wakeRestartTimerRef.current !== null) {
        window.clearTimeout(wakeRestartTimerRef.current);
        wakeRestartTimerRef.current = null;
      }
      stopWakeRecognition();
      return;
    }
    if (!wakeRecRef.current && !wakeFallbackStopRef.current && !wakeTriggeredRef.current) {
      const t = window.setTimeout(() => {
        startWakeListener();
      }, 450);
      const onFirstGesture = () => {
        if (wakeModeRef.current && !wakeRecRef.current && !wakeFallbackStopRef.current && !wakeTriggeredRef.current) {
          startWakeListener();
        }
      };
      window.addEventListener("pointerdown", onFirstGesture, { once: true });
      return () => {
        window.clearTimeout(t);
        window.removeEventListener("pointerdown", onFirstGesture);
      };
    }
  }, [wakeMode, qaMode, geminiLive.liveState, listening, startWakeListener, stopWakeRecognition]);

  useEffect(() => {
    return () => {
      if (wakeRestartTimerRef.current !== null) {
        window.clearTimeout(wakeRestartTimerRef.current);
      }
      stopWakeRecognition();
    };
  }, [stopWakeRecognition]);

  const handleWakeToggle = () => {
    const next = !wakeMode;
    wakeModeRef.current = next;
    setWakeMode(next);
    try {
      localStorage.setItem("jarvis_wake_enabled", String(next));
    } catch {}
    setWakeHeard("");
    setWakeError("");
    if (next) {
      // Start synchronously inside user click gesture (required by iOS Safari / mobile browsers)
      startWakeListener();
    } else {
      stopWakeRecognition();
    }
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

  const handleSubmit = async (overrideText?: string) => {
    const text = (overrideText !== undefined ? overrideText : draft).trim();
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
  const selectableProviders = providers.filter(
    (p) => p.id !== "local-llama" && p.id !== "perplexity"
  );
  const allKnownPairs = selectableProviders.flatMap((p) => p.models.map((m) => `${p.id}::${m}`));
  const isKnown = allKnownPairs.includes(`${provider}::${model}`);
  const isCustomModel = !isKnown && model !== "";

  return (
    <div className="flex h-full min-h-0 flex-1 overflow-hidden">
      {/* Mobile Sessions Backdrop */}
      {mobileSessionsOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/60 backdrop-blur-xs md:hidden"
          onClick={() => setMobileSessionsOpen(false)}
        />
      )}

      {/* Sessions Sidebar */}
      <aside
        className={`${
          mobileSessionsOpen
            ? "fixed inset-y-0 left-0 z-50 flex w-72 bg-zinc-900/95 backdrop-blur-md"
            : "hidden md:flex w-72 bg-zinc-900/40"
        } flex-col border-r border-zinc-800`}
      >
        <div className="space-y-2 border-b border-zinc-800 p-3">
          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                setMobileSessionsOpen(false);
                void handleNewChat();
              }}
              className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-amber-500 px-3 py-2 text-sm font-medium text-zinc-950 transition hover:bg-amber-400"
            >
              <Plus className="h-4 w-4" />
              <span>{t("chat.new")}</span>
            </button>
            <button
              type="button"
              onClick={() => setMobileSessionsOpen(false)}
              className="rounded-lg border border-zinc-800 p-2 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 md:hidden"
              aria-label="Закрыть историю чатов"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
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
                    setMobileSessionsOpen(false);
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
                      onClick={() => {
                        setSelectedId(s.id);
                        setMobileSessionsOpen(false);
                      }}
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
      <section className="flex min-w-0 flex-1 flex-col bg-zinc-950">
        {/* Top bar: equal full-width controls on desktop (grid-cols-4), clean 2-row controls on mobile */}
        <header className="flex flex-col gap-1.5 border-b border-zinc-800 px-3 py-2 md:gap-2 md:px-5 md:py-3">
          <div className="flex w-full min-w-0 flex-col gap-1.5 md:grid md:grid-cols-4 md:items-center md:gap-2.5">
            {/* Column 1 (and mobile row 1): History / New on mobile + Model Selector + mobile Auto-Speak */}
            <div className="flex w-full min-w-0 items-center gap-1.5 md:contents">
              <button
                type="button"
                onClick={() => setMobileSessionsOpen(true)}
                aria-label="История чатов"
                className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-zinc-800 bg-zinc-900 text-zinc-300 hover:border-amber-500/50 md:hidden"
              >
                <History className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={() => void handleNewChat()}
                aria-label={t("chat.new")}
                className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-amber-500 text-zinc-950 hover:bg-amber-400 md:hidden"
              >
                <Plus className="h-4 w-4" />
              </button>

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
                title={activeProviderSpec?.detail || "Выберите модель ИИ"}
                className="h-9 min-w-0 flex-1 md:w-full truncate rounded-lg border border-zinc-800 bg-zinc-900 px-2.5 font-mono text-xs font-medium text-zinc-100 outline-none transition hover:border-zinc-700 focus:border-amber-500"
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
                  const visibleModels =
                    p.id === "openrouter" ? p.models.filter((m) => m.endsWith(":free")) : p.models;
                  return (
                    <optgroup key={p.id} label={`${icon} ${p.label} [${status}]`}>
                      {visibleModels.map((m) => (
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

              {/* Mobile compact Auto-Speak + WS status indicator */}
              <button
                type="button"
                onClick={() => {
                  const next = !autoSpeak;
                  setAutoSpeak(next);
                  try {
                    localStorage.setItem("jarvis_chat_auto_speak", String(next));
                  } catch {}
                }}
                title={autoSpeak ? "Auto-Speak enabled" : "Auto-Speak disabled"}
                className={`relative inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border transition md:hidden ${
                  autoSpeak
                    ? "border-amber-500/50 bg-amber-500/15 text-amber-300"
                    : "border-zinc-800 bg-zinc-900 text-zinc-400"
                }`}
              >
                <Volume2 className={`h-4 w-4 ${autoSpeak ? "text-amber-400" : "text-zinc-500"}`} />
                <span
                  className={`absolute top-1 right-1 h-1.5 w-1.5 rounded-full ${
                    wsConnected ? "bg-emerald-400" : "bg-amber-400"
                  }`}
                />
              </button>
            </div>

            {/* Columns 2-3 on desktop / row 2 on mobile (50/50 equal width) */}
            <div className="grid w-full grid-cols-2 gap-1.5 md:contents">
              <select
                value={runMode}
                onChange={(e) => setRunMode(e.target.value as "suggest" | "auto" | "full" | "consensus" | "debate")}
                title="Режим работы и координации моделей"
                className={`h-9 w-full truncate rounded-lg border px-2.5 text-xs font-medium outline-none transition ${
                  runMode === "consensus"
                    ? "border-emerald-500/50 bg-emerald-950/40 text-emerald-300"
                    : runMode === "debate"
                    ? "border-purple-500/50 bg-purple-950/40 text-purple-300"
                    : "border-zinc-800 bg-zinc-900 text-amber-300 focus:border-amber-500"
                }`}
              >
                <option value="full">⚡ Режим: Full (MCP)</option>
                <option value="auto">🛡️ Режим: Auto (Safe)</option>
                <option value="suggest">👁️ Режим: Suggest</option>
                <option value="consensus">🤝 Consensus (Multi-AI)</option>
                <option value="debate">⚔️ Debate (Multi-AI)</option>
              </select>

              <select
                value={selectedVoice}
                onChange={(e) => {
                  const v = e.target.value;
                  setSelectedVoice(v);
                  selectedVoiceRef.current = v;
                  try {
                    localStorage.setItem("jarvis_selected_voice", v);
                  } catch {}
                  void api.patchSettings({ voice: { tts_voice: v } }).catch(() => {});
                  if (geminiLive.liveState !== "off") {
                    void geminiLive.startLive(selectedIdRef.current || undefined, v);
                  }
                }}
                title="Голос Jarvis (работает и в Gemini Live, и в обычной озвучке)"
                className="h-9 w-full truncate rounded-lg border border-zinc-800 bg-zinc-900 px-2.5 text-xs font-medium text-zinc-200 outline-none transition hover:border-zinc-700 focus:border-amber-500"
              >
                {VOICE_CATALOG.map((v) => (
                  <option key={v.id} value={v.id} title={v.styleHint}>
                    🎙️ {v.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Column 4 on desktop: equal-width Auto-Speak button with live WS dot */}
            <button
              type="button"
              onClick={() => {
                const next = !autoSpeak;
                setAutoSpeak(next);
                try {
                  localStorage.setItem("jarvis_chat_auto_speak", String(next));
                } catch {}
              }}
              title={
                (autoSpeak ? "Auto-Speak enabled" : "Auto-Speak disabled") +
                ` · ${wsConnected ? t("status.wsConnected") : t("status.wsReconnecting")}`
              }
              className={`hidden md:flex h-9 w-full items-center justify-between gap-2 rounded-lg border px-3 text-xs font-medium transition ${
                autoSpeak
                  ? "border-amber-500/50 bg-amber-500/15 text-amber-300"
                  : "border-zinc-800 bg-zinc-900 text-zinc-300 hover:border-zinc-700 hover:text-zinc-100"
              }`}
            >
              <span className="flex items-center gap-1.5 truncate">
                <Volume2 className={`h-3.5 w-3.5 shrink-0 ${autoSpeak ? "text-amber-400" : "text-zinc-400"}`} />
                <span className="truncate">{t("chat.autoSpeak")}</span>
              </span>
              <span
                className={`h-2 w-2 shrink-0 rounded-full ${wsConnected ? "bg-emerald-400" : "bg-amber-400"}`}
                title={wsConnected ? t("status.wsConnected") : t("status.wsReconnecting")}
              />
            </button>
          </div>

          {(isCustomModel || model === "") && (
            <div className="flex w-full items-center gap-2">
              <input
                type="text"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="vendor/model-name:free"
                className="h-9 min-w-0 flex-1 rounded-lg border border-amber-500/50 bg-zinc-950 px-2.5 font-mono text-xs text-zinc-100 outline-none focus:border-amber-500"
              />
              <select
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                className="h-9 rounded-lg border border-zinc-800 bg-zinc-900 px-2.5 text-xs text-zinc-300 outline-none focus:border-amber-500"
              >
                {selectableProviders.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>
          )}
        </header>

        <div className="flex min-h-0 flex-1 overflow-hidden">
          <div className="flex min-w-0 flex-1 flex-col">
            {/* Message List */}
            <div className="flex-1 overflow-y-auto px-3 py-4 sm:px-6 sm:py-6">
              <div className="mx-auto max-w-3xl space-y-4 sm:space-y-5">
                {messages.length === 0 && !activeTurn && (
                  <div className="mt-3 space-y-4 sm:mt-10 sm:space-y-6">
                    <div className="rounded-2xl border border-zinc-800/80 bg-gradient-to-b from-zinc-900/70 to-zinc-950/70 p-5 text-center shadow-lg shadow-black/20 sm:p-8">
                      <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl border border-amber-500/30 bg-amber-500/10 text-amber-400">
                        <Sparkles className="h-6 w-6" />
                      </div>
                      <h3 className="text-lg font-semibold text-zinc-100">{t("chat.emptyTitle")}</h3>
                      <p className="mt-2 text-sm text-zinc-400">{t("chat.emptySubtitle")}</p>
                    </div>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      {QUICK_START_PROMPTS.map((qp) => {
                        const Icon = qp.icon;
                        return (
                          <button
                            key={qp.id}
                            type="button"
                            onClick={() => void handleQuickPrompt(qp.prompt)}
                            className="group flex items-start gap-3 rounded-xl border border-zinc-800/90 bg-zinc-900/50 p-3.5 text-left transition hover:border-amber-500/40 hover:bg-zinc-900/90"
                          >
                            <div className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border ${qp.color}`}>
                              <Icon className="h-4 w-4" />
                            </div>
                            <div className="min-w-0">
                              <div className="text-xs font-semibold text-zinc-100 group-hover:text-amber-300 transition">
                                {qp.title}
                              </div>
                              <div className="mt-0.5 text-[11px] leading-relaxed text-zinc-400">
                                {qp.desc}
                              </div>
                            </div>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}

                {messages.map((m) => {
                  const isUser = m.role === "user";
                  const isArtifactOpen = artifact?.id === m.id;
                  return (
                    <div key={m.id} className={`flex gap-2 sm:gap-3 ${isUser ? "justify-end" : "justify-start"}`}>
                      {!isUser && (
                        <div className="mt-1 hidden h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-amber-500/15 text-amber-400 sm:flex">
                          <Bot className="h-4 w-4" />
                        </div>
                      )}
                      <div
                        className={`max-w-[94%] sm:max-w-[85%] overflow-x-hidden break-words rounded-2xl px-3.5 py-2.5 sm:px-4 sm:py-3 ${
                          isUser
                            ? "bg-amber-500/15 text-zinc-100 border border-amber-500/30"
                            : "bg-zinc-900/80 border border-zinc-800"
                        }`}
                      >
                        {!isUser && (
                          <div className="mb-2 flex flex-wrap items-center justify-between gap-2 border-b border-zinc-800/70 pb-1.5 text-[11px] font-mono text-zinc-500">
                            <span className="font-sans font-medium text-zinc-400">Jarvis</span>
                            <div className="flex items-center gap-1">
                              <button
                                type="button"
                                onClick={() => void handleCopyText(m.id, m.content)}
                                className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200 transition"
                                title="Копировать текст ответа"
                              >
                                {copiedId === m.id ? (
                                  <>
                                    <Check className="h-3 w-3 text-emerald-400" />
                                    <span className="text-emerald-400">Скопировано</span>
                                  </>
                                ) : (
                                  <>
                                    <Copy className="h-3 w-3" />
                                    <span>Копировать</span>
                                  </>
                                )}
                              </button>
                              <button
                                type="button"
                                onClick={() =>
                                  setArtifact((prev) =>
                                    prev?.id === m.id
                                      ? null
                                      : { id: m.id, title: extractArtifactTitle(m.content), content: m.content }
                                  )
                                }
                                className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] transition ${
                                  isArtifactOpen
                                    ? "bg-amber-500/20 text-amber-300 font-medium"
                                    : "text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200"
                                }`}
                                title="Открыть в боковой панели артефакта"
                              >
                                <PanelRightOpen className="h-3 w-3" />
                                <span>Панель</span>
                              </button>
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
                              <button
                                type="button"
                                onClick={() => {
                                  const blob = new Blob([m.content], { type: "text/markdown;charset=utf-8" });
                                  const url = URL.createObjectURL(blob);
                                  const a = document.createElement("a");
                                  a.href = url;
                                  a.download = `jarvis_response_${m.id || Date.now()}.md`;
                                  document.body.appendChild(a);
                                  a.click();
                                  document.body.removeChild(a);
                                  URL.revokeObjectURL(url);
                                }}
                                className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300 transition"
                                title="Скачать текст ответа в .md на компьютер"
                              >
                                <Download className="h-3 w-3" />
                                <span>Скачать</span>
                              </button>
                            </div>
                          </div>
                        )}
                        <MarkdownText content={m.content} />
                      </div>
                      {isUser && (
                        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-zinc-800 text-zinc-300">
                          <User className="h-4 w-4" />
                        </div>
                      )}
                    </div>
                  );
                })}

                {/* Live streaming turn (clean output only) */}
                {activeTurn && (
                  <div className="flex gap-2 sm:gap-3">
                    <div className="mt-1 hidden h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-amber-500/15 text-amber-400 sm:flex">
                      <Loader2 className="h-4 w-4 animate-spin" />
                    </div>
                    <div className="max-w-[94%] sm:max-w-[85%] flex-1 rounded-2xl border border-amber-500/30 bg-zinc-900/80 px-3.5 py-2.5 sm:px-4 sm:py-3">
                      {activeTurn.text ? (
                        <MarkdownText content={activeTurn.text} />
                      ) : (
                        <p className="flex items-center gap-2 text-sm text-zinc-400">
                          <Loader2 className="h-3.5 w-3.5 animate-spin text-amber-400 sm:hidden" />
                          <span>{t("chat.thinking")}</span>
                        </p>
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
                <span>Озвучивание ответа ({(() => { try { return localStorage.getItem("jarvis_selected_voice") || "ru-RU-DmitryNeural"; } catch { return "ru-RU-DmitryNeural"; } })()})...</span>
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

        {/* Human-in-the-Loop Approval Gate Banner */}
        {activeApprovals.length > 0 && (
          <div className="border-t border-amber-500/40 bg-amber-500/10 px-6 py-3">
            <div className="mx-auto max-w-3xl space-y-3">
              {activeApprovals.map((req) => (
                <div key={req.approval_id} className="rounded-xl border border-amber-500/30 bg-zinc-950/90 p-3.5 shadow-lg">
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-1.5 text-xs font-semibold text-amber-300">
                      <ShieldAlert className="h-4 w-4 text-amber-400" />
                      Запрос подтверждения (Human-in-the-Loop)
                    </span>
                    <span className="rounded bg-amber-500/20 px-2 py-0.5 font-mono text-[11px] text-amber-200 border border-amber-500/30">
                      {req.tool_name}
                    </span>
                  </div>
                  <p className="mt-1.5 text-xs text-zinc-300">{req.reason}</p>
                  {req.args && Object.keys(req.args).length > 0 && (
                    <pre className="mt-2 max-h-24 overflow-auto rounded bg-zinc-900 p-2 text-[11px] font-mono text-zinc-400 border border-zinc-800">
                      {JSON.stringify(req.args, null, 2)}
                    </pre>
                  )}
                  <div className="mt-3 flex items-center justify-end gap-2">
                    <button
                      type="button"
                      onClick={() => void onResolveApproval?.(req.approval_id, "deny")}
                      className="rounded-lg border border-rose-500/40 bg-rose-500/20 px-3 py-1.5 text-xs font-medium text-rose-300 hover:bg-rose-500/30 transition"
                    >
                      Отклонить
                    </button>
                    <button
                      type="button"
                      onClick={() => void onResolveApproval?.(req.approval_id, "approve")}
                      className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-3.5 py-1.5 text-xs font-medium text-white hover:bg-emerald-500 transition shadow"
                    >
                      <Check className="h-3.5 w-3.5" />
                      Разрешить выполнение
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Composer */}
        <div
          className={`border-t border-zinc-800 bg-zinc-950 px-2.5 pt-2.5 pb-[max(0.625rem,env(safe-area-inset-bottom))] sm:px-6 sm:py-4 transition-colors ${
            isDragging ? "border-amber-500/60 bg-amber-500/10" : ""
          }`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {/* Wake Word Standby Banner */}
          {(wakeMode || wakeError) && !qaMode && geminiLive.liveState === "off" && (
            <div className="mx-auto mb-2.5 flex max-w-3xl flex-wrap items-center justify-between gap-2 rounded-xl border border-amber-500/35 bg-amber-950/25 px-3 py-2 text-xs text-amber-200">
              <div className="flex min-w-0 items-center gap-2">
                <Ear className="h-4 w-4 shrink-0 animate-pulse text-amber-400" />
                {editingWakeWord ? (
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      setEditingWakeWord(false);
                      try {
                        localStorage.setItem("jarvis_wake_word", wakeWord.trim() || "Джарвис");
                      } catch {}
                    }}
                    className="flex items-center gap-1.5"
                  >
                    <span className="text-zinc-400">Фраза:</span>
                    <input
                      autoFocus
                      value={wakeWord}
                      onChange={(e) => setWakeWord(e.target.value)}
                      onBlur={() => {
                        setEditingWakeWord(false);
                        try {
                          localStorage.setItem("jarvis_wake_word", wakeWord.trim() || "Джарвис");
                        } catch {}
                      }}
                      className="w-32 rounded border border-amber-500/50 bg-zinc-900 px-2 py-0.5 text-xs text-zinc-100 outline-none"
                    />
                  </form>
                ) : (
                  <span className="truncate font-medium">
                    Жду кодовую фразу: <strong className="text-amber-300">«{wakeWord}»</strong>
                    {wakeError ? (
                      <span className="ml-2 text-rose-300">({wakeError})</span>
                    ) : wakeHeard ? (
                      <span className="ml-2 text-zinc-400">· Слышу: «{wakeHeard}»</span>
                    ) : null}
                  </span>
                )}
              </div>
              <div className="flex shrink-0 items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => setEditingWakeWord((v) => !v)}
                  className="rounded-lg border border-zinc-700 bg-zinc-900/80 px-2 py-1 text-[11px] text-zinc-300 hover:border-amber-500/50"
                >
                  {editingWakeWord ? "Готово" : "Изменить"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    if (wakeMode) handleWakeToggle();
                    else setWakeError("");
                  }}
                  className="rounded-lg border border-rose-500/40 bg-rose-500/20 px-2 py-1 text-[11px] font-medium text-rose-200 hover:bg-rose-500/30"
                >
                  {wakeMode ? "Выкл" : "Закрыть"}
                </button>
              </div>
            </div>
          )}

          {/* Live Voice Q&A Mode Status Bar (Gemini Live or Web Speech) */}
          {(qaMode || geminiLive.liveState !== "off" || geminiLive.liveError) && (
            <div className={`mx-auto mb-2.5 flex max-w-3xl items-center justify-between gap-2 rounded-xl border px-3 py-2 text-xs ${
              geminiLive.liveError && geminiLive.liveState === "off"
                ? "border-rose-500/40 bg-rose-950/30 text-rose-200"
                : "border-emerald-500/40 bg-emerald-950/30 text-emerald-200"
            }`}>
              <div className="flex min-w-0 items-center gap-2">
                {geminiLive.liveState !== "off" || qaMode ? (
                  <span className="relative flex h-2.5 w-2.5 shrink-0">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                    <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-400" />
                  </span>
                ) : (
                  <AlertCircle className="h-4 w-4 shrink-0 text-rose-400" />
                )}
                <span className="truncate font-medium">
                  {geminiLive.liveError && geminiLive.liveState === "off"
                    ? geminiLive.liveError
                    : geminiLive.liveState === "connecting"
                    ? "Gemini Live: подключение..."
                    : geminiLive.liveState === "listening"
                    ? `Gemini Live: слушаю...${!geminiLive.toolsEnabled ? " (⚠️ без инструментов)" : ""}`
                    : geminiLive.liveState === "speaking"
                    ? "Gemini Live: Jarvis отвечает..."
                    : qaStatus === "listening"
                    ? "Режим Вопрос-Ответ: слушаю вас..."
                    : qaStatus === "thinking"
                    ? "Режим Вопрос-Ответ: Jarvis думает..."
                    : qaStatus === "speaking"
                    ? "Режим Вопрос-Ответ: Jarvis отвечает..."
                    : "Режим Вопрос-Ответ активен"}
                </span>
              </div>
              <div className="flex shrink-0 items-center gap-1.5">
                {qaMode && qaStatus === "speaking" && (
                  <button
                    type="button"
                    onClick={() => startQaListening()}
                    className="rounded-lg border border-amber-500/40 bg-amber-500/20 px-2 py-1 text-[11px] font-medium text-amber-200 hover:bg-amber-500/30"
                  >
                    Спросить ещё
                  </button>
                )}
                {(qaMode || geminiLive.liveState !== "off") && (
                  <button
                    type="button"
                    onClick={() => {
                      if (geminiLive.liveState !== "off") geminiLive.stopLive();
                      if (qaMode) stopQaMode();
                    }}
                    className="rounded-lg border border-rose-500/40 bg-rose-500/20 px-2 py-1 text-[11px] font-medium text-rose-200 hover:bg-rose-500/30"
                  >
                    Завершить
                  </button>
                )}
              </div>
            </div>
          )}

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

          <div className="mx-auto grid w-full max-w-3xl grid-cols-5 items-center gap-1.5 sm:flex sm:items-end sm:gap-2">
            {/* Hidden native file input */}
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.txt,.md,.json,.csv,.tsv,.py,.js,.ts,.tsx,.jsx,.html,.css,.yaml,.yml,.xml,.sh,.sql,.log,.zip"
              className="hidden"
              onChange={handleFileInputChange}
            />

            {/* Left Action Buttons (4 equal cells on mobile, inline flex on desktop) */}
            <div className="contents sm:order-1 sm:flex sm:items-center sm:gap-2">
              {/* Plus / Attach Files Button */}
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                title={t("chat.attachFiles")}
                className="relative flex h-10 w-full sm:h-11 sm:w-11 shrink-0 items-center justify-center rounded-xl border border-zinc-800 bg-zinc-900 text-zinc-300 transition hover:border-amber-500 hover:text-amber-400 hover:bg-zinc-800 active:scale-95"
              >
                <Plus className="h-5 w-5" />
                {attachedFiles.length > 0 && (
                  <span className="absolute -top-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-amber-500 text-[10px] font-bold text-zinc-950">
                    {attachedFiles.length}
                  </span>
                )}
              </button>

              {/* Microphone Button for Voice Dictation */}
              <button
                type="button"
                onClick={handleMicToggle}
                title={listening ? t("chat.micStop") : t("chat.micStart")}
                className={`relative flex h-10 w-full sm:h-11 sm:w-11 shrink-0 items-center justify-center rounded-xl border transition active:scale-95 ${
                  listening && !qaMode
                    ? "border-rose-500 bg-rose-500/20 text-rose-300 shadow-lg shadow-rose-500/20"
                    : "border-zinc-800 bg-zinc-900 text-zinc-300 hover:border-amber-500 hover:text-amber-400"
                }`}
              >
                {listening && !qaMode ? (
                  <span className="relative flex h-4 w-4 items-center justify-center">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-rose-400 opacity-75" />
                    <MicOff className="relative h-4 w-4 text-rose-400" />
                  </span>
                ) : (
                  <Mic className="h-5 w-5" />
                )}
              </button>

              {/* Live Voice Q&A Mode Button */}
              <button
                type="button"
                onClick={() => void handleQaToggle()}
                title={
                  qaMode || geminiLive.liveState !== "off"
                    ? "Остановить режим Вопрос-Ответ"
                    : geminiLive.configured
                    ? "Gemini Live: голосовой диалог в реальном времени"
                    : "Голосовой режим Вопрос-Ответ с Jarvis"
                }
                className={`relative flex h-10 w-full sm:h-11 sm:w-11 shrink-0 items-center justify-center rounded-xl border transition active:scale-95 ${
                  qaMode || geminiLive.liveState !== "off"
                    ? "border-emerald-500 bg-emerald-500/20 text-emerald-300 shadow-lg shadow-emerald-500/20"
                    : "border-zinc-800 bg-zinc-900 text-zinc-300 hover:border-emerald-500/60 hover:text-emerald-400"
                }`}
              >
                {(qaMode || geminiLive.liveState !== "off") && (
                  <span className="absolute -top-1 -right-1 flex h-2.5 w-2.5">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
                    <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-400" />
                  </span>
                )}
                <AudioLines className="h-5 w-5" />
              </button>

              {/* Wake Word Standby Button */}
              <button
                type="button"
                onClick={handleWakeToggle}
                title={wakeMode ? `Выключить ожидание фразы «${wakeWord}»` : `Активация по кодовой фразе «${wakeWord}»`}
                className={`relative flex h-10 w-full sm:h-11 sm:w-11 shrink-0 items-center justify-center rounded-xl border transition active:scale-95 ${
                  wakeMode
                    ? "border-amber-500 bg-amber-500/20 text-amber-300 shadow-lg shadow-amber-500/20"
                    : "border-zinc-800 bg-zinc-900 text-zinc-300 hover:border-amber-500/60 hover:text-amber-400"
                }`}
              >
                {wakeMode && (
                  <span className="absolute -top-1 -right-1 flex h-2.5 w-2.5">
                    <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-amber-400" />
                  </span>
                )}
                <Ear className="h-5 w-5" />
              </button>
            </div>

            {/* Input Textarea (full 5-col width row 2 on mobile, flex-1 middle on desktop) */}
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
              className="col-span-5 order-last w-full sm:order-2 sm:w-auto sm:min-w-0 sm:flex-1 resize-none rounded-xl border border-zinc-800 bg-zinc-900 px-3 py-2 sm:px-3.5 sm:py-2.5 text-base sm:text-sm text-zinc-100 outline-none focus:border-amber-500"
            />

            {/* Send or Stop Button (5th equal cell on mobile, right of textarea on desktop) */}
            <div className="sm:order-3 shrink-0">
              {activeTurn ? (
                <button
                  onClick={() => onStop(selectedId)}
                  title={t("chat.stop")}
                  className="flex h-10 w-full sm:h-11 sm:w-auto shrink-0 items-center justify-center gap-1.5 rounded-xl bg-rose-600 sm:px-4 text-sm font-medium text-white hover:bg-rose-500"
                >
                  <Square className="h-4 w-4 sm:h-4 sm:w-4" />
                  <span className="hidden sm:inline">{t("chat.stop")}</span>
                </button>
              ) : (
                <button
                  onClick={() => void handleSubmit()}
                  disabled={!draft.trim() && attachedFiles.length === 0}
                  title={t("chat.send")}
                  className="flex h-10 w-full sm:h-11 sm:w-auto shrink-0 items-center justify-center gap-1.5 rounded-xl bg-amber-500 sm:px-4 text-sm font-medium text-zinc-950 transition hover:bg-amber-400 disabled:opacity-40"
                >
                  <Send className="h-4 w-4 sm:h-4 sm:w-4" />
                  <span className="hidden sm:inline">{t("chat.send")}</span>
                </button>
              )}
            </div>
          </div>
        </div>
          </div>

          {/* Right Split-View Artifact Panel (LobeChat / Cherry Studio style) */}
          {artifact && (
            <aside className="fixed inset-0 z-40 md:static md:inset-auto md:z-auto flex w-full md:w-[44%] md:max-w-2xl shrink-0 flex-col border-l border-zinc-800/90 bg-zinc-900/95 md:bg-zinc-900/75 backdrop-blur-md">
              <div className="flex items-center justify-between gap-2 border-b border-zinc-800 px-4 py-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5 text-xs font-semibold text-zinc-100 truncate">
                    <FileText className="h-3.5 w-3.5 shrink-0 text-amber-400" />
                    <span className="truncate">{artifact.title}</span>
                  </div>
                  <div className="mt-0.5 text-[10px] font-mono text-zinc-500">
                    {artifact.content.length} символов · Markdown Артефакт
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => setArtifactTab(artifactTab === "preview" ? "raw" : "preview")}
                    className="rounded-md border border-zinc-700/80 bg-zinc-800/70 px-2 py-1 text-[11px] text-zinc-300 hover:bg-zinc-700 transition"
                  >
                    {artifactTab === "preview" ? "Исходник" : "Превью"}
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleCopyText(`art_${artifact.id}`, artifact.content)}
                    className="rounded-md border border-zinc-700/80 bg-zinc-800/70 p-1.5 text-zinc-300 hover:bg-zinc-700 transition"
                    title="Копировать"
                  >
                    {copiedId === `art_${artifact.id}` ? (
                      <Check className="h-3.5 w-3.5 text-emerald-400" />
                    ) : (
                      <Copy className="h-3.5 w-3.5" />
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleSaveToBrain(artifact.id, artifact.content)}
                    disabled={savingBrainId === artifact.id}
                    className="inline-flex items-center gap-1 rounded-md border border-amber-500/40 bg-amber-500/15 px-2 py-1 text-[11px] font-medium text-amber-300 hover:bg-amber-500/25 disabled:opacity-50 transition"
                    title="Сохранить заметку во Второй мозг (wiki/notes)"
                  >
                    <Brain className="h-3.5 w-3.5" />
                    <span>{savingBrainId === artifact.id ? "..." : "В мозг"}</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      const blob = new Blob([artifact.content], { type: "text/markdown;charset=utf-8" });
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement("a");
                      a.href = url;
                      a.download = `jarvis_artifact_${artifact.id}.md`;
                      document.body.appendChild(a);
                      a.click();
                      document.body.removeChild(a);
                      URL.revokeObjectURL(url);
                    }}
                    className="rounded-md border border-zinc-700/80 bg-zinc-800/70 p-1.5 text-zinc-300 hover:bg-zinc-700 transition"
                    title="Скачать .md"
                  >
                    <Download className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setArtifact(null)}
                    className="rounded-md p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition"
                    title="Закрыть панель"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
              </div>
              {savedBrainMsg && (
                <div className="border-b border-emerald-500/30 bg-emerald-500/10 px-4 py-1.5 text-[11px] text-emerald-300">
                  {savedBrainMsg}
                </div>
              )}
              <div className="flex-1 overflow-y-auto p-5">
                {artifactTab === "preview" ? (
                  <MarkdownText content={artifact.content} />
                ) : (
                  <pre className="whitespace-pre-wrap break-words rounded-xl border border-zinc-800 bg-zinc-950 p-4 font-mono text-xs text-zinc-300">
                    {artifact.content}
                  </pre>
                )}
              </div>
            </aside>
          )}
        </div>
      </section>
    </div>
  );
}
