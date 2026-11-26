import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ApprovalItem } from "../api/client";

export interface LiveToolEvent {
  type: "tool_call" | "tool_result";
  tool_name?: string;
  content?: string;
  timestamp?: number;
}

export interface LiveTurnState {
  sessionId: string;
  provider: string;
  model: string;
  text: string;
  tools: LiveToolEvent[];
}

export interface ChatSocketHandlers {
  onFinal?: (sessionId: string) => void;
  onTurnStart?: (sessionId: string) => void;
  onBackgroundUpdate?: () => void;
}

export function useChatSocket(handlers: ChatSocketHandlers = {}) {
  const [connected, setConnected] = useState(false);
  const [liveTurns, setLiveTurns] = useState<Record<string, LiveTurnState>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [pendingApprovals, setPendingApprovals] = useState<ApprovalItem[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    void api
      .listPendingApprovals()
      .then((res) => {
        if (res?.pending) setPendingApprovals(res.pending);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    let closed = false;
    let retryTimer: number | undefined;

    const connect = () => {
      if (closed) return;
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${proto}//${window.location.host}/ws/chat`);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
      };

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data as string) as Record<string, unknown>;
          const type = String(msg.type ?? "");
          const sid = String(msg.session_id ?? "");

          if (type === "hello") {
            const active = (msg.active_jobs as Array<Record<string, unknown>>) ?? [];
            const next: Record<string, LiveTurnState> = {};
            for (const job of active) {
              const jsid = String(job.session_id ?? "");
              if (!jsid) continue;
              next[jsid] = {
                sessionId: jsid,
                provider: String(job.provider ?? ""),
                model: String(job.model ?? ""),
                text: String(job.accumulated_text ?? ""),
                tools: (job.tool_events as LiveToolEvent[]) ?? [],
              };
            }
            setLiveTurns(next);
          } else if (type === "turn_start" && sid) {
            setErrors((prev) => {
              const copy = { ...prev };
              delete copy[sid];
              return copy;
            });
            setLiveTurns((prev) => ({
              ...prev,
              [sid]: {
                sessionId: sid,
                provider: String(msg.provider ?? ""),
                model: String(msg.model ?? ""),
                text: "",
                tools: [],
              },
            }));
            handlersRef.current.onTurnStart?.(sid);
          } else if (type === "stream" && sid) {
            const delta = String(msg.content ?? "");
            setLiveTurns((prev) => {
              const cur = prev[sid] ?? {
                sessionId: sid,
                provider: String(msg.provider ?? ""),
                model: String(msg.model ?? ""),
                text: "",
                tools: [],
              };
              return {
                ...prev,
                [sid]: { ...cur, text: cur.text + delta },
              };
            });
          } else if ((type === "tool_call" || type === "tool_result") && sid) {
            setLiveTurns((prev) => {
              const cur = prev[sid];
              if (!cur) return prev;
              return {
                ...prev,
                [sid]: {
                  ...cur,
                  tools: [
                    ...cur.tools,
                    {
                      type,
                      tool_name: String(msg.tool_name ?? ""),
                      content: String(msg.content ?? ""),
                      timestamp: Date.now(),
                    },
                  ],
                },
              };
            });
          } else if (type === "final" && sid) {
            setLiveTurns((prev) => {
              const copy = { ...prev };
              delete copy[sid];
              return copy;
            });
            handlersRef.current.onFinal?.(sid);
          } else if (type === "error" && sid) {
            setLiveTurns((prev) => {
              const copy = { ...prev };
              delete copy[sid];
              return copy;
            });
            setErrors((prev) => ({ ...prev, [sid]: String(msg.content ?? "Unknown error") }));
          } else if (type === "approval_request") {
            const item: ApprovalItem = {
              approval_id: String(msg.approval_id ?? ""),
              session_id: String(msg.session_id ?? ""),
              brain: String(msg.brain ?? ""),
              tool_name: String(msg.tool_name ?? ""),
              args: (msg.args as Record<string, unknown>) ?? {},
              reason: String(msg.reason ?? ""),
              channel: String(msg.channel ?? "web"),
              status: "pending",
              created_at: Number(msg.created_at ?? Date.now() / 1000),
            };
            setPendingApprovals((prev) => [...prev.filter((p) => p.approval_id !== item.approval_id), item]);
          } else if (type === "approval_resolved") {
            const aid = String(msg.approval_id ?? "");
            setPendingApprovals((prev) => prev.filter((p) => p.approval_id !== aid));
          } else if (type === "inbox_item" || type === "kanban_updated") {
            handlersRef.current.onBackgroundUpdate?.();
          }
        } catch {
          // Ignore malformed frame
        }
      };

      ws.onclose = () => {
        setConnected(false);
        if (!closed) {
          retryTimer = window.setTimeout(connect, 1500);
        }
      };
    };

    connect();
    return () => {
      closed = true;
      if (retryTimer) window.clearTimeout(retryTimer);
      wsRef.current?.close();
    };
  }, []);

  const sendMessage = useCallback(
    (sessionId: string, content: string, provider = "", model = "", mode = "full") => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        setErrors((prev) => ({ ...prev, [sessionId]: "WebSocket is disconnected" }));
        return false;
      }
      ws.send(
        JSON.stringify({
          type: "send",
          session_id: sessionId,
          content,
          provider,
          model,
          mode,
        })
      );
      return true;
    },
    []
  );

  const stopTurn = useCallback((sessionId: string) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "stop", session_id: sessionId }));
    }
  }, []);

  const resolveApproval = useCallback(async (approvalId: string, decision: "approve" | "deny") => {
    try {
      await api.resolveApproval(approvalId, decision);
      setPendingApprovals((prev) => prev.filter((p) => p.approval_id !== approvalId));
    } catch (err) {
      console.error("Failed to resolve approval:", err);
    }
  }, []);

  return { connected, liveTurns, errors, pendingApprovals, resolveApproval, sendMessage, stopTurn };
}
