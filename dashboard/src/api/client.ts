export interface SystemStatus {
  version: string;
  active_brain: string;
  brains_count: number;
  fts_enabled: boolean;
  model: {
    main_provider: string;
    main_model: string;
    aux_provider: string;
    aux_model: string;
  };
  local_llama: {
    base_url: string;
    model: string;
    num_ctx?: number;
  };
  cli_providers: {
    codex_enabled: boolean;
    antigravity_enabled: boolean;
  };
  secrets_configured: Record<string, boolean>;
}

export interface BrainItem {
  name: string;
  is_git_repo: boolean;
  note_count: number;
  active: boolean;
}

export interface BrainsResponse {
  active_brain: string;
  brains: BrainItem[];
}

export interface SessionItem {
  id: string;
  title: string;
  brain: string;
  channel: string;
  pinned_provider: string;
  pinned_model: string;
  created_at: number;
  updated_at: number;
  message_count: number;
}

export interface MessageItem {
  id: number;
  session_id: string;
  role: "user" | "assistant" | string;
  content: string;
  provider: string;
  model: string;
  meta: Record<string, unknown>;
  created_at: number;
}

export interface SearchHit {
  session_id: string;
  session_title: string;
  message_id: number;
  role: string;
  snippet: string;
  created_at: number;
}

export interface ProviderStatus {
  id: string;
  label: string;
  kind: "api" | "local" | "cli";
  default_model: string;
  models: string[];
  ready: boolean;
  detail: string;
  phase2_cli: boolean;
}

export interface ProvidersResponse {
  providers: ProviderStatus[];
  model: SystemStatus["model"];
  local_llama: SystemStatus["local_llama"];
  secrets_configured: Record<string, boolean>;
}

export interface HubToolInfo {
  name: string;
  description: string;
  source: string;
  required_perm: string;
  connection_perm: string;
  allowed_in_mode: boolean;
  reason: string;
}

export interface MCPConnectionInfo {
  id: string;
  name: string;
  transport: "http" | "stdio";
  perm: "readonly" | "safe" | "full";
  enabled: boolean;
  url: string;
  has_headers: boolean;
  command: string;
  args: string[];
  has_env: boolean;
  write_tools: string[];
}

export interface AuditLogEntry {
  ts: number;
  brain: string;
  session_id: string;
  run_mode: string;
  tool: string;
  source: string;
  allowed: boolean;
  reason: string;
  duration_ms: number;
}

export interface SkillInfo {
  slug: string;
  name: string;
  description: string;
  group: string;
  enabled: boolean;
  is_system: boolean;
  path: string;
}

export interface PluginInfo {
  slug: string;
  name: string;
  description: string;
  version: string;
  scope: "bundled" | "global" | "vault";
  enabled: boolean;
  min_mode: string;
  tools: string[];
  hooks: string[];
  error: string;
}

export interface AgentInfo {
  slug: string;
  name: string;
  role: string;
  tags: string[];
  skills: string[];
  model: string;
  model_provider: string;
  prompt: string;
  path: string;
}

export interface WorkflowInfo {
  slug: string;
  name: string;
  description: string;
  group: string;
  status: string;
  steps: { agent: string; task: string; verify_agent: string; max_retries: number }[];
  path: string;
}

export interface WorkflowRunInfo {
  id: string;
  brain: string;
  workflow_slug: string;
  status: string;
  input_text: string;
  final_output: string;
  steps_log: Array<Record<string, unknown>>;
  created_at: number;
  finished_at: number | null;
}

export interface GraphNode {
  id: string;
  label: string;
  group: string;
  degree: number;
  exists: boolean;
}

export interface GraphResponse {
  brain: string;
  nodes: GraphNode[];
  edges: { source: string; target: string }[];
  stats: { total_notes: number; total_nodes: number; total_edges: number };
}

export interface CoreMemoryResponse {
  brain: string;
  raw: string;
  sections: {
    user: string[];
    persona: string[];
    context: string[];
  };
  char_count: number;
  total_facts: number;
}

export interface CharacterCard {
  name: string;
  role: string;
  tone: string;
  avatar: string;
  expertise: string[];
  directives: string[];
  bio: string;
  raw_markdown: string;
}

