import { useCallback, useEffect, useState } from "react";
import { Bot, GitMerge, Play, Plus, Puzzle, Wrench } from "lucide-react";
import {
  api,
  type AgentInfo,
  type PluginInfo,
  type SkillInfo,
  type WorkflowInfo,
  type WorkflowRunInfo,
} from "../api/client";

type TabId = "skills" | "plugins" | "agents" | "workflows";

export function StudioView() {
  const [tab, setTab] = useState<TabId>("skills");
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowInfo[]>([]);
  const [runs, setRuns] = useState<WorkflowRunInfo[]>([]);

  const [agentSlug, setAgentSlug] = useState("");
  const [agentName, setAgentName] = useState("");
  const [agentRole, setAgentRole] = useState("");
  const [agentPrompt, setAgentPrompt] = useState("");
  const [wfInputs, setWfInputs] = useState<Record<string, string>>({});
  const [runningSlug, setRunningSlug] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [sk, pl, ag, wf, rn] = await Promise.all([
        api.listSkills(),
        api.listPlugins(),
        api.listAgents(),
        api.listWorkflows(),
        api.listWorkflowRuns(),
      ]);
      setSkills(sk.skills);
      setPlugins(pl.plugins);
      setAgents(ag.agents);
      setWorkflows(wf.workflows);
      setRuns(rn.runs);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load Studio");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleToggleSkill = async (slug: string, enabled: boolean) => {
    await api.toggleSkill(slug, enabled);
    await refresh();
  };

  const handleTogglePlugin = async (slug: string, enabled: boolean) => {
    await api.togglePlugin(slug, enabled);
    await refresh();
  };

  const handleCreateAgent = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.saveAgent({
        slug: agentSlug.trim().toLowerCase(),
        name: agentName.trim(),
        role: agentRole.trim(),
        prompt: agentPrompt.trim(),
      });
      setAgentSlug("");
      setAgentName("");
      setAgentRole("");
      setAgentPrompt("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save agent");
    }
  };

  const handleRunWorkflow = async (slug: string) => {
    const input = (wfInputs[slug] || "").trim();
    if (!input) return;
    setRunningSlug(slug);
    try {
      await api.runWorkflow(slug, input);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Workflow run failed");
    } finally {
      setRunningSlug(null);
    }
  };

  const tabs: { id: TabId; label: string; count: number; icon: typeof Puzzle }[] = [
    { id: "skills", label: "Skills", count: skills.length, icon: Puzzle },
    { id: "plugins", label: "Plugins", count: plugins.length, icon: Wrench },
    { id: "agents", label: "Agents", count: agents.length, icon: Bot },
    { id: "workflows", label: "Workflows", count: workflows.length, icon: GitMerge },
  ];

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-8">
      <header className="border-b border-zinc-800 pb-4">
        <h2 className="text-2xl font-semibold tracking-tight">Studio</h2>
        <p className="text-sm text-zinc-400">
          Extensible capabilities stored as plain Markdown & Python plugins without touching core code.
        </p>
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

      {tab === "skills" && (
        <div className="grid gap-3 md:grid-cols-2">
          {skills.map((s) => (
            <div key={s.slug} className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <h3 className="text-sm font-semibold text-zinc-100">{s.name}</h3>
                  <p className="font-mono text-[11px] text-zinc-500">
                    {s.slug} · {s.group} {s.is_system ? "· system" : ""}
                  </p>
                </div>
                <button
                  onClick={() => void handleToggleSkill(s.slug, !s.enabled)}
                  className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
                    s.enabled
                      ? "bg-emerald-500/15 text-emerald-300"
                      : "bg-zinc-800 text-zinc-400"
                  }`}
                >
                  {s.enabled ? "ON" : "OFF"}
                </button>
              </div>
              <p className="mt-2 text-xs text-zinc-300">{s.description}</p>
            </div>
          ))}
        </div>
      )}

      {tab === "plugins" && (
        <div className="space-y-3">
          {plugins.map((p) => (
            <div
              key={p.slug}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-zinc-800 bg-zinc-900/50 p-4"
            >
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-semibold text-zinc-100">{p.name}</h3>
                  <span className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-[10px] text-zinc-300">
                    {p.scope}
                  </span>
                  <span className="rounded bg-amber-500/15 px-1.5 py-0.5 font-mono text-[10px] text-amber-300">
                    min: {p.min_mode}
                  </span>
                </div>
                <p className="text-xs text-zinc-400">{p.description}</p>
                {p.tools.length > 0 && (
                  <p className="font-mono text-[11px] text-emerald-300">
                    Tools: {p.tools.join(", ")}
                  </p>
                )}
                {p.error && <p className="text-xs text-rose-300">{p.error}</p>}
              </div>
              <button
                onClick={() => void handleTogglePlugin(p.slug, !p.enabled)}
                className={`rounded-lg px-3 py-1.5 text-xs font-medium ${
                  p.enabled
                    ? "bg-emerald-500/20 text-emerald-300 hover:bg-emerald-500/30"
                    : "bg-zinc-800 text-zinc-300 hover:bg-zinc-700"
                }`}
              >
                {p.enabled ? "Enabled" : "Disabled"}
              </button>
            </div>
          ))}
        </div>
      )}

      {tab === "agents" && (
        <div className="space-y-5">
          <form onSubmit={handleCreateAgent} className="space-y-3 rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
            <h3 className="flex items-center gap-2 text-sm font-semibold">
              <Plus className="h-4 w-4 text-amber-400" /> New Specialist Agent
            </h3>
            <div className="grid gap-3 sm:grid-cols-3">
              <input
                value={agentSlug}
                onChange={(e) => setAgentSlug(e.target.value)}
                placeholder="slug (e.g. code-reviewer)"
                required
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 font-mono text-xs text-zinc-100"
              />
              <input
                value={agentName}
                onChange={(e) => setAgentName(e.target.value)}
                placeholder="Name"
                required
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-xs text-zinc-100"
              />
              <input
                value={agentRole}
                onChange={(e) => setAgentRole(e.target.value)}
                placeholder="One-line role"
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-xs text-zinc-100"
              />
            </div>
            <textarea
              rows={3}
              value={agentPrompt}
              onChange={(e) => setAgentPrompt(e.target.value)}
              placeholder="Agent system prompt..."
              required
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-xs text-zinc-100"
            />
            <button
              type="submit"
              className="rounded-lg bg-amber-500 px-3.5 py-1.5 text-xs font-medium text-zinc-950 hover:bg-amber-400"
            >
              Save Agent
            </button>
          </form>

          <div className="grid gap-3 md:grid-cols-2">
            {agents.map((a) => (
              <div key={a.slug} className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
                <h4 className="text-sm font-semibold text-zinc-100">{a.name}</h4>
                <p className="font-mono text-[11px] text-zinc-500">{a.path}</p>
                <p className="mt-1 text-xs text-zinc-300">{a.role}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === "workflows" && (
        <div className="space-y-5">
          {workflows.length === 0 ? (
            <p className="text-sm text-zinc-400">
              No workflows in this brain yet. Ask Jarvis in chat to create one in workflows/&lt;slug&gt;.md!
            </p>
          ) : (
            workflows.map((wf) => (
              <div key={wf.slug} className="space-y-3 rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
                <div>
                  <h3 className="text-sm font-semibold text-zinc-100">{wf.name}</h3>
                  <p className="text-xs text-zinc-400">
                    {wf.description} · {wf.steps.length} step(s)
                  </p>
                </div>
                <div className="flex gap-2">
                  <input
                    value={wfInputs[wf.slug] ?? ""}
                    onChange={(e) => setWfInputs((p) => ({ ...p, [wf.slug]: e.target.value }))}
                    placeholder="Workflow input..."
                    className="flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-xs text-zinc-100"
                  />
                  <button
                    onClick={() => void handleRunWorkflow(wf.slug)}
                    disabled={runningSlug === wf.slug}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-amber-500 px-3 py-1.5 text-xs font-medium text-zinc-950 hover:bg-amber-400 disabled:opacity-50"
                  >
                    <Play className="h-3.5 w-3.5" /> Run
                  </button>
                </div>
              </div>
            ))
          )}

          {runs.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-sm font-semibold text-zinc-300">Recent Workflow Runs</h3>
              {runs.map((r) => (
                <div key={r.id} className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-3 text-xs">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-zinc-200">{r.workflow_slug}</span>
                    <span
                      className={`rounded-full px-2 py-0.5 font-medium ${
                        r.status === "done"
                          ? "bg-emerald-500/15 text-emerald-300"
                          : "bg-rose-500/15 text-rose-300"
                      }`}
                    >
                      {r.status}
                    </span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-zinc-400">{r.final_output}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
