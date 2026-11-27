import { useCallback, useEffect, useRef, useState } from "react";

export type GeminiLiveState = "off" | "connecting" | "listening" | "speaking";

interface UseGeminiLiveOptions {
  onTranscript?: () => void;
}

function downsampleTo16kPcm16(input: Float32Array, inputRate: number): ArrayBuffer {
  const targetRate = 16000;
  if (inputRate === targetRate) {
    const out = new Int16Array(input.length);
    for (let i = 0; i < input.length; i++) {
      const s = Math.max(-1, Math.min(1, input[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out.buffer;
  }
  const ratio = inputRate / targetRate;
  const newLength = Math.max(1, Math.round(input.length / ratio));
  const out = new Int16Array(newLength);
  for (let i = 0; i < newLength; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    let count = 0;
    for (let j = start; j < end; j++) {
      sum += input[j];
      count++;
    }
    const sample = count > 0 ? sum / count : input[start] ?? 0;
    const clamped = Math.max(-1, Math.min(1, sample));
    out[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
  }
  return out.buffer;
}

function arrayBufferToBase64(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i++) {
    bin += String.fromCharCode(bytes[i]);
  }
  return btoa(bin);
}

function base64ToInt16(b64: string): Int16Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) {
    bytes[i] = bin.charCodeAt(i);
  }
  return new Int16Array(bytes.buffer);
}

export function useGeminiLive({ onTranscript }: UseGeminiLiveOptions = {}) {
  const [configured, setConfigured] = useState<boolean>(false);
  const [liveState, setLiveState] = useState<GeminiLiveState>("off");
  const [liveError, setLiveError] = useState<string>("");

  const wsRef = useRef<WebSocket | null>(null);
  const captureCtxRef = useRef<AudioContext | null>(null);
  const playCtxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const procRef = useRef<ScriptProcessorNode | null>(null);
  const sourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
  const nextPlayTimeRef = useRef<number>(0);

  useEffect(() => {
    let active = true;
    fetch("/api/voice/live/status")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (active && data && typeof data.configured === "boolean") {
          setConfigured(data.configured);
        }
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);

  const clearAudioQueue = useCallback(() => {
    for (const src of sourcesRef.current) {
      try {
        src.stop();
      } catch {}
    }
    sourcesRef.current.clear();
    if (playCtxRef.current) {
      nextPlayTimeRef.current = playCtxRef.current.currentTime;
    } else {
      nextPlayTimeRef.current = 0;
    }
  }, []);

  const stopLive = useCallback(() => {
    clearAudioQueue();
    if (procRef.current) {
      try {
        procRef.current.disconnect();
      } catch {}
      procRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (captureCtxRef.current) {
      void captureCtxRef.current.close().catch(() => {});
      captureCtxRef.current = null;
    }
    if (playCtxRef.current) {
      void playCtxRef.current.close().catch(() => {});
      playCtxRef.current = null;
    }
    if (wsRef.current) {
      try {
        if (wsRef.current.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({ type: "stop" }));
        }
        wsRef.current.close();
      } catch {}
      wsRef.current = null;
    }
    setLiveState("off");
  }, [clearAudioQueue]);

  useEffect(() => {
    return () => {
      stopLive();
    };
  }, [stopLive]);

  const schedulePcmPlayback = useCallback((b64: string, sampleRate: number) => {
    const ctx = playCtxRef.current;
    if (!ctx) return;
    if (ctx.state === "suspended") {
      void ctx.resume();
    }
    const pcm = base64ToInt16(b64);
    const floats = new Float32Array(pcm.length);
    for (let i = 0; i < pcm.length; i++) {
      floats[i] = pcm[i] / 32768;
    }
    const buffer = ctx.createBuffer(1, floats.length, sampleRate);
    buffer.copyToChannel(floats, 0);

    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(ctx.destination);

    const now = ctx.currentTime;
    const startAt = Math.max(now + 0.02, nextPlayTimeRef.current);
    nextPlayTimeRef.current = startAt + buffer.duration;

    sourcesRef.current.add(src);
    setLiveState("speaking");
    src.onended = () => {
      sourcesRef.current.delete(src);
      if (sourcesRef.current.size === 0 && wsRef.current?.readyState === WebSocket.OPEN) {
        setLiveState("listening");
      }
    };
    src.start(startAt);
  }, []);

  const startLive = useCallback(
    async (sessionId?: string) => {
      stopLive();
      setLiveError("");
      setLiveState("connecting");

      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
            channelCount: 1,
          },
        });
        streamRef.current = stream;

        const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
        const captureCtx = new AudioCtx();
        const playCtx = new AudioCtx();
        captureCtxRef.current = captureCtx;
        playCtxRef.current = playCtx;
        nextPlayTimeRef.current = playCtx.currentTime;

        const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
        const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
        const ws = new WebSocket(`${proto}//${window.location.host}/ws/voice/live${qs}`);
        wsRef.current = ws;

        ws.onmessage = (ev) => {
          try {
            const msg = JSON.parse(ev.data as string);
            if (msg.type === "ready") {
              setLiveState("listening");
              // Start microphone streaming once Gemini setup is complete
              const source = captureCtx.createMediaStreamSource(stream);
              const proc = captureCtx.createScriptProcessor(4096, 1, 1);
              procRef.current = proc;
              proc.onaudioprocess = (audioEv) => {
                if (ws.readyState !== WebSocket.OPEN) return;
                const input = audioEv.inputBuffer.getChannelData(0);
                const pcm16 = downsampleTo16kPcm16(input, captureCtx.sampleRate);
                ws.send(
                  JSON.stringify({
                    type: "audio",
                    data: arrayBufferToBase64(pcm16),
                  })
                );
              };
              source.connect(proc);
              proc.connect(captureCtx.destination);
            } else if (msg.type === "audio" && msg.data) {
              schedulePcmPlayback(msg.data, Number(msg.rate) || 24000);
            } else if (msg.type === "interrupted") {
              clearAudioQueue();
              setLiveState("listening");
            } else if (msg.type === "turn_complete") {
              onTranscript?.();
            } else if (msg.type === "error") {
              setLiveError(String(msg.message || "Gemini Live error"));
              stopLive();
            }
          } catch {}
        };

        ws.onerror = () => {
          setLiveError("Ошибка WebSocket-соединения с Gemini Live.");
          stopLive();
        };

        ws.onclose = () => {
          if (wsRef.current === ws) {
            stopLive();
          }
        };
      } catch (err) {
        setLiveError(err instanceof Error ? err.message : "Нет доступа к микрофону");
        stopLive();
      }
    },
    [clearAudioQueue, onTranscript, schedulePcmPlayback, stopLive]
  );

  return {
    configured,
    liveState,
    liveError,
    startLive,
    stopLive,
  };
}
