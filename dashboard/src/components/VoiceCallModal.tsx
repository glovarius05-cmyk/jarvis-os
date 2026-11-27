import { useCallback, useEffect, useRef, useState } from "react";
import {
  Mic,
  MicOff,
  PhoneOff,
  Radio,
  RefreshCw,
  Sparkles,
  Square,
  Volume2,
  X,
} from "lucide-react";
import { api, type MessageItem } from "../api/client";
import type { LiveTurnState } from "../hooks/useChatSocket";
import { isFemaleVoice, VOICE_CATALOG } from "../voiceCatalog";

interface VoiceCallModalProps {
  isOpen: boolean;
  onClose: () => void;
  activeBrain: string;
  provider: string;
  model: string;
  runMode: string;
  sessionId: string;
  initialUtterance?: string;
  onSend: (sessionId: string, content: string, provider: string, model: string, mode: string) => boolean;
  activeTurn: LiveTurnState | undefined;
  onStopTurn: (sessionId: string) => void;
  messages: MessageItem[];
  t: (key: string) => string;
  onSessionCreated?: (id: string) => void;
}

type CallStatus = "idle" | "listening" | "thinking" | "speaking";

// Determine if utterance is just a greeting wake phrase
function isPureGreeting(text: string): boolean {
  if (!text) return true;
  const t = text.trim().toLowerCase().replace(/[.,!?;:()]/g, "").trim();
  if (!t) return true;
  const greetings = new Set([
    "привет",
    "хай",
    "салют",
    "здравствуй",
    "здравствуйте",
    "добрый день",
    "доброе утро",
    "добрый вечер",
    "доброй ночи",
    "эй",
    "слушай",
    "hello",
    "hi",
    "hey",
    "джарвис",
    "jarvis",
    "привет джарвис",
    "привет jarvis",
    "эй джарвис",
    "hey jarvis",
  ]);
  return greetings.has(t);
}

// Clean markdown syntax from text so TTS reads clean spoken prose
function cleanSpokenText(text: string): string {
  return (text || "")
    .replace(/```[\s\S]*?```/g, "")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^[-*+]\s+/gm, "")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/__([^_]+)__/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/—/g, ", ")
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

