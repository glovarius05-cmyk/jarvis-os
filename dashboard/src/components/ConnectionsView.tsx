import { useCallback, useEffect, useState } from "react";
import {
  Check,
  CheckCircle2,
  Code2,
  Copy,
  ExternalLink,
  Key,
  Plug,
  Plus,
  RefreshCw,
  Share2,
  Shield,
  Sparkles,
  Terminal,
  Trash2,
  Wrench,
} from "lucide-react";
import {
  api,
  type AuditLogEntry,
  type HubToolInfo,
  type MCPConnectionInfo,
} from "../api/client";

const SOCIAL_PLATFORMS = [
  { id: "gmail", name: "Gmail", desc: "Read, search, draft, and send emails via Composio" },
  { id: "googlecalendar", name: "Google Calendar", desc: "View events, schedule meetings, and manage reminders" },
  { id: "github", name: "GitHub", desc: "Manage repos, issues, pull requests, and releases" },
  { id: "notion", name: "Notion", desc: "Read and write pages, databases, and notes" },
  { id: "twitter", name: "Twitter / X", desc: "Short updates, threads, mentions (280 chars)" },
  { id: "linkedin", name: "LinkedIn", desc: "Professional articles, company updates (3k chars)" },
  { id: "instagram", name: "Instagram", desc: "Captions, media posts, analytics (2.2k chars)" },
  { id: "threads", name: "Threads", desc: "Micro-blogging updates (500 chars)" },
  { id: "facebook", name: "Facebook", desc: "Pages, feeds, audience engagement" },
  { id: "youtube", name: "YouTube", desc: "Video descriptions, community updates" },
  { id: "reddit", name: "Reddit", desc: "Subreddit posts, discussions" },
];

