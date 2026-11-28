import type {
  AuthCredentialsPayload,
  AuthLoginResponse,
  AuthStatusResponse,
  BrainGraphResponse,
  BrainSearchHit,
  BrainsResponse,
  FileUploadResult,
  KanbanTask,
  MessageItem,
  ProvidersResponse,
  ReminderItem,
  RunMode,
  SessionItem,
  SystemStatus,
  VoiceLiveStatus,
} from "../types/api";

export function normalizeBaseUrl(raw: string): string {
  const trimmed = raw.trim().replace(/\/+$/, "");
  if (!trimmed) return "";
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  // Default plain IP:port to http and hostnames to https
  if (/^\d{1,3}(\.\d{1,3}){3}(:\d+)?$/i.test(trimmed) || trimmed.startsWith("localhost")) {
    return `http://${trimmed}`;
  }
  return `https://${trimmed}`;
}

export class JarvisApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export class JarvisApiClient {
  private baseUrl: string;
  private token: string;

  constructor(baseUrl: string, token = "") {
    this.baseUrl = normalizeBaseUrl(baseUrl);
    this.token = token;
  }

  getBaseUrl(): string {
    return this.baseUrl;
  }

  getToken(): string {
    return this.token;
  }

  setCredentials(baseUrl: string, token: string): void {
    this.baseUrl = normalizeBaseUrl(baseUrl);
    this.token = token;
  }

