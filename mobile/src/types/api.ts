export type RunMode = "suggest" | "auto" | "full";

export interface AuthStatusResponse {
  login_required: boolean;
  has_admin: boolean;
  authenticated: boolean;
  username: string | null;
  totp_enabled: boolean;
}

export interface AuthCredentialsPayload {
  username: string;
  password: string;
  totp_code?: string;
}

export interface AuthLoginResponse {
  ok: boolean;
  username: string;
  token?: string;
}

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
  secrets_configured: Record<string, boolean>;
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

export interface BrainSearchHit {
  path: string;
  title?: string;
  snippet: string;
  score?: number;
}

export interface BrainGraphNode {
  id: string;
  path?: string;
  title?: string;
  label?: string;
  tags?: string[];
  word_count?: number;
  size?: number;
}

export interface BrainGraphResponse {
  brain: string;
  nodes: BrainGraphNode[];
  edges?: Array<{ source: string; target: string }>;
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
  id: number | string;
  session_id: string;
  role: "user" | "assistant" | string;
  content: string;
  provider: string;
  model: string;
  meta: Record<string, unknown>;
  created_at: number;
}

export interface FileUploadResult {
  ok: boolean;
  filename: string;
  path: string;
  size: number;
  is_image: boolean;
  is_text: boolean;
  text_content?: string | null;
  url: string;
}

export interface ChatToolEvent {
  type: "tool_call" | "tool_result";
  tool_name?: string;
  tool_args?: Record<string, unknown>;
  content?: string;
}

export interface ChatWsEvent {
  type: "hello" | "pong" | "turn_start" | "stream" | "tool_call" | "tool_result" | "error" | "final";
  session_id?: string;
  content?: string;
  provider?: string;
  model?: string;
  mode?: string;
  tool_name?: string;
  tool_args?: Record<string, unknown>;
  active_jobs?: Array<{ session_id: string; accumulated_text?: string; provider?: string; model?: string }>;
}

export interface VoiceLiveStatus {
  configured: boolean;
  provider: string;
  model: string;
}

export interface VoiceLiveWsEvent {
  type: "ready" | "pong" | "transcript" | "audio" | "tool" | "turn_complete" | "interrupted" | "end_conversation" | "error";
  role?: "user" | "assistant";
  text?: string;
  data?: string;
  rate?: number;
  name?: string;
  status?: "running" | "done";
  result?: string;
  reason?: string;
  message?: string;
  tools_enabled?: boolean;
}

export interface KanbanTask {
  id: string;
  brain: string;
  title: string;
  description: string;
  status: "todo" | "running" | "needs_user" | "review" | "done" | "failed" | "cancelled" | string;
  mode: RunMode;
  route: string;
  question?: string;
  result?: string;
  error?: string;
  created_at: number;
  updated_at: number;
}

export interface ReminderItem {
  id: string;
  brain: string;
  name: string;
  prompt: string;
  cron: string;
  run_at_ts: number;
  notify_only: boolean;
  mode: RunMode;
  enabled: boolean;
  next_run_ts?: number;
  last_run_ts?: number;
}

export interface ServerConfig {
  baseUrl: string;
  token: string;
  username: string;
}
