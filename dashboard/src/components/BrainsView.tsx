import { useCallback, useEffect, useMemo, useState } from "react";
import { Brain, Cloud, Download, FileText, FolderDown, GitBranch, Network, Radio, RefreshCw, Save, Search, X } from "lucide-react";
import { api, type BrainItem, type BrainSearchHit, type BrainSyncStatus, type GitRemoteStatus, type GraphResponse } from "../api/client";

interface BrainsViewProps {
  activeBrain: string;
  brains: BrainItem[];
  onUpdated: () => Promise<void>;
  t: (key: string) => string;
}

const GROUP_COLORS: Record<string, string> = {
  memory: "#f59e0b",
  wiki: "#38bdf8",
  sources: "#a78bfa",
  agents: "#34d399",
  workflows: "#fb7185",
  skills: "#facc15",
  javis: "#94a3b8",
  unresolved: "#52525b",
  root: "#e4e4e7",
};

export function BrainsView({ activeBrain, brains, onUpdated, t }: BrainsViewProps) {
  const [newBrain, setNewBrain] = useState("");
  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [selectedPath, setSelectedPath] = useState<string>("memory/MEMORY.md");
  const [editorContent, setEditorContent] = useState<string>("");
  const [savedNotice, setSavedNotice] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<BrainSearchHit[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [reindexStatus, setReindexStatus] = useState<string | null>(null);
  const [syncStatus, setSyncStatus] = useState<BrainSyncStatus | null>(null);
  const [isSyncing, setIsSyncing] = useState(false);
  const [gitRemote, setGitRemote] = useState<GitRemoteStatus | null>(null);
  const [showRemoteModal, setShowRemoteModal] = useState(false);
  const [remoteUrlInput, setRemoteUrlInput] = useState("");
  const [isGitSyncing, setIsGitSyncing] = useState(false);
  const [gitSyncMsg, setGitSyncMsg] = useState<string | null>(null);

  const loadGitRemote = useCallback(async () => {
    try {
      const s = await api.getGitRemoteStatus();
      setGitRemote(s);
      if (s.remote_url) setRemoteUrlInput(s.remote_url);
    } catch {
      // ignore
    }
  }, []);

  const handleSaveRemoteUrl = async () => {
    try {
      const s = await api.configureGitRemote(remoteUrlInput);
      setGitRemote(s);
      setGitSyncMsg("Remote URL saved");
      window.setTimeout(() => setGitSyncMsg(null), 2500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to configure remote");
    }
  };

  const handleGitSync = async (action: "all" | "pull" | "push" = "all") => {
    setIsGitSyncing(true);
    setGitSyncMsg(`Syncing (${action})...`);
    try {
      const res = await api.syncGitRemote(action);
      if (res.ok) {
        setGitSyncMsg(res.message || "Synced successfully");
        setGitRemote(res.status);
      } else {
        setGitSyncMsg(res.error || "Git sync error");
      }
      window.setTimeout(() => setGitSyncMsg(null), 4000);
      void loadGraph();
    } catch (err) {
      setGitSyncMsg(err instanceof Error ? err.message : "Sync failed");
      window.setTimeout(() => setGitSyncMsg(null), 4000);
    } finally {
      setIsGitSyncing(false);
    }
  };

  const loadSyncStatus = useCallback(async () => {
    try {
      const s = await api.getSyncStatus();
      setSyncStatus(s);
    } catch {
      // ignore
    }
  }, []);

  const handleTriggerSync = async () => {
    setIsSyncing(true);
    try {
      await api.triggerSync(false);
      await loadSyncStatus();
      await loadGraph();
    } catch {
      // ignore
    } finally {
      setIsSyncing(false);
    }
  };

  const handleSearch = async (q: string) => {
    setSearchQuery(q);
    if (!q.trim()) {
      setSearchResults([]);
      return;
    }
    setIsSearching(true);
    try {
      const res = await api.searchBrain(q.trim(), 5);
      setSearchResults(res.hits || []);
    } catch {
      setSearchResults([]);
    } finally {
      setIsSearching(false);
    }
  };

  const handleReindex = async () => {
    setReindexStatus("Indexing...");
    try {
      const res = await api.reindexBrain(true);
      setReindexStatus(`Indexed ${res.indexed_files} files (${res.total_chunks} chunks)`);
      window.setTimeout(() => setReindexStatus(null), 3000);
      void loadGraph();
    } catch {
      setReindexStatus("Reindex failed");
    }
  };

  const handleApplyTemplate = async (templateId: string) => {
    try {
      const res = await api.applyCharacterTemplate(templateId);
      setEditorContent(res.character.raw_markdown);
      setSavedNotice(true);
      window.setTimeout(() => setSavedNotice(false), 1500);
      void loadGraph();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to apply character template");
    }
  };

  const loadGraph = useCallback(async () => {
    try {
      const g = await api.getBrainGraph();
      setGraph(g);
    } catch {
      setGraph(null);
    }
  }, []);

  const openFile = useCallback(async (relPath: string) => {
    if (relPath.startsWith("unresolved:")) return;
    setSelectedPath(relPath);
    try {
      const res = await api.readBrainFile(relPath);
      setEditorContent(res.content);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to open file");
    }
  }, []);

  useEffect(() => {
    void loadGraph();
    void openFile("memory/MEMORY.md");
    void loadSyncStatus();
    void loadGitRemote();
  }, [activeBrain, loadGraph, openFile, loadSyncStatus, loadGitRemote]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newBrain.trim()) return;
    try {
      await api.createBrain(newBrain.trim(), true);
      setNewBrain("");
      setError(null);
      await onUpdated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create brain");
    }
  };

  const handleSwitch = async (name: string) => {
    await api.patchSettings({ active_brain: name });
    await onUpdated();
  };

  const handleSaveFile = async () => {
    try {
      await api.writeBrainFile(selectedPath, editorContent);
      setSavedNotice(true);
      window.setTimeout(() => setSavedNotice(false), 1500);
      await loadGraph();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save file");
    }
  };

  // Deterministic circular/radial layout for lightweight offline SVG Knowledge Graph
  const layout = useMemo(() => {
    const nodes = graph?.nodes ?? [];
    const cx = 260;
    const cy = 180;
    const positions: Record<string, { x: number; y: number }> = {};
    nodes.forEach((n, idx) => {
      const angle = (2 * Math.PI * idx) / Math.max(1, nodes.length);
      const ring = n.degree > 0 ? 115 : 148;
      positions[n.id] = {
        x: cx + ring * Math.cos(angle),
        y: cy + ring * Math.sin(angle),
      };
    });
    return positions;
  }, [graph]);

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-8">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-zinc-800 pb-4">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">{t("nav.brains")}</h2>
          <p className="text-sm text-zinc-400">
            {t("brain.active")}: <span className="font-medium text-amber-300">{activeBrain}</span>
            {graph && ` · ${graph.stats.total_notes} notes · ${graph.stats.total_edges} [[wikilinks]]`}
          </p>
        </div>
        <form onSubmit={handleCreate} className="flex gap-2">
          <input
            value={newBrain}
            onChange={(e) => setNewBrain(e.target.value)}
            placeholder={t("brain.createPlaceholder")}
            className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-sm text-zinc-100 outline-none focus:border-amber-400"
          />
          <button
            type="submit"
            className="rounded-lg bg-amber-500 px-3 py-1.5 text-sm font-medium text-zinc-950 hover:bg-amber-400"
          >
            {t("brain.createButton")}
          </button>
        </form>
      </header>

      {error && (
        <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-200">
          {error}
        </div>
      )}

      {/* Brain switcher strip */}
      <div className="grid gap-2.5 sm:grid-cols-3">
        {brains.map((b) => (
          <div
            key={b.name}
            className={`flex items-center justify-between rounded-xl border p-3 ${
              b.active ? "border-amber-500/50 bg-amber-500/10" : "border-zinc-800 bg-zinc-900/50"
            }`}
          >
            <div className="flex items-center gap-2.5">
              <Brain className={`h-4 w-4 ${b.active ? "text-amber-400" : "text-zinc-500"}`} />
              <div>
                <p className="text-xs font-semibold text-zinc-100">{b.name}</p>
                <p className="flex items-center gap-1 text-[11px] text-zinc-400">
                  <GitBranch className="h-3 w-3 text-emerald-400" />
                  <span>{b.note_count} md</span>
                </p>
              </div>
            </div>
            {!b.active && (
              <button
                onClick={() => void handleSwitch(b.name)}
                className="rounded border border-zinc-700 px-2 py-0.5 text-[11px] text-zinc-200 hover:border-amber-500"
              >
                {t("brain.switch")}
              </button>
            )}
          </div>
        ))}
      </div>

      {/* Knowledge Graph + Markdown Editor split */}
      <div className="grid gap-4 lg:grid-cols-2">
        <section className="flex flex-col rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
          <div className="mb-3 flex items-center justify-between gap-2 text-sm font-semibold text-zinc-200">
            <span className="flex items-center gap-2">
              <Network className="h-4 w-4 text-amber-400" />
              <span>Knowledge Graph &amp; Hybrid Search</span>
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setShowRemoteModal(true)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-900 px-2.5 py-1 text-xs font-normal text-zinc-300 hover:border-sky-500 hover:text-zinc-100"
                title="Configure and sync remote Git repository (GitHub/GitLab)"
              >
                <Cloud className={`h-3 w-3 ${gitRemote?.configured ? "text-sky-400" : "text-zinc-500"}`} />
                <span>{gitRemote?.configured ? (gitRemote.ahead > 0 ? `Push (${gitRemote.ahead})` : "Cloud Sync") : "Set Remote"}</span>
              </button>
              <button
                type="button"
                onClick={() => void handleTriggerSync()}
                disabled={isSyncing}
                className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-900 px-2.5 py-1 text-xs font-normal text-zinc-300 hover:border-emerald-500 hover:text-zinc-100 disabled:opacity-50"
                title="Sync external notes from Obsidian/disk & git commit"
              >
                <Radio className={`h-3 w-3 ${syncStatus?.running ? "text-emerald-400 animate-pulse" : "text-zinc-500"}`} />
                <span>{isSyncing ? "Syncing..." : "Obsidian Sync"}</span>
              </button>
              <button
                type="button"
                onClick={() => void handleReindex()}
                className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-900 px-2.5 py-1 text-xs font-normal text-zinc-300 hover:border-amber-500 hover:text-zinc-100"
                title="Index/re-index brain for hybrid semantic search"
              >
                <RefreshCw className="h-3 w-3" />
                <span>{reindexStatus || "Re-index RAG"}</span>
              </button>
              <a
                href={api.getBrainZipUrl(activeBrain)}
                download={`jarvis_brain_${activeBrain.replace(/\s+/g, "_")}.zip`}
                className="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/10 px-2.5 py-1 text-xs font-medium text-amber-300 hover:bg-amber-500/20 hover:text-amber-200 transition"
                title="Скачать весь архив файлов и базы знаний из облака VPS (ZIP)"
              >
                <FolderDown className="h-3 w-3 text-amber-400" />
                <span>Скачать всё (ZIP)</span>
              </a>
            </div>
          </div>

          {syncStatus && (
            <div className="mb-2.5 flex items-center justify-between rounded-md bg-zinc-950/60 px-2.5 py-1 text-[11px] text-zinc-400 border border-zinc-800/60">
              <span className="flex items-center gap-1.5 truncate">
                <span className={`inline-block h-2 w-2 rounded-full flex-shrink-0 ${syncStatus.running ? "bg-emerald-400" : "bg-zinc-500"}`} />
                <span>Obsidian Watcher: {syncStatus.running ? "Live" : "Idle"}</span>
                {syncStatus.last_synced_path && (
                  <span className="text-zinc-500 truncate max-w-[180px]">• Last: {syncStatus.last_synced_path}</span>
                )}
              </span>
              <span className="text-zinc-500 flex-shrink-0 ml-2">Events: {syncStatus.total_events_processed}</span>
            </div>
          )}

          {/* Hybrid Semantic Search Bar */}
          <div className="relative mb-3">
            <Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-zinc-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => void handleSearch(e.target.value)}
              placeholder="Поиск по заметкам (BM25 + Semantic RAG)..."
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 py-1.5 pl-8 pr-3 text-xs text-zinc-100 outline-none focus:border-amber-500"
            />
            {isSearching && (
              <span className="absolute right-3 top-2 text-[10px] text-zinc-500">Searching...</span>
            )}
          </div>

          {searchResults.length > 0 && (
            <div className="mb-3 space-y-1.5 max-h-48 overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-950/80 p-2 text-xs">
              {searchResults.map((h, i) => (
                <div
                  key={i}
                  onClick={() => void openFile(h.path)}
                  className="cursor-pointer rounded p-1.5 hover:bg-zinc-800/60 transition"
                >
                  <div className="flex items-center justify-between gap-1 text-[11px]">
                    <span className="font-mono font-medium text-sky-400 truncate">{h.path} &gt; {h.heading}</span>
                    <span className="rounded bg-emerald-500/10 px-1.5 py-0.5 text-[10px] text-emerald-400 font-mono">
                      {h.score}% • {h.match_type}
                    </span>
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-[11px] text-zinc-400">{h.snippet}</p>
                </div>
              ))}
            </div>
          )}

          <svg viewBox="0 0 520 360" className="h-80 w-full rounded-lg bg-zinc-950/90">
            {(graph?.edges ?? []).map((e, idx) => {
              const a = layout[e.source];
              const b = layout[e.target];
              if (!a || !b) return null;
              return (
                <line
                  key={idx}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke="#3f3f46"
                  strokeWidth={1.2}
                />
              );
            })}
            {(graph?.nodes ?? []).map((n) => {
              const pt = layout[n.id];
              if (!pt) return null;
              const isSelected = n.id === selectedPath;
              const fill = GROUP_COLORS[n.group] ?? "#e4e4e7";
              return (
                <g
                  key={n.id}
                  onClick={() => void openFile(n.id)}
                  className="cursor-pointer"
                >
                  <circle
                    cx={pt.x}
                    cy={pt.y}
                    r={isSelected ? 9 : Math.min(8, 4.5 + n.degree)}
                    fill={fill}
                    stroke={isSelected ? "#ffffff" : "#18181b"}
                    strokeWidth={isSelected ? 2 : 1}
                  />
                  <text
                    x={pt.x}
                    y={pt.y + 15}
                    textAnchor="middle"
                    fill="#a1a1aa"
                    fontSize={9}
                  >
                    {n.label.slice(0, 18)}
                  </text>
                </g>
              );
            })}
          </svg>
        </section>

        <section className="flex flex-col rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
          <div className="mb-3 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <span className="flex items-center gap-1.5 font-mono text-xs text-zinc-300">
                <FileText className="h-4 w-4 text-sky-400" />
                {selectedPath}
              </span>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => void openFile("memory/CHARACTER.md")}
                  className={`rounded px-2 py-0.5 text-[11px] font-medium transition ${
                    selectedPath === "memory/CHARACTER.md"
                      ? "bg-amber-500/20 text-amber-300 border border-amber-500/40"
                      : "bg-zinc-800 text-zinc-400 hover:text-zinc-200"
                  }`}
                  title="Brain Persona & Character Card (CHARACTER.md)"
                >
                  🎭 Character
                </button>
                <button
                  type="button"
                  onClick={() => void openFile("memory/CORE_MEMORY.md")}
                  className={`rounded px-2 py-0.5 text-[11px] font-medium transition ${
                    selectedPath === "memory/CORE_MEMORY.md"
                      ? "bg-amber-500/20 text-amber-300 border border-amber-500/40"
                      : "bg-zinc-800 text-zinc-400 hover:text-zinc-200"
                  }`}
                  title="Core Memory (Always-in-context self-editing state)"
                >
                  🧠 Core Memory
                </button>
                <button
                  type="button"
                  onClick={() => void openFile("memory/MEMORY.md")}
                  className={`rounded px-2 py-0.5 text-[11px] font-medium transition ${
                    selectedPath === "memory/MEMORY.md"
                      ? "bg-amber-500/20 text-amber-300 border border-amber-500/40"
                      : "bg-zinc-800 text-zinc-400 hover:text-zinc-200"
                  }`}
                  title="Long-Term Memory Index"
                >
                  📜 Index
                </button>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <a
                href={api.getBrainRawUrl(selectedPath, true)}
                download={selectedPath.split("/").pop() || "file.md"}
                className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-800 px-2.5 py-1 text-xs font-medium text-zinc-200 hover:bg-zinc-700 hover:text-white transition"
                title="Скачать этот файл из облака VPS на компьютер"
              >
                <Download className="h-3.5 w-3.5 text-sky-400" />
                <span>Скачать файл</span>
              </a>
              <button
                onClick={() => void handleSaveFile()}
                className="inline-flex items-center gap-1.5 rounded-lg bg-amber-500 px-3 py-1 text-xs font-medium text-zinc-950 hover:bg-amber-400"
              >
                <Save className="h-3.5 w-3.5" />
                <span>{savedNotice ? "Saved" : "Save"}</span>
              </button>
            </div>
          </div>

          {selectedPath === "memory/CHARACTER.md" && (
            <div className="mb-2.5 flex flex-wrap items-center gap-1.5 rounded-lg border border-zinc-800/80 bg-zinc-950/80 p-2 text-xs">
              <span className="text-zinc-400 font-medium mr-1">Templates:</span>
              <button
                type="button"
                onClick={() => void handleApplyTemplate("default")}
                className="rounded border border-zinc-700 bg-zinc-900 px-2 py-0.5 text-[11px] text-zinc-300 hover:border-amber-400 transition"
              >
                🧠 Default
              </button>
              <button
                type="button"
                onClick={() => void handleApplyTemplate("architect")}
                className="rounded border border-zinc-700 bg-zinc-900 px-2 py-0.5 text-[11px] text-zinc-300 hover:border-amber-400 transition"
              >
                🛡️ Architect
              </button>
              <button
                type="button"
                onClick={() => void handleApplyTemplate("researcher")}
                className="rounded border border-zinc-700 bg-zinc-900 px-2 py-0.5 text-[11px] text-zinc-300 hover:border-amber-400 transition"
              >
                🔬 Researcher
              </button>
              <button
                type="button"
                onClick={() => void handleApplyTemplate("writer")}
                className="rounded border border-zinc-700 bg-zinc-900 px-2 py-0.5 text-[11px] text-zinc-300 hover:border-amber-400 transition"
              >
                ✍️ Writer
              </button>
              <button
                type="button"
                onClick={() => void handleApplyTemplate("executive")}
                className="rounded border border-zinc-700 bg-zinc-900 px-2 py-0.5 text-[11px] text-zinc-300 hover:border-amber-400 transition"
              >
                ⚡ Executive
              </button>
            </div>
          )}
          <textarea
            value={editorContent}
            onChange={(e) => setEditorContent(e.target.value)}
            className="h-80 w-full flex-1 resize-none rounded-lg border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs leading-relaxed text-zinc-200 outline-none focus:border-amber-500"
          />
        </section>
      </div>

      {showRemoteModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-2xl border border-zinc-800 bg-zinc-900 p-6 shadow-2xl">
            <div className="flex items-center justify-between border-b border-zinc-800 pb-3">
              <div className="flex items-center gap-2">
                <Cloud className="h-5 w-5 text-sky-400" />
                <h3 className="font-semibold text-zinc-100">Second Brain Cloud Git Sync</h3>
              </div>
              <button
                onClick={() => setShowRemoteModal(false)}
                className="rounded p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="mt-4 space-y-4">
              <div>
                <label className="mb-1 block text-xs font-medium text-zinc-300">
                  Remote Git Origin URL (GitHub / GitLab)
                </label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={remoteUrlInput}
                    onChange={(e) => setRemoteUrlInput(e.target.value)}
                    placeholder="git@github.com:username/notes.git"
                    className="flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-xs text-zinc-100 outline-none focus:border-sky-500"
                  />
                  <button
                    onClick={() => void handleSaveRemoteUrl()}
                    className="rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:border-sky-500"
                  >
                    Save
                  </button>
                </div>
              </div>

              {gitRemote && (
                <div className="rounded-lg border border-zinc-800 bg-zinc-950/70 p-3 text-xs space-y-1.5 text-zinc-400">
                  <div className="flex justify-between">
                    <span>Active Brain:</span>
                    <span className="font-medium text-zinc-200">{activeBrain}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Branch:</span>
                    <span className="font-mono text-zinc-300">{gitRemote.branch || "main"}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Status:</span>
                    <span className={gitRemote.clean ? "text-emerald-400" : "text-amber-400"}>
                      {gitRemote.clean ? "Working tree clean" : "Uncommitted local notes"}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span>Sync State:</span>
                    <span className="text-zinc-300 font-mono">
                      Ahead: {gitRemote.ahead} | Behind: {gitRemote.behind}
                    </span>
                  </div>
                </div>
              )}

              {gitSyncMsg && (
                <div className="rounded-lg border border-sky-500/30 bg-sky-500/10 p-2.5 text-xs text-sky-200">
                  {gitSyncMsg}
                </div>
              )}

              <div className="flex flex-wrap gap-2 pt-2">
                <button
                  type="button"
                  disabled={!gitRemote?.configured || isGitSyncing}
                  onClick={() => void handleGitSync("pull")}
                  className="flex-1 rounded-lg border border-zinc-700 bg-zinc-800 py-2 text-xs font-medium text-zinc-200 hover:border-sky-500 disabled:opacity-40"
                >
                  Pull (Rebase)
                </button>
                <button
                  type="button"
                  disabled={!gitRemote?.configured || isGitSyncing}
                  onClick={() => void handleGitSync("push")}
                  className="flex-1 rounded-lg border border-zinc-700 bg-zinc-800 py-2 text-xs font-medium text-zinc-200 hover:border-sky-500 disabled:opacity-40"
                >
                  Push
                </button>
                <button
                  type="button"
                  disabled={!gitRemote?.configured || isGitSyncing}
                  onClick={() => void handleGitSync("all")}
                  className="w-full rounded-lg bg-sky-500 py-2 text-xs font-semibold text-zinc-950 hover:bg-sky-400 disabled:opacity-40"
                >
                  {isGitSyncing ? "Syncing..." : "Full Sync (Pull + Push)"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
