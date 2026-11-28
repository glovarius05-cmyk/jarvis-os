import { Ionicons } from "@expo/vector-icons";
import {
  RecordingPresets,
  requestRecordingPermissionsAsync,
  setAudioModeAsync,
  useAudioRecorder,
  useAudioStream,
} from "expo-audio";
import * as Haptics from "expo-haptics";
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import type { JarvisApiClient } from "../api/client";
import { COLORS } from "../constants/theme";
import { VOICE_CATALOG } from "../constants/voices";
import { useVoicePlayer } from "../hooks/useVoicePlayer";
import type { VoiceLiveWsEvent } from "../types/api";
import { base64ToBytes, bytesToBase64, pcm16ChunksToWavBase64, resampleInt16To16k } from "../utils/pcmWav";

interface Props {
  client: JarvisApiClient;
  activeBrain: string;
  sessionId: string;
  selectedVoice: string;
  onSelectVoice: (voiceId: string) => void;
}

interface TranscriptLine {
  id: string;
  role: "user" | "assistant" | "tool" | "system";
  text: string;
}

export function VoiceScreen({ client, activeBrain, sessionId, selectedVoice, onSelectVoice }: Props) {
  const [mode, setMode] = useState<"live" | "turn">("live");
  const [liveConfigured, setLiveConfigured] = useState<boolean | null>(null);
  const [liveStatus, setLiveStatus] = useState<"idle" | "connecting" | "connected">("idle");
  const [muted, setMuted] = useState(false);
  const [voicePickerOpen, setVoicePickerOpen] = useState(false);
  const [lines, setLines] = useState<TranscriptLine[]>([]);
  const [textInput, setTextInput] = useState("");
  const [turnBusy, setTurnBusy] = useState(false);
  const [turnRecording, setTurnRecording] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const mutedRef = useRef(false);
  mutedRef.current = muted;
  const pcmBufferRef = useRef<{ chunks: string[]; rate: number }>({ chunks: [], rate: 24000 });
  const playbackChainRef = useRef<Promise<void>>(Promise.resolve());

  const { speakingId, speakText, playAudioBytes, stop: stopPlayback } = useVoicePlayer(client);
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);

  const appendLine = useCallback((role: TranscriptLine["role"], text: string) => {
    const clean = text.trim();
    if (!clean) return;
    setLines((prev) => {
      const last = prev[prev.length - 1];
      if (last && (role === "user" || role === "assistant") && last.role === role) {
        const updated = [...prev];
        updated[updated.length - 1] = { ...last, text: `${last.text} ${clean}`.replace(/\s+/g, " ") };
        return updated;
      }
      return [...prev.slice(-40), { id: `${Date.now()}_${Math.random()}`, role, text: clean }];
    });
  }, []);

  const flushLiveAudio = useCallback(() => {
    const { chunks, rate } = pcmBufferRef.current;
    if (chunks.length === 0) return;
    const batch = [...chunks];
    pcmBufferRef.current.chunks = [];
    playbackChainRef.current = playbackChainRef.current.then(async () => {
      try {
        const wavB64 = pcm16ChunksToWavBase64(batch, rate);
        const wavBytes = base64ToBytes(wavB64);
        await playAudioBytes(wavBytes, `live_${Date.now()}.wav`, "gemini-live");
      } catch {}
    });
  }, [playAudioBytes]);

  const { stream: micStream } = useAudioStream({
    sampleRate: 16000,
    channels: 1,
    encoding: "int16",
    onBuffer: (buf) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN || mutedRef.current) return;
      const raw = new Int16Array(buf.data);
      const pcm16 = resampleInt16To16k(raw, buf.sampleRate || 16000);
      const b64 = bytesToBase64(new Uint8Array(pcm16.buffer, pcm16.byteOffset, pcm16.byteLength));
      ws.send(JSON.stringify({ type: "audio", data: b64 }));
    },
  });

  useEffect(() => {
    client
      .getVoiceLiveStatus()
      .then((s) => {
        setLiveConfigured(Boolean(s.configured));
        if (!s.configured) setMode("turn");
      })
      .catch(() => setLiveConfigured(false));
  }, [client]);

  const disconnectLive = useCallback(() => {
    try {
      micStream.stop();
    } catch {}
    try {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: "stop" }));
      }
      wsRef.current?.close();
    } catch {}
    wsRef.current = null;
    pcmBufferRef.current.chunks = [];
    stopPlayback();
    setLiveStatus("idle");
  }, [micStream, stopPlayback]);

  useEffect(() => disconnectLive, [disconnectLive]);

  const startLive = async () => {
    if (liveStatus !== "idle") {
      disconnectLive();
      return;
    }
    const perm = await requestRecordingPermissionsAsync();
    if (!perm.granted) {
      Alert.alert("Микрофон", "Разрешите доступ к микрофону для голосового диалога.");
      return;
    }
    await setAudioModeAsync({
      allowsRecording: true,
      playsInSilentMode: true,
    });

    setLiveStatus("connecting");
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    const url = client.buildWsUrl("/ws/voice/live", {
      voice: selectedVoice,
      session_id: sessionId,
    });
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onmessage = async (evt) => {
      try {
        const msg = JSON.parse(String(evt.data || "{}")) as VoiceLiveWsEvent;
        if (msg.type === "ready") {
          setLiveStatus("connected");
          appendLine("system", "Gemini Live подключён. Говорите...");
          await micStream.start();
        } else if (msg.type === "transcript" && msg.text) {
          appendLine(msg.role === "user" ? "user" : "assistant", msg.text);
        } else if (msg.type === "tool" && msg.name) {
          if (msg.status === "running") {
            appendLine("tool", `Инструмент: ${msg.name}...`);
          }
        } else if (msg.type === "audio" && msg.data) {
          pcmBufferRef.current.rate = msg.rate || 24000;
          pcmBufferRef.current.chunks.push(msg.data);
          if (pcmBufferRef.current.chunks.length >= 16) {
            flushLiveAudio();
          }
        } else if (msg.type === "turn_complete") {
          flushLiveAudio();
        } else if (msg.type === "interrupted") {
          pcmBufferRef.current.chunks = [];
          stopPlayback();
        } else if (msg.type === "end_conversation") {
          flushLiveAudio();
          disconnectLive();
        } else if (msg.type === "error") {
          appendLine("system", `Ошибка: ${msg.message || "Gemini Live error"}`);
          disconnectLive();
        }
      } catch {}
    };

    ws.onerror = () => {
      appendLine("system", "Ошибка соединения с Gemini Live");
      disconnectLive();
    };

    ws.onclose = () => {
      try {
        micStream.stop();
      } catch {}
      setLiveStatus("idle");
    };
  };

  const handleSendTextToLive = () => {
    const clean = textInput.trim();
    if (!clean) return;
    if (mode === "live" && wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "text", text: clean }));
      appendLine("user", clean);
      setTextInput("");
      return;
    }
    if (mode === "turn") {
      setTextInput("");
      runVoiceTurnText(clean);
    }
  };

  const runVoiceTurnText = async (transcript: string) => {
    setTurnBusy(true);
    appendLine("user", transcript);
    try {
      const res = await client.voiceTurn({
        transcript,
        brain: activeBrain,
        session_id: sessionId || undefined,
      });
      appendLine("assistant", res.reply);
      await speakText(res.reply, `turn_${Date.now()}`, selectedVoice);
    } catch (e) {
      appendLine("system", `Ошибка: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setTurnBusy(false);
    }
  };

  const toggleTurnRecord = async () => {
    if (turnBusy) return;
    if (!turnRecording) {
      const perm = await requestRecordingPermissionsAsync();
      if (!perm.granted) {
        Alert.alert("Микрофон", "Разрешите доступ к микрофону в настройках.");
        return;
      }
      await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
      await recorder.prepareToRecordAsync();
      recorder.record();
      setTurnRecording(true);
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
      return;
    }

    setTurnRecording(false);
    setTurnBusy(true);
    try {
      await recorder.stop();
      const uri = recorder.uri;
      if (!uri) throw new Error("Аудио не записано");
      const transcript = await client.transcribeAudioFile(uri, "voice.m4a", "audio/m4a");
      if (!transcript.trim()) {
        appendLine("system", "Речь не распознана, попробуйте ещё раз");
        setTurnBusy(false);
        return;
      }
      await runVoiceTurnText(transcript);
    } catch (e) {
      appendLine("system", `Ошибка STT: ${e instanceof Error ? e.message : String(e)}`);
      setTurnBusy(false);
    }
  };

  const currentVoice = VOICE_CATALOG.find((v) => v.id === selectedVoice) || VOICE_CATALOG[0];

  return (
    <View style={styles.container}>
      {/* Mode & Voice Header */}
      <View style={styles.topPanel}>
        <View style={styles.modeTabs}>
          <Pressable
            onPress={() => {
              disconnectLive();
              setMode("live");
            }}
            style={[styles.modeTab, mode === "live" && styles.modeTabActive]}
          >
            <Text style={[styles.modeTabText, mode === "live" && styles.modeTabTextActive]}>
              Gemini Live
            </Text>
          </Pressable>
          <Pressable
            onPress={() => {
              disconnectLive();
              setMode("turn");
            }}
            style={[styles.modeTab, mode === "turn" && styles.modeTabActive]}
          >
            <Text style={[styles.modeTabText, mode === "turn" && styles.modeTabTextActive]}>
              STT + Edge TTS
            </Text>
          </Pressable>
        </View>

        <Pressable onPress={() => setVoicePickerOpen(true)} style={styles.voiceBtn}>
          <Ionicons name="volume-high-outline" size={16} color={COLORS.amber} />
          <Text numberOfLines={1} style={styles.voiceBtnText}>
            Голос: {currentVoice.label}
          </Text>
          <Ionicons name="chevron-down" size={16} color={COLORS.textMuted} />
        </Pressable>
      </View>

      {mode === "live" && liveConfigured === false && (
        <View style={styles.warnBox}>
          <Ionicons name="information-circle-outline" size={18} color={COLORS.amber} />
          <Text style={styles.warnText}>
            Gemini API ключ не настроен на сервере - переключитесь на вкладку «STT + Edge TTS» выше.
          </Text>
        </View>
      )}

      {/* Orb Control */}
      <View style={styles.orbSection}>
        {mode === "live" ? (
          <>
            <Pressable
              onPress={startLive}
              style={[
                styles.orbButton,
                liveStatus === "connected" && styles.orbLiveActive,
                liveStatus === "connecting" && styles.orbConnecting,
              ]}
            >
              {liveStatus === "connecting" ? (
                <ActivityIndicator size="large" color={COLORS.amber} />
              ) : (
                <Ionicons
                  name={liveStatus === "connected" ? "stop" : "mic"}
                  size={38}
                  color={liveStatus === "connected" ? "#09090b" : COLORS.amber}
                />
              )}
            </Pressable>
            <Text style={styles.orbLabel}>
              {liveStatus === "idle"
                ? "Нажмите для живого диалога Gemini Live"
                : liveStatus === "connecting"
                ? "Подключение к Gemini Live..."
                : speakingId === "gemini-live"
                ? "Jarvis отвечает..."
                : muted
                ? "Микрофон на паузе"
                : "Слушаю вас в реальном времени..."}
            </Text>
            {liveStatus === "connected" && (
              <Pressable onPress={() => setMuted((m) => !m)} style={styles.muteBtn}>
                <Ionicons name={muted ? "mic-off" : "mic"} size={16} color={COLORS.text} />
                <Text style={styles.muteText}>{muted ? "Включить микрофон" : "Пауза микрофона"}</Text>
              </Pressable>
            )}
          </>
        ) : (
          <>
            <Pressable
              onPress={toggleTurnRecord}
              disabled={turnBusy}
              style={[
                styles.orbButton,
                turnRecording && styles.orbLiveActive,
              ]}
            >
              {turnBusy ? (
                <ActivityIndicator size="large" color={COLORS.amber} />
              ) : (
                <Ionicons
                  name={turnRecording ? "stop" : "mic"}
                  size={38}
                  color={turnRecording ? "#09090b" : COLORS.amber}
                />
              )}
            </Pressable>
            <Text style={styles.orbLabel}>
              {turnRecording
                ? "Идёт запись... Нажмите снова для отправки"
                : turnBusy
                ? "Распознаю и готовлю ответ..."
                : speakingId
                ? "Озвучиваю ответ... (нажмите микрофон чтобы задать новый)"
                : "Нажмите микрофон -> продиктуйте вопрос -> прослушайте ответ"}
            </Text>
          </>
        )}
      </View>

      {/* Live Transcripts */}
      <FlatList
        data={lines}
        keyExtractor={(item) => item.id}
        style={styles.transcriptList}
        contentContainerStyle={styles.transcriptContent}
        ListEmptyComponent={
          <Text style={styles.emptyTranscript}>
            Здесь будет отображаться стенограмма вашего голосового диалога.
          </Text>
        }
        renderItem={({ item }) => (
          <View
            style={[
              styles.lineCard,
              item.role === "user" && styles.lineUser,
              item.role === "tool" && styles.lineTool,
              item.role === "system" && styles.lineSystem,
            ]}
          >
            <Text style={styles.lineRole}>
              {item.role === "user"
                ? "Вы"
                : item.role === "assistant"
                ? "Jarvis"
                : item.role === "tool"
                ? "MCP"
                : "Система"}
            </Text>
            <Text selectable style={styles.lineText}>
              {item.text}
            </Text>
          </View>
        )}
      />

      {/* Quick Text Input inside Voice Session */}
      <View style={styles.textRow}>
        <TextInput
          value={textInput}
          onChangeText={setTextInput}
          onSubmitEditing={handleSendTextToLive}
          placeholder={
            mode === "live"
              ? "Написать в живую сессию..."
              : "Спросить голосовой движок текстом..."
          }
          placeholderTextColor={COLORS.textMuted}
          style={styles.textInput}
        />
        <Pressable onPress={handleSendTextToLive} style={styles.sendBtn}>
          <Ionicons name="send" size={18} color="#09090b" />
        </Pressable>
      </View>

      {/* 12 Voices Picker Modal */}
      <Modal
        visible={voicePickerOpen}
        transparent
        animationType="slide"
        onRequestClose={() => setVoicePickerOpen(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalSheet}>
            <View style={styles.modalHead}>
              <Text style={styles.modalTitle}>Выбор голоса Jarvis (12 голосов)</Text>
              <Pressable onPress={() => setVoicePickerOpen(false)}>
                <Ionicons name="close" size={22} color={COLORS.text} />
              </Pressable>
            </View>
            <ScrollView contentContainerStyle={styles.voiceList}>
              {VOICE_CATALOG.map((v) => {
                const active = v.id === selectedVoice;
                return (
                  <Pressable
                    key={v.id}
                    onPress={() => {
                      onSelectVoice(v.id);
                      setVoicePickerOpen(false);
                    }}
                    style={[styles.voiceRow, active && styles.voiceRowActive]}
                  >
                    <View style={{ flex: 1 }}>
                      <Text style={[styles.voiceName, active && { color: COLORS.amber }]}>
                        {v.label}
                      </Text>
                      <Text style={styles.voiceHint}>{v.styleHint}</Text>
                    </View>
                    {active && <Ionicons name="checkmark-circle" size={20} color={COLORS.amber} />}
                  </Pressable>
                );
              })}
            </ScrollView>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.bg },
  topPanel: {
    padding: 12,
    gap: 10,
    backgroundColor: COLORS.surface,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
  },
  modeTabs: {
    flexDirection: "row",
    backgroundColor: COLORS.bg,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: COLORS.border,
    padding: 3,
  },
  modeTab: { flex: 1, paddingVertical: 8, alignItems: "center", borderRadius: 8 },
  modeTabActive: { backgroundColor: COLORS.cardAlt },
  modeTabText: { color: COLORS.textSecondary, fontSize: 13, fontWeight: "600" },
  modeTabTextActive: { color: COLORS.text },
  voiceBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: 10,
    backgroundColor: COLORS.card,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  voiceBtnText: { flex: 1, color: COLORS.text, fontSize: 13, fontWeight: "500" },
  warnBox: {
    margin: 12,
    marginBottom: 0,
    padding: 10,
    borderRadius: 10,
    backgroundColor: COLORS.amberSoft,
    borderWidth: 1,
    borderColor: COLORS.amberBorder,
    flexDirection: "row",
    gap: 8,
    alignItems: "center",
  },
  warnText: { flex: 1, color: COLORS.amber, fontSize: 12 },
  orbSection: { alignItems: "center", paddingVertical: 20, gap: 12 },
  orbButton: {
    width: 96,
    height: 96,
    borderRadius: 48,
    backgroundColor: COLORS.amberSoft,
    borderWidth: 2,
    borderColor: COLORS.amber,
    alignItems: "center",
    justifyContent: "center",
  },
  orbLiveActive: {
    backgroundColor: COLORS.amber,
    borderColor: "#fef3c7",
  },
  orbConnecting: { opacity: 0.7 },
  orbLabel: {
    color: COLORS.textSecondary,
    fontSize: 13,
    textAlign: "center",
    paddingHorizontal: 24,
  },
  muteBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 14,
    paddingVertical: 7,
    borderRadius: 999,
    backgroundColor: COLORS.cardAlt,
    borderWidth: 1,
    borderColor: COLORS.borderLight,
  },
  muteText: { color: COLORS.text, fontSize: 12, fontWeight: "600" },
  transcriptList: { flex: 1 },
  transcriptContent: { padding: 12, gap: 8, paddingBottom: 16 },
  emptyTranscript: {
    color: COLORS.textMuted,
    textAlign: "center",
    marginTop: 20,
    fontSize: 13,
  },
  lineCard: {
    padding: 10,
    borderRadius: 12,
    backgroundColor: COLORS.card,
    borderWidth: 1,
    borderColor: COLORS.border,
    gap: 3,
  },
  lineUser: {
    backgroundColor: COLORS.amberSoft,
    borderColor: COLORS.amberBorder,
  },
  lineTool: {
    backgroundColor: COLORS.skySoft,
    borderColor: "rgba(56, 189, 248, 0.3)",
  },
  lineSystem: {
    backgroundColor: COLORS.surface,
  },
  lineRole: { color: COLORS.textMuted, fontSize: 11, fontWeight: "700" },
  lineText: { color: COLORS.text, fontSize: 14, lineHeight: 19 },
  textRow: {
    flexDirection: "row",
    gap: 8,
    padding: 10,
    borderTopWidth: 1,
    borderTopColor: COLORS.border,
    backgroundColor: COLORS.surface,
  },
  textInput: {
    flex: 1,
    height: 42,
    backgroundColor: COLORS.bg,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: COLORS.border,
    paddingHorizontal: 12,
    color: COLORS.text,
    fontSize: 14,
  },
  sendBtn: {
    width: 42,
    height: 42,
    borderRadius: 12,
    backgroundColor: COLORS.amber,
    alignItems: "center",
    justifyContent: "center",
  },
  modalOverlay: {
    flex: 1,
    justifyContent: "flex-end",
    backgroundColor: "rgba(0,0,0,0.65)",
  },
  modalSheet: {
    maxHeight: "75%",
    backgroundColor: COLORS.card,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    borderWidth: 1,
    borderColor: COLORS.borderLight,
    paddingBottom: 24,
  },
  modalHead: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    padding: 16,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
  },
  modalTitle: { color: COLORS.text, fontSize: 16, fontWeight: "700" },
  voiceList: { padding: 12, gap: 8 },
  voiceRow: {
    flexDirection: "row",
    alignItems: "center",
    padding: 12,
    borderRadius: 12,
    backgroundColor: COLORS.bg,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  voiceRowActive: {
    borderColor: COLORS.amberBorder,
    backgroundColor: COLORS.amberSoft,
  },
  voiceName: { color: COLORS.text, fontSize: 14, fontWeight: "600" },
  voiceHint: { color: COLORS.textSecondary, fontSize: 12, marginTop: 2 },
});
