import { Ionicons } from "@expo/vector-icons";
import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Modal,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";
import type { JarvisApiClient } from "../api/client";
import { COLORS } from "../constants/theme";
import type { KanbanTask, ReminderItem, RunMode } from "../types/api";
import { formatRelativeTime } from "../utils/voiceHelpers";

interface Props {
  client: JarvisApiClient;
  activeBrain: string;
}

const STATUS_BADGES: Record<string, { label: string; color: string; bg: string }> = {
  todo: { label: "К выполнению", color: COLORS.textSecondary, bg: COLORS.cardAlt },
  running: { label: "В работе", color: COLORS.sky, bg: COLORS.skySoft },
  needs_user: { label: "Ждёт ответа", color: COLORS.amber, bg: COLORS.amberSoft },
  review: { label: "Проверка", color: COLORS.emerald, bg: COLORS.emeraldSoft },
  done: { label: "Готово", color: COLORS.emerald, bg: COLORS.emeraldSoft },
  failed: { label: "Ошибка", color: COLORS.rose, bg: COLORS.roseSoft },
  cancelled: { label: "Отменена", color: COLORS.textMuted, bg: COLORS.cardAlt },
};

export function TasksScreen({ client, activeBrain }: Props) {
  const [segment, setSegment] = useState<"kanban" | "reminders">("kanban");
  const [tasks, setTasks] = useState<KanbanTask[]>([]);
  const [autoEnabled, setAutoEnabled] = useState(false);
  const [reminders, setReminders] = useState<ReminderItem[]>([]);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [loading, setLoading] = useState(false);

  const [newTaskModal, setNewTaskModal] = useState(false);
  const [taskTitle, setTaskTitle] = useState("");
  const [taskDesc, setTaskDesc] = useState("");
  const [taskMode, setTaskMode] = useState<RunMode>("auto");

  const [answerModalTask, setAnswerModalTask] = useState<KanbanTask | null>(null);
  const [answerText, setAnswerText] = useState("");

  const [newRemModal, setNewRemModal] = useState(false);
  const [remName, setRemName] = useState("");
  const [remPrompt, setRemPrompt] = useState("");
  const [remCron, setRemCron] = useState("0 9 * * *");

  const loadWork = useCallback(async () => {
    setLoading(true);
    try {
      const [tRes, rRes] = await Promise.all([
        client.listTasks(activeBrain),
        client.listReminders(activeBrain),
      ]);
      setTasks(tRes.tasks || []);
      setAutoEnabled(Boolean(tRes.auto_enabled));
      setReminders(rRes.reminders || []);
    } catch (e) {
      Alert.alert("Ошибка загрузки", e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [activeBrain, client]);

  useEffect(() => {
    loadWork();
  }, [loadWork]);

  const handleCreateTask = async () => {
    if (!taskTitle.trim()) return;
    try {
      await client.createTask(
        { title: taskTitle.trim(), description: taskDesc.trim(), mode: taskMode },
        activeBrain
      );
      setTaskTitle("");
      setTaskDesc("");
      setNewTaskModal(false);
      await loadWork();
    } catch (e) {
      Alert.alert("Ошибка", e instanceof Error ? e.message : String(e));
    }
  };

  const handleCreateReminder = async () => {
    if (!remName.trim() || !remPrompt.trim()) return;
    try {
      await client.createReminder(
        { name: remName.trim(), prompt: remPrompt.trim(), cron: remCron.trim(), mode: "auto" },
        activeBrain
      );
      setRemName("");
      setRemPrompt("");
      setNewRemModal(false);
      await loadWork();
    } catch (e) {
      Alert.alert("Ошибка", e instanceof Error ? e.message : String(e));
    }
  };

  const handleToggleAuto = async (val: boolean) => {
    setAutoEnabled(val);
    try {
      await client.toggleKanbanAuto(val, activeBrain);
    } catch {
      setAutoEnabled(!val);
    }
  };

  const handleAnswer = async () => {
    if (!answerModalTask || !answerText.trim()) return;
    try {
      await client.answerTask(answerModalTask.id, answerText.trim());
      setAnswerModalTask(null);
      setAnswerText("");
      await loadWork();
    } catch (e) {
      Alert.alert("Ошибка", e instanceof Error ? e.message : String(e));
    }
  };

  const filteredTasks = tasks.filter((t) => statusFilter === "all" || t.status === statusFilter);

  return (
    <View style={styles.container}>
      {/* Top Bar */}
      <View style={styles.topBar}>
        <View style={styles.segmentWrap}>
          <Pressable
            onPress={() => setSegment("kanban")}
            style={[styles.segmentBtn, segment === "kanban" && styles.segmentBtnActive]}
          >
            <Text style={[styles.segmentText, segment === "kanban" && styles.segmentTextActive]}>
              Kanban ({tasks.length})
            </Text>
          </Pressable>
          <Pressable
            onPress={() => setSegment("reminders")}
            style={[styles.segmentBtn, segment === "reminders" && styles.segmentBtnActive]}
          >
            <Text style={[styles.segmentText, segment === "reminders" && styles.segmentTextActive]}>
              Напоминания ({reminders.length})
            </Text>
          </Pressable>
        </View>

        <Pressable
          onPress={() => (segment === "kanban" ? setNewTaskModal(true) : setNewRemModal(true))}
          style={styles.addBtn}
        >
          <Ionicons name="add" size={20} color="#09090b" />
        </Pressable>
      </View>

      {segment === "kanban" ? (
        <>
          <View style={styles.autoRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles.autoTitle}>Авто-исполнение Kanban 24/7</Text>
              <Text style={styles.autoSub}>Фоновый планировщик для задач ({activeBrain})</Text>
            </View>
            <Switch
              value={autoEnabled}
              onValueChange={handleToggleAuto}
              trackColor={{ false: COLORS.cardAlt, true: COLORS.amberBorder }}
              thumbColor={autoEnabled ? COLORS.amber : COLORS.textMuted}
            />
          </View>

          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.filterRow}
          >
            {[
              { id: "all", label: "Все" },
              { id: "todo", label: "К выполнению" },
              { id: "running", label: "В работе" },
              { id: "needs_user", label: "Ждёт ответа" },
              { id: "review", label: "Проверка" },
              { id: "done", label: "Готово" },
            ].map((f) => (
              <Pressable
                key={f.id}
                onPress={() => setStatusFilter(f.id)}
                style={[styles.filterChip, statusFilter === f.id && styles.filterChipActive]}
              >
                <Text
                  style={[
                    styles.filterText,
                    statusFilter === f.id && styles.filterTextActive,
                  ]}
                >
                  {f.label}
                </Text>
              </Pressable>
            ))}
          </ScrollView>

          <FlatList
            data={filteredTasks}
            keyExtractor={(t) => t.id}
            refreshControl={<RefreshControl refreshing={loading} onRefresh={loadWork} tintColor={COLORS.amber} />}
            contentContainerStyle={styles.listContent}
            ListEmptyComponent={<Text style={styles.empty}>Задач пока нет</Text>}
            renderItem={({ item }) => {
              const badge = STATUS_BADGES[item.status] || {
                label: item.status,
                color: COLORS.textSecondary,
                bg: COLORS.cardAlt,
              };
              return (
                <View style={styles.card}>
                  <View style={styles.cardHead}>
                    <View style={[styles.badge, { backgroundColor: badge.bg }]}>
                      <Text style={[styles.badgeText, { color: badge.color }]}>{badge.label}</Text>
                    </View>
                    <Text style={styles.metaText}>{item.mode.toUpperCase()} · {formatRelativeTime(item.updated_at)}</Text>
                  </View>

                  <Text style={styles.cardTitle}>{item.title}</Text>
                  {!!item.description && <Text style={styles.cardBody}>{item.description}</Text>}
                  {!!item.question && (
                    <View style={styles.questionBox}>
                      <Text style={styles.questionLabel}>Вопрос от Jarvis:</Text>
                      <Text style={styles.questionText}>{item.question}</Text>
                    </View>
                  )}
                  {!!item.result && (
                    <Text numberOfLines={4} style={styles.resultText}>
                      {item.result}
                    </Text>
                  )}
                  {!!item.error && <Text style={styles.errText}>{item.error}</Text>}

                  <View style={styles.actionRow}>
                    {(item.status === "todo" || item.status === "failed") && (
                      <Pressable
                        onPress={async () => {
                          await client.runTask(item.id);
                          await loadWork();
                        }}
                        style={styles.actionPrimary}
                      >
                        <Ionicons name="play" size={14} color="#09090b" />
                        <Text style={styles.actionPrimaryText}>Запустить</Text>
                      </Pressable>
                    )}
                    {item.status === "needs_user" && (
                      <Pressable
                        onPress={() => {
                          setAnswerModalTask(item);
                          setAnswerText("");
                        }}
                        style={styles.actionPrimary}
                      >
                        <Ionicons name="chatbubble-outline" size={14} color="#09090b" />
                        <Text style={styles.actionPrimaryText}>Ответить</Text>
                      </Pressable>
                    )}
                    {item.status === "review" && (
                      <Pressable
                        onPress={async () => {
                          await client.acceptTask(item.id);
                          await loadWork();
                        }}
                        style={styles.actionSuccess}
                      >
                        <Ionicons name="checkmark" size={14} color="#09090b" />
                        <Text style={styles.actionPrimaryText}>Принять</Text>
                      </Pressable>
                    )}
                    {item.status !== "done" && item.status !== "cancelled" && (
                      <Pressable
                        onPress={async () => {
                          await client.cancelTask(item.id);
                          await loadWork();
                        }}
                        style={styles.actionGhost}
                      >
                        <Text style={styles.actionGhostText}>Отменить</Text>
                      </Pressable>
                    )}
                  </View>
                </View>
              );
            }}
          />
        </>
      ) : (
        <FlatList
          data={reminders}
          keyExtractor={(r) => r.id}
          refreshControl={<RefreshControl refreshing={loading} onRefresh={loadWork} tintColor={COLORS.amber} />}
          contentContainerStyle={styles.listContent}
          ListEmptyComponent={<Text style={styles.empty}>Напоминаний пока нет</Text>}
          renderItem={({ item }) => (
            <View style={styles.card}>
              <View style={styles.cardHead}>
                <Text style={styles.cardTitle}>{item.name}</Text>
                <Text style={styles.metaText}>{item.cron || "Разовое"}</Text>
              </View>
              <Text style={styles.cardBody}>{item.prompt}</Text>
              <View style={styles.actionRow}>
                <Pressable
                  onPress={async () => {
                    await client.runReminder(item.id);
                    Alert.alert("Запущено", "Напоминание выполнено");
                  }}
                  style={styles.actionPrimary}
                >
                  <Ionicons name="play" size={14} color="#09090b" />
                  <Text style={styles.actionPrimaryText}>Выполнить сейчас</Text>
                </Pressable>
                <Pressable
                  onPress={async () => {
                    await client.deleteReminder(item.id);
                    await loadWork();
                  }}
                  style={styles.actionGhost}
                >
                  <Text style={[styles.actionGhostText, { color: COLORS.rose }]}>Удалить</Text>
                </Pressable>
              </View>
            </View>
          )}
        />
      )}

      {/* New Task Modal */}
      <Modal visible={newTaskModal} transparent animationType="fade" onRequestClose={() => setNewTaskModal(false)}>
        <View style={styles.overlay}>
          <View style={styles.dialog}>
            <Text style={styles.dialogTitle}>Новая задача в Kanban</Text>
            <TextInput
              value={taskTitle}
              onChangeText={setTaskTitle}
              placeholder="Название задачи"
              placeholderTextColor={COLORS.textMuted}
              style={styles.dialogInput}
            />
            <TextInput
              value={taskDesc}
              onChangeText={setTaskDesc}
              placeholder="Подробное описание (необязательно)"
              placeholderTextColor={COLORS.textMuted}
              multiline
              style={[styles.dialogInput, { height: 90 }]}
            />
            <View style={styles.modeRow}>
              {(["suggest", "auto", "full"] as RunMode[]).map((m) => (
                <Pressable
                  key={m}
                  onPress={() => setTaskMode(m)}
                  style={[styles.modeChip, taskMode === m && styles.modeChipActive]}
                >
                  <Text style={[styles.modeText, taskMode === m && styles.modeTextActive]}>
                    {m.toUpperCase()}
                  </Text>
                </Pressable>
              ))}
            </View>
            <View style={styles.dialogActions}>
              <Pressable onPress={() => setNewTaskModal(false)} style={styles.dialogCancel}>
                <Text style={styles.dialogCancelText}>Отмена</Text>
              </Pressable>
              <Pressable onPress={handleCreateTask} style={styles.dialogConfirm}>
                <Text style={styles.dialogConfirmText}>Создать</Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>

      {/* Answer Blocked Task Modal */}
      <Modal
        visible={answerModalTask !== null}
        transparent
        animationType="fade"
        onRequestClose={() => setAnswerModalTask(null)}
      >
        <View style={styles.overlay}>
          <View style={styles.dialog}>
            <Text style={styles.dialogTitle}>Ответ для задачи</Text>
            <Text style={styles.cardBody}>{answerModalTask?.question}</Text>
            <TextInput
              value={answerText}
              onChangeText={setAnswerText}
              placeholder="Ваш ответ..."
              placeholderTextColor={COLORS.textMuted}
              multiline
              style={[styles.dialogInput, { height: 90 }]}
            />
            <View style={styles.dialogActions}>
              <Pressable onPress={() => setAnswerModalTask(null)} style={styles.dialogCancel}>
                <Text style={styles.dialogCancelText}>Отмена</Text>
              </Pressable>
              <Pressable onPress={handleAnswer} style={styles.dialogConfirm}>
                <Text style={styles.dialogConfirmText}>Отправить</Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>

      {/* New Reminder Modal */}
      <Modal visible={newRemModal} transparent animationType="fade" onRequestClose={() => setNewRemModal(false)}>
        <View style={styles.overlay}>
          <View style={styles.dialog}>
            <Text style={styles.dialogTitle}>Новое напоминание / Cron</Text>
            <TextInput
              value={remName}
              onChangeText={setRemName}
              placeholder="Название, например: Утренняя сводка"
              placeholderTextColor={COLORS.textMuted}
              style={styles.dialogInput}
            />
            <TextInput
              value={remCron}
              onChangeText={setRemCron}
              placeholder="Cron (например 0 9 * * *)"
              placeholderTextColor={COLORS.textMuted}
              autoCapitalize="none"
              style={styles.dialogInput}
            />
            <TextInput
              value={remPrompt}
              onChangeText={setRemPrompt}
              placeholder="Промпт для Jarvis..."
              placeholderTextColor={COLORS.textMuted}
              multiline
              style={[styles.dialogInput, { height: 90 }]}
            />
            <View style={styles.dialogActions}>
              <Pressable onPress={() => setNewRemModal(false)} style={styles.dialogCancel}>
                <Text style={styles.dialogCancelText}>Отмена</Text>
              </Pressable>
              <Pressable onPress={handleCreateReminder} style={styles.dialogConfirm}>
                <Text style={styles.dialogConfirmText}>Создать</Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.bg },
  topBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    backgroundColor: COLORS.surface,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
  },
  segmentWrap: {
    flex: 1,
    flexDirection: "row",
    backgroundColor: COLORS.bg,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: COLORS.border,
    padding: 3,
  },
  segmentBtn: { flex: 1, paddingVertical: 8, alignItems: "center", borderRadius: 8 },
  segmentBtnActive: { backgroundColor: COLORS.cardAlt },
  segmentText: { color: COLORS.textSecondary, fontSize: 13, fontWeight: "600" },
  segmentTextActive: { color: COLORS.text },
  addBtn: {
    width: 38,
    height: 38,
    borderRadius: 10,
    backgroundColor: COLORS.amber,
    alignItems: "center",
    justifyContent: "center",
  },
  autoRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
    backgroundColor: COLORS.card,
  },
  autoTitle: { color: COLORS.text, fontSize: 13, fontWeight: "600" },
  autoSub: { color: COLORS.textMuted, fontSize: 11 },
  filterRow: { paddingHorizontal: 12, paddingVertical: 10, gap: 8 },
  filterChip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 999,
    backgroundColor: COLORS.card,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  filterChipActive: { backgroundColor: COLORS.amberSoft, borderColor: COLORS.amberBorder },
  filterText: { color: COLORS.textSecondary, fontSize: 12, fontWeight: "600" },
  filterTextActive: { color: COLORS.amber },
  listContent: { padding: 12, gap: 10, paddingBottom: 32 },
  empty: { color: COLORS.textMuted, textAlign: "center", marginTop: 40, fontSize: 14 },
  card: {
    backgroundColor: COLORS.card,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: COLORS.border,
    padding: 13,
    gap: 8,
  },
  cardHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 8 },
  badge: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6 },
  badgeText: { fontSize: 11, fontWeight: "700" },
  metaText: { color: COLORS.textMuted, fontSize: 11 },
  cardTitle: { color: COLORS.text, fontSize: 15, fontWeight: "700" },
  cardBody: { color: COLORS.textSecondary, fontSize: 13, lineHeight: 18 },
  questionBox: {
    backgroundColor: COLORS.amberSoft,
    borderColor: COLORS.amberBorder,
    borderWidth: 1,
    borderRadius: 10,
    padding: 10,
    gap: 4,
  },
  questionLabel: { color: COLORS.amber, fontSize: 12, fontWeight: "700" },
  questionText: { color: COLORS.text, fontSize: 13 },
  resultText: { color: COLORS.emerald, fontSize: 13, lineHeight: 18 },
  errText: { color: COLORS.rose, fontSize: 12 },
  actionRow: { flexDirection: "row", gap: 8, flexWrap: "wrap", marginTop: 2 },
  actionPrimary: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    backgroundColor: COLORS.amber,
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 8,
  },
  actionSuccess: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    backgroundColor: COLORS.emerald,
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 8,
  },
  actionPrimaryText: { color: "#09090b", fontSize: 12, fontWeight: "700" },
  actionGhost: {
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: COLORS.borderLight,
  },
  actionGhostText: { color: COLORS.textSecondary, fontSize: 12, fontWeight: "600" },
  overlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.72)",
    justifyContent: "center",
    padding: 20,
  },
  dialog: {
    backgroundColor: COLORS.card,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: COLORS.borderLight,
    padding: 16,
    gap: 10,
  },
  dialogTitle: { color: COLORS.text, fontSize: 16, fontWeight: "700" },
  dialogInput: {
    backgroundColor: COLORS.bg,
    borderWidth: 1,
    borderColor: COLORS.borderLight,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    color: COLORS.text,
    fontSize: 14,
  },
  modeRow: { flexDirection: "row", gap: 8 },
  modeChip: {
    flex: 1,
    alignItems: "center",
    paddingVertical: 8,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: COLORS.borderLight,
    backgroundColor: COLORS.bg,
  },
  modeChipActive: { backgroundColor: COLORS.amberSoft, borderColor: COLORS.amberBorder },
  modeText: { color: COLORS.textSecondary, fontSize: 12, fontWeight: "700" },
  modeTextActive: { color: COLORS.amber },
  dialogActions: { flexDirection: "row", justifyContent: "flex-end", gap: 10, marginTop: 4 },
  dialogCancel: { paddingHorizontal: 14, paddingVertical: 10 },
  dialogCancelText: { color: COLORS.textSecondary, fontSize: 14 },
  dialogConfirm: {
    backgroundColor: COLORS.amber,
    borderRadius: 10,
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  dialogConfirmText: { color: "#09090b", fontWeight: "700", fontSize: 14 },
});
