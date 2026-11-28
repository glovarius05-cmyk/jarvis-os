import { useCallback, useEffect, useRef, useState } from "react";
import type { JarvisApiClient } from "../api/client";
import type { ChatWsEvent, RunMode } from "../types/api";

export function useChatSocket(
  client: JarvisApiClient | null,
  onEvent: (event: ChatWsEvent) => void
) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    if (!client) {
      setConnected(false);
      return;
    }

    let closedByUser = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let pingTimer: ReturnType<typeof setInterval> | null = null;
    let retryDelay = 1200;

    const connect = () => {
      if (closedByUser) return;
      const wsUrl = client.buildWsUrl("/ws/chat");
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        retryDelay = 1200;
        if (pingTimer) clearInterval(pingTimer);
        pingTimer = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "ping" }));
          }
        }, 25000);
      };

      ws.onmessage = (evt) => {
        try {
          const parsed = JSON.parse(String(evt.data || "{}")) as ChatWsEvent;
          onEventRef.current(parsed);
        } catch {}
      };

      ws.onerror = () => {
        setConnected(false);
      };

      ws.onclose = () => {
        setConnected(false);
        if (pingTimer) {
          clearInterval(pingTimer);
          pingTimer = null;
        }
        if (!closedByUser) {
          retryTimer = setTimeout(() => {
            retryDelay = Math.min(retryDelay * 1.5, 10000);
            connect();
          }, retryDelay);
        }
      };
    };

    connect();

    return () => {
      closedByUser = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (pingTimer) clearInterval(pingTimer);
      try {
        wsRef.current?.close();
      } catch {}
      wsRef.current = null;
    };
  }, [client]);

  const sendMessage = useCallback(
    async (payload: {
      session_id: string;
      content: string;
      provider: string;
      model: string;
      mode: RunMode;
    }): Promise<void> => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "send", ...payload }));
        return;
      }
      if (!client) throw new Error("Сервер не подключён");
      await client.sendChat(payload);
    },
    [client]
  );

  const stopTurn = useCallback(
    async (sessionId: string): Promise<void> => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "stop", session_id: sessionId }));
      }
      if (client && sessionId) {
        await client.stopChat(sessionId).catch(() => {});
      }
    },
    [client]
  );

  return { connected, sendMessage, stopTurn };
}
