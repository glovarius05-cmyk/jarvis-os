import { Ionicons } from "@expo/vector-icons";
import {
  RecordingPresets,
  requestRecordingPermissionsAsync,
  setAudioModeAsync,
  useAudioRecorder,
} from "expo-audio";
import * as DocumentPicker from "expo-document-picker";
import { File } from "expo-file-system";
import * as Haptics from "expo-haptics";
import * as ImagePicker from "expo-image-picker";
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import type { JarvisApiClient } from "../api/client";
import { COLORS } from "../constants/theme";
import { useChatSocket } from "../hooks/useChatSocket";
import { useVoicePlayer } from "../hooks/useVoicePlayer";
import { loadLastSessionId, saveLastSessionId } from "../storage/credentials";
import type {
  BrainItem,
  ChatWsEvent,
  FileUploadResult,
  MessageItem,
  ProviderStatus,
  RunMode,
  SessionItem,
} from "../types/api";
import {
  filterProvidersForPicker,
  formatRelativeTime,
  getVisibleModelsForProvider,
  shortProviderLabel,
} from "../utils/voiceHelpers";

interface Props {
  client: JarvisApiClient;
  activeBrain: string;
  brains: BrainItem[];
  providers: ProviderStatus[];
  defaultProvider: string;
  defaultModel: string;
  selectedVoice: string;
  onChangeBrain: (name: string) => Promise<void>;
  onOpenServerSettings: () => void;
  onActiveSessionChange: (sid: string) => void;
}

