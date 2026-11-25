import { useEffect, useMemo, useRef, useState } from "react";
import {
  Brain,
  CornerDownLeft,
  Cpu,
  FileText,
  ListChecks,
  MessageSquare,
  Plug,
  Puzzle,
  Radio,
  Search,
  Sparkles,
  Terminal,
  X,
} from "lucide-react";
import { api } from "../api/client";

export interface PaletteNavItem {
  id: string;
  label: string;
  category: "Навигация" | "Чаты" | "Задачи" | "Второй мозг";
  subtitle?: string;
  page: string;
}

interface CommandPaletteProps {
  open: boolean;
  activeBrain: string;
  navItems: Array<{ id: string; label: string }>;
  onSelectPage: (page: string) => void;
  onClose: () => void;
}

const ICON_BY_PAGE: Record<string, typeof MessageSquare> = {
  chat: MessageSquare,
  brains: Brain,
  studio: Puzzle,
  models: Cpu,
  connections: Plug,
  tasks: ListChecks,
  learn: Sparkles,
  channels: Radio,
  terminal: Terminal,
};

export function CommandPalette({
  open,
  activeBrain,
  navItems,
  onSelectPage,
  onClose,
}: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [dynamicItems, setDynamicItems] = useState<PaletteNavItem[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) {
      setQuery("");
      setActiveIndex(0);
      return;
    }
    window.setTimeout(() => inputRef.current?.focus(), 20);

    let cancelled = false;
    void Promise.all([
      api.listSessions(activeBrain).catch(() => ({ sessions: [] })),
      api.listTasks().catch(() => ({ tasks: [] })),
      api.listCloudFiles().catch(() => ({ files: [] })),
    ]).then(([sessRes, taskRes, filesRes]) => {
      if (cancelled) return;
      const items: PaletteNavItem[] = [];
      for (const s of (sessRes.sessions ?? []).slice(0, 15)) {
        items.push({
          id: `chat:${s.id}`,
          label: s.title || "Чат",
          subtitle: "История чатов",
          category: "Чаты",
          page: "chat",
        });
      }
      for (const tk of (taskRes.tasks ?? []).slice(0, 15)) {
        items.push({
          id: `task:${tk.id}`,
          label: tk.title,
          subtitle: `Статус: ${tk.status}`,
          category: "Задачи",
          page: "tasks",
        });
      }
      for (const f of (filesRes.files ?? []).slice(0, 20)) {
        items.push({
          id: `file:${f.path}`,
          label: f.name,
          subtitle: f.path,
          category: "Второй мозг",
          page: "brains",
        });
      }
      setDynamicItems(items);
    });

    return () => {
      cancelled = true;
    };
  }, [open, activeBrain]);

  const allItems = useMemo<PaletteNavItem[]>(() => {
    const base: PaletteNavItem[] = navItems.map((n) => ({
      id: `nav:${n.id}`,
      label: n.label,
      subtitle: "Раздел",
      category: "Навигация",
      page: n.id,
    }));
    const merged = [...base, ...dynamicItems];
    const q = query.trim().toLowerCase();
    if (!q) return merged.slice(0, 14);
    return merged
      .filter(
        (it) =>
          it.label.toLowerCase().includes(q) ||
          (it.subtitle || "").toLowerCase().includes(q) ||
          it.category.toLowerCase().includes(q)
      )
      .slice(0, 18);
  }, [navItems, dynamicItems, query]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  if (!open) return null;

  const choose = (item: PaletteNavItem | undefined) => {
    if (!item) return;
    onSelectPage(item.page);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/65 pt-[12vh] px-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-xl overflow-hidden rounded-2xl border border-zinc-700/80 bg-zinc-900/95 shadow-2xl shadow-black/60"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2.5 border-b border-zinc-800 px-4 py-3">
          <Search className="h-4 w-4 text-amber-400 shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setActiveIndex((i) => (allItems.length ? (i + 1) % allItems.length : 0));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setActiveIndex((i) => (allItems.length ? (i - 1 + allItems.length) % allItems.length : 0));
              } else if (e.key === "Enter") {
                e.preventDefault();
                choose(allItems[activeIndex]);
              } else if (e.key === "Escape") {
                e.preventDefault();
                onClose();
              }
            }}
            placeholder="Поиск разделов, чатов, задач и заметок..."
            className="flex-1 bg-transparent text-sm text-zinc-100 placeholder:text-zinc-500 outline-none"
          />
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="max-h-80 overflow-y-auto p-2">
          {allItems.length === 0 ? (
            <div className="py-8 text-center text-xs text-zinc-500">Ничего не найдено</div>
          ) : (
            allItems.map((item, idx) => {
              const Icon = item.category === "Второй мозг" ? FileText : ICON_BY_PAGE[item.page] ?? Search;
              const isSelected = idx === activeIndex;
              return (
                <button
                  key={item.id}
                  type="button"
                  onMouseEnter={() => setActiveIndex(idx)}
                  onClick={() => choose(item)}
                  className={`flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2 text-left transition ${
                    isSelected
                      ? "bg-amber-500/15 text-amber-200 border border-amber-500/30"
                      : "text-zinc-300 border border-transparent hover:bg-zinc-800/60"
                  }`}
                >
                  <div className="flex min-w-0 items-center gap-2.5">
                    <Icon className={`h-4 w-4 shrink-0 ${isSelected ? "text-amber-400" : "text-zinc-400"}`} />
                    <div className="min-w-0">
                      <div className="truncate text-xs font-medium">{item.label}</div>
                      {item.subtitle && (
                        <div className="truncate text-[10px] text-zinc-500">{item.subtitle}</div>
                      )}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <span className="rounded bg-zinc-800/90 px-1.5 py-0.5 text-[10px] text-zinc-400">
                      {item.category}
                    </span>
                    {isSelected && <CornerDownLeft className="h-3 w-3 text-amber-400" />}
                  </div>
                </button>
              );
            })
          )}
        </div>

        <div className="flex items-center justify-between border-t border-zinc-800 bg-zinc-950/60 px-4 py-2 text-[10px] text-zinc-500">
          <span>↑↓ навигация · Enter открыть · Esc закрыть</span>
          <span className="font-mono">Cmd+K / Ctrl+K</span>
        </div>
      </div>
    </div>
  );
}