export function ConnectionsView() {
  const [connections, setConnections] = useState<MCPConnectionInfo[]>([]);
  const [tools, setTools] = useState<HubToolInfo[]>([]);
  const [audit, setAudit] = useState<AuditLogEntry[]>([]);
  const [catalog, setCatalog] = useState<Array<{
    id: string;
    name: string;
    description: string;
    transport: "http" | "stdio";
    default_url?: string;
    default_command?: string;
    default_args?: string[];
    default_perm: "readonly" | "safe" | "full";
    write_tools?: string[];
  }>>([]);
  const [previewMode, setPreviewMode] = useState<"suggest" | "auto" | "full">("full");

  const [connId, setConnId] = useState("");
  const [name, setName] = useState("");
  const [transport, setTransport] = useState<"http" | "stdio">("http");
  const [perm, setPerm] = useState<"readonly" | "safe" | "full">("readonly");
  const [url, setUrl] = useState("");
  const [command, setCommand] = useState("");
  const [argsText, setArgsText] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Composio Social State
  const [composioKey, setComposioKey] = useState("");
  const [composioConfigured, setComposioConfigured] = useState(false);
  const [composioValid, setComposioValid] = useState(false);
  const [connectingApp, setConnectingApp] = useState<string | null>(null);
  const [composioAccounts, setComposioAccounts] = useState<Array<{ app: string; status: string; id: string }>>([]);
  const [composioLoading, setComposioLoading] = useState(false);
  const [composioMsg, setComposioMsg] = useState<string | null>(null);

  // Stdio MCP Bridge State
  const [mcpConfig, setMcpConfig] = useState<{
    python_path: string;
    bridge_script: string;
    hub_url: string;
    has_token: boolean;
    cursor_config: { mcpServers: Record<string, unknown> };
    claude_desktop_config: { mcpServers: Record<string, unknown> };
  } | null>(null);
  const [copiedConfig, setCopiedConfig] = useState(false);
  const [configTarget, setConfigTarget] = useState<"cursor" | "claude">("cursor");

  const refreshComposio = useCallback(async () => {
    try {
      const st = await api.getComposioStatus();
      setComposioConfigured(st.configured);
      setComposioValid(Boolean(st.valid));
      setComposioAccounts(st.accounts || []);
      setComposioMsg(st.message);
    } catch {
      // ignore
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [cRes, tRes, aRes, catRes] = await Promise.all([
        api.listConnections(),
        api.listHubTools(previewMode),
        api.listAudit(20),
        api.getCatalog(),
      ]);
      setConnections(cRes.connections);
      setTools(tRes.tools);
      setAudit(aRes.entries);
      setCatalog(catRes.connectors || []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load MCP Hub");
    }
    void refreshComposio();
    void api.getHubMcpConfig().then(setMcpConfig).catch(() => {});
  }, [previewMode, refreshComposio]);

  const handleSelectPreset = (p: {
    id: string;
    name: string;
    transport: "http" | "stdio";
    default_url?: string;
    default_command?: string;
    default_args?: string[];
    default_perm: "readonly" | "safe" | "full";
  }) => {
    setConnId(p.id);
    setName(p.name);
    setTransport(p.transport);
    setPerm(p.default_perm);
    setUrl(p.default_url || "");
    setCommand(p.default_command || "");
    setArgsText((p.default_args || []).join(" "));
  };

  const handleSaveComposioKey = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!composioKey.trim()) return;
    setComposioLoading(true);
    try {
      await api.patchSettings({ composio_api_key: composioKey.trim() });
      setComposioKey("");
      await refreshComposio();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save Composio key");
    } finally {
      setComposioLoading(false);
    }
  };

  const handleConnectPlatform = async (appId: string) => {
    setConnectingApp(appId);
    setComposioMsg(null);
    try {
      const res = await api.connectComposioApp(appId);
      if (res.auth_url) {
        const popup = window.open(res.auth_url, "_blank", "noopener,noreferrer");
        if (!popup || popup.closed || typeof popup.closed === "undefined") {
          window.location.href = res.auth_url;
        }
      } else {
        setComposioMsg(`Could not get authorization URL for ${appId}`);
      }
    } catch (err) {
      setComposioMsg(err instanceof Error ? err.message : `Failed to connect ${appId}`);
    } finally {
      setConnectingApp(null);
    }
  };

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.upsertConnection({
        id: connId.trim().toLowerCase(),
        name: name.trim() || connId.trim(),
        transport,
        perm,
        enabled: true,
        url: transport === "http" ? url.trim() : "",
        command: transport === "stdio" ? command.trim() : "",
        args: transport === "stdio" ? argsText.split(" ").filter(Boolean) : [],
      });
      setConnId("");
      setName("");
      setUrl("");
      setCommand("");
      setArgsText("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save connection");
    }
  };

  const handleDelete = async (id: string) => {
    await api.deleteConnection(id);
    await refresh();
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-8">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-zinc-800 pb-4">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">MCP Hub & Permissions</h2>
          <p className="text-sm text-zinc-400">
            Single tool gateway for API engines (in-process) and CLI engines (/hub/mcp) with hard readonly/safe/full gates.
          </p>
        </div>
        <select
          value={previewMode}
          onChange={(e) => setPreviewMode(e.target.value as "suggest" | "auto" | "full")}
          className="rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-xs font-medium text-amber-300"
        >
          <option value="suggest">Preview mode: suggest (readonly)</option>
          <option value="auto">Preview mode: auto (safe)</option>
          <option value="full">Preview mode: full</option>
        </select>
      </header>

      {error && (
        <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-200">
          {error}
        </div>
      )}

      {/* Stdio MCP Bridge for Cursor, VS Code & Claude Desktop */}
      <section className="space-y-4 rounded-xl border border-emerald-900/30 bg-zinc-900/60 p-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
            <Code2 className="h-4 w-4 text-emerald-400" />
            <span>Connect Jarvis OS to Cursor, VS Code & Claude Desktop (Stdio MCP)</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-xs font-medium text-emerald-400">
              <CheckCircle2 className="h-3 w-3" /> 30 Tools Live
            </span>
          </div>
        </div>

        <p className="text-xs text-zinc-400">
          Export all Jarvis OS tools (Second Brain notes, Multi-AI Consensus, Tasks, Web Search, Social Media)
          directly into Cursor, VS Code (Cline/Roo-Code) or Claude Desktop using the lightweight stdio bridge.
        </p>

        {mcpConfig && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setConfigTarget("cursor")}
                  className={`rounded-lg px-3 py-1 text-xs font-medium transition ${
                    configTarget === "cursor"
                      ? "bg-zinc-800 text-amber-300 border border-zinc-700"
                      : "text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  Cursor (.cursor/mcp.json)
                </button>
                <button
                  type="button"
                  onClick={() => setConfigTarget("claude")}
                  className={`rounded-lg px-3 py-1 text-xs font-medium transition ${
                    configTarget === "claude"
                      ? "bg-zinc-800 text-amber-300 border border-zinc-700"
                      : "text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  Claude Desktop (claude_desktop_config.json)
                </button>
              </div>

              <button
                type="button"
                onClick={() => {
                  const snippet = JSON.stringify(
                    configTarget === "cursor" ? mcpConfig.cursor_config : mcpConfig.claude_desktop_config,
                    null,
                    2
                  );
                  void navigator.clipboard.writeText(snippet);
                  setCopiedConfig(true);
                  setTimeout(() => setCopiedConfig(false), 2000);
                }}
                className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-1 text-xs font-medium text-zinc-200 transition hover:bg-zinc-700 hover:text-white"
              >
                {copiedConfig ? (
                  <>
                    <Check className="h-3.5 w-3.5 text-emerald-400" />
                    <span className="text-emerald-400">Copied!</span>
                  </>
                ) : (
                  <>
                    <Copy className="h-3.5 w-3.5" />
                    <span>Copy Config</span>
                  </>
                )}
              </button>
            </div>

            <pre className="overflow-x-auto rounded-lg border border-zinc-800 bg-zinc-950 p-3.5 font-mono text-[11px] leading-relaxed text-zinc-300">
              {JSON.stringify(
                configTarget === "cursor" ? mcpConfig.cursor_config : mcpConfig.claude_desktop_config,
                null,
                2
              )}
            </pre>

            <div className="flex items-center gap-1.5 text-[11px] text-zinc-500">
              <Terminal className="h-3 w-3" />
              <span>
                {configTarget === "cursor"
                  ? "Save in <project_root>/.cursor/mcp.json or in Cursor Settings > Features > MCP"
                  : "Save in ~/Library/Application Support/Claude/claude_desktop_config.json"}
              </span>
            </div>
          </div>
        )}
      </section>

      {/* Composio & Social Networks Section */}
      <section className="space-y-4 rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
            <Share2 className="h-4 w-4 text-sky-400" />
            <span>Cloud Apps & Integrations via Composio (Gmail, Calendar, GitHub, Socials)</span>
          </div>
          <div className="flex items-center gap-2">
            {composioConfigured ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-xs font-medium text-emerald-400">
                <CheckCircle2 className="h-3 w-3" /> Key Encrypted & Active
              </span>
            ) : (
              <span className="rounded-full bg-zinc-800 px-2.5 py-0.5 text-xs text-zinc-400">
                API Key Not Configured
              </span>
            )}
            <button
              onClick={() => void refreshComposio()}
              className="rounded p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
              title="Refresh social accounts"
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        <p className="text-xs text-zinc-400">
          Manage, inspect feeds, and publish content to Twitter/X, LinkedIn, Instagram, and more. All write actions strictly require Full mode.
        </p>

        {/* Composio Key Form */}
        <form onSubmit={handleSaveComposioKey} className="flex flex-wrap items-center gap-2">
          <div className="relative flex-1 min-w-[240px]">
            <Key className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-zinc-500" />
            <input
              type="password"
              value={composioKey}
              onChange={(e) => setComposioKey(e.target.value)}
              placeholder={composioConfigured ? "Update Composio API key (comp_...)" : "Enter Composio API key"}
              className="w-full rounded-lg border border-zinc-700 bg-zinc-950 pl-8 pr-3 py-1.5 font-mono text-xs text-zinc-100"
            />
          </div>
          <button
            type="submit"
            disabled={composioLoading || !composioKey.trim()}
            className="rounded-lg bg-sky-500 px-3.5 py-1.5 text-xs font-medium text-zinc-950 hover:bg-sky-400 disabled:opacity-50"
          >
            {composioLoading ? "Saving..." : "Save Key"}
          </button>
        </form>

        {composioMsg && (
          <div className={`rounded-lg p-2.5 text-xs ${composioValid ? "bg-zinc-950 text-zinc-400" : "border border-rose-500/30 bg-rose-500/10 text-rose-300"}`}>
            <p>{composioMsg}</p>
            {!composioValid && (
              <p className="mt-1 text-[11px] text-zinc-400">
                Зайдите на <a href="https://app.composio.dev/settings" target="_blank" rel="noopener noreferrer" className="text-sky-400 underline">app.composio.dev/settings</a>, скопируйте ваш API-ключ (обычно начинается на <code className="text-amber-300 font-mono">ak_</code> или <code className="text-amber-300 font-mono">comp_</code>), вставьте его в поле выше и нажмите <strong>Save Key</strong>.
              </p>
            )}
          </div>
        )}

        {/* Platform Grid */}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {SOCIAL_PLATFORMS.map((platform) => {
            const isConnected = composioAccounts.some(
              (acc) => acc.app.toLowerCase() === platform.id || (platform.id === "twitter" && acc.app.toLowerCase() === "x")
            );
            return (
              <div
                key={platform.id}
                className="flex flex-col justify-between rounded-lg border border-zinc-800/80 bg-zinc-950/50 p-3"
              >
                <div>
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-zinc-200">{platform.name}</span>
                    {isConnected ? (
                      <span className="rounded bg-emerald-500/20 px-2 py-0.5 text-[10px] font-semibold text-emerald-400">
                        Connected
                      </span>
                    ) : (
                      <span className="rounded bg-zinc-800 px-2 py-0.5 text-[10px] text-zinc-500">
                        Disconnected
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-[11px] text-zinc-400">{platform.desc}</p>
                </div>
                <div className="mt-3 pt-2 border-t border-zinc-800/60">
                  <button
                    onClick={() => void handleConnectPlatform(platform.id)}
                    disabled={!composioConfigured || !composioValid || connectingApp === platform.id}
                    title={!composioValid ? "Enter a valid Composio API key first" : ""}
                    className="inline-flex w-full items-center justify-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-300 hover:bg-zinc-800 hover:text-white disabled:opacity-40"
                  >
                    <span>{connectingApp === platform.id ? "Connecting..." : isConnected ? "Reconnect" : "Connect"}</span>
                    <ExternalLink className="h-3 w-3" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* Recommended MCP Catalog Presets */}
      {catalog.length > 0 && (
        <section className="space-y-3 rounded-xl border border-zinc-800 bg-zinc-900/40 p-5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
              <Sparkles className="h-4 w-4 text-amber-400" />
              <span>Recommended MCP Presets ({catalog.length})</span>
            </div>
            <span className="text-xs text-zinc-500">Click any preset to prefill connection form</span>
          </div>
          <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
            {catalog.map((cat) => {
              const isAlreadyConnected = connections.some((c) => c.id === cat.id);
              return (
                <div
                  key={cat.id}
                  onClick={() => handleSelectPreset(cat)}
                  className={`cursor-pointer flex flex-col justify-between rounded-lg border p-3 transition-colors ${
                    connId === cat.id
                      ? "border-amber-500/80 bg-amber-500/10"
                      : "border-zinc-800 bg-zinc-950/40 hover:border-zinc-700 hover:bg-zinc-900/60"
                  }`}
                >
                  <div>
                    <div className="flex items-center justify-between gap-1.5">
                      <span className="text-xs font-medium text-zinc-200">{cat.name}</span>
                      <div className="flex items-center gap-1">
                        <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[9px] font-mono text-zinc-400">
                          {cat.transport}
                        </span>
                        {isAlreadyConnected && (
                          <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-400">
                            Active
                          </span>
                        )}
                      </div>
                    </div>
                    <p className="mt-1 line-clamp-2 text-[11px] text-zinc-400">{cat.description}</p>
                  </div>
                  <div className="mt-2.5 flex items-center justify-between pt-2 border-t border-zinc-800/50 text-[10px] text-zinc-500">
                    <span>Perm: <span className="text-amber-400/90">{cat.default_perm}</span></span>
                    <span className="text-amber-300 font-medium">Click to Fill</span>
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* Add external MCP connection */}
      <form onSubmit={handleAdd} className="space-y-3 rounded-xl border border-zinc-800 bg-zinc-900/50 p-5">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Plug className="h-4 w-4 text-amber-400" />
          <span>Add External MCP Server</span>
        </div>
        <div className="grid gap-3 sm:grid-cols-4">
          <input
            value={connId}
            onChange={(e) => setConnId(e.target.value)}
            placeholder="id (e.g. github)"
            required
            className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 font-mono text-xs text-zinc-100"
          />
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Display name"
            required
            className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-xs text-zinc-100"
          />
          <select
            value={transport}
            onChange={(e) => setTransport(e.target.value as "http" | "stdio")}
            className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-xs text-zinc-100"
          >
            <option value="http">HTTP JSON-RPC</option>
            <option value="stdio">Stdio Subprocess</option>
          </select>
          <select
            value={perm}
            onChange={(e) => setPerm(e.target.value as "readonly" | "safe" | "full")}
            className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-xs text-zinc-100"
          >
            <option value="readonly">Perm: readonly</option>
            <option value="safe">Perm: safe</option>
            <option value="full">Perm: full</option>
          </select>
        </div>

        {transport === "http" ? (
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="http://127.0.0.1:8080/mcp"
            required
            className="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 font-mono text-xs text-zinc-100"
          />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            <input
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              placeholder="Command (e.g. npx)"
              required
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 font-mono text-xs text-zinc-100"
            />
            <input
              value={argsText}
              onChange={(e) => setArgsText(e.target.value)}
              placeholder="Space-separated args"
              className="rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-1.5 font-mono text-xs text-zinc-100"
            />
          </div>
        )}

        <button
          type="submit"
          className="inline-flex items-center gap-1.5 rounded-lg bg-amber-500 px-3.5 py-1.5 text-xs font-medium text-zinc-950 hover:bg-amber-400"
        >
          <Plus className="h-3.5 w-3.5" /> Save Connection
        </button>
      </form>

      {/* Active Connections */}
      {connections.length > 0 && (
        <section className="space-y-2">
          <h3 className="text-sm font-semibold text-zinc-300">Connected MCP Servers ({connections.length})</h3>
          <div className="grid gap-2 sm:grid-cols-2">
            {connections.map((c) => (
              <div
                key={c.id}
                className="flex items-center justify-between rounded-xl border border-zinc-800 bg-zinc-900/60 p-3.5"
              >
                <div>
                  <p className="text-sm font-medium text-zinc-100">
                    {c.name} <span className="font-mono text-xs text-zinc-500">({c.id})</span>
                  </p>
                  <p className="text-xs text-zinc-400">
                    {c.transport} · perm: <span className="text-amber-300">{c.perm}</span>
                  </p>
                </div>
                <button
                  onClick={() => void handleDelete(c.id)}
                  className="rounded p-1.5 text-zinc-500 hover:bg-zinc-800 hover:text-rose-400"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Discovered Tools Matrix */}
      <section className="space-y-2">
        <div className="flex items-center gap-2 text-sm font-semibold text-zinc-300">
          <Wrench className="h-4 w-4 text-amber-400" />
          <span>Active Hub Tools ({tools.length})</span>
        </div>
        <div className="overflow-hidden rounded-xl border border-zinc-800">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-zinc-800 bg-zinc-900/80 text-zinc-400">
              <tr>
                <th className="px-3 py-2">Tool</th>
                <th className="px-3 py-2">Source</th>
                <th className="px-3 py-2">Requires</th>
                <th className="px-3 py-2">In {previewMode}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70 bg-zinc-950/60">
              {tools.map((tool) => (
                <tr key={tool.name}>
                  <td className="px-3 py-2 font-mono text-zinc-100">{tool.name}</td>
                  <td className="px-3 py-2 text-zinc-400">{tool.source}</td>
                  <td className="px-3 py-2 font-mono text-zinc-300">{tool.required_perm}</td>
                  <td className="px-3 py-2">
                    <span
                      className={`rounded-full px-2 py-0.5 font-medium ${
                        tool.allowed_in_mode
                          ? "bg-emerald-500/15 text-emerald-300"
                          : "bg-rose-500/15 text-rose-300"
                      }`}
                    >
                      {tool.allowed_in_mode ? "ALLOW" : "BLOCKED"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Audit Log */}
      <section className="space-y-2">
        <div className="flex items-center gap-2 text-sm font-semibold text-zinc-300">
          <Shield className="h-4 w-4 text-emerald-400" />
          <span>Recent Audit Log ({audit.length})</span>
        </div>
        {audit.length === 0 ? (
          <p className="text-xs text-zinc-500">No tool calls recorded yet.</p>
        ) : (
          <div className="space-y-1 rounded-xl border border-zinc-800 bg-zinc-900/40 p-3 font-mono text-[11px]">
            {audit.map((a, idx) => (
              <div key={idx} className="flex items-center justify-between gap-2 text-zinc-300">
                <span>
                  <span className={a.allowed ? "text-emerald-400" : "text-rose-400"}>
                    {a.allowed ? "ALLOW" : "DENY "}
                  </span>{" "}
                  {a.tool} ({a.run_mode}) · {a.brain}
                </span>
                <span className="text-zinc-500">{a.duration_ms.toFixed(0)}ms</span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
