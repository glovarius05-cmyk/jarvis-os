import { useCallback, useEffect, useState } from "react";
import {
  BarChart3,
  Brain,
  GitCommitHorizontal,
  Plus,
  RotateCcw,
  Search,
  ShieldCheck,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  Trash2,
} from "lucide-react";
import { api, type ExperienceItem } from "../api/client";

export function LearnUsageView() {
  const [commits, setCommits] = useState<
    { sha: string; short_sha: string; date: string; message: string; is_learning: boolean }[]
  >([]);
  const [isGit, setIsGit] = useState(true);
  const [usage, setUsage] = useState<{
    totals: { calls: number; input_tokens: number; output_tokens: number; cost_usd: number };
    by_provider: Array<Record<string, unknown>>;
  } | null>(null);
  const [lastResult, setLastResult] = useState<string>("");
  const [running, setRunning] = useState(false);

  // Experience Memory State
  const [experiences, setExperiences] = useState<ExperienceItem[]>([]);
  const [expStats, setExpStats] = useState<{ total_solutions: number; average_confidence: number; by_type: Record<string, number> } | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [showAddForm, setShowAddForm] = useState(false);
  const [newError, setNewError] = useState("");
  const [newPatch, setNewPatch] = useState("");
  const [newCmd, setNewCmd] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const refreshExperience = useCallback(async () => {
    try {
      const [listRes, statsRes] = await Promise.all([
        api.listExperience(50),
        api.getExperienceStats(),
      ]);
      setExperiences(listRes.solutions);
      setExpStats(statsRes);
    } catch {
      // ignore
    }
  }, []);

  const refresh = useCallback(async () => {
    const [hist, usg] = await Promise.all([api.getLearnHistory(), api.getUsageSummary(30)]);
    setCommits(hist.commits);
    setIsGit(hist.is_git_repo);
    setUsage(usg);
    void refreshExperience();
  }, [refreshExperience]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleFeedback = async (id: string, success: boolean) => {
    await api.submitExperienceFeedback(id, success);
    await refreshExperience();
  };

  const handleDeleteExperience = async (id: string) => {
    await api.deleteExperience(id);
    await refreshExperience();
  };

  const handleRecordFix = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newError.trim() || !newPatch.trim()) return;
    setIsSubmitting(true);
    try {
      await api.recordExperienceFix({
        error_text: newError.trim(),
        solution_patch: newPatch.trim(),
        failing_command: newCmd.trim(),
      });
      setNewError("");
      setNewPatch("");
      setNewCmd("");
      setShowAddForm(false);
      await refreshExperience();
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleSearchExperience = async () => {
    if (!searchQuery.trim()) {
      await refreshExperience();
      return;
    }
    const res = await api.searchExperience(searchQuery.trim());
    setExperiences(res.matches);
  };

  const handleLearn = async (dryRun: boolean) => {
    setRunning(true);
    try {
      const res = await api.runLearning(dryRun);
      setLastResult(JSON.stringify(res, null, 2));
      await refresh();
    } finally {
      setRunning(false);
    }
  };

  const handleRollback = async (sha: string) => {
    const res = await api.rollbackCommit(sha);
    setLastResult(res.message);
    await refresh();
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-8">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-zinc-800 pb-4">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Self-Learning & Usage</h2>
          <p className="text-sm text-zinc-400">
            Read-only extraction fork -&gt; secret/injection scan -&gt; trusted promote() -&gt; 1-click Git rollback.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => void handleLearn(true)}
            disabled={running}
            className="rounded-lg border border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:border-amber-500"
          >
            Dry Run
          </button>
          <button
            onClick={() => void handleLearn(false)}
            disabled={running}
            className="inline-flex items-center gap-1.5 rounded-lg bg-amber-500 px-3.5 py-1.5 text-xs font-medium text-zinc-950 hover:bg-amber-400"
          >
            <Sparkles className="h-3.5 w-3.5" /> Learn Now
          </button>
        </div>
      </header>

      {/* Usage summary cards */}
      <section className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
          <p className="text-xs text-zinc-400">30d LLM Calls</p>
          <p className="mt-1 text-xl font-semibold text-zinc-100">{usage?.totals.calls ?? 0}</p>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
          <p className="text-xs text-zinc-400">30d Tokens (In / Out)</p>
          <p className="mt-1 font-mono text-sm font-semibold text-zinc-100">
            {usage?.totals.input_tokens ?? 0} / {usage?.totals.output_tokens ?? 0}
          </p>
        </div>
        <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4">
          <p className="flex items-center gap-1.5 text-xs text-zinc-400">
            <BarChart3 className="h-3.5 w-3.5 text-emerald-400" /> Est. Cost (USD)
          </p>
          <p className="mt-1 font-mono text-xl font-semibold text-emerald-300">
            ${(usage?.totals.cost_usd ?? 0).toFixed(4)}
          </p>
        </div>
      </section>

      {lastResult && (
        <pre className="overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs text-zinc-300">
          {lastResult}
        </pre>
      )}

      {/* Experience Learning & Self-Healing Memory (Proxima-style memory.db) */}
      <section className="space-y-4 rounded-xl border border-zinc-800 bg-zinc-900/40 p-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
            <ShieldCheck className="h-4 w-4 text-emerald-400" />
            <span>Self-Healing Memory (Experience & Patch Database)</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-xs font-medium text-emerald-400">
              {expStats?.total_solutions ?? 0} Remembered Solutions
            </span>
            <button
              onClick={() => setShowAddForm(!showAddForm)}
              className="inline-flex items-center gap-1 rounded-lg border border-zinc-700 bg-zinc-800 px-2.5 py-1 text-xs text-zinc-200 hover:bg-zinc-700 hover:text-white"
            >
              <Plus className="h-3.5 w-3.5" />
              <span>{showAddForm ? "Cancel" : "Add Fix"}</span>
            </button>
          </div>
        </div>

        <p className="text-xs text-zinc-400">
          Caches compilation, runtime, and test failures with their successful diff patches. When a tool or command fails,
          the agent immediately consults this database to apply proven solutions and prevent repetitive loops.
        </p>

        {/* Add Fix Form */}
        {showAddForm && (
          <form onSubmit={handleRecordFix} className="space-y-3 rounded-lg border border-zinc-700/80 bg-zinc-950 p-4">
            <p className="text-xs font-semibold text-zinc-200">Record a New Error Solution</p>
            <div className="space-y-2">
              <input
                type="text"
                placeholder="Failing command (e.g. pytest tests/test_abc.py, npm run build)"
                value={newCmd}
                onChange={(e) => setNewCmd(e.target.value)}
                className="w-full rounded border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 font-mono text-xs text-zinc-100 outline-none focus:border-amber-500"
              />
              <textarea
                placeholder="Error text / traceback message..."
                value={newError}
                onChange={(e) => setNewError(e.target.value)}
                rows={2}
                required
                className="w-full rounded border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 font-mono text-xs text-zinc-100 outline-none focus:border-amber-500"
              />
              <textarea
                placeholder="Solution / patch / command that fixes this error..."
                value={newPatch}
                onChange={(e) => setNewPatch(e.target.value)}
                rows={3}
                required
                className="w-full rounded border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 font-mono text-xs text-emerald-300 outline-none focus:border-amber-500"
              />
            </div>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowAddForm(false)}
                className="rounded px-3 py-1 text-xs text-zinc-400 hover:text-zinc-200"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={isSubmitting}
                className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                {isSubmitting ? "Saving..." : "Save Solution"}
              </button>
            </div>
          </form>
        )}

        {/* Search Bar */}
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-zinc-500" />
            <input
              type="text"
              placeholder="Search past solutions by error keyword, command or exception..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void handleSearchExperience();
              }}
              className="w-full rounded-lg border border-zinc-800 bg-zinc-950 pl-8 pr-3 py-1.5 font-mono text-xs text-zinc-200 outline-none focus:border-amber-500"
            />
          </div>
          <button
            type="button"
            onClick={() => void handleSearchExperience()}
            className="rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:bg-zinc-700 hover:text-white"
          >
            Search
          </button>
        </div>

        {/* Solutions List */}
        <div className="space-y-2.5">
          {experiences.length === 0 ? (
            <p className="rounded-lg border border-zinc-800/80 bg-zinc-950/40 p-4 text-center text-xs text-zinc-500">
              No solutions recorded yet. Solutions are captured automatically when agent tests or commands are fixed.
            </p>
          ) : (
            experiences.map((exp) => {
              const confPct = Math.round(exp.confidence_score * 100);
              return (
                <div
                  key={exp.id}
                  className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3 text-xs space-y-2"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="rounded bg-amber-500/20 px-2 py-0.5 font-mono text-[10px] font-semibold text-amber-400">
                        {exp.error_type}
                      </span>
                      <span className="font-mono text-zinc-300 font-medium truncate max-w-[360px]" title={exp.error_signature}>
                        {exp.error_signature}
                      </span>
                    </div>

                    <div className="flex items-center gap-2">
                      <span
                        className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                          confPct >= 70
                            ? "bg-emerald-500/15 text-emerald-400"
                            : confPct >= 40
                            ? "bg-amber-500/15 text-amber-400"
                            : "bg-rose-500/15 text-rose-400"
                        }`}
                        title="Exponential Moving Average (EMA) confidence score"
                      >
                        {confPct}% Confidence
                      </span>
                      <span className="text-[10px] text-zinc-500">
                        Used {exp.success_count}x
                      </span>

                      {/* Feedback buttons */}
                      <button
                        onClick={() => void handleFeedback(exp.id, true)}
                        className="rounded p-1 text-zinc-500 hover:bg-zinc-800 hover:text-emerald-400"
                        title="Worked (+ score)"
                      >
                        <ThumbsUp className="h-3 w-3" />
                      </button>
                      <button
                        onClick={() => void handleFeedback(exp.id, false)}
                        className="rounded p-1 text-zinc-500 hover:bg-zinc-800 hover:text-rose-400"
                        title="Failed (- score)"
                      >
                        <ThumbsDown className="h-3 w-3" />
                      </button>
                      <button
                        onClick={() => void handleDeleteExperience(exp.id)}
                        className="rounded p-1 text-zinc-500 hover:bg-zinc-800 hover:text-rose-400"
                        title="Delete solution"
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </div>
                  </div>

                  {exp.failing_command && (
                    <div className="font-mono text-[11px] text-zinc-400">
                      <span className="text-zinc-600">Cmd:</span> {exp.failing_command}
                    </div>
                  )}

                  <pre className="overflow-x-auto rounded border border-zinc-800/80 bg-zinc-900/60 p-2 font-mono text-[11px] text-emerald-300">
                    {exp.solution_patch}
                  </pre>
                </div>
              );
            })
          )}
        </div>
      </section>

      {/* Git learning history + 1-click rollback */}
      <section className="space-y-2.5">
        <div className="flex items-center gap-2 text-sm font-semibold text-zinc-200">
          <Brain className="h-4 w-4 text-amber-400" />
          <span>Brain Git History ({isGit ? "Git safety net ON" : "No Git"})</span>
        </div>
        <div className="space-y-2">
          {commits.map((c) => (
            <div
              key={c.sha}
              className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-zinc-800 bg-zinc-900/50 p-3 text-xs"
            >
              <div className="flex items-center gap-2">
                <GitCommitHorizontal className="h-4 w-4 text-zinc-400" />
                <span className="font-mono text-amber-300">{c.short_sha}</span>
                <span className="text-zinc-200">{c.message}</span>
              </div>
              {c.is_learning && (
                <button
                  onClick={() => void handleRollback(c.sha)}
                  className="inline-flex items-center gap-1 rounded border border-zinc-700 px-2.5 py-1 text-xs text-zinc-200 hover:border-rose-500 hover:text-rose-300"
                >
                  <RotateCcw className="h-3 w-3" /> Rollback
                </button>
              )}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
