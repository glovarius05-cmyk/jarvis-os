import { useCallback, useEffect, useState } from "react";
import { Brain, Cpu, ListChecks, MessageSquare, Plug, Puzzle, Radio, Sparkles, Terminal } from "lucide-react";
import { api, type BrainItem, type OnboardingStatus, type ProvidersResponse, type SystemStatus } from "./api/client";
import { BrainsView } from "./components/BrainsView";
import { ChannelsView } from "./components/ChannelsView";
import { ChatView } from "./components/ChatView";
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

  const { connected, liveTurns, errors, sendMessage, stopTurn } = useChatSocket({
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
      <aside className="flex w-56 shrink-0 flex-col border-r border-zinc-800 bg-zinc-900/60 p-4">
        <div className="mb-6 flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-amber-500/15 text-amber-400">
            <Brain className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-sm font-semibold tracking-tight">{t("app.title")}</h1>
            <p className="text-xs text-zinc-400">v{status?.version ?? "0.1.0"}</p>
          </div>
        </div>

        <nav className="flex flex-1 flex-col gap-1">
          {navItems.map((item) => {
            const Icon = item.icon;
            const active = page === item.id;
            return (
              <button
                key={item.id}
                onClick={() => setPage(item.id)}
                className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm transition ${
                  active
                    ? "bg-amber-500/15 font-medium text-amber-300"
                    : "text-zinc-400 hover:bg-zinc-800/70 hover:text-zinc-100"
                }`}
              >
                <Icon className="h-4 w-4" />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="mt-4 flex items-center justify-between border-t border-zinc-800 pt-3">
          <span className="flex items-center gap-1.5 text-xs text-zinc-400">
            <span className={`h-2 w-2 rounded-full ${status ? "bg-emerald-400" : "bg-rose-400"}`} />
            {status ? t("status.online") : t("status.offline")}
          </span>
          <button
            onClick={() => setLang(lang === "ru" ? "en" : "ru")}
            className="rounded border border-zinc-700 px-2 py-0.5 text-xs font-medium text-zinc-300 hover:border-zinc-500"
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
    </div>
  );
}