export function ChatScreen({
  client,
  activeBrain,
  brains,
  providers,
  defaultProvider,
  defaultModel,
  selectedVoice,
  onChangeBrain,
  onOpenServerSettings,
  onActiveSessionChange,
}: Props) {
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [sessionId, setSessionId] = useState<string>("");
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [streamingText, setStreamingText] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);

  const [provider, setProvider] = useState(defaultProvider || "openrouter");
  const [model, setModel] = useState(defaultModel || "openrouter/auto");
  const [runMode, setRunMode] = useState<RunMode>("full");
  const [autoSpeak, setAutoSpeak] = useState(false);

  const [inputText, setInputText] = useState("");
  const [attachments, setAttachments] = useState<FileUploadResult[]>([]);
  const [uploading, setUploading] = useState(false);

  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);

  const [sessionsModal, setSessionsModal] = useState(false);
  const [sessionSearch, setSessionSearch] = useState("");
  const [modelModal, setModelModal] = useState(false);
  const [customModelInput, setCustomModelInput] = useState("");
  const [brainModal, setBrainModal] = useState(false);

  const listRef = useRef<FlatList<MessageItem> | null>(null);
  const activeSessionRef = useRef(sessionId);
  activeSessionRef.current = sessionId;
  const autoSpeakRef = useRef(autoSpeak);
  autoSpeakRef.current = autoSpeak;

  const { speakingId, speakText, stop: stopTts } = useVoicePlayer(client);
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);

  const selectableProviders = filterProvidersForPicker(providers);

  const loadSessionMessages = useCallback(
    async (sid: string) => {
      if (!sid) return;
      try {
        const res = await client.getSession(sid);
        setMessages(res.messages || []);
      } catch {}
    },
    [client]
  );

  const loadSessions = useCallback(async () => {
    try {
      const res = await client.listSessions(activeBrain, 50);
      const items = res.sessions || [];
      setSessions(items);
      if (items.length > 0) {
        const saved = await loadLastSessionId();
        const target = items.find((s) => s.id === saved) || items[0];
        setSessionId(target.id);
        onActiveSessionChange(target.id);
        await saveLastSessionId(target.id);
        await loadSessionMessages(target.id);
      } else {
        const created = await client.createSession({ brain: activeBrain, title: "Mobile Chat" });
        setSessions([created]);
        setSessionId(created.id);
        onActiveSessionChange(created.id);
        await saveLastSessionId(created.id);
        setMessages([]);
      }
    } catch (e) {
      Alert.alert("Ошибка сессий", e instanceof Error ? e.message : String(e));
    }
  }, [activeBrain, client, loadSessionMessages, onActiveSessionChange]);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  const handleWsEvent = useCallback(
    (ev: ChatWsEvent) => {
      const sid = activeSessionRef.current;
      if (ev.type === "hello") {
        const active = (ev.active_jobs || []).find((j) => j.session_id === sid);
        if (active) {
          setIsStreaming(true);
          setStreamingText(active.accumulated_text || "");
        }
        return;
      }
      if (ev.session_id && sid && ev.session_id !== sid) return;

      if (ev.type === "turn_start") {
        setIsStreaming(true);
        setStreamingText("");
      } else if (ev.type === "stream") {
        setIsStreaming(true);
        setStreamingText((prev) => prev + (ev.content || ""));
      } else if (ev.type === "error") {
        setIsStreaming(false);
        setStreamingText("");
        Alert.alert("Ошибка модели", ev.content || "Неизвестная ошибка");
      } else if (ev.type === "final") {
        setIsStreaming(false);
        const finalContent = ev.content || "";
        setStreamingText("");
        if (sid) {
          loadSessionMessages(sid);
        }
        if (autoSpeakRef.current && finalContent) {
          speakText(finalContent, `auto_${Date.now()}`, selectedVoice);
        }
      }
    },
    [loadSessionMessages, selectedVoice, speakText]
  );

  const { connected, sendMessage, stopTurn } = useChatSocket(client, handleWsEvent);

  const handleNewChat = async () => {
    try {
      const created = await client.createSession({ brain: activeBrain, title: "Новый чат" });
      setSessions((prev) => [created, ...prev]);
      setSessionId(created.id);
      onActiveSessionChange(created.id);
      await saveLastSessionId(created.id);
      setMessages([]);
      setStreamingText("");
      setIsStreaming(false);
      setSessionsModal(false);
    } catch (e) {
      Alert.alert("Ошибка", e instanceof Error ? e.message : String(e));
    }
  };

  const handleSelectSession = async (s: SessionItem) => {
    setSessionId(s.id);
    onActiveSessionChange(s.id);
    await saveLastSessionId(s.id);
    setSessionsModal(false);
    setStreamingText("");
    setIsStreaming(false);
    await loadSessionMessages(s.id);
  };

  const uploadLocalFile = async (uri: string, filename: string, contentType: string) => {
    setUploading(true);
    try {
      const file = new File(uri);
      const b64 = await file.base64();
      const uploaded = await client.uploadBase64({
        filename,
        content_base64: b64,
        content_type: contentType,
        brain: activeBrain,
      });
      setAttachments((prev) => [...prev, uploaded]);
    } catch (e) {
      Alert.alert("Ошибка загрузки файла", e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  };

  const handlePickAttachment = () => {
    Alert.alert("Прикрепить файл", "Выберите источник", [
      {
        text: "Фото из галереи",
        onPress: async () => {
          const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
          if (!perm.granted) return;
          const res = await ImagePicker.launchImageLibraryAsync({ quality: 0.85 });
          if (!res.canceled && res.assets[0]) {
            const a = res.assets[0];
            await uploadLocalFile(a.uri, a.fileName || "photo.jpg", a.mimeType || "image/jpeg");
          }
        },
      },
      {
        text: "Снять на камеру",
        onPress: async () => {
          const perm = await ImagePicker.requestCameraPermissionsAsync();
          if (!perm.granted) return;
          const res = await ImagePicker.launchCameraAsync({ quality: 0.85 });
          if (!res.canceled && res.assets[0]) {
            const a = res.assets[0];
            await uploadLocalFile(a.uri, a.fileName || "camera.jpg", a.mimeType || "image/jpeg");
          }
        },
      },
      {
        text: "Документ",
        onPress: async () => {
          const res = await DocumentPicker.getDocumentAsync({ copyToCacheDirectory: true });
          if (!res.canceled && res.assets[0]) {
            const d = res.assets[0];
            await uploadLocalFile(d.uri, d.name, d.mimeType || "application/octet-stream");
          }
        },
      },
      { text: "Отмена", style: "cancel" },
    ]);
  };

  const handleSend = async () => {
    if (isStreaming) {
      if (sessionId) await stopTurn(sessionId);
      setIsStreaming(false);
      return;
    }

    const rawText = inputText.trim();
    if (!rawText && attachments.length === 0) return;

    let finalPrompt = rawText;
    if (attachments.length > 0) {
      const attLines = attachments.map((att) => {
        if (att.is_text && att.text_content) {
          return `[Файл ${att.filename} (${att.path})]:\n\`\`\`\n${att.text_content.slice(0, 8000)}\n\`\`\``;
        }
        return `[Вложение ${att.filename}: ${att.path}]`;
      });
      finalPrompt = `${attLines.join("\n\n")}${rawText ? `\n\n${rawText}` : ""}`;
    }

    let sid = sessionId;
    if (!sid) {
      const created = await client.createSession({ brain: activeBrain, title: rawText.slice(0, 40) || "Mobile" });
      sid = created.id;
      setSessionId(sid);
      onActiveSessionChange(sid);
      await saveLastSessionId(sid);
    }

    const optimisticMsg: MessageItem = {
      id: `local_${Date.now()}`,
      session_id: sid,
      role: "user",
      content: finalPrompt,
      provider,
      model,
      meta: {},
      created_at: Date.now() / 1000,
    };

    setMessages((prev) => [...prev, optimisticMsg]);
    setInputText("");
    setAttachments([]);
    setIsStreaming(true);
    setStreamingText("");
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});

    try {
      await sendMessage({
        session_id: sid,
        content: finalPrompt,
        provider,
        model,
        mode: runMode,
      });
    } catch (e) {
      setIsStreaming(false);
      Alert.alert("Ошибка отправки", e instanceof Error ? e.message : String(e));
    }
  };

  const toggleDictation = async () => {
    if (transcribing) return;
    if (!recording) {
      const perm = await requestRecordingPermissionsAsync();
      if (!perm.granted) {
        Alert.alert("Микрофон", "Разрешите доступ к микрофону для голосового ввода.");
        return;
      }
      await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
      await recorder.prepareToRecordAsync();
      recorder.record();
      setRecording(true);
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
      return;
    }

    setRecording(false);
    setTranscribing(true);
    try {
      await recorder.stop();
      const uri = recorder.uri;
      if (uri) {
        const text = await client.transcribeAudioFile(uri, "dictation.m4a", "audio/m4a");
        if (text.trim()) {
          setInputText((prev) => (prev ? `${prev} ${text.trim()}` : text.trim()));
        }
      }
    } catch (e) {
      Alert.alert("Ошибка диктовки", e instanceof Error ? e.message : String(e));
    } finally {
      setTranscribing(false);
    }
  };

  const cycleRunMode = () => {
    const order: RunMode[] = ["full", "auto", "suggest"];
    const next = order[(order.indexOf(runMode) + 1) % order.length];
    setRunMode(next);
  };

  const activeSessionObj = sessions.find((s) => s.id === sessionId);

  const displayedMessages: MessageItem[] = streamingText
    ? [
        ...messages,
        {
          id: "streaming_now",
          session_id: sessionId,
          role: "assistant",
          content: streamingText,
          provider,
          model,
          meta: {},
          created_at: Date.now() / 1000,
        },
      ]
    : messages;

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      keyboardVerticalOffset={Platform.OS === "ios" ? 10 : 0}
    >
      {/* Header Bar */}
      <View style={styles.header}>
        <Pressable onPress={() => setBrainModal(true)} style={styles.brainPill}>
          <View style={[styles.dot, { backgroundColor: connected ? COLORS.emerald : COLORS.amber }]} />
          <Text numberOfLines={1} style={styles.brainPillText}>
            {activeBrain}
          </Text>
          <Ionicons name="chevron-down" size={14} color={COLORS.textMuted} />
        </Pressable>

        <Text numberOfLines={1} style={styles.sessionTitle}>
          {activeSessionObj?.title || "Чат"}
        </Text>

        <Pressable onPress={onOpenServerSettings} style={styles.headerIcon}>
          <Ionicons name="server-outline" size={20} color={COLORS.textSecondary} />
        </Pressable>
      </View>

      {/* Uniform Equal-Width Top Controls + Full-Width Model Selector */}
      <View style={styles.controlsWrap}>
        <View style={styles.uniformRow}>
          <Pressable onPress={cycleRunMode} style={styles.uniformBtn}>
            <Ionicons name="shield-checkmark-outline" size={14} color={COLORS.amber} />
            <Text style={styles.uniformBtnText}>{runMode.toUpperCase()}</Text>
          </Pressable>

          <Pressable
            onPress={() => {
              if (autoSpeak) stopTts();
              setAutoSpeak((v) => !v);
            }}
            style={[styles.uniformBtn, autoSpeak && styles.uniformBtnActive]}
          >
            <Ionicons
              name={autoSpeak ? "volume-high" : "volume-mute-outline"}
              size={14}
              color={autoSpeak ? COLORS.amber : COLORS.textSecondary}
            />
            <Text style={[styles.uniformBtnText, autoSpeak && { color: COLORS.amber }]}>
              Звук
            </Text>
          </Pressable>

          <Pressable onPress={() => setSessionsModal(true)} style={styles.uniformBtn}>
            <Ionicons name="chatbubbles-outline" size={14} color={COLORS.textSecondary} />
            <Text style={styles.uniformBtnText}>Чаты</Text>
          </Pressable>

          <Pressable onPress={handleNewChat} style={styles.uniformBtn}>
            <Ionicons name="add-circle-outline" size={14} color={COLORS.textSecondary} />
            <Text style={styles.uniformBtnText}>Новый</Text>
          </Pressable>
        </View>

        <Pressable onPress={() => setModelModal(true)} style={styles.modelFullBtn}>
          <Ionicons name="hardware-chip-outline" size={15} color={COLORS.amber} />
          <Text numberOfLines={1} style={styles.modelFullText}>
            {shortProviderLabel(provider)}: {model}
          </Text>
          <Ionicons name="chevron-down" size={15} color={COLORS.textMuted} />
        </Pressable>
      </View>

      {/* Messages Stream */}
      <FlatList
        ref={listRef}
        data={displayedMessages}
        keyExtractor={(item, idx) => `${item.id}_${idx}`}
        style={styles.msgList}
        contentContainerStyle={styles.msgContent}
        onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: true })}
        ListEmptyComponent={
          <View style={styles.emptyBox}>
            <Ionicons name="sparkles" size={32} color={COLORS.amber} />
            <Text style={styles.emptyTitle}>Jarvis OS готов к работе</Text>
            <Text style={styles.emptySub}>
              Спросите что угодно, продиктуйте голосом или прикрепите фото/файл.
            </Text>
          </View>
        }
        renderItem={({ item }) => {
          const isUser = item.role === "user";
          const msgId = String(item.id);
          return (
            <View style={[styles.bubbleWrap, isUser ? styles.bubbleWrapUser : styles.bubbleWrapBot]}>
              <View style={[styles.bubble, isUser ? styles.bubbleUser : styles.bubbleBot]}>
                <Text selectable style={isUser ? styles.textUser : styles.textBot}>
                  {item.content}
                </Text>
              </View>
              {!isUser && item.id !== "streaming_now" && (
                <View style={styles.msgFooter}>
                  <Pressable
                    onPress={() => speakText(item.content, msgId, selectedVoice)}
                    style={styles.msgAction}
                  >
                    <Ionicons
                      name={speakingId === msgId ? "stop-circle-outline" : "volume-medium-outline"}
                      size={16}
                      color={speakingId === msgId ? COLORS.amber : COLORS.textMuted}
                    />
                    <Text style={styles.msgActionText}>
                      {speakingId === msgId ? "Стоп" : "Озвучить"}
                    </Text>
                  </Pressable>
                </View>
              )}
            </View>
          );
        }}
      />

      {isStreaming && !streamingText && (
        <View style={styles.thinkingBar}>
          <ActivityIndicator size="small" color={COLORS.amber} />
          <Text style={styles.thinkingText}>Jarvis думает...</Text>
        </View>
      )}

      {/* Attachments Strip */}
      {attachments.length > 0 && (
        <ScrollView horizontal contentContainerStyle={styles.attStrip}>
          {attachments.map((att) => (
            <View key={att.path} style={styles.attChip}>
              <Ionicons name={att.is_image ? "image-outline" : "document-outline"} size={14} color={COLORS.amber} />
              <Text numberOfLines={1} style={styles.attName}>
                {att.filename}
              </Text>
              <Pressable onPress={() => setAttachments((p) => p.filter((x) => x.path !== att.path))}>
                <Ionicons name="close" size={14} color={COLORS.textMuted} />
              </Pressable>
            </View>
          ))}
        </ScrollView>
      )}

      {/* Composer: Uniform Button Row ABOVE Full-Width Input */}
      <View style={styles.composerWrap}>
        <View style={styles.composerTopActions}>
          <Pressable onPress={handlePickAttachment} disabled={uploading} style={styles.composerToolBtn}>
            {uploading ? (
              <ActivityIndicator size="small" color={COLORS.amber} />
            ) : (
              <Ionicons name="attach" size={18} color={COLORS.textSecondary} />
            )}
            <Text style={styles.composerToolText}>Файл</Text>
          </Pressable>

          <Pressable
            onPress={toggleDictation}
            disabled={transcribing}
            style={[styles.composerToolBtn, recording && styles.composerToolRecording]}
          >
            {transcribing ? (
              <ActivityIndicator size="small" color={COLORS.amber} />
            ) : (
              <Ionicons
                name={recording ? "stop-circle" : "mic-outline"}
                size={18}
                color={recording ? COLORS.rose : COLORS.textSecondary}
              />
            )}
            <Text style={[styles.composerToolText, recording && { color: COLORS.rose }]}>
              {recording ? "Стоп" : "Голос"}
            </Text>
          </Pressable>

          <Pressable
            onPress={handleSend}
            style={[
              styles.composerSendBtn,
              isStreaming && { backgroundColor: COLORS.rose },
            ]}
          >
            <Ionicons name={isStreaming ? "stop" : "send"} size={16} color="#09090b" />
            <Text style={styles.composerSendText}>{isStreaming ? "Стоп" : "Отправить"}</Text>
          </Pressable>
        </View>

        <TextInput
          value={inputText}
          onChangeText={setInputText}
          placeholder="Сообщение для Jarvis..."
          placeholderTextColor={COLORS.textMuted}
          multiline
          maxLength={12000}
          style={styles.fullWidthInput}
        />
      </View>

      {/* Model Picker Modal */}
      <Modal visible={modelModal} transparent animationType="slide" onRequestClose={() => setModelModal(false)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalSheet}>
            <View style={styles.modalHead}>
              <Text style={styles.modalTitle}>Выбор модели ИИ</Text>
              <Pressable onPress={() => setModelModal(false)}>
                <Ionicons name="close" size={22} color={COLORS.text} />
              </Pressable>
            </View>
            <ScrollView contentContainerStyle={{ padding: 14, gap: 14 }}>
              {selectableProviders.map((p) => {
                const models = getVisibleModelsForProvider(p);
                return (
                  <View key={p.id} style={styles.provGroup}>
                    <View style={styles.provHead}>
                      <Text style={styles.provLabel}>{shortProviderLabel(p.id)}</Text>
                      <Text style={[styles.provStatus, p.ready ? { color: COLORS.emerald } : { color: COLORS.textMuted }]}>
                        {p.ready ? "● Готов" : "○ Не настроен"}
                      </Text>
                    </View>
                    {models.map((m) => {
                      const active = provider === p.id && model === m;
                      return (
                        <Pressable
                          key={`${p.id}::${m}`}
                          onPress={() => {
                            setProvider(p.id);
                            setModel(m);
                            setModelModal(false);
                          }}
                          style={[styles.modelItem, active && styles.modelItemActive]}
                        >
                          <Text style={[styles.modelItemText, active && { color: COLORS.amber }]}>
                            {m}
                          </Text>
                          {active && <Ionicons name="checkmark" size={18} color={COLORS.amber} />}
                        </Pressable>
                      );
                    })}
                  </View>
                );
              })}

              <View style={styles.customBox}>
                <Text style={styles.provLabel}>Своя модель ({shortProviderLabel(provider)})</Text>
                <View style={{ flexDirection: "row", gap: 8 }}>
                  <TextInput
                    value={customModelInput}
                    onChangeText={setCustomModelInput}
                    placeholder="Введите ID модели..."
                    placeholderTextColor={COLORS.textMuted}
                    autoCapitalize="none"
                    style={styles.customInput}
                  />
                  <Pressable
                    onPress={() => {
                      if (!customModelInput.trim()) return;
                      setModel(customModelInput.trim());
                      setCustomModelInput("");
                      setModelModal(false);
                    }}
                    style={styles.customSaveBtn}
                  >
                    <Text style={{ color: "#09090b", fontWeight: "700" }}>OK</Text>
                  </Pressable>
                </View>
              </View>

              <Pressable
                onPress={async () => {
                  await client.patchSettings({ model: { main_provider: provider, main_model: model } });
                  Alert.alert("Сохранено", `${shortProviderLabel(provider)}: ${model} установлена по умолчанию`);
                }}
                style={styles.setDefaultBtn}
              >
                <Text style={styles.setDefaultText}>Сделать текущей моделью по умолчанию</Text>
              </Pressable>
            </ScrollView>
          </View>
        </View>
      </Modal>

      {/* Sessions Drawer Modal */}
      <Modal visible={sessionsModal} transparent animationType="slide" onRequestClose={() => setSessionsModal(false)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalSheet}>
            <View style={styles.modalHead}>
              <Text style={styles.modalTitle}>Чаты ({activeBrain})</Text>
              <View style={{ flexDirection: "row", gap: 14, alignItems: "center" }}>
                <Pressable onPress={handleNewChat}>
                  <Ionicons name="add-circle" size={24} color={COLORS.amber} />
                </Pressable>
                <Pressable onPress={() => setSessionsModal(false)}>
                  <Ionicons name="close" size={22} color={COLORS.text} />
                </Pressable>
              </View>
            </View>
            <TextInput
              value={sessionSearch}
              onChangeText={setSessionSearch}
              placeholder="Фильтр по названию чата..."
              placeholderTextColor={COLORS.textMuted}
              style={styles.sessSearchInput}
            />
            <ScrollView contentContainerStyle={{ padding: 12, gap: 8 }}>
              {sessions
                .filter((s) => !sessionSearch.trim() || s.title.toLowerCase().includes(sessionSearch.toLowerCase()))
                .map((s) => {
                  const active = s.id === sessionId;
                  return (
                    <View key={s.id} style={[styles.sessRow, active && styles.sessRowActive]}>
                      <Pressable onPress={() => handleSelectSession(s)} style={{ flex: 1 }}>
                        <Text numberOfLines={1} style={[styles.sessTitle, active && { color: COLORS.amber }]}>
                          {s.title}
                        </Text>
                        <Text style={styles.sessMeta}>
                          {s.message_count} сообщ. · {formatRelativeTime(s.updated_at)}
                        </Text>
                      </Pressable>
                      <Pressable
                        onPress={async () => {
                          await client.deleteSession(s.id);
                          setSessions((p) => p.filter((x) => x.id !== s.id));
                        }}
                        style={{ padding: 6 }}
                      >
                        <Ionicons name="trash-outline" size={16} color={COLORS.textMuted} />
                      </Pressable>
                    </View>
                  );
                })}
            </ScrollView>
          </View>
        </View>
      </Modal>

      {/* Brain Switcher Modal */}
      <Modal visible={brainModal} transparent animationType="fade" onRequestClose={() => setBrainModal(false)}>
        <View style={styles.centerOverlay}>
          <View style={styles.centerDialog}>
            <Text style={styles.modalTitle}>Выбрать Brain Vault</Text>
            {brains.map((b) => (
              <Pressable
                key={b.name}
                onPress={async () => {
                  await onChangeBrain(b.name);
                  setBrainModal(false);
                }}
                style={[styles.modelItem, b.name === activeBrain && styles.modelItemActive]}
              >
                <Text style={styles.modelItemText}>
                  {b.name} ({b.note_count} заметок)
                </Text>
                {b.name === activeBrain && <Ionicons name="checkmark" size={18} color={COLORS.amber} />}
              </Pressable>
            ))}
            <Pressable onPress={() => setBrainModal(false)} style={{ alignItems: "center", paddingTop: 6 }}>
              <Text style={{ color: COLORS.textSecondary }}>Закрыть</Text>
            </Pressable>
          </View>
        </View>
      </Modal>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.bg },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
    backgroundColor: COLORS.surface,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
  },
  brainPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 999,
    backgroundColor: COLORS.card,
    borderWidth: 1,
    borderColor: COLORS.border,
    maxWidth: 140,
  },
  dot: { width: 7, height: 7, borderRadius: 4 },
  brainPillText: { color: COLORS.text, fontSize: 12, fontWeight: "600" },
  sessionTitle: { flex: 1, color: COLORS.textSecondary, fontSize: 13, fontWeight: "500" },
  headerIcon: { padding: 6 },
  controlsWrap: {
    paddingHorizontal: 10,
    paddingVertical: 8,
    gap: 6,
    backgroundColor: COLORS.surface,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
  },
  uniformRow: { flexDirection: "row", gap: 6 },
  uniformBtn: {
    flex: 1,
    height: 34,
    borderRadius: 9,
    backgroundColor: COLORS.card,
    borderWidth: 1,
    borderColor: COLORS.border,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 4,
  },
  uniformBtnActive: {
    backgroundColor: COLORS.amberSoft,
    borderColor: COLORS.amberBorder,
  },
  uniformBtnText: { color: COLORS.textSecondary, fontSize: 11, fontWeight: "700" },
  modelFullBtn: {
    width: "100%",
    height: 36,
    borderRadius: 9,
    backgroundColor: COLORS.card,
    borderWidth: 1,
    borderColor: COLORS.border,
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 10,
    gap: 6,
  },
  modelFullText: { flex: 1, color: COLORS.text, fontSize: 12, fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace" },
  msgList: { flex: 1 },
  msgContent: { padding: 12, gap: 12, paddingBottom: 20 },
  emptyBox: { alignItems: "center", marginTop: 60, paddingHorizontal: 28, gap: 8 },
  emptyTitle: { color: COLORS.text, fontSize: 17, fontWeight: "700" },
  emptySub: { color: COLORS.textSecondary, fontSize: 13, textAlign: "center", lineHeight: 19 },
  bubbleWrap: { gap: 4 },
  bubbleWrapUser: { alignItems: "flex-end" },
  bubbleWrapBot: { alignItems: "flex-start" },
  bubble: { maxWidth: "88%", borderRadius: 16, paddingHorizontal: 14, paddingVertical: 10 },
  bubbleUser: {
    backgroundColor: COLORS.amber,
    borderBottomRightRadius: 4,
  },
  bubbleBot: {
    backgroundColor: COLORS.card,
    borderWidth: 1,
    borderColor: COLORS.border,
    borderBottomLeftRadius: 4,
  },
  textUser: { color: "#09090b", fontSize: 15, lineHeight: 21, fontWeight: "500" },
  textBot: { color: COLORS.text, fontSize: 15, lineHeight: 22 },
  msgFooter: { flexDirection: "row", gap: 10, paddingHorizontal: 4 },
  msgAction: { flexDirection: "row", alignItems: "center", gap: 4, paddingVertical: 2 },
  msgActionText: { color: COLORS.textMuted, fontSize: 11 },
  thinkingBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: 14,
    paddingVertical: 6,
  },
  thinkingText: { color: COLORS.textSecondary, fontSize: 12 },
  attStrip: { paddingHorizontal: 10, paddingVertical: 6, gap: 8 },
  attChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: COLORS.card,
    borderWidth: 1,
    borderColor: COLORS.amberBorder,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 5,
    maxWidth: 180,
  },
  attName: { color: COLORS.text, fontSize: 12 },
  composerWrap: {
    borderTopWidth: 1,
    borderTopColor: COLORS.border,
    backgroundColor: COLORS.surface,
    padding: 10,
    gap: 8,
  },
  composerTopActions: { flexDirection: "row", gap: 8 },
  composerToolBtn: {
    flex: 1,
    height: 36,
    borderRadius: 10,
    backgroundColor: COLORS.card,
    borderWidth: 1,
    borderColor: COLORS.border,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 5,
  },
  composerToolRecording: {
    borderColor: "rgba(244, 63, 94, 0.5)",
    backgroundColor: COLORS.roseSoft,
  },
  composerToolText: { color: COLORS.textSecondary, fontSize: 12, fontWeight: "600" },
  composerSendBtn: {
    flex: 1.3,
    height: 36,
    borderRadius: 10,
    backgroundColor: COLORS.amber,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
  },
  composerSendText: { color: "#09090b", fontSize: 13, fontWeight: "700" },
  fullWidthInput: {
    width: "100%",
    minHeight: 46,
    maxHeight: 130,
    backgroundColor: COLORS.bg,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: COLORS.border,
    paddingHorizontal: 14,
    paddingVertical: 10,
    color: COLORS.text,
    fontSize: 15,
  },
  modalOverlay: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(0,0,0,0.68)" },
  modalSheet: {
    maxHeight: "82%",
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
  provGroup: { gap: 6 },
  provHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  provLabel: { color: COLORS.textSecondary, fontSize: 12, fontWeight: "700" },
  provStatus: { fontSize: 11, fontWeight: "600" },
  modelItem: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: 10,
    backgroundColor: COLORS.bg,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  modelItemActive: { borderColor: COLORS.amberBorder, backgroundColor: COLORS.amberSoft },
  modelItemText: { color: COLORS.text, fontSize: 13 },
  customBox: { gap: 8, marginTop: 4 },
  customInput: {
    flex: 1,
    height: 40,
    backgroundColor: COLORS.bg,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: COLORS.borderLight,
    paddingHorizontal: 12,
    color: COLORS.text,
    fontSize: 13,
  },
  customSaveBtn: {
    paddingHorizontal: 16,
    borderRadius: 10,
    backgroundColor: COLORS.amber,
    justifyContent: "center",
  },
  setDefaultBtn: {
    paddingVertical: 12,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: COLORS.amberBorder,
    backgroundColor: COLORS.amberSoft,
    alignItems: "center",
  },
  setDefaultText: { color: COLORS.amber, fontSize: 13, fontWeight: "700" },
  sessSearchInput: {
    marginHorizontal: 12,
    marginTop: 10,
    height: 40,
    backgroundColor: COLORS.bg,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: COLORS.border,
    paddingHorizontal: 12,
    color: COLORS.text,
    fontSize: 14,
  },
  sessRow: {
    flexDirection: "row",
    alignItems: "center",
    padding: 12,
    borderRadius: 12,
    backgroundColor: COLORS.bg,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  sessRowActive: { borderColor: COLORS.amberBorder, backgroundColor: COLORS.amberSoft },
  sessTitle: { color: COLORS.text, fontSize: 14, fontWeight: "600" },
  sessMeta: { color: COLORS.textMuted, fontSize: 11, marginTop: 2 },
  centerOverlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.7)",
    justifyContent: "center",
    padding: 20,
  },
  centerDialog: {
    backgroundColor: COLORS.card,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: COLORS.borderLight,
    padding: 16,
    gap: 10,
  },
});
