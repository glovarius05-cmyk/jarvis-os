import { useCallback, useEffect, useState } from "react";
import {
  Brain,
  Cpu,
  ListChecks,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Plug,
  Puzzle,
  Radio,
  Search,
  Sparkles,
  Terminal,
} from "lucide-react";
import { api, type BrainItem, type OnboardingStatus, type ProvidersResponse, type SystemStatus } from "./api/client";
import { BrainsView } from "./components/BrainsView";
import { ChannelsView } from "./components/ChannelsView";
import { ChatView } from "./components/ChatView";
import { CommandPalette } from "./components/CommandPalette";
import { ConnectionsView } from "./components/ConnectionsView";
import { LearnUsageView } from "./components/LearnUsageView";
import { ModelsView } from "./components/ModelsView";
import { OnboardingModal } from "./components/OnboardingModal";
import { StudioView } from "./components/StudioView";
import { TasksView } from "./components/TasksView";
import { TerminalView } from "./components/TerminalView";
import { useChatSocket } from "./hooks/useChatSocket";
import { translate, type UiLang } from "./i18n";

type PageId = "chat" | "brains" | "studio" | "models" | "connections" | "tasks" | "learn" | "channels" | "terminal";

export function App() {
  const [lang, setLang] = useState<UiLang>("ru");
  const [page, setPage] = useState<PageId>("chat");
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [brains, setBrains] = useState<BrainItem[]>([]);
  const [providersData, setProvidersData] = useState<ProvidersResponse | null>(null);
  const [chatRefreshTick, setChatRefreshTick] = useState(0);
  const [onboardingStatus, setOnboardingStatus] = useState<OnboardingStatus | null>(null);
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [cmdOpen, setCmdOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem("jarvis_sidebar_collapsed") === "true";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCmdOpen((prev) => !prev);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const toggleSidebar = () => {
    setSidebarCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem("jarvis_sidebar_collapsed", String(next));
      } catch {}
      return next;
    });
  };

  const t = (key: string) => translate(lang, key);

  const refreshAll = useCallback(async () => {
    try {
      const [st, br, pr, onb] = await Promise.all([
        api.getSystemStatus(),
        api.listBrains(),
        api.listProviders(),
        api.getOnboardingStatus().catch(() => null),
      ]);
      setStatus(st);
      setBrains(br.brains);
      setProvidersData(pr);
      if (onb && !onb.completed) {
        setOnboardingStatus(onb);
        setShowOnboarding(true);
      }
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  const { connected, liveTurns, errors, pendingApprovals, resolveApproval, sendMessage, stopTurn } = useChatSocket({
    onFinal: () => setChatRefreshTick((n) => n + 1),
    onTurnStart: () => setChatRefreshTick((n) => n + 1),
  });

  const navItems: { id: PageId; label: string; icon: typeof MessageSquare }[] = [
    { id: "chat", label: t("nav.chat"), icon: MessageSquare },
    { id: "brains", label: t("nav.brains"), icon: Brain },
    { id: "studio", label: t("nav.studio"), icon: Puzzle },
    { id: "models", label: t("nav.models"), icon: Cpu },
    { id: "connections", label: t("nav.connections"), icon: Plug },
    { id: "tasks", label: t("nav.tasks"), icon: ListChecks },
    { id: "learn", label: t("nav.learn"), icon: Sparkles },
    { id: "channels", label: t("nav.channels"), icon: Radio },
    { id: "terminal", label: t("nav.terminal"), icon: Terminal },
  ];

  const activeBrain = status?.active_brain ?? "Brain Default";

  return (
    <div className="flex min-h-screen bg-zinc-950 text-zinc-100">
      <aside
        className={`flex shrink-0 flex-col border-r border-zinc-800/80 bg-zinc-900/70 backdrop-blur-md transition-all duration-200 ${
          sidebarCollapsed ? "w-16 p-2.5 items-center" : "w-60 p-4"
        }`}
      >
        <div className={`mb-4 flex items-center ${sidebarCollapsed ? "justify-center" : "justify-between gap-2"}`}>
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-amber-500/30 bg-amber-500/15 text-amber-400 shadow-sm shadow-amber-500/10">
              <Brain className="h-5 w-5" />
            </div>
            {!sidebarCollapsed && (
              <div className="min-w-0">
                <h1 className="truncate text-sm font-semibold tracking-tight">{t("app.title")}</h1>
                <p className="text-[11px] text-zinc-400">v{status?.version ?? "0.1.0"}</p>
              </div>
            )}
          </div>
          {!sidebarCollapsed && (
            <button
              type="button"
              onClick={toggleSidebar}
              title="Свернуть боковое меню"
              className="rounded-lg p-1.5 text-zinc-500 hover:bg-zinc-800/80 hover:text-zinc-200 transition"
            >
              <PanelLeftClose className="h-4 w-4" />
            </button>
          )}
        </div>

        {sidebarCollapsed ? (
          <div className="mb-3 flex flex-col items-center gap-1.5">
            <button
              type="button"
              onClick={toggleSidebar}
              title="Развернуть боковое меню"
              className="flex h-9 w-9 items-center justify-center rounded-lg text-zinc-400 hover:bg-zinc-800/70 hover:text-zinc-100 transition"
            >
              <PanelLeftOpen className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={() => setCmdOpen(true)}
              title="Быстрый поиск (Cmd+K)"
              className="flex h-9 w-9 items-center justify-center rounded-lg border border-zinc-800 bg-zinc-950/60 text-zinc-400 hover:border-amber-500/40 hover:text-amber-300 transition"
            >
              <Search className="h-4 w-4" />
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setCmdOpen(true)}
            className="mb-4 flex w-full items-center justify-between gap-2 rounded-xl border border-zinc-800 bg-zinc-950/70 px-3 py-2 text-xs text-zinc-400 hover:border-amber-500/40 hover:text-zinc-200 transition"
          >
            <span className="flex items-center gap-2">
              <Search className="h-3.5 w-3.5 text-zinc-500" />
              <span>Быстрый поиск...</span>
            </span>
            <kbd className="rounded border border-zinc-700/80 bg-zinc-900 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
              ⌘K
            </kbd>
          </button>
        )}

        <nav className={`flex flex-1 flex-col gap-1 ${sidebarCollapsed ? "w-full items-center" : ""}`}>
          {navItems.map((item) => {
            const Icon = item.icon;
            const active = page === item.id;
            return (
              <button
                key={item.id}
                onClick={() => setPage(item.id)}
                title={sidebarCollapsed ? item.label : undefined}
                className={`flex items-center rounded-xl transition ${
                  sidebarCollapsed
                    ? "h-10 w-10 justify-center"
                    : "gap-2.5 px-3 py-2 text-left text-sm"
                } ${
                  active
                    ? "bg-amber-500/15 font-medium text-amber-300 border border-amber-500/25"
                    : "text-zinc-400 border border-transparent hover:bg-zinc-800/70 hover:text-zinc-100"
                }`}
              >
                <Icon className="h-4 w-4 shrink-0" />
                {!sidebarCollapsed && <span className="truncate">{item.label}</span>}
              </button>
            );
          })}
        </nav>

        <div
          className={`mt-4 flex items-center border-t border-zinc-800/80 pt-3 ${
            sidebarCollapsed ? "flex-col gap-2" : "justify-between"
          }`}
        >
          <span
            className="flex items-center gap-1.5 text-xs text-zinc-400"
            title={status ? t("status.online") : t("status.offline")}
          >
            <span className={`h-2 w-2 rounded-full ${status ? "bg-emerald-400" : "bg-rose-400"}`} />
            {!sidebarCollapsed && <span>{status ? t("status.online") : t("status.offline")}</span>}
          </span>
          <button
            onClick={() => setLang(lang === "ru" ? "en" : "ru")}
            className="rounded border border-zinc-700 px-2 py-0.5 text-[11px] font-medium text-zinc-300 hover:border-zinc-500"
          >
            {lang.toUpperCase()}
          </button>
        </div>
      </aside>

      <main className="min-w-0 flex-1">
        {page === "chat" && (
          <ChatView
            activeBrain={activeBrain}
            providers={providersData?.providers ?? []}
            defaultProvider={status?.model.main_provider ?? "openrouter"}
            defaultModel={status?.model.main_model ?? "openrouter/auto"}
            wsConnected={connected}
            liveTurns={liveTurns}
            errors={errors}
            onSend={sendMessage}
            onStop={stopTurn}
            pendingApprovals={pendingApprovals}
            onResolveApproval={resolveApproval}
            onNavigate={(target) => {
              if (["chat", "brains", "studio", "models", "connections", "tasks", "learn", "channels"].includes(target)) {
                setPage(target as PageId);
              }
            }}
            refreshTrigger={chatRefreshTick}
            t={t}
          />
        )}
        {page === "brains" && (
          <BrainsView activeBrain={activeBrain} brains={brains} onUpdated={refreshAll} t={t} />
        )}
        {page === "studio" && <StudioView />}
        {page === "models" && (
          <ModelsView data={providersData} onUpdated={refreshAll} t={t} />
        )}
        {page === "connections" && <ConnectionsView />}
        {page === "tasks" && <TasksView />}
        {page === "learn" && <LearnUsageView />}
        {page === "channels" && <ChannelsView />}
        {page === "terminal" && <TerminalView />}
      </main>

      {showOnboarding && onboardingStatus && (
        <OnboardingModal
          status={onboardingStatus}
          onCompleted={() => {
            setShowOnboarding(false);
            void refreshAll();
          }}
          onDismiss={() => setShowOnboarding(false)}
        />
      )}

      <CommandPalette
        open={cmdOpen}
        activeBrain={activeBrain}
        navItems={navItems.map((n) => ({ id: n.id, label: n.label }))}
        onSelectPage={(target) => setPage(target as PageId)}
        onClose={() => setCmdOpen(false)}
      />
    </div>
  );
}
