import { useCallback, useEffect, useState } from "react";
import { Plus, ShieldCheck, Terminal, Trash2, Webhook } from "lucide-react";
import { api } from "../api/client";

export function TerminalView() {
  const [command, setCommand] = useState("");
  const [cwd, setCwd] = useState("");
  const [history, setHistory] = useState<
    { command: string; cwd: string; exit_code: number; stdout: string; stderr: string }[]
  >([]);
  const [running, setRunning] = useState(false);

  const [webhooks, setWebhooks] = useState<
    Array<{ id: string; name: string; action: string; brain: string; mode: string }>
  >([]);
  const [hookName, setHookName] = useState("");
  const [newHookSecret, setNewHookSecret] = useState<{ id: string; token: string } | null>(null);

  const [totpEnabled, setTotpEnabled] = useState(false);
  const [totpSetup, setTotpSetup] = useState<{ secret: string; uri: string } | null>(null);
  const [totpCode, setTotpCode] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = useCallback(async () => {
    const [wh, auth] = await Promise.all([api.listWebhooks(), api.getAuthStatus()]);
    setWebhooks(wh.webhooks);
    setTotpEnabled(auth.totp_enabled);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleExec = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!command.trim()) return;
    setRunning(true);
    try {
      const res = await api.execTerminal(command.trim(), cwd.trim());
      setHistory((prev) => [res, ...prev].slice(0, 20));
      setCommand("");
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Command failed");
    } finally {
      setRunning(false);
    }
  };

  const handleCreateWebhook = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!hookName.trim()) return;
    const created = await api.createWebhook({ name: hookName.trim(), action: "kanban", mode: "auto" });
    setNewHookSecret({ id: created.id, token: created.token });
    setHookName("");
    await refresh();
  };

  const handleTotpStart = async () => {
    const res = await api.setupTotp();
    setTotpSetup(res);
  };

  const handleTotpEnable = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.enableTotp(totpCode.trim());
      setTotpSetup(null);
      setTotpCode("");
      setNotice("2FA TOTP enabled.");
      await refresh();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Invalid code");
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-8">
      <header className="border-b border-zinc-800 pb-4">
        <h2 className="text-2xl font-semibold tracking-tight">Code Terminal, Webhooks & 2FA</h2>
        <p className="text-sm text-zinc-400">
          Run fenced shell commands (auto-redacts secrets), manage SHA-256 webhook triggers, and configure 2FA TOTP.
        </p>
      </header>

      {notice && (
        <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-200">
          {notice}
        </div>
      )}

      {/* Owner Terminal */}
      <section className="space-y-3 rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Terminal className="h-4 w-4 text-amber-400" />
          <span>Owner Shell Terminal</span>
        </div>
        <form onSubmit={handleExec} className="space-y-2">
          <input
            value={cwd}
            onChange={(e) => setCwd(e.target.value)}
            placeholder="Optional working directory (default: active Second Brain folder)"
            className="w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-1.5 font-mono text-xs text-zinc-300"
          />
          <div className="flex gap-2">
            <input
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              placeholder="e.g. git status"
              required
              className="flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 font-mono text-xs text-zinc-100"
            />
            <button
              type="submit"
              disabled={running}
              className="rounded-lg bg-amber-500 px-4 py-2 text-xs font-medium text-zinc-950 hover:bg-amber-400 disabled:opacity-50"
            >
              Run
            </button>
          </div>
        </form>

        {history.length > 0 && (
          <div className="space-y-2 pt-2">
            {history.map((h, i) => (
              <div key={i} className="rounded-lg border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs">
                <div className="flex items-center justify-between text-zinc-400">
                  <span>$ {h.command}</span>
                  <span className={h.exit_code === 0 ? "text-emerald-400" : "text-rose-400"}>
                    exit {h.exit_code}
                  </span>
                </div>
                {h.stdout && <pre className="mt-1.5 whitespace-pre-wrap text-zinc-200">{h.stdout}</pre>}
                {h.stderr && <pre className="mt-1.5 whitespace-pre-wrap text-rose-300">{h.stderr}</pre>}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Incoming Webhooks */}
      <section className="space-y-3 rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Webhook className="h-4 w-4 text-sky-400" />
          <span>Incoming Webhooks (SHA-256 Token Hash Only)</span>
        </div>
        <form onSubmit={handleCreateWebhook} className="flex gap-2">
          <input
            value={hookName}
            onChange={(e) => setHookName(e.target.value)}
            placeholder="Webhook name (e.g. Stripe Alert)"
            required
            className="flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-xs text-zinc-100"
          />
          <button
            type="submit"
            className="inline-flex items-center gap-1 rounded-lg bg-amber-500 px-3.5 py-1.5 text-xs font-medium text-zinc-950 hover:bg-amber-400"
          >
            <Plus className="h-3.5 w-3.5" /> Create Hook
          </button>
        </form>

        {newHookSecret && (
          <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-3 font-mono text-xs text-emerald-200">
            <p className="font-sans font-semibold">Save this one-time token (only its hash is stored):</p>
            <p className="mt-1 break-all">
              curl -X POST http://127.0.0.1:7777/hooks/{newHookSecret.id} -H &quot;X-Webhook-Token: {newHookSecret.token}&quot; -d &apos;&#123;&quot;event&quot;:&quot;ping&quot;&#125;&apos;
            </p>
          </div>
        )}

        <div className="space-y-1.5">
          {webhooks.map((w) => (
            <div key={w.id} className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-xs">
              <span>
                <strong className="text-zinc-100">{w.name}</strong>{" "}
                <span className="font-mono text-zinc-500">/hooks/{w.id} · {w.action}</span>
              </span>
              <button
                onClick={() => void api.deleteWebhook(w.id).then(refresh)}
                className="rounded p-1 text-zinc-500 hover:text-rose-400"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      </section>

      {/* 2FA TOTP */}
      <section className="space-y-3 rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <ShieldCheck className="h-4 w-4 text-emerald-400" />
            <span>Two-Factor Authentication (TOTP)</span>
          </div>
          <span
            className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
              totpEnabled ? "bg-emerald-500/15 text-emerald-300" : "bg-zinc-800 text-zinc-400"
            }`}
          >
            {totpEnabled ? "Enabled" : "Disabled"}
          </span>
        </div>

        {!totpEnabled && !totpSetup && (
          <button
            onClick={() => void handleTotpStart()}
            className="rounded-lg border border-zinc-700 px-3.5 py-1.5 text-xs font-medium text-zinc-200 hover:border-emerald-400"
          >
            Generate 2FA Secret
          </button>
        )}

        {totpSetup && (
          <form onSubmit={handleTotpEnable} className="space-y-2 rounded-lg bg-zinc-950 p-3 text-xs">
            <p className="text-zinc-400">Add this Base32 secret to your authenticator app:</p>
            <p className="font-mono text-amber-300">{totpSetup.secret}</p>
            <div className="flex gap-2 pt-1">
              <input
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value)}
                placeholder="6-digit code"
                required
                className="w-40 rounded border border-zinc-700 bg-zinc-900 px-2.5 py-1 font-mono text-zinc-100"
              />
              <button
                type="submit"
                className="rounded bg-emerald-500 px-3 py-1 font-medium text-zinc-950"
              >
                Verify & Enable
              </button>
            </div>
          </form>
        )}
      </section>
    </div>
  );
}
