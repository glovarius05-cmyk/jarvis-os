import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";
import type { ServerConfig } from "../types/api";

const KEYS = {
  baseUrl: "jarvis_mobile_base_url",
  token: "jarvis_mobile_session_token",
  username: "jarvis_mobile_username",
  voice: "jarvis_mobile_voice",
  sessionId: "jarvis_mobile_last_session",
} as const;

async function setItem(key: string, value: string): Promise<void> {
  if (Platform.OS === "web") {
    try {
      globalThis.localStorage?.setItem(key, value);
    } catch {}
    return;
  }
  await SecureStore.setItemAsync(key, value);
}

async function getItem(key: string): Promise<string> {
  if (Platform.OS === "web") {
    try {
      return globalThis.localStorage?.getItem(key) || "";
    } catch {
      return "";
    }
  }
  return (await SecureStore.getItemAsync(key)) || "";
}

async function deleteItem(key: string): Promise<void> {
  if (Platform.OS === "web") {
    try {
      globalThis.localStorage?.removeItem(key);
    } catch {}
    return;
  }
  await SecureStore.deleteItemAsync(key);
}

export async function loadServerConfig(): Promise<ServerConfig | null> {
  const baseUrl = (await getItem(KEYS.baseUrl)).trim();
  if (!baseUrl) return null;
  const token = (await getItem(KEYS.token)).trim();
  const username = (await getItem(KEYS.username)).trim();
  return { baseUrl, token, username };
}

export async function saveServerConfig(cfg: ServerConfig): Promise<void> {
  await setItem(KEYS.baseUrl, cfg.baseUrl.trim());
  if (cfg.token) await setItem(KEYS.token, cfg.token);
  else await deleteItem(KEYS.token);
  if (cfg.username) await setItem(KEYS.username, cfg.username);
  else await deleteItem(KEYS.username);
}

export async function clearServerToken(): Promise<void> {
  await deleteItem(KEYS.token);
  await deleteItem(KEYS.username);
}

export async function loadSavedVoice(): Promise<string> {
  return (await getItem(KEYS.voice)) || "Charon";
}

export async function saveVoice(voiceId: string): Promise<void> {
  await setItem(KEYS.voice, voiceId);
}

export async function loadLastSessionId(): Promise<string> {
  return await getItem(KEYS.sessionId);
}

export async function saveLastSessionId(sessionId: string): Promise<void> {
  if (sessionId) await setItem(KEYS.sessionId, sessionId);
  else await deleteItem(KEYS.sessionId);
}
