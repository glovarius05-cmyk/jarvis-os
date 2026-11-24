import { useCallback, useEffect, useState } from "react";
import { Bell, Check, Clock, ListChecks, Play, Plus, Repeat, Trash2, X } from "lucide-react";
import {
  api,
  type InboxItemInfo,
  type KanbanTaskInfo,
  type LoopInfo,
  type ReminderInfo,
} from "../api/client";

type WorkTab = "kanban" | "loops" | "reminders" | "inbox";

export function TasksView() {
  const [tab, setTab] = useState<WorkTab>("kanban");
  const [autoEnabled, setAutoEnabled] = useState(false);
  const [tasks, setTasks] = useState<KanbanTaskInfo[]>([]);
  const [loops, setLoops] = useState<LoopInfo[]>([]);
  const [reminders, setReminders] = useState<ReminderInfo[]>([]);
  const [inbox, setInbox] = useState<InboxItemInfo[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);

  const [taskTitle, setTaskTitle] = useState("");
  const [taskDesc, setTaskDesc] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});

  const [loopSlug, setLoopSlug] = useState("");
  const [loopName, setLoopName] = useState("");
  const [loopPrompt, setLoopPrompt] = useState("");
  const [loopInterval, setLoopInterval] = useState(60);

  const [remName, setRemName] = useState("");
  const [remCron, setRemCron] = useState("0 9 * * 1-5");
  const [remPrompt, setRemPrompt] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [tRes, lRes, rRes, iRes] = await Promise.all([
        api.listTasks(),
        api.listLoops(),
        api.listReminders(),
        api.listInbox(),
      ]);
      setAutoEnabled(tRes.auto_enabled);
      setTasks(tRes.tasks);
      setLoops(lRes.loops);
      setReminders(rRes.reminders);
      setInbox(iRes.items);
      setUnreadCount(iRes.unread_count);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load background work");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleAddTask = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!taskTitle.trim()) return;
    await api.createTask({ title: taskTitle.trim(), description: taskDesc.trim(), mode: "auto" });
    setTaskTitle("");
    setTaskDesc("");
    await refresh();
  };

  const handleRunTask = async (id: string) => {
    setBusyId(id);
    try {
      await api.runTaskNow(id);
      await refresh();
    } finally {
      setBusyId(null);
    }
  };

  const handleAddLoop = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.createLoop({
        slug: loopSlug.trim().toLowerCase(),
        name: loopName.trim(),
        prompt: loopPrompt.trim(),
        interval_min: loopInterval,
        enabled: false,
      });
      setLoopSlug("");
      setLoopName("");
      setLoopPrompt("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create loop");
    }
  };

  const handleAddReminder = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.createReminder({
        name: remName.trim(),
        cron: remCron.trim(),
        prompt: remPrompt.trim(),
        allow_missing: true,
      });
      setRemName("");
      setRemPrompt("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create reminder");
    }
  };

  const handleRunReminder = async (id: string) => {
    setBusyId(id);
    setError(null);
    try {
      await api.runReminder(id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to run reminder");
    } finally {
      setBusyId(null);
    }
  };

  const tabs: { id: WorkTab; label: string; count: number; icon: typeof ListChecks }[] = [
    { id: "kanban", label: "Kanban", count: tasks.length, icon: ListChecks },
    { id: "loops", label: "Loops", count: loops.length, icon: Repeat },
    { id: "reminders", label: "Reminders / Cron", count: reminders.length, icon: Clock },
    { id: "inbox", label: "Inbox", count: unreadCount, icon: Bell },
  ];

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-8">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-zinc-800 pb-4">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Autonomous Work & Inbox</h2>
          <p className="text-sm text-zinc-400">
            Background Kanban queue, Multi-Loop runner, 5-field Cron reminders, and unified results Inbox.
          </p>
        </div>
        <button
          onClick={() => void api.toggleKanbanAuto(!autoEnabled).then(refresh)}
          className={`rounded-lg border px-3 py-1.5 text-xs font-medium ${
            autoEnabled
              ? "border-emerald-500/50 bg-emerald-500/15 text-emerald-300"
              : "border-zinc-700 bg-zinc-900 text-zinc-300"
          }`}
        >
          Auto-Orchestration: {autoEnabled ? "ON" : "OFF"}
        </button>
      </header>

      <div className="flex flex-wrap gap-2">
        {tabs.map((item) => {
          const Icon = item.icon;
          const active = tab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => setTab(item.id)}
              className={`inline-flex items-center gap-2 rounded-lg border px-3.5 py-2 text-xs font-medium transition ${
                active
                  ? "border-amber-500/60 bg-amber-500/15 text-amber-300"
                  : "border-zinc-800 bg-zinc-900/50 text-zinc-400 hover:text-zinc-100"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              <span>{item.label}</span>
              <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-300">
                {item.count}
              </span>
            </button>
          );
        })}
      </div>

      {error && (
        <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-200">
          {error}
        </div>
      )}

      {tab === "kanban" && (
        <div className="space-y-5">
          <form onSubmit={handleAddTask} className="space-y-2.5 rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
            <div className="flex gap-2">
              <input
                value={taskTitle}
                onChange={(e) => setTaskTitle(e.target.value)}
                placeholder="Assign a goal to Jarvis..."
                required
                className="flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-sm text-zinc-100"
              />
              <button
                type="submit"
                className="inline-flex items-center gap-1 rounded-lg bg-amber-500 px-3.5 py-1.5 text-xs font-medium text-zinc-950 hover:bg-amber-400"
              >
                <Plus className="h-3.5 w-3.5" /> Add Task
              </button>
            </div>
            <input
              value={taskDesc}
              onChange={(e) => setTaskDesc(e.target.value)}
              placeholder="Optional details / acceptance hints..."
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-1.5 text-xs text-zinc-300"
            />
          </form>

          <div className="space-y-2.5">
            {tasks.map((task) => (
              <div key={task.id} className="space-y-2 rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <h3 className="text-sm font-semibold text-zinc-100">{task.title}</h3>
                    <p className="font-mono text-[11px] text-zinc-500">
                      {task.id} · mode={task.mode} · attempts={task.attempts}
                    </p>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="rounded-full bg-zinc-800 px-2.5 py-0.5 text-[11px] font-medium text-amber-300">
                      {task.status}
                    </span>
                    {task.status === "ready" && (
                      <button
                        onClick={() => void handleRunTask(task.id)}
                        disabled={busyId === task.id}
                        className="inline-flex items-center gap-1 rounded bg-amber-500 px-2.5 py-1 text-xs font-medium text-zinc-950 hover:bg-amber-400"
                      >
                        <Play className="h-3 w-3" /> Run
                      </button>
                    )}
                    {task.status === "review" && (
                      <button
                        onClick={() => void api.acceptTask(task.id).then(refresh)}
                        className="inline-flex items-center gap-1 rounded bg-emerald-500/20 px-2.5 py-1 text-xs text-emerald-300"
                      >
                        <Check className="h-3 w-3" /> Accept
                      </button>
                    )}
                    {task.status !== "done" && task.status !== "cancelled" && (
                      <button
                        onClick={() => void api.cancelTask(task.id).then(refresh)}
                        className="rounded p-1 text-zinc-500 hover:bg-zinc-800 hover:text-rose-400"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                </div>

                {task.block_reason && (
                  <p className="text-xs text-amber-300">Note: {task.block_reason}</p>
                )}
                {task.status === "blocked" && (
                  <div className="flex gap-2">
                    <input
                      value={answers[task.id] ?? ""}
                      onChange={(e) => setAnswers((p) => ({ ...p, [task.id]: e.target.value }))}
                      placeholder="Reply to unblock this task..."
                      className="flex-1 rounded border border-zinc-700 bg-zinc-950 px-2.5 py-1 text-xs text-zinc-100"
                    />
                    <button
                      onClick={() =>
                        void api.answerTask(task.id, answers[task.id] ?? "").then(() => {
                          setAnswers((p) => ({ ...p, [task.id]: "" }));
                          return refresh();
                        })
                      }
                      className="rounded bg-amber-500 px-3 py-1 text-xs font-medium text-zinc-950"
                    >
                      Answer
                    </button>
                  </div>
                )}
                {task.result && (
                  <p className="whitespace-pre-wrap rounded-lg bg-zinc-950/70 p-2.5 text-xs text-zinc-300">
                    {task.result}
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === "loops" && (
        <div className="space-y-5">
          <form onSubmit={handleAddLoop} className="space-y-3 rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
            <div className="grid gap-2 sm:grid-cols-3">
              <input
                value={loopSlug}
                onChange={(e) => setLoopSlug(e.target.value)}
                placeholder="slug (e.g. inbox-triage)"
                required
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 font-mono text-xs text-zinc-100"
              />
              <input
                value={loopName}
                onChange={(e) => setLoopName(e.target.value)}
                placeholder="Loop name"
                required
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-xs text-zinc-100"
              />
              <input
                type="number"
                min={5}
                value={loopInterval}
                onChange={(e) => setLoopInterval(Number(e.target.value))}
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-xs text-zinc-100"
              />
            </div>
            <textarea
              rows={2}
              value={loopPrompt}
              onChange={(e) => setLoopPrompt(e.target.value)}
              placeholder="What one concrete unit of work should this loop perform?"
              required
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-xs text-zinc-100"
            />
            <button
              type="submit"
              className="rounded-lg bg-amber-500 px-3.5 py-1.5 text-xs font-medium text-zinc-950 hover:bg-amber-400"
            >
              Create Loop (starts disabled)
            </button>
          </form>

          <div className="space-y-2.5">
            {loops.map((lp) => (
              <div key={lp.slug} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
                <div>
                  <h3 className="text-sm font-semibold text-zinc-100">{lp.name}</h3>
                  <p className="text-xs text-zinc-400">
                    Every {lp.interval_min}m · mode={lp.mode} · runs today: {lp.state.runs_today}
                  </p>
                  {lp.state.auto_paused_reason && (
                    <p className="mt-1 text-xs text-rose-300">{lp.state.auto_paused_reason}</p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => void api.runLoopNow(lp.slug).then(refresh)}
                    className="inline-flex items-center gap-1 rounded border border-zinc-700 px-2.5 py-1 text-xs text-zinc-200 hover:border-amber-500"
                  >
                    <Play className="h-3 w-3" /> Run
                  </button>
                  <button
                    onClick={() => void api.toggleLoop(lp.slug, !lp.enabled).then(refresh)}
                    className={`rounded px-2.5 py-1 text-xs font-medium ${
                      lp.enabled ? "bg-emerald-500/20 text-emerald-300" : "bg-zinc-800 text-zinc-400"
                    }`}
                  >
                    {lp.enabled ? "ON" : "OFF"}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === "reminders" && (
        <div className="space-y-5">
          <form onSubmit={handleAddReminder} className="space-y-2.5 rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
            <div className="grid gap-2 sm:grid-cols-2">
              <input
                value={remName}
                onChange={(e) => setRemName(e.target.value)}
                placeholder="Reminder name"
                required
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-xs text-zinc-100"
              />
              <input
                value={remCron}
                onChange={(e) => setRemCron(e.target.value)}
                placeholder="Cron (e.g. 0 9 * * 1-5)"
                required
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 font-mono text-xs text-zinc-100"
              />
            </div>
            <input
              value={remPrompt}
              onChange={(e) => setRemPrompt(e.target.value)}
              placeholder="Instruction or reminder text..."
              required
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-xs text-zinc-100"
            />
            <button
              type="submit"
              className="rounded-lg bg-amber-500 px-3.5 py-1.5 text-xs font-medium text-zinc-950 hover:bg-amber-400"
            >
              Add Reminder
            </button>
          </form>

          <div className="space-y-2">
            {reminders.map((r) => (
              <div key={r.id} className="flex items-center justify-between rounded-xl border border-zinc-800 bg-zinc-900/50 p-3.5">
                <div>
                  <p className="text-sm font-medium text-zinc-100">{r.name}</p>
                  <p className="font-mono text-xs text-zinc-400">
                    {r.cron} · {r.prompt}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void handleRunReminder(r.id)}
                    disabled={busyId === r.id}
                    title="Запустить напоминание прямо сейчас (Тест)"
                    className="inline-flex items-center gap-1 rounded border border-zinc-700 bg-zinc-800/80 px-2.5 py-1 text-xs text-zinc-200 hover:border-amber-500 hover:text-amber-300 disabled:opacity-50 transition"
                  >
                    <Play className="h-3 w-3" />
                    <span>{busyId === r.id ? "Запуск..." : "Тест"}</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => void api.deleteReminder(r.id).then(refresh)}
                    className="rounded p-1.5 text-zinc-500 hover:bg-zinc-800 hover:text-rose-400"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === "inbox" && (
        <div className="space-y-3">
          {unreadCount > 0 && (
            <button
              onClick={() => void api.markInboxRead().then(refresh)}
              className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:border-amber-500"
            >
              Mark all read ({unreadCount})
            </button>
          )}
          {inbox.length === 0 ? (
            <p className="text-sm text-zinc-400">Inbox is empty.</p>
          ) : (
            inbox.map((item) => (
              <div
                key={item.id}
                className={`rounded-xl border p-4 ${
                  item.read ? "border-zinc-800 bg-zinc-900/40" : "border-amber-500/40 bg-amber-500/10"
                }`}
              >
                <div className="flex items-center justify-between text-xs">
                  <span className="font-semibold text-zinc-100">{item.title}</span>
                  <span className="font-mono text-zinc-400">{item.source}</span>
                </div>
                <p className="mt-1.5 whitespace-pre-wrap text-xs text-zinc-300">{item.body}</p>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
