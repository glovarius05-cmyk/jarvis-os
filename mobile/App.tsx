import { Ionicons } from "@expo/vector-icons";
import { StatusBar } from "expo-status-bar";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import { JarvisApiClient, JarvisApiError } from "./src/api/client";
import { COLORS } from "./src/constants/theme";
import { ChatScreen } from "./src/screens/ChatScreen";
import { ServerSetupScreen } from "./src/screens/ServerSetupScreen";
import { TasksScreen } from "./src/screens/TasksScreen";
import { VaultScreen } from "./src/screens/VaultScreen";
import { VoiceScreen } from "./src/screens/VoiceScreen";
import {
  clearServerToken,
  loadSavedVoice,
  loadServerConfig,
  saveServerConfig,
  saveVoice,
} from "./src/storage/credentials";
import type { BrainItem, ProviderStatus, ServerConfig, SystemStatus } from "./src/types/api";

type TabId = "chat" | "voice" | "vault" | "tasks";

export default function App() {
  const [booting, setBooting] = useState(true);
  const [serverConfig, setServerConfig] = useState<ServerConfig | null>(null);
  const [showSetup, setShowSetup] = useState(false);

  const [tab, setTab] = useState<TabId>("chat");
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [brains, setBrains] = useState<BrainItem[]>([]);
  const [providers, setProviders] = useState<ProviderStatus[]>([]);
  const [activeBrain, setActiveBrain] = useState("Brain Default");
  const [activeSessionId, setActiveSessionId] = useState("");
  const [selectedVoice, setSelectedVoice] = useState("Charon");

  const client = useMemo(
    () => (serverConfig ? new JarvisApiClient(serverConfig.baseUrl, serverConfig.token) : null),
    [serverConfig]
  );

  const loadWorkspace = useCallback(async (c: JarvisApiClient) => {
    try {
      const auth = await c.getAuthStatus();
      if (auth.login_required && !auth.authenticated) {
        await clearServerToken();
        setShowSetup(true);
        return;
      }
      const [sys, br, prov] = await Promise.all([
        c.getSystemStatus(),
        c.listBrains(),
        c.getProviders(),
      ]);
      setStatus(sys);
      setBrains(br.brains || []);
      setActiveBrain(br.active_brain || sys.active_brain || "Brain Default");
      setProviders(prov.providers || []);
      setShowSetup(false);
    } catch (e) {
      if (e instanceof JarvisApiError && e.status === 401) {
        await clearServerToken();
      }
      setShowSetup(true);
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const [savedCfg, savedVoice] = await Promise.all([loadServerConfig(), loadSavedVoice()]);
        setSelectedVoice(savedVoice);
        if (savedCfg) {
          setServerConfig(savedCfg);
          await loadWorkspace(new JarvisApiClient(savedCfg.baseUrl, savedCfg.token));
        } else {
          setShowSetup(true);
        }
      } finally {
        setBooting(false);
      }
    })();
  }, [loadWorkspace]);

  const handleAuthenticated = async (cfg: ServerConfig) => {
    await saveServerConfig(cfg);
    setServerConfig(cfg);
    setShowSetup(false);
    await loadWorkspace(new JarvisApiClient(cfg.baseUrl, cfg.token));
  };

  const handleChangeBrain = async (brainName: string) => {
    if (!client) return;
    setActiveBrain(brainName);
    await client.patchSettings({ active_brain: brainName });
    const br = await client.listBrains();
    setBrains(br.brains || []);
  };

  const handleRefreshBrains = async () => {
    if (!client) return;
    const br = await client.listBrains();
    setBrains(br.brains || []);
    setActiveBrain(br.active_brain);
  };

  const handleSelectVoice = async (voiceId: string) => {
    setSelectedVoice(voiceId);
    await saveVoice(voiceId);
    if (client) {
      await client.patchSettings({ voice: { tts_voice: voiceId } }).catch(() => {});
    }
  };

  if (booting) {
    return (
      <SafeAreaProvider>
        <View style={styles.center}>
          <StatusBar style="light" />
          <ActivityIndicator size="large" color={COLORS.amber} />
          <Text style={styles.bootText}>Подключаюсь к Jarvis OS...</Text>
        </View>
      </SafeAreaProvider>
    );
  }

  if (showSetup || !client) {
    return (
      <SafeAreaProvider>
        <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
          <StatusBar style="light" />
          <ServerSetupScreen
            initialUrl={serverConfig?.baseUrl}
            onAuthenticated={handleAuthenticated}
            onCancel={serverConfig ? () => setShowSetup(false) : undefined}
          />
        </SafeAreaView>
      </SafeAreaProvider>
    );
  }

  return (
    <SafeAreaProvider>
      <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
        <StatusBar style="light" />
        <View style={styles.body}>
          {tab === "chat" && (
            <ChatScreen
              client={client}
              activeBrain={activeBrain}
              brains={brains}
              providers={providers}
              defaultProvider={status?.model?.main_provider || "openrouter"}
              defaultModel={status?.model?.main_model || "openrouter/auto"}
              selectedVoice={selectedVoice}
              onChangeBrain={handleChangeBrain}
              onOpenServerSettings={() => setShowSetup(true)}
              onActiveSessionChange={setActiveSessionId}
            />
          )}
          {tab === "voice" && (
            <VoiceScreen
              client={client}
              activeBrain={activeBrain}
              sessionId={activeSessionId}
              selectedVoice={selectedVoice}
              onSelectVoice={handleSelectVoice}
            />
          )}
          {tab === "vault" && (
            <VaultScreen
              client={client}
              activeBrain={activeBrain}
              brains={brains}
              onChangeBrain={handleChangeBrain}
              onRefreshBrains={handleRefreshBrains}
            />
          )}
          {tab === "tasks" && <TasksScreen client={client} activeBrain={activeBrain} />}
        </View>

        <View style={styles.tabBar}>
          {(
            [
              { id: "chat", label: "Чат", icon: "chatbubbles" },
              { id: "voice", label: "Голос", icon: "mic" },
              { id: "vault", label: "Заметки", icon: "library" },
              { id: "tasks", label: "Задачи", icon: "checkbox" },
            ] as const
          ).map((item) => {
            const active = tab === item.id;
            return (
              <Pressable key={item.id} onPress={() => setTab(item.id)} style={styles.tabItem}>
                <Ionicons
                  name={active ? item.icon : (`${item.icon}-outline` as keyof typeof Ionicons.glyphMap)}
                  size={22}
                  color={active ? COLORS.amber : COLORS.textMuted}
                />
                <Text style={[styles.tabLabel, active && styles.tabLabelActive]}>{item.label}</Text>
              </Pressable>
            );
          })}
        </View>
      </SafeAreaView>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: COLORS.bg },
  body: { flex: 1 },
  center: {
    flex: 1,
    backgroundColor: COLORS.bg,
    alignItems: "center",
    justifyContent: "center",
    gap: 12,
  },
  bootText: { color: COLORS.textSecondary, fontSize: 14 },
  tabBar: {
    flexDirection: "row",
    backgroundColor: COLORS.surface,
    borderTopWidth: 1,
    borderTopColor: COLORS.border,
    paddingTop: 6,
    paddingBottom: 6,
  },
  tabItem: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    gap: 3,
    paddingVertical: 4,
  },
  tabLabel: { fontSize: 11, color: COLORS.textMuted, fontWeight: "600" },
  tabLabelActive: { color: COLORS.amber },
});