export interface CharacterResponse {
  brain: string;
  character: CharacterCard;
  rendered_prompt: string;
}

export interface CharacterTemplate {
  id: string;
  name: string;
  role: string;
  tone: string;
  avatar: string;
  expertise: string[];
  directives: string[];
  bio: string;
}

export interface OnboardingStatus {
  completed: boolean;
  has_keys: boolean;
  has_ollama: boolean;
  local_models: string[];
  templates: CharacterTemplate[];
  active_brain: string;
  needs_admin: boolean;
}

export interface OnboardingPayload {
  provider: string;
  model?: string;
  api_key?: string;
  character_template?: string;
  admin_user?: string;
  admin_password?: string;
}

export interface BrainSearchHit {
  path: string;
  heading: string;
  chunk_index: number;
  snippet: string;
  score: number;
  match_type: "hybrid" | "keyword" | "semantic";
}

export interface BrainSearchResponse {
  brain: string;
  query: string;
  hits: BrainSearchHit[];
  total: number;
}

export interface BrainSyncStatus {
  enabled: boolean;
  running: boolean;
  watched_dir: string;
  total_events_processed: number;
  last_event_type: string | null;
  last_synced_path: string | null;
  last_synced_brain: string | null;
  last_sync_timestamp: string | null;
  error_count: number;
  last_error: string | null;
}

export interface GitRemoteStatus {
  brain: string;
  is_git: boolean;
  configured: boolean;
  remote_url: string;
  branch: string;
  ahead: number;
  behind: number;
  clean: boolean;
}

export interface KanbanTaskInfo {
  id: string;
  brain: string;
  title: string;
  description: string;
  spec: string;
  status: string;
  mode: string;
  route: string;
  owner_chat: string;
  result: string;
  block_reason: string;
  attempts: number;
}

export interface LoopInfo {
  slug: string;
  name: string;
  enabled: boolean;
  mode: string;
  interval_min: number;
  quiet_hours: string;
  prompt: string;
  state: {
    last_run: number;
    last_status: string;
    last_summary: string;
    runs_today: number;
    fail_streak: number;
    auto_paused_reason: string;
  };
}

export interface ReminderInfo {
  id: string;
  name: string;
  prompt: string;
  cron: string;
  notify_only: boolean;
  mode: string;
  enabled: boolean;
}

export interface InboxItemInfo {
  id: string;
  brain: string;
  source: string;
  title: string;
  body: string;
  read: boolean;
  created_at: number;
}

