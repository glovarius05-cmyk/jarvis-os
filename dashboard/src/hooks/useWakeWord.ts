import { useCallback, useEffect, useRef, useState } from "react";

// Ascending 3-tone chime for wake word activation
export function playWakeChime() {
  try {
    const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const now = ctx.currentTime;

    const osc1 = ctx.createOscillator();
    const osc2 = ctx.createOscillator();
    const gain = ctx.createGain();

    osc1.type = "sine";
    osc2.type = "triangle";

    // C5 -> G5 -> C6 chime
    osc1.frequency.setValueAtTime(523.25, now);
    osc1.frequency.exponentialRampToValueAtTime(783.99, now + 0.1);
    osc1.frequency.exponentialRampToValueAtTime(1046.5, now + 0.22);

    osc2.frequency.setValueAtTime(261.63, now);
    osc2.frequency.exponentialRampToValueAtTime(523.25, now + 0.22);

    gain.gain.setValueAtTime(0.12, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.28);

    osc1.connect(gain);
    osc2.connect(gain);
    gain.connect(ctx.destination);

    osc1.start(now);
    osc2.start(now);
    osc1.stop(now + 0.28);
    osc2.stop(now + 0.28);
  } catch {
    // Ignore audio context autoplay restrictions
  }
}

// Wake word pattern matching: "Привет Jarvis", "Привет Джарвис", "Hey Jarvis", "Джарвис"
const WAKE_WORD_REGEX = /(?:привет|хай|салют|здравствуй|здравствуйте|эй|слушай|hey|hi|hello)?\s*[,.\s]*(?:джарвис|jarvis)/i;

interface UseWakeWordOptions {
  enabled: boolean;
  paused?: boolean; // Set to true when voice call modal is open
  onWake: (trailingUtterance?: string) => void;
  lang?: string;
}

export function useWakeWord({
  enabled,
  paused = false,
  onWake,
  lang = "ru-RU",
}: UseWakeWordOptions) {
  const [supported, setSupported] = useState(false);
  const [listening, setListening] = useState(false);
  const [lastWakeTime, setLastWakeTime] = useState<number>(0);
  const [error, setError] = useState<string | null>(null);

  const recRef = useRef<unknown>(null);
  const onWakeRef = useRef(onWake);
  onWakeRef.current = onWake;
  const isRunningRef = useRef(false);
  const cooldownRef = useRef(false);

  // Check Web Speech API support
  useEffect(() => {
    const SpeechRec =
      (window as unknown as Record<string, unknown>).SpeechRecognition ||
      (window as unknown as Record<string, unknown>).webkitSpeechRecognition;
    setSupported(typeof SpeechRec === "function");
  }, []);

  const stop = useCallback(() => {
    isRunningRef.current = false;
    setListening(false);
    const rec = recRef.current as { stop?: () => void; abort?: () => void } | null;
    if (rec) {
      try {
        if (rec.abort) rec.abort();
        else if (rec.stop) rec.stop();
      } catch {}
    }
    recRef.current = null;
  }, []);

  const start = useCallback(() => {
    if (!enabled || paused || isRunningRef.current) return;

    const SpeechRec =
      (window as unknown as Record<string, unknown>).SpeechRecognition ||
      (window as unknown as Record<string, unknown>).webkitSpeechRecognition;

    if (typeof SpeechRec !== "function") {
      setSupported(false);
      return;
    }

    try {
      const rec = new (SpeechRec as new () => {
        lang: string;
        continuous: boolean;
        interimResults: boolean;
        onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string; isFinal?: boolean }>> }) => void) | null;
        onerror: ((e: { error?: string }) => void) | null;
        onend: (() => void) | null;
        start: () => void;
        stop: () => void;
        abort: () => void;
      })();

      rec.lang = lang;
      rec.continuous = true;
      rec.interimResults = true;
      recRef.current = rec;

      rec.onresult = (ev) => {
        if (cooldownRef.current) return;

        for (let i = 0; i < ev.results.length; ++i) {
          const item = ev.results[i]?.[0];
          const transcript = item?.transcript || "";
          if (!transcript) continue;

          const match = transcript.match(WAKE_WORD_REGEX);
          if (match && typeof match.index === "number") {
            const trailing = transcript.slice(match.index + match[0].length).replace(/^[,.!?\s]+/, "").trim();

            // Trigger wake event
            cooldownRef.current = true;
            playWakeChime();
            setLastWakeTime(Date.now());
            onWakeRef.current(trailing);

            // Cooldown for 2 seconds to avoid rapid duplicate triggers
            setTimeout(() => {
              cooldownRef.current = false;
            }, 2000);
            break;
          }
        }
      };

      rec.onerror = (ev) => {
        if (ev?.error === "no-speech") {
          return;
        }
        if (ev?.error === "not-allowed") {
          setError("Микрофон заблокирован браузером для ожидания команды.");
          stop();
          return;
        }
      };

      rec.onend = () => {
        if (isRunningRef.current && !paused) {
          // Restart recognition after gentle pause (browsers terminate long continuous recognition)
          setTimeout(() => {
            if (isRunningRef.current && !paused) {
              try {
                rec.start();
              } catch {
                isRunningRef.current = false;
                setListening(false);
              }
            }
          }, 400);
        } else {
          setListening(false);
        }
      };

      rec.start();
      isRunningRef.current = true;
      setListening(true);
      setError(null);
    } catch (err) {
      console.error("Failed to start wake word listener", err);
      isRunningRef.current = false;
      setListening(false);
    }
  }, [enabled, lang, paused, stop]);

  useEffect(() => {
    if (enabled && !paused) {
      start();
    } else {
      stop();
    }
    return () => {
      stop();
    };
  }, [enabled, paused, start, stop]);

  return {
    supported,
    listening,
    error,
    lastWakeTime,
  };
}