// Gentle pleasant audio chime using Web Audio API
function playChime(type: "start" | "ready") {
  try {
    const ctx = new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.onended = () => {
      void ctx.close().catch(() => {});
    };

    if (type === "start") {
      osc.frequency.setValueAtTime(440, ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(880, ctx.currentTime + 0.12);
      gain.gain.setValueAtTime(0.08, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.18);
      osc.start();
      osc.stop(ctx.currentTime + 0.18);
    } else {
      osc.frequency.setValueAtTime(587.33, ctx.currentTime);
      gain.gain.setValueAtTime(0.06, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.15);
      osc.start();
      osc.stop(ctx.currentTime + 0.15);
    }
  } catch {
    // Ignore audio context autoplay limitations
  }
}

export function VoiceCallModal({
  isOpen,
  onClose,
  activeBrain,
  provider,
  model,
  runMode,
  sessionId,
  initialUtterance,
  onSend,
  activeTurn,
  onStopTurn,
  messages,
  t,
  onSessionCreated,
}: VoiceCallModalProps) {
  const [status, setStatus] = useState<CallStatus>("idle");
  const [handsFree, setHandsFree] = useState(true);
  const [isMuted, setIsMuted] = useState(false);
  const [selectedVoice, setSelectedVoice] = useState<string>(() => {
    try {
      return localStorage.getItem("jarvis_selected_voice") || "ru-RU-DmitryNeural";
    } catch {
      return "ru-RU-DmitryNeural";
    }
  });
  const [liveTranscript, setLiveTranscript] = useState("");
  const [lastUserSpeech, setLastUserSpeech] = useState("");
  const [lastJarvisReply, setLastJarvisReply] = useState("");
  const [errorNotice, setErrorNotice] = useState<string | null>(null);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioUrlRef = useRef<string | null>(null);
  const recRef = useRef<unknown>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const resumeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSpokenTurnTextRef = useRef<string>("");
  const prevTurnRef = useRef<LiveTurnState | undefined>(undefined);
  const isSpeakingRef = useRef(false);
  const isListeningRef = useRef(false);
  const isOpenRef = useRef(isOpen);
  isOpenRef.current = isOpen;
  const handsFreeRef = useRef(handsFree);
  handsFreeRef.current = handsFree;
  const isMutedRef = useRef(isMuted);
  isMutedRef.current = isMuted;

  // Stop audio playback
  const stopAudio = useCallback(() => {
    isSpeakingRef.current = false;
    if (resumeTimerRef.current) {
      clearTimeout(resumeTimerRef.current);
      resumeTimerRef.current = null;
    }
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      audioRef.current = null;
    }
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = null;
    }
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
  }, []);

  // Stop speech recognition
  const stopListening = useCallback(() => {
    isListeningRef.current = false;
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }
    const rec = recRef.current as { stop?: () => void } | null;
    if (rec?.stop) {
      try {
        rec.stop();
      } catch {}
    }
    recRef.current = null;
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((t) => t.stop());
      mediaStreamRef.current = null;
    }
  }, []);

  // Play Jarvis speech via Edge TTS or browser fallback
  const speakReply = useCallback(
    async (rawText: string) => {
      const clean = cleanSpokenText(rawText);
      if (!clean) return;

      stopAudio();
      stopListening();
      setStatus("speaking");
      isSpeakingRef.current = true;
      setLastJarvisReply(clean);

      try {
        if (!selectedVoice.startsWith("browser")) {
          // Timeout race at 10s (Edge TTS on longer replies takes ~3-6s, so 3.5s was timing out prematurely!)
          const ttsPromise = api.synthesizeTTS(clean, selectedVoice);
          const timeoutPromise = new Promise<{ audioUrl?: string }>((resolve) =>
            setTimeout(() => resolve({}), 10000)
          );
          const res = await Promise.race([ttsPromise, timeoutPromise]);
          if (res.audioUrl && isSpeakingRef.current && isOpenRef.current) {
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
              isSpeakingRef.current = false;
              releaseAudio();
              if (isOpenRef.current && handsFreeRef.current && !isMutedRef.current) {
                // Short cooldown for echo dissipation before opening mic
                resumeTimerRef.current = setTimeout(() => {
                  resumeTimerRef.current = null;
                  if (isOpenRef.current && handsFreeRef.current && !isMutedRef.current && !isSpeakingRef.current) {
                    void startListening();
                  }
                }, 350);
              } else {
                setStatus("idle");
              }
            };

            audio.onerror = () => {
              releaseAudio();
              // Fallback to browser Web Speech API
              fallbackBrowserSpeak(clean);
            };

            try {
              await audio.play();
              return;
            } catch (playErr) {
              releaseAudio();
              console.warn("Audio play blocked/failed, using speech synthesis fallback:", playErr);
              fallbackBrowserSpeak(clean);
              return;
            }
          } else if (res.audioUrl) {
            URL.revokeObjectURL(res.audioUrl);
          }
        }

        fallbackBrowserSpeak(clean);
      } catch (err) {
        console.error("Voice TTS error:", err);
        fallbackBrowserSpeak(clean);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [isOpen, handsFree, isMuted, selectedVoice, stopAudio, stopListening]
  );

  const fallbackBrowserSpeak = (text: string) => {
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
      const utter = new SpeechSynthesisUtterance(text);
      utter.lang = /[а-яА-ЯёЁ]/.test(text) ? "ru-RU" : "en-US";

      // Choose male voice and configure pitch for authentic masculine baritone
      const isMale = !isFemaleVoice(selectedVoice);
      const voices = window.speechSynthesis.getVoices();
      const ruVoices = voices.filter((v) => v.lang.toLowerCase().startsWith("ru"));

      if (isMale) {
        const maleRuVoice = ruVoices.find((v) =>
          /yuri|pavel|dmitry|александр|максим|павел|юрий|муж|male|man/i.test(v.name)
        );
        if (maleRuVoice) {
          utter.voice = maleRuVoice;
        } else if (ruVoices.length > 0) {
          utter.voice = ruVoices[0];
        }
        // Pitch 0.82 gives a deep, calm male assistant tone
        utter.pitch = 0.82;
        utter.rate = 1.0;
      } else {
        const femaleRuVoice = ruVoices.find((v) =>
          /milena|svetlana|katya|irina|жен|female/i.test(v.name)
        );
        if (femaleRuVoice) {
          utter.voice = femaleRuVoice;
        } else if (ruVoices.length > 0) {
          utter.voice = ruVoices[0];
        }
        utter.pitch = 1.05;
        utter.rate = 1.02;
      }

      const handleSpeechDone = () => {
        isSpeakingRef.current = false;
        if (isOpenRef.current && handsFreeRef.current && !isMutedRef.current) {
          resumeTimerRef.current = setTimeout(() => {
            resumeTimerRef.current = null;
            if (isOpenRef.current && handsFreeRef.current && !isMutedRef.current && !isSpeakingRef.current) {
              void startListening();
            }
          }, 350);
        } else {
          setStatus("idle");
        }
      };
      utter.onend = handleSpeechDone;
      utter.onerror = handleSpeechDone;
      window.speechSynthesis.speak(utter);
    } else {
      isSpeakingRef.current = false;
      setStatus("idle");
    }
  };

  // Submit recognized user utterance to Jarvis
  const submitUtterance = useCallback(
    async (text: string) => {
      const clean = text.trim();
      if (!clean) return;

      stopListening();
      stopAudio();
      setStatus("thinking");
      setLastUserSpeech(clean);
      setLiveTranscript("");

      let sid = sessionId;
      if (!sid) {
        try {
          const created = await api.createSession({ brain: activeBrain, title: "Голосовой диалог" });
          sid = created.id;
          if (onSessionCreated) {
            onSessionCreated(sid);
          }
        } catch (err) {
          console.error("Failed to create voice session", err);
        }
      }

      const ok = onSend(sid, clean, provider, model, runMode);
      if (!ok) {
        setErrorNotice("Не удалось отправить сообщение. Проверьте подключение к серверу.");
        setStatus("idle");
      }
    },
    [activeBrain, model, onSend, onSessionCreated, provider, runMode, sessionId, stopAudio, stopListening]
  );

  // Start microphone listening
  const startListening = useCallback(async () => {
    if (isSpeakingRef.current || isMuted) return;

    stopAudio();
    stopListening();
    setErrorNotice(null);
    setLiveTranscript("");
    setStatus("listening");
    isListeningRef.current = true;
    playChime("start");

    const SpeechRec =
      (window as unknown as Record<string, unknown>).SpeechRecognition ||
      (window as unknown as Record<string, unknown>).webkitSpeechRecognition;

    if (typeof SpeechRec === "function") {
      const rec = new (SpeechRec as new () => {
        lang: string;
        continuous: boolean;
        interimResults: boolean;
        onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string; isFinal?: boolean }>> }) => void) | null;
        onerror: ((e: { error?: string }) => void) | null;
        onend: (() => void) | null;
        start: () => void;
        stop: () => void;
      })();

      rec.lang = "ru-RU";
      rec.continuous = true;
      rec.interimResults = true;
      recRef.current = rec;

      rec.onresult = (ev) => {
        if (!isListeningRef.current) return;
        let interim = "";
        let finalChunk = "";

        for (let i = 0; i < ev.results.length; ++i) {
          const item = ev.results[i]?.[0];
          const isFinal = Boolean((ev.results[i] as unknown as { isFinal?: boolean })?.isFinal);
          if (item?.transcript) {
            if (isFinal) {
              finalChunk += ` ${item.transcript}`;
            } else {
              interim += item.transcript;
            }
          }
        }

        const currentText = (finalChunk || interim).trim();
        if (currentText) {
          setLiveTranscript(currentText);

          // Reset silence timer on new speech activity
          if (silenceTimerRef.current) {
            clearTimeout(silenceTimerRef.current);
          }

          if (handsFree) {
            // Auto-send after 1.3s of silence
            silenceTimerRef.current = setTimeout(() => {
              if (isListeningRef.current && currentText.trim()) {
                submitUtterance(currentText.trim());
              }
            }, 1300);
          }
        }
      };

      rec.onerror = (ev) => {
        if (ev?.error === "no-speech") {
          return;
        }
        if (ev?.error === "not-allowed") {
          setErrorNotice("Доступ к микрофону заблокирован браузером. Разрешите микрофон в настройках браузера.");
          setStatus("idle");
          isListeningRef.current = false;
          return;
        }
        // Fallback to media recorder
        void startMediaRecorderSTT();
      };

      rec.onend = () => {
        if (isListeningRef.current && status === "listening" && !isSpeakingRef.current && isOpen) {
          try {
            rec.start();
          } catch {
            isListeningRef.current = false;
          }
        }
      };

      try {
        rec.start();
        return;
      } catch {
        void startMediaRecorderSTT();
        return;
      }
    }

    await startMediaRecorderSTT();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handsFree, isMuted, isOpen, status, stopAudio, stopListening, submitUtterance]);

  // MediaRecorder STT Fallback
  const startMediaRecorderSTT = async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      setErrorNotice("Браузер не поддерживает запись аудио в этом режиме.");
      setStatus("idle");
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
        if (!isListeningRef.current) return;

        const blob = new Blob(chunks, { type: "audio/webm" });
        if (blob.size < 500) {
          setStatus("idle");
          return;
        }

        setStatus("thinking");
        try {
          const res = await api.transcribeAudio(blob, "voice.webm");
          if (res.text?.trim()) {
            submitUtterance(res.text.trim());
          } else {
            setStatus("idle");
          }
        } catch {
          setErrorNotice("Не удалось распознать голос через серверный STT.");
          setStatus("idle");
        }
      };

      mediaRecorder.start();
      recRef.current = mediaRecorder;
      setStatus("listening");
      isListeningRef.current = true;
    } catch {
      setErrorNotice("Не удалось получить доступ к микрофону. Проверьте разрешения браузера.");
      setStatus("idle");
      isListeningRef.current = false;
    }
  };

  // Keep track of activeTurn and detect when it finishes to speak it aloud
  useEffect(() => {
    if (!isOpen) {
      prevTurnRef.current = undefined;
      return;
    }

    if (activeTurn) {
      // Turn is actively streaming
      prevTurnRef.current = activeTurn;
      if (status !== "thinking") {
        setStatus("thinking");
      }
    } else if (prevTurnRef.current) {
      // activeTurn just transitioned from streaming to completed!
      const finishedTurn = prevTurnRef.current;
      prevTurnRef.current = undefined;

      const replyText = finishedTurn.text?.trim();
      if (replyText && replyText !== lastSpokenTurnTextRef.current) {
        lastSpokenTurnTextRef.current = replyText;
        void speakReply(replyText);
      } else {
        // Fallback: check messages
        const lastMsg = [...messages].reverse().find((m) => m.role === "assistant");
        if (lastMsg && lastMsg.content && lastMsg.content !== lastSpokenTurnTextRef.current) {
          lastSpokenTurnTextRef.current = lastMsg.content;
          void speakReply(lastMsg.content);
        } else {
          setStatus("idle");
        }
      }
    }
  }, [activeTurn, isOpen, messages, speakReply, status]);

  // On open: initialize and start listening or speak greeting or submit initial utterance
  useEffect(() => {
    if (isOpen) {
      setStatus("idle");
      setErrorNotice(null);
      setLiveTranscript("");
      lastSpokenTurnTextRef.current = "";
      prevTurnRef.current = undefined;

      const trimmed = (initialUtterance || "").trim();
      if (trimmed && !isPureGreeting(trimmed)) {
        // User asked a specific question with wake word, e.g. "Привет Jarvis, какая сейчас погода?"
        const tId = setTimeout(() => {
          void submitUtterance(trimmed);
        }, 300);
        return () => clearTimeout(tId);
      } else {
        // User just said "Привет Jarvis!" or opened voice modal: respond aloud with greeting!
        // Once playback finishes, speakReply automatically opens mic in hands-free listening mode.
        const tId = setTimeout(() => {
          void speakReply("Привет! Я на связи, слушаю вас.");
        }, 300);
        return () => clearTimeout(tId);
      }
    } else {
      stopAudio();
      stopListening();
      setStatus("idle");
    }
  }, [initialUtterance, isOpen, speakReply, stopAudio, stopListening, submitUtterance]);

  // Interrupt button handler: cuts Jarvis off and listens
  const handleInterrupt = () => {
    stopAudio();
    if (activeTurn) {
      onStopTurn(sessionId);
    }
    void startListening();
  };

  // Manual send in push-to-talk mode
  const handleManualSend = () => {
    if (liveTranscript.trim()) {
      submitUtterance(liveTranscript.trim());
    } else if (status === "listening") {
      stopListening();
      setStatus("idle");
    } else {
      void startListening();
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-md p-4 animate-in fade-in duration-200">
      <div className="relative flex flex-col w-full max-w-xl rounded-2xl border border-zinc-800 bg-zinc-950/95 shadow-2xl overflow-hidden">
        {/* Subtle glowing ambient accent */}
        <div className="pointer-events-none absolute -top-32 left-1/2 -translate-x-1/2 h-64 w-96 rounded-full bg-amber-500/15 blur-3xl" />

        {/* Header Bar */}
        <div className="relative flex items-center justify-between border-b border-zinc-800/80 px-6 py-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-amber-500/40 bg-amber-500/15 text-amber-400">
              <Radio className="h-4 w-4 animate-pulse" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-zinc-100">{t("chat.voiceCallTitle") || "Общение голосом с Jarvis"}</h3>
                <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium text-amber-300 border border-amber-500/30">
                  <Sparkles className="h-2.5 w-2.5" /> Live Voice
                </span>
              </div>
              <p className="text-[11px] text-zinc-400 font-mono">
                {provider} · {model} · {activeBrain}
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 transition"
            title="Закрыть голосовой диалог"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Center Visualizer & Status */}
        <div className="relative flex flex-col items-center justify-center px-6 py-10">
          {/* Animated Glowing Voice Orb */}
          <div className="relative flex items-center justify-center my-6">
            {/* Concentric pulsing rings */}
            {status === "listening" && (
              <>
                <div className="absolute h-44 w-44 rounded-full border border-emerald-500/30 bg-emerald-500/5 animate-ping duration-1000" />
                <div className="absolute h-36 w-36 rounded-full border border-emerald-400/40 bg-emerald-500/10 animate-pulse" />
              </>
            )}
            {status === "speaking" && (
              <>
                <div className="absolute h-44 w-44 rounded-full border border-amber-500/30 bg-amber-500/5 animate-ping duration-700" />
                <div className="absolute h-36 w-36 rounded-full border border-amber-400/40 bg-amber-500/10 animate-pulse" />
              </>
            )}
            {status === "thinking" && (
              <div className="absolute h-36 w-36 rounded-full border-2 border-dashed border-amber-400/50 animate-spin duration-3000" />
            )}

            {/* Central Interactive Orb Button */}
            <button
              type="button"
              onClick={() => {
                if (status === "speaking") {
                  handleInterrupt();
                } else if (status === "listening") {
                  handleManualSend();
                } else {
                  void startListening();
                }
              }}
              title={status === "speaking" ? "Перебить речь" : status === "listening" ? "Отправить фразу" : "Начать говорить"}
              className={`relative z-10 flex h-24 w-24 items-center justify-center rounded-full border transition-all duration-300 shadow-lg ${
                status === "listening"
                  ? "border-emerald-400 bg-gradient-to-br from-emerald-500 to-teal-600 text-zinc-950 shadow-emerald-500/30 scale-105"
                  : status === "speaking"
                  ? "border-amber-400 bg-gradient-to-br from-amber-500 to-orange-600 text-zinc-950 shadow-amber-500/30 animate-pulse"
                  : status === "thinking"
                  ? "border-amber-500/50 bg-zinc-900 text-amber-300"
                  : "border-zinc-700 bg-zinc-900 text-zinc-300 hover:border-amber-400 hover:text-amber-300"
              }`}
            >
              {status === "listening" && <Mic className="h-9 w-9 animate-bounce" />}
              {status === "speaking" && <Volume2 className="h-9 w-9" />}
              {status === "thinking" && <RefreshCw className="h-8 w-8 animate-spin text-amber-400" />}
              {status === "idle" && <Mic className="h-8 w-8" />}
            </button>
          </div>

          {/* Spoken Equalizer Animation Bars */}
          {status === "speaking" && (
            <div className="flex items-center justify-center gap-1.5 h-6 mb-3">
              {[40, 75, 55, 95, 60, 85, 45, 90, 65, 50].map((h, i) => (
                <div
                  key={i}
                  className="w-1 bg-amber-400 rounded-full animate-pulse"
                  style={{
                    height: `${h}%`,
                    animationDuration: `${0.4 + (i % 4) * 0.15}s`,
                    animationDelay: `${i * 0.05}s`,
                  }}
                />
              ))}
            </div>
          )}

          {/* Status Label */}
          <div className="text-center space-y-1">
            <p className="text-sm font-semibold tracking-wide">
              {status === "listening" && <span className="text-emerald-400">● Слушаю вас... говорите свободно</span>}
              {status === "thinking" && <span className="text-amber-400 animate-pulse">● Джарвис думает и готовит ответ...</span>}
              {status === "speaking" && <span className="text-amber-300">● Джарвис говорит...</span>}
              {status === "idle" && <span className="text-zinc-400">Готов к разговору. Нажмите микрофон.</span>}
            </p>
            <p className="text-xs text-zinc-500 max-w-sm">
              {status === "listening"
                ? handsFree
                  ? "Автоматическая отправка после паузы в речи"
                  : "Нажмите на круг или кнопку 'Отправить', когда закончите"
                : status === "speaking"
                ? "Нажмите 'Перебить', чтобы прервать речь в любой момент"
                : ""}
            </p>
          </div>

          {/* Live Subtitles Box */}
          <div className="mt-5 w-full rounded-xl border border-zinc-800/80 bg-zinc-900/60 p-4 space-y-3 min-h-[110px] max-h-48 overflow-y-auto">
            {liveTranscript ? (
              <div>
                <span className="text-[10px] font-semibold text-emerald-400 uppercase tracking-wider">Вы говорите:</span>
                <p className="mt-0.5 text-xs text-zinc-100 font-medium italic">"{liveTranscript}"</p>
              </div>
            ) : lastUserSpeech && status !== "listening" ? (
              <div>
                <span className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider">Вы сказали:</span>
                <p className="mt-0.5 text-xs text-zinc-300 font-medium">"{lastUserSpeech}"</p>
              </div>
            ) : null}

            {lastJarvisReply ? (
              <div className="pt-2 border-t border-zinc-800/50">
                <span className="text-[10px] font-semibold text-amber-400 uppercase tracking-wider">Джарвис:</span>
                <p className="mt-0.5 text-xs text-zinc-200 leading-relaxed">{lastJarvisReply}</p>
              </div>
            ) : !liveTranscript && !lastUserSpeech ? (
              <div className="flex h-full min-h-[70px] items-center justify-center text-center text-xs text-zinc-500 italic">
                {t("chat.voiceCallSubtitle") || "Говорите свободно в микрофон — Jarvis ответит вам естественным голосом."}
              </div>
            ) : null}
          </div>

          {errorNotice && (
            <div className="mt-3 w-full rounded-lg bg-rose-500/10 border border-rose-500/20 px-3 py-2 text-xs text-rose-400 text-center">
              {errorNotice}
            </div>
          )}
        </div>

        {/* Controls & Preferences Footer */}
        <div className="border-t border-zinc-800 bg-zinc-900/80 px-6 py-4 space-y-3">
          {/* Main Controls Row */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              {/* Hands-free vs Push-to-Talk Toggle */}
              <button
                type="button"
                onClick={() => setHandsFree((prev) => !prev)}
                className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition ${
                  handsFree
                    ? "border-amber-500/50 bg-amber-500/15 text-amber-300"
                    : "border-zinc-700 bg-zinc-800 text-zinc-400"
                }`}
                title="Авто-отправка после паузы в речи"
              >
                <span>{handsFree ? "⚡ Hands-Free диалог" : "По нажатию"}</span>
              </button>

              {/* Voice Selector */}
              <select
                value={selectedVoice}
                onChange={(e) => {
                  const val = e.target.value;
                  setSelectedVoice(val);
                  try {
                    localStorage.setItem("jarvis_selected_voice", val);
                  } catch {}
                  void api.patchSettings({ voice: { tts_voice: val, tts_rate: "+5%" } });
                }}
                className="rounded-lg border border-zinc-700 bg-zinc-800 px-2.5 py-1.5 text-xs text-zinc-300 outline-none focus:border-amber-500"
                title="Выбор голоса синтеза речи"
              >
                {VOICE_CATALOG.map((v) => (
                  <option key={v.id} value={v.id} title={v.styleHint}>
                    {v.label}
                  </option>
                ))}
                <option value="browser-male">Голос: Мужской баритон (Браузер)</option>
                <option value="browser-female">Голос: Женский (Браузер)</option>
              </select>
            </div>

            <div className="flex items-center gap-2">
              {/* Interrupt / Stop Button */}
              {status === "speaking" && (
                <button
                  type="button"
                  onClick={handleInterrupt}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/15 px-3 py-1.5 text-xs font-medium text-amber-300 hover:bg-amber-500/25 transition"
                >
                  <Square className="h-3.5 w-3.5" />
                  <span>Перебить</span>
                </button>
              )}

              {/* Mic Mute Toggle */}
              <button
                type="button"
                onClick={() => {
                  const nextMute = !isMuted;
                  setIsMuted(nextMute);
                  if (nextMute) {
                    stopListening();
                    setStatus("idle");
                  } else {
                    void startListening();
                  }
                }}
                className={`rounded-lg border p-2 text-xs transition ${
                  isMuted
                    ? "border-rose-500/50 bg-rose-500/15 text-rose-400"
                    : "border-zinc-700 bg-zinc-800 text-zinc-300 hover:bg-zinc-700"
                }`}
                title={isMuted ? "Включить микрофон" : "Отключить микрофон"}
              >
                {isMuted ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
              </button>

              {/* End Call Button */}
              <button
                type="button"
                onClick={onClose}
                className="inline-flex items-center gap-1.5 rounded-lg border border-rose-500/50 bg-rose-500/20 px-3.5 py-1.5 text-xs font-semibold text-rose-300 hover:bg-rose-500/30 transition shadow-sm"
              >
                <PhoneOff className="h-3.5 w-3.5" />
                <span>Завершить звонок</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