export interface ExperienceItem {
  id: string;
  error_signature: string;
  error_type: string;
  failing_command: string;
  solution_patch: string;
  brain: string;
  success_count: number;
  failure_count: number;
  confidence_score: number;
  created_at: number;
  updated_at: number;
  last_used_at: number;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export const api = {
  getSystemStatus: () => requestJson<SystemStatus>("/api/system/status"),
  getSettings: () => requestJson<Record<string, unknown>>("/api/settings"),
  getComposioStatus: () =>
    requestJson<{ configured: boolean; valid?: boolean; masked_key?: string; accounts: Array<{ app: string; status: string; id: string }>; message: string }>(
      "/api/hub/composio/status"
    ),
  resetComposioKey: () =>
    requestJson<{ ok: boolean; message: string }>("/api/hub/composio/reset", {
      method: "POST",
    }),
  connectComposioApp: (app: string, redirect_url = "") =>
    requestJson<{ ok: boolean; app: string; auth_url: string }>("/api/hub/composio/connect", {
      method: "POST",
      body: JSON.stringify({ app, redirect_url }),
    }),
  confirmComposioApp: (app: string, connected = true) =>
    requestJson<{ ok: boolean; connected_apps: string[] }>("/api/hub/composio/confirm", {
      method: "POST",
      body: JSON.stringify({ app, connected }),
    }),
  listBrains: () => requestJson<BrainsResponse>("/api/brains"),
  createBrain: (name: string, activate = true) =>
    requestJson<BrainItem>("/api/brains", {
      method: "POST",
      body: JSON.stringify({ name, activate }),
    }),
  patchSettings: (updates: Record<string, unknown>) =>
    requestJson<Record<string, unknown>>("/api/settings", {
      method: "PATCH",
      body: JSON.stringify({ updates }),
    }),
  listProviders: () => requestJson<ProvidersResponse>("/api/models/providers"),
  testLocalLlama: (base_url: string) =>
    requestJson<{ ok: boolean; base_url: string; models: string[]; error: string }>(
      "/api/models/test-local",
      {
        method: "POST",
        body: JSON.stringify({ base_url }),
      }
    ),
  listSessions: (brain?: string) =>
    requestJson<{ sessions: SessionItem[] }>(
      brain ? `/api/sessions?brain=${encodeURIComponent(brain)}` : "/api/sessions"
    ),
  createSession: (payload: { title?: string; brain?: string } = {}) =>
    requestJson<SessionItem>("/api/sessions", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getSession: (sessionId: string) =>
    requestJson<{ session: SessionItem; messages: MessageItem[] }>(
      `/api/sessions/${encodeURIComponent(sessionId)}`
    ),
  deleteSession: (sessionId: string) =>
    requestJson<{ ok: boolean }>(`/api/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    }),
  searchSessions: (q: string, brain?: string) => {
    const params = new URLSearchParams({ q });
    if (brain) params.set("brain", brain);
    return requestJson<{ query: string; hits: SearchHit[] }>(`/api/sessions/search?${params.toString()}`);
  },
  stopChat: (sessionId: string) =>
    requestJson<{ ok: boolean; stopped: boolean }>(`/api/chat/stop/${encodeURIComponent(sessionId)}`, {
      method: "POST",
    }),
  listHubTools: (mode = "full") =>
    requestJson<{ brain: string; mode: string; tools: HubToolInfo[] }>(
      `/api/hub/tools?mode=${encodeURIComponent(mode)}`
    ),
  getCatalog: () =>
    requestJson<{
      connectors: Array<{
        id: string;
        name: string;
        description: string;
        transport: "http" | "stdio";
        default_url?: string;
        default_command?: string;
        default_args?: string[];
        default_perm: "readonly" | "safe" | "full";
        write_tools?: string[];
      }>;
    }>("/api/hub/catalog"),
  listConnections: () => requestJson<{ connections: MCPConnectionInfo[] }>("/api/hub/connections"),
  upsertConnection: (payload: Record<string, unknown>) =>
    requestJson<MCPConnectionInfo>("/api/hub/connections", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteConnection: (id: string) =>
    requestJson<{ ok: boolean }>(`/api/hub/connections/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  listAudit: (limit = 30) => requestJson<{ entries: AuditLogEntry[] }>(`/api/hub/audit?limit=${limit}`),
  listSkills: () => requestJson<{ brain: string; skills: SkillInfo[] }>("/api/studio/skills"),
  toggleSkill: (slug: string, enabled: boolean) =>
    requestJson<{ slug: string; enabled: boolean }>(`/api/studio/skills/${encodeURIComponent(slug)}/toggle`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
  listPlugins: () => requestJson<{ brain: string; plugins: PluginInfo[] }>("/api/studio/plugins"),
  togglePlugin: (slug: string, enabled: boolean) =>
    requestJson<PluginInfo>(`/api/studio/plugins/${encodeURIComponent(slug)}/toggle`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
  listAgents: () => requestJson<{ brain: string; agents: AgentInfo[] }>("/api/studio/agents"),
  saveAgent: (payload: Record<string, unknown>) =>
    requestJson<AgentInfo>("/api/studio/agents", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listWorkflows: () => requestJson<{ brain: string; workflows: WorkflowInfo[] }>("/api/studio/workflows"),
  runWorkflow: (slug: string, input_text: string) =>
    requestJson<WorkflowRunInfo>(`/api/studio/workflows/${encodeURIComponent(slug)}/run`, {
      method: "POST",
      body: JSON.stringify({ input_text, mode: "auto" }),
    }),
  listWorkflowRuns: () => requestJson<{ runs: WorkflowRunInfo[] }>("/api/studio/workflows/runs"),
  getBrainGraph: () => requestJson<GraphResponse>("/api/brain/graph"),
  getCoreMemory: (brain?: string) =>
    requestJson<CoreMemoryResponse>(`/api/brains/core-memory${brain ? `?brain=${encodeURIComponent(brain)}` : ""}`),
  rememberCoreMemory: (fact: string, section: string = "user", brain?: string) =>
    requestJson<{ ok: boolean; message: string; view: CoreMemoryResponse }>("/api/brains/core-memory/remember", {
      method: "POST",
      body: JSON.stringify({ fact, section, brain }),
    }),
  forgetCoreMemory: (query: string, section: string = "user", brain?: string) =>
    requestJson<{ ok: boolean; message: string; view: CoreMemoryResponse }>("/api/brains/core-memory/forget", {
      method: "POST",
      body: JSON.stringify({ query, section, brain }),
    }),
  saveCoreMemory: (content: string, brain?: string) =>
    requestJson<{ ok: boolean; view: CoreMemoryResponse }>("/api/brains/core-memory", {
      method: "PUT",
      body: JSON.stringify({ content, brain }),
    }),
  getBrainCharacter: (brain?: string) =>
    requestJson<CharacterResponse>(`/api/brains/character${brain ? `?brain=${encodeURIComponent(brain)}` : ""}`),
  saveBrainCharacter: (content: string, brain?: string) =>
    requestJson<{ ok: boolean; brain: string; character: CharacterCard; rendered_prompt: string }>(
      "/api/brains/character",
      {
        method: "PUT",
        body: JSON.stringify({ content, brain }),
      }
    ),
  applyCharacterTemplate: (template_id: string, brain?: string) =>
    requestJson<{ ok: boolean; brain: string; template_id: string; character: CharacterCard; rendered_prompt: string }>(
      "/api/brains/character/template",
      {
        method: "POST",
        body: JSON.stringify({ template_id, brain }),
      }
    ),
  listCharacterTemplates: () =>
    requestJson<{ templates: CharacterTemplate[] }>("/api/brains/character/templates"),
  searchBrain: (query: string, limit: number = 10, path_filter: string = "", brain?: string) =>
    requestJson<BrainSearchResponse>(
      `/api/brains/search?query=${encodeURIComponent(query)}&limit=${limit}&path_filter=${encodeURIComponent(path_filter)}${brain ? `&brain=${encodeURIComponent(brain)}` : ""}`
    ),
  reindexBrain: (force: boolean = false, brain?: string) =>
    requestJson<{ ok: boolean; brain: string; indexed_files: number; total_chunks: number }>("/api/brains/reindex", {
      method: "POST",
      body: JSON.stringify({ force, brain }),
    }),
  getSyncStatus: () => requestJson<BrainSyncStatus>("/api/brains/sync/status"),
  triggerSync: (force: boolean = false, brain?: string) =>
    requestJson<{ ok: boolean; brains_synced: string[]; indexed_files: number; total_chunks: number; timestamp: string }>(
      "/api/brains/sync/trigger",
      {
        method: "POST",
        body: JSON.stringify({ force, brain }),
      }
    ),
  getGitRemoteStatus: (brain?: string) =>
    requestJson<GitRemoteStatus>(`/api/brains/git/remote${brain ? `?brain=${encodeURIComponent(brain)}` : ""}`),
  configureGitRemote: (remote_url: string, brain?: string) =>
    requestJson<{ ok: boolean; brain: string } & GitRemoteStatus>("/api/brains/git/remote", {
      method: "POST",
      body: JSON.stringify({ remote_url, brain }),
    }),
  syncGitRemote: (action: "all" | "pull" | "push" = "all", brain?: string) =>
    requestJson<{ ok: boolean; brain: string; action: string; message: string; error?: string; status: GitRemoteStatus }>(
      "/api/brains/git/sync",
      {
        method: "POST",
        body: JSON.stringify({ action, brain }),
      }
    ),
  getOnboardingStatus: () => requestJson<OnboardingStatus>("/api/onboarding/status"),
  completeOnboarding: (payload: OnboardingPayload) =>
    requestJson<{ ok: boolean; message: string; active_brain: string }>("/api/onboarding/complete", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  readBrainFile: (path: string) =>
    requestJson<{ brain: string; path: string; content: string }>(
      `/api/brain/file?path=${encodeURIComponent(path)}`
    ),
  writeBrainFile: (path: string, content: string) =>
    requestJson<{ ok: boolean }>("/api/brain/file", {
      method: "PUT",
      body: JSON.stringify({ path, content }),
    }),
  listTasks: () =>
    requestJson<{ brain: string; auto_enabled: boolean; tasks: KanbanTaskInfo[] }>("/api/work/tasks"),
  createTask: (payload: Record<string, unknown>) =>
    requestJson<KanbanTaskInfo>("/api/work/tasks", { method: "POST", body: JSON.stringify(payload) }),
  toggleKanbanAuto: (enabled: boolean) =>
    requestJson<{ auto_enabled: boolean }>("/api/work/tasks/auto", {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
  runTaskNow: (id: string) =>
    requestJson<KanbanTaskInfo>(`/api/work/tasks/${encodeURIComponent(id)}/run`, { method: "POST" }),
  answerTask: (id: string, answer: string) =>
    requestJson<KanbanTaskInfo>(`/api/work/tasks/${encodeURIComponent(id)}/answer`, {
      method: "POST",
      body: JSON.stringify({ answer }),
    }),
  acceptTask: (id: string) =>
    requestJson<KanbanTaskInfo>(`/api/work/tasks/${encodeURIComponent(id)}/accept`, { method: "POST" }),
  cancelTask: (id: string) =>
    requestJson<KanbanTaskInfo>(`/api/work/tasks/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  listLoops: () => requestJson<{ brain: string; loops: LoopInfo[] }>("/api/work/loops"),
  createLoop: (payload: Record<string, unknown>) =>
    requestJson<LoopInfo>("/api/work/loops", { method: "POST", body: JSON.stringify(payload) }),
  toggleLoop: (slug: string, enabled: boolean) =>
    requestJson<LoopInfo>(`/api/work/loops/${encodeURIComponent(slug)}/toggle`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
  runLoopNow: (slug: string) =>
    requestJson<Record<string, unknown>>(`/api/work/loops/${encodeURIComponent(slug)}/run`, {
      method: "POST",
    }),
  listReminders: () => requestJson<{ brain: string; reminders: ReminderInfo[] }>("/api/work/reminders"),
  createReminder: (payload: Record<string, unknown>) =>
    requestJson<Record<string, unknown>>("/api/work/reminders", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteReminder: (id: string) =>
    requestJson<{ ok: boolean }>(`/api/work/reminders/${encodeURIComponent(id)}`, { method: "DELETE" }),
  listInbox: () => requestJson<{ unread_count: number; items: InboxItemInfo[] }>("/api/work/inbox"),
  markInboxRead: () => requestJson<{ ok: boolean }>("/api/work/inbox/read", { method: "POST" }),
  runLearning: (dry_run = false) =>
    requestJson<Record<string, unknown>>("/api/learn/run", {
      method: "POST",
      body: JSON.stringify({ dry_run }),
    }),
  getLearnHistory: () =>
    requestJson<{
      brain: string;
      is_git_repo: boolean;
      commits: { sha: string; short_sha: string; date: string; message: string; is_learning: boolean }[];
    }>("/api/learn/history"),
  rollbackCommit: (sha: string) =>
    requestJson<{ ok: boolean; message: string }>("/api/learn/rollback", {
      method: "POST",
      body: JSON.stringify({ sha }),
    }),
  getUsageSummary: (days = 30) =>
    requestJson<{
      days: number;
      totals: { calls: number; input_tokens: number; output_tokens: number; cost_usd: number };
      by_category: Array<Record<string, unknown>>;
      by_provider: Array<Record<string, unknown>>;
    }>(`/api/learn/usage?days=${days}`),
  getTelegramStatus: () =>
    requestJson<{
      configured: boolean;
      enabled: boolean;
      polling: boolean;
      allowed_chat_ids: string[];
      bot_username: string;
      last_error: string;
    }>("/api/channels/telegram/status"),
  saveTelegramConfig: (payload: { enabled: boolean; bot_token: string; allowed_chat_ids: string[] }) =>
    requestJson<Record<string, unknown>>("/api/channels/telegram/config", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  testTelegram: () =>
    requestJson<{ ok: boolean; username?: string; error?: string }>("/api/channels/telegram/test", {
      method: "POST",
    }),
  voiceTurn: (transcript: string, session_id = "") =>
    requestJson<{
      skipped: boolean;
      spoken_text: string;
      raw_text: string;
      ui_action: string;
      ui_target: string;
      delegated_request: string;
      delegated_session_id: string;
    }>("/api/voice/turn", {
      method: "POST",
      body: JSON.stringify({ transcript, session_id }),
    }),
  execTerminal: (command: string, cwd = "") =>
    requestJson<{ command: string; cwd: string; exit_code: number; stdout: string; stderr: string; timed_out: boolean }>(
      "/api/terminal/exec",
      { method: "POST", body: JSON.stringify({ command, cwd }) }
    ),
  listWebhooks: () =>
    requestJson<{ webhooks: Array<{ id: string; name: string; action: string; brain: string; mode: string }> }>(
      "/api/webhooks"
    ),
  createWebhook: (payload: Record<string, unknown>) =>
    requestJson<{ id: string; name: string; token: string }>("/api/webhooks", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteWebhook: (id: string) =>
    requestJson<{ ok: boolean }>(`/api/webhooks/${encodeURIComponent(id)}`, { method: "DELETE" }),
  getHubMcpConfig: () =>
    requestJson<{
      python_path: string;
      bridge_script: string;
      hub_url: string;
      has_token: boolean;
      cursor_config: { mcpServers: Record<string, unknown> };
      claude_desktop_config: { mcpServers: Record<string, unknown> };
    }>("/api/hub/mcp-config"),
  getAuthStatus: () =>
    requestJson<{ login_required: boolean; has_admin: boolean; authenticated: boolean; totp_enabled: boolean }>(
      "/api/auth/status"
    ),
  setupTotp: () => requestJson<{ secret: string; uri: string }>("/api/auth/totp/setup", { method: "POST" }),
  enableTotp: (code: string) =>
    requestJson<{ ok: boolean; totp_enabled: boolean }>("/api/auth/totp/enable", {
      method: "POST",
      body: JSON.stringify({ code }),
    }),
  synthesizeTTS: async (
    text: string,
    voice = "",
    rate = ""
  ): Promise<{ audioUrl?: string; fallbackText?: string; reason?: string }> => {
    const res = await fetch("/api/voice/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, voice, rate }),
    });
    const ctype = res.headers.get("content-type") || "";
    if (ctype.includes("audio")) {
      const blob = await res.blob();
      const audioUrl = URL.createObjectURL(blob);
      return { audioUrl };
    }
    const data = (await res.json().catch(() => ({}))) as Record<string, string>;
    return { fallbackText: data.spoken_text || text, reason: data.reason };
  },
  transcribeAudio: async (audioBlob: Blob, filename = "recording.webm"): Promise<{ text: string }> => {
    const fd = new FormData();
    fd.append("file", audioBlob, filename);
    const res = await fetch("/api/voice/stt", {
      method: "POST",
      body: fd,
    });
    if (!res.ok) {
      throw new Error(`STT failed: ${res.statusText}`);
    }
    return (await res.json()) as { text: string };
  },
  listExperience: (limit = 50, error_type = "") =>
    requestJson<{ solutions: ExperienceItem[]; count: number }>(
      `/api/experience/list?limit=${limit}&error_type=${encodeURIComponent(error_type)}`
    ),
  getExperienceStats: () =>
    requestJson<{ total_solutions: number; average_confidence: number; by_type: Record<string, number> }>(
      "/api/experience/stats"
    ),
  recordExperienceFix: (payload: { error_text: string; solution_patch: string; failing_command?: string; brain?: string }) =>
    requestJson<{ ok: boolean; solution: ExperienceItem }>("/api/experience/record", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  searchExperience: (query: string, failing_command = "") =>
    requestJson<{ query: string; matches: ExperienceItem[]; count: number }>("/api/experience/search", {
      method: "POST",
      body: JSON.stringify({ query, failing_command }),
    }),
  submitExperienceFeedback: (solution_id: string, success: boolean) =>
    requestJson<{ ok: boolean; solution: ExperienceItem }>("/api/experience/feedback", {
      method: "POST",
      body: JSON.stringify({ solution_id, success }),
    }),
  deleteExperience: (solution_id: string) =>
    requestJson<{ ok: boolean }>(`/api/experience/${encodeURIComponent(solution_id)}`, { method: "DELETE" }),
};
