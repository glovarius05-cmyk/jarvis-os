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
  Text,
  TextInput,
  View,
} from "react-native";
import type { JarvisApiClient } from "../api/client";
import { COLORS } from "../constants/theme";
import type { BrainGraphNode, BrainItem, BrainSearchHit } from "../types/api";

interface Props {
  client: JarvisApiClient;
  activeBrain: string;
  brains: BrainItem[];
  onChangeBrain: (brainName: string) => Promise<void>;
  onRefreshBrains: () => Promise<void>;
}

export function VaultScreen({ client, activeBrain, brains, onChangeBrain, onRefreshBrains }: Props) {
  const [nodes, setNodes] = useState<BrainGraphNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [searchHits, setSearchHits] = useState<BrainSearchHit[] | null>(null);
  const [searching, setSearching] = useState(false);

  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [noteContent, setNoteContent] = useState("");
  const [editing, setEditing] = useState(false);
  const [savingNote, setSavingNote] = useState(false);

  const [newNoteModal, setNewNoteModal] = useState(false);
  const [newNotePath, setNewNotePath] = useState("");
  const [newNoteBody, setNewNoteBody] = useState("");

  const [newBrainModal, setNewBrainModal] = useState(false);
  const [newBrainName, setNewBrainName] = useState("");

  const loadGraph = useCallback(async () => {
    setLoading(true);
    try {
      const res = await client.getBrainGraph(activeBrain);
      setNodes(res.nodes || []);
    } catch (e) {
      Alert.alert("Ошибка Vault", e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [activeBrain, client]);

  useEffect(() => {
    setSearchHits(null);
    setQuery("");
    loadGraph();
  }, [loadGraph]);

  const handleSearch = async () => {
    const q = query.trim();
    if (!q) {
      setSearchHits(null);
      return;
    }
    setSearching(true);
    try {
      const res = await client.searchBrain(q, activeBrain, 30);
      setSearchHits(res.hits || []);
    } catch (e) {
      Alert.alert("Ошибка поиска", e instanceof Error ? e.message : String(e));
    } finally {
      setSearching(false);
    }
  };

  const openNote = async (path: string) => {
    setSelectedPath(path);
    setEditing(false);
    setNoteContent("Загрузка...");
    try {
      const res = await client.readBrainFile(path, activeBrain);
      setNoteContent(res.content);
    } catch (e) {
      setNoteContent(`Не удалось прочитать заметку: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const handleSaveNote = async () => {
    if (!selectedPath) return;
    setSavingNote(true);
    try {
      await client.writeBrainFile(selectedPath, noteContent, activeBrain);
      setEditing(false);
      await loadGraph();
    } catch (e) {
      Alert.alert("Ошибка сохранения", e instanceof Error ? e.message : String(e));
    } finally {
      setSavingNote(false);
    }
  };

  const handleCreateNote = async () => {
    let p = newNotePath.trim();
    if (!p) {
      Alert.alert("Путь пуст", "Укажите имя заметки, например notes/idea.md");
      return;
    }
    if (!p.includes(".")) p += ".md";
    setSavingNote(true);
    try {
      await client.writeBrainFile(p, newNoteBody || `# ${p}\n\n`, activeBrain);
      setNewNoteModal(false);
      setNewNotePath("");
      setNewNoteBody("");
      await loadGraph();
      await openNote(p);
    } catch (e) {
      Alert.alert("Ошибка создания", e instanceof Error ? e.message : String(e));
    } finally {
      setSavingNote(false);
    }
  };

  const handleCreateBrain = async () => {
    const name = newBrainName.trim();
    if (!name) return;
    try {
      await client.createBrain(name, true);
      setNewBrainName("");
      setNewBrainModal(false);
      await onRefreshBrains();
    } catch (e) {
      Alert.alert("Ошибка", e instanceof Error ? e.message : String(e));
    }
  };

  const filteredNodes = nodes.filter((n) => {
    if (!query.trim()) return true;
    const label = `${n.path || n.id} ${n.title || n.label || ""}`.toLowerCase();
    return label.includes(query.trim().toLowerCase());
  });

  return (
    <View style={styles.container}>
      {/* Top Brain Strip */}
      <View style={styles.header}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.brainScroll}>
          {brains.map((b) => (
            <Pressable
              key={b.name}
              onPress={() => onChangeBrain(b.name)}
              style={[styles.brainChip, b.name === activeBrain && styles.brainChipActive]}
            >
              <Ionicons
                name="library-outline"
                size={14}
                color={b.name === activeBrain ? "#09090b" : COLORS.textSecondary}
              />
              <Text
                style={[
                  styles.brainChipText,
                  b.name === activeBrain && styles.brainChipTextActive,
                ]}
              >
                {b.name} ({b.note_count})
              </Text>
            </Pressable>
          ))}
          <Pressable onPress={() => setNewBrainModal(true)} style={styles.addChip}>
            <Ionicons name="add" size={16} color={COLORS.amber} />
            <Text style={styles.addChipText}>Мозг</Text>
          </Pressable>
        </ScrollView>

        <View style={styles.searchRow}>
          <View style={styles.searchBox}>
            <Ionicons name="search" size={16} color={COLORS.textMuted} />
            <TextInput
              value={query}
              onChangeText={(txt) => {
                setQuery(txt);
                if (!txt.trim()) setSearchHits(null);
              }}
              onSubmitEditing={handleSearch}
              returnKeyType="search"
              placeholder="Поиск по Vault (гибридный)..."
              placeholderTextColor={COLORS.textMuted}
              style={styles.searchInput}
            />
            {!!query && (
              <Pressable
                onPress={() => {
                  setQuery("");
                  setSearchHits(null);
                }}
              >
                <Ionicons name="close-circle" size={18} color={COLORS.textMuted} />
              </Pressable>
            )}
          </View>

          <Pressable onPress={handleSearch} style={styles.iconBtn}>
            {searching ? (
              <ActivityIndicator size="small" color={COLORS.amber} />
            ) : (
              <Ionicons name="arrow-forward" size={18} color={COLORS.amber} />
            )}
          </Pressable>

          <Pressable onPress={() => setNewNoteModal(true)} style={styles.newNoteBtn}>
            <Ionicons name="add" size={20} color="#09090b" />
          </Pressable>
        </View>
      </View>

      {/* Search Results OR Note List */}
      {searchHits ? (
        <FlatList
          data={searchHits}
          keyExtractor={(item, idx) => `${item.path}_${idx}`}
          contentContainerStyle={styles.listContent}
          ListEmptyComponent={<Text style={styles.emptyText}>Ничего не найдено по запросу</Text>}
          renderItem={({ item }) => (
            <Pressable onPress={() => openNote(item.path)} style={styles.noteCard}>
              <View style={styles.noteTop}>
                <Ionicons name="document-text-outline" size={16} color={COLORS.amber} />
                <Text numberOfLines={1} style={styles.noteTitle}>
                  {item.title || item.path}
                </Text>
              </View>
              <Text numberOfLines={1} style={styles.notePath}>
                {item.path}
              </Text>
              {!!item.snippet && (
                <Text numberOfLines={3} style={styles.noteSnippet}>
                  {item.snippet}
                </Text>
              )}
            </Pressable>
          )}
        />
      ) : (
        <FlatList
          data={filteredNodes}
          keyExtractor={(item) => item.id}
          refreshControl={<RefreshControl refreshing={loading} onRefresh={loadGraph} tintColor={COLORS.amber} />}
          contentContainerStyle={styles.listContent}
          ListEmptyComponent={
            <Text style={styles.emptyText}>
              {loading ? "Загрузка заметок..." : "Заметок пока нет. Нажмите + чтобы создать первую."}
            </Text>
          }
          renderItem={({ item }) => {
            const notePath = item.path || item.id;
            return (
              <Pressable onPress={() => openNote(notePath)} style={styles.noteCard}>
                <View style={styles.noteTop}>
                  <Ionicons name="document-text-outline" size={16} color={COLORS.sky} />
                  <Text numberOfLines={1} style={styles.noteTitle}>
                    {item.title || item.label || notePath}
                  </Text>
                </View>
                <Text numberOfLines={1} style={styles.notePath}>
                  {notePath}
                </Text>
                {Array.isArray(item.tags) && item.tags.length > 0 && (
                  <View style={styles.tagRow}>
                    {item.tags.slice(0, 4).map((t) => (
                      <View key={t} style={styles.tagBadge}>
                        <Text style={styles.tagText}>#{t}</Text>
                      </View>
                    ))}
                  </View>
                )}
              </Pressable>
            );
          }}
        />
      )}

      {/* Note Viewer / Editor Modal */}
      <Modal visible={selectedPath !== null} animationType="slide" onRequestClose={() => setSelectedPath(null)}>
        <View style={styles.modalContainer}>
          <View style={styles.modalHeader}>
            <Pressable onPress={() => setSelectedPath(null)} style={styles.modalBtn}>
              <Ionicons name="chevron-down" size={22} color={COLORS.text} />
            </Pressable>
            <Text numberOfLines={1} style={styles.modalTitle}>
              {selectedPath}
            </Text>
            {editing ? (
              <Pressable onPress={handleSaveNote} disabled={savingNote} style={styles.savePill}>
                {savingNote ? (
                  <ActivityIndicator size="small" color="#09090b" />
                ) : (
                  <Text style={styles.savePillText}>Сохранить</Text>
                )}
              </Pressable>
            ) : (
              <Pressable onPress={() => setEditing(true)} style={styles.editPill}>
                <Ionicons name="create-outline" size={16} color={COLORS.amber} />
                <Text style={styles.editPillText}>Править</Text>
              </Pressable>
            )}
          </View>

          {editing ? (
            <TextInput
              value={noteContent}
              onChangeText={setNoteContent}
              multiline
              textAlignVertical="top"
              style={styles.editorInput}
            />
          ) : (
            <ScrollView style={styles.readerScroll} contentContainerStyle={styles.readerContent}>
              <Text selectable style={styles.readerText}>
                {noteContent}
              </Text>
            </ScrollView>
          )}
        </View>
      </Modal>

      {/* New Note Modal */}
      <Modal visible={newNoteModal} transparent animationType="fade" onRequestClose={() => setNewNoteModal(false)}>
        <View style={styles.overlay}>
          <View style={styles.dialog}>
            <Text style={styles.dialogTitle}>Новая заметка в {activeBrain}</Text>
            <TextInput
              value={newNotePath}
              onChangeText={setNewNotePath}
              placeholder="Путь, например notes/idea.md"
              placeholderTextColor={COLORS.textMuted}
              autoCapitalize="none"
              style={styles.dialogInput}
            />
            <TextInput
              value={newNoteBody}
              onChangeText={setNewNoteBody}
              placeholder="Текст заметки (Markdown)..."
              placeholderTextColor={COLORS.textMuted}
              multiline
              textAlignVertical="top"
              style={[styles.dialogInput, { height: 140 }]}
            />
            <View style={styles.dialogActions}>
              <Pressable onPress={() => setNewNoteModal(false)} style={styles.dialogCancel}>
                <Text style={styles.dialogCancelText}>Отмена</Text>
              </Pressable>
              <Pressable onPress={handleCreateNote} disabled={savingNote} style={styles.dialogConfirm}>
                <Text style={styles.dialogConfirmText}>Создать</Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>

      {/* New Brain Modal */}
      <Modal visible={newBrainModal} transparent animationType="fade" onRequestClose={() => setNewBrainModal(false)}>
        <View style={styles.overlay}>
          <View style={styles.dialog}>
            <Text style={styles.dialogTitle}>Создать новый Brain Vault</Text>
            <TextInput
              value={newBrainName}
              onChangeText={setNewBrainName}
              placeholder="Например: Work Brain"
              placeholderTextColor={COLORS.textMuted}
              style={styles.dialogInput}
            />
            <View style={styles.dialogActions}>
              <Pressable onPress={() => setNewBrainModal(false)} style={styles.dialogCancel}>
                <Text style={styles.dialogCancelText}>Отмена</Text>
              </Pressable>
              <Pressable onPress={handleCreateBrain} style={styles.dialogConfirm}>
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
  header: {
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
    backgroundColor: COLORS.surface,
    paddingBottom: 10,
  },
  brainScroll: { paddingHorizontal: 12, paddingTop: 10, paddingBottom: 8, gap: 8 },
  brainChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 999,
    backgroundColor: COLORS.card,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  brainChipActive: { backgroundColor: COLORS.amber, borderColor: COLORS.amber },
  brainChipText: { color: COLORS.textSecondary, fontSize: 13, fontWeight: "600" },
  brainChipTextActive: { color: "#09090b" },
  addChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 7,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: COLORS.amberBorder,
    backgroundColor: COLORS.amberSoft,
  },
  addChipText: { color: COLORS.amber, fontSize: 13, fontWeight: "600" },
  searchRow: { flexDirection: "row", alignItems: "center", paddingHorizontal: 12, gap: 8 },
  searchBox: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: COLORS.bg,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: COLORS.border,
    paddingHorizontal: 12,
    height: 40,
  },
  searchInput: { flex: 1, color: COLORS.text, fontSize: 14 },
  iconBtn: {
    width: 40,
    height: 40,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: COLORS.border,
    backgroundColor: COLORS.card,
    alignItems: "center",
    justifyContent: "center",
  },
  newNoteBtn: {
    width: 40,
    height: 40,
    borderRadius: 12,
    backgroundColor: COLORS.amber,
    alignItems: "center",
    justifyContent: "center",
  },
  listContent: { padding: 12, gap: 10, paddingBottom: 32 },
  emptyText: { color: COLORS.textMuted, textAlign: "center", marginTop: 40, fontSize: 14 },
  noteCard: {
    backgroundColor: COLORS.card,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: COLORS.border,
    padding: 12,
    gap: 4,
  },
  noteTop: { flexDirection: "row", alignItems: "center", gap: 8 },
  noteTitle: { flex: 1, color: COLORS.text, fontSize: 15, fontWeight: "600" },
  notePath: { color: COLORS.textMuted, fontSize: 12, fontFamily: "monospace" },
  noteSnippet: { color: COLORS.textSecondary, fontSize: 13, marginTop: 4, lineHeight: 18 },
  tagRow: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 6 },
  tagBadge: {
    backgroundColor: COLORS.cardAlt,
    borderRadius: 6,
    paddingHorizontal: 7,
    paddingVertical: 2,
  },
  tagText: { color: COLORS.textSecondary, fontSize: 11 },
  modalContainer: { flex: 1, backgroundColor: COLORS.bg, paddingTop: 48 },
  modalHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
    backgroundColor: COLORS.surface,
  },
  modalBtn: { padding: 4 },
  modalTitle: { flex: 1, color: COLORS.text, fontSize: 14, fontWeight: "600" },
  editPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: COLORS.amberBorder,
    backgroundColor: COLORS.amberSoft,
  },
  editPillText: { color: COLORS.amber, fontSize: 13, fontWeight: "600" },
  savePill: {
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 999,
    backgroundColor: COLORS.amber,
  },
  savePillText: { color: "#09090b", fontSize: 13, fontWeight: "700" },
  editorInput: {
    flex: 1,
    padding: 16,
    color: COLORS.text,
    fontSize: 14,
    lineHeight: 21,
  },
  readerScroll: { flex: 1 },
  readerContent: { padding: 16, paddingBottom: 48 },
  readerText: { color: COLORS.text, fontSize: 15, lineHeight: 22 },
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