  buildWsUrl(path: string, params?: Record<string, string>): string {
    const wsBase = this.baseUrl.replace(/^http:\/\//i, "ws://").replace(/^https:\/\//i, "wss://");
    const qsEntries = Object.entries(params ?? {}).filter(([, v]) => Boolean(v));
    if (this.token) qsEntries.push(["token", this.token]);
    const qs = qsEntries.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
    return `${wsBase}${path}${qs ? `?${qs}` : ""}`;
  }

  resolveAssetUrl(relativePath: string): string {
    if (/^https?:\/\//i.test(relativePath)) return relativePath;
    const prefix = relativePath.startsWith("/") ? "" : "/";
    return `${this.baseUrl}${prefix}${relativePath}`;
  }

  authHeaders(extra?: Record<string, string>): Record<string, string> {
    const hdrs: Record<string, string> = { ...(extra ?? {}) };
    if (this.token) hdrs.Authorization = `Bearer ${this.token}`;
    return hdrs;
  }

  private async requestJson<T>(path: string, init?: RequestInit): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const headers = this.authHeaders({
        "Content-Type": "application/json",
        ...((init?.headers as Record<string, string>) ?? {}),
      });
      const res = await fetch(`${this.baseUrl}${path}`, {
        ...init,
        headers,
        signal: init?.signal ?? controller.signal,
      });
      if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try {
          const txt = await res.text();
          if (txt) {
            try {
              const parsed = JSON.parse(txt);
              msg = parsed.detail || parsed.message || txt;
            } catch {
              msg = txt;
            }
          }
        } catch {}
        throw new JarvisApiError(res.status, msg);
      }
      return (await res.json()) as T;
    } finally {
      clearTimeout(timer);
    }
  }

  // ---- Auth & System ----
  health = () => this.requestJson<{ status: string; version: string }>("/api/health");
  getAuthStatus = () => this.requestJson<AuthStatusResponse>("/api/auth/status");

  login = (payload: AuthCredentialsPayload) =>
    this.requestJson<AuthLoginResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    });

  setupAdmin = (payload: AuthCredentialsPayload) =>
    this.requestJson<AuthLoginResponse>("/api/auth/setup", {
      method: "POST",
      body: JSON.stringify(payload),
    });

  logout = () => this.requestJson<{ ok: boolean }>("/api/auth/logout", { method: "POST" });

  getSystemStatus = () => this.requestJson<SystemStatus>("/api/system/status");
  getProviders = () => this.requestJson<ProvidersResponse>("/api/models/providers");
  getSettings = () => this.requestJson<Record<string, unknown>>("/api/settings");
  patchSettings = (updates: Record<string, unknown>) =>
    this.requestJson<Record<string, unknown>>("/api/settings", {
      method: "PATCH",
      body: JSON.stringify({ updates }),
    });

  // ---- Brains & Vault ----
  listBrains = () => this.requestJson<BrainsResponse>("/api/brains");
  createBrain = (name: string, activate = true) =>
    this.requestJson<{ name: string; active: boolean }>("/api/brains", {
      method: "POST",
      body: JSON.stringify({ name, activate }),
    });

  searchBrain = (query: string, brain?: string, limit = 20) => {
    const params = new URLSearchParams({ query, limit: String(limit) });
    if (brain) params.set("brain", brain);
    return this.requestJson<{ brain: string; hits: BrainSearchHit[]; total: number }>(
      `/api/brains/search?${params.toString()}`
    );
  };

  getBrainGraph = (brain?: string) => {
    const qs = brain ? `?brain=${encodeURIComponent(brain)}` : "";
    return this.requestJson<BrainGraphResponse>(`/api/brain/graph${qs}`);
  };

  readBrainFile = (path: string, brain?: string) => {
    const params = new URLSearchParams({ path });
    if (brain) params.set("brain", brain);
    return this.requestJson<{ brain: string; path: string; content: string }>(
      `/api/brain/file?${params.toString()}`
    );
  };

  writeBrainFile = (path: string, content: string, brain?: string) => {
    const qs = brain ? `?brain=${encodeURIComponent(brain)}` : "";
    return this.requestJson<{ ok: boolean; path: string }>(`/api/brain/file${qs}`, {
      method: "PUT",
      body: JSON.stringify({ path, content }),
    });
  };

  // ---- Sessions & Chat ----
  listSessions = (brain?: string, limit = 50) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (brain) params.set("brain", brain);
    return this.requestJson<{ sessions: SessionItem[] }>(`/api/sessions?${params.toString()}`);
  };

  searchSessions = (q: string, brain?: string) => {
    const params = new URLSearchParams({ q });
    if (brain) params.set("brain", brain);
    return this.requestJson<{ hits: Array<{ session_id: string; session_title: string; snippet: string }> }>(
      `/api/sessions/search?${params.toString()}`
    );
  };

  createSession = (payload: {
    brain?: string;
    title?: string;
    channel?: string;
    pinned_provider?: string;
    pinned_model?: string;
  }) =>
    this.requestJson<SessionItem>("/api/sessions", {
      method: "POST",
      body: JSON.stringify({ channel: "mobile", ...payload }),
    });

  getSession = (sessionId: string) =>
    this.requestJson<{ session: SessionItem; messages: MessageItem[] }>(`/api/sessions/${sessionId}`);

  updateSession = (sessionId: string, payload: { title?: string; pinned_provider?: string; pinned_model?: string }) =>
    this.requestJson<SessionItem>(`/api/sessions/${sessionId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });

  deleteSession = (sessionId: string) =>
    this.requestJson<{ ok: boolean }>(`/api/sessions/${sessionId}`, { method: "DELETE" });

  sendChat = (payload: {
    session_id?: string;
    content: string;
    provider?: string;
    model?: string;
    mode?: RunMode;
  }) =>
    this.requestJson<{ ok: boolean; session_id: string }>("/api/chat/send", {
      method: "POST",
      body: JSON.stringify(payload),
    });

  stopChat = (sessionId: string) =>
    this.requestJson<{ ok: boolean; stopped: boolean }>(`/api/chat/stop/${sessionId}`, { method: "POST" });

  uploadBase64 = (payload: {
    filename: string;
    content_base64: string;
    content_type?: string;
    brain?: string;
  }) =>
    this.requestJson<FileUploadResult>("/api/chat/upload-base64", {
      method: "POST",
      body: JSON.stringify(payload),
    });

  // ---- Voice ----
  getVoiceLiveStatus = () => this.requestJson<VoiceLiveStatus>("/api/voice/live/status");

  voiceTurn = (payload: { transcript: string; brain?: string; session_id?: string }) =>
    this.requestJson<{ transcript: string; reply: string; provider: string; model: string }>("/api/voice/turn", {
      method: "POST",
      body: JSON.stringify(payload),
    });

  synthesizeTtsBytes = async (
    text: string,
    voice?: string
  ): Promise<{ bytes?: Uint8Array; fallback?: string; spoken_text?: string }> => {
    const res = await fetch(`${this.baseUrl}/api/voice/tts`, {
      method: "POST",
      headers: this.authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ text, voice }),
    });
    if (!res.ok) {
      throw new JarvisApiError(res.status, `TTS HTTP ${res.status}`);
    }
    const ct = (res.headers.get("content-type") || "").toLowerCase();
    if (ct.includes("audio/")) {
      const buf = await res.arrayBuffer();
      return { bytes: new Uint8Array(buf) };
    }
    return (await res.json()) as { fallback?: string; spoken_text?: string };
  };

  transcribeAudioFile = async (uri: string, filename = "voice.m4a", mimeType = "audio/m4a"): Promise<string> => {
    const form = new FormData();
    form.append("file", {
      uri,
      name: filename,
      type: mimeType,
    } as unknown as Blob);
    const res = await fetch(`${this.baseUrl}/api/voice/stt`, {
      method: "POST",
      headers: this.authHeaders(),
      body: form,
    });
    if (!res.ok) {
      const txt = await res.text();
      throw new JarvisApiError(res.status, txt || `STT HTTP ${res.status}`);
    }
    const data = (await res.json()) as { text?: string };
    return data.text || "";
  };

  // ---- Work: Kanban & Reminders ----
  listTasks = (brain?: string) => {
    const qs = brain ? `?brain=${encodeURIComponent(brain)}` : "";
    return this.requestJson<{ brain: string; auto_enabled: boolean; tasks: KanbanTask[] }>(
      `/api/work/tasks${qs}`
    );
  };

  createTask = (
    payload: { title: string; description?: string; mode?: RunMode; route?: string },
    brain?: string
  ) => {
    const qs = brain ? `?brain=${encodeURIComponent(brain)}` : "";
    return this.requestJson<KanbanTask>(`/api/work/tasks${qs}`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  };

  toggleKanbanAuto = (enabled: boolean, brain?: string) => {
    const qs = brain ? `?brain=${encodeURIComponent(brain)}` : "";
    return this.requestJson<{ brain: string; auto_enabled: boolean }>(`/api/work/tasks/auto${qs}`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    });
  };

  runTask = (taskId: string) =>
    this.requestJson<KanbanTask>(`/api/work/tasks/${taskId}/run`, { method: "POST" });

  answerTask = (taskId: string, answer: string) =>
    this.requestJson<KanbanTask>(`/api/work/tasks/${taskId}/answer`, {
      method: "POST",
      body: JSON.stringify({ answer }),
    });

  acceptTask = (taskId: string) =>
    this.requestJson<KanbanTask>(`/api/work/tasks/${taskId}/accept`, { method: "POST" });

  cancelTask = (taskId: string) =>
    this.requestJson<KanbanTask>(`/api/work/tasks/${taskId}/cancel`, { method: "POST" });

  listReminders = (brain?: string) => {
    const qs = brain ? `?brain=${encodeURIComponent(brain)}` : "";
    return this.requestJson<{ brain: string; reminders: ReminderItem[] }>(`/api/work/reminders${qs}`);
  };

  createReminder = (
    payload: {
      name: string;
      prompt: string;
      cron?: string;
      run_at_ts?: number;
      notify_only?: boolean;
      mode?: RunMode;
    },
    brain?: string
  ) => {
    const qs = brain ? `?brain=${encodeURIComponent(brain)}` : "";
    return this.requestJson<ReminderItem>(`/api/work/reminders${qs}`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  };

  runReminder = (reminderId: string) =>
    this.requestJson<{ ok: boolean }>(`/api/work/reminders/${reminderId}/run`, { method: "POST" });

  deleteReminder = (reminderId: string) =>
    this.requestJson<{ ok: boolean }>(`/api/work/reminders/${reminderId}`, { method: "DELETE" });
}
