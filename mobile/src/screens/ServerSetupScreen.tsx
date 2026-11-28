import { Ionicons } from "@expo/vector-icons";
import React, { useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { JarvisApiClient, normalizeBaseUrl } from "../api/client";
import { COLORS } from "../constants/theme";
import type { AuthStatusResponse, ServerConfig } from "../types/api";

interface Props {
  initialUrl?: string;
  onAuthenticated: (cfg: ServerConfig) => void;
  onCancel?: () => void;
}

const PRESET_URLS = [
  "https://assasin-jarvis.duckdns.org",
  "http://202.71.13.37:7777",
];

export function ServerSetupScreen({ initialUrl = "", onAuthenticated, onCancel }: Props) {
  const [urlInput, setUrlInput] = useState(initialUrl || PRESET_URLS[0]);
  const [checking, setChecking] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [authStatus, setAuthStatus] = useState<AuthStatusResponse | null>(null);
  const [probedUrl, setProbedUrl] = useState("");

  const [username, setUsername] = useState("owner");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");

  const handleCheckServer = async () => {
    const clean = normalizeBaseUrl(urlInput);
    if (!clean) {
      setError("Укажите адрес сервера Jarvis OS");
      return;
    }
    setError("");
    setChecking(true);
    try {
      const client = new JarvisApiClient(clean);
      await client.health();
      const st = await client.getAuthStatus();
      setProbedUrl(clean);
      setAuthStatus(st);
      if (!st.login_required || st.authenticated) {
        onAuthenticated({
          baseUrl: clean,
          token: "",
          username: st.username || "local",
        });
      }
    } catch (e) {
      setAuthStatus(null);
      setError(
        `Не удалось подключиться к ${clean}: ${e instanceof Error ? e.message : String(e)}`
      );
    } finally {
      setChecking(false);
    }
  };

  const handleAuthSubmit = async () => {
    if (!probedUrl || !authStatus) return;
    if (!username.trim() || password.length < 8) {
      setError("Введите логин и пароль (минимум 8 символов)");
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      const client = new JarvisApiClient(probedUrl);
      const res = authStatus.has_admin
        ? await client.login({
            username: username.trim(),
            password,
            totp_code: totpCode.trim(),
          })
        : await client.setupAdmin({
            username: username.trim(),
            password,
          });
      onAuthenticated({
        baseUrl: probedUrl,
        token: res.token || "",
        username: res.username || username.trim(),
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка входа");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <ScrollView
        style={styles.container}
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
      >
        <View style={styles.hero}>
          <View style={styles.logoBadge}>
            <Ionicons name="sparkles" size={28} color={COLORS.amber} />
          </View>
          <Text style={styles.title}>Jarvis OS Mobile</Text>
          <Text style={styles.subtitle}>
            Подключите свой VPS или локальный сервер для чата, голоса Gemini Live, Obsidian Vault и задач
          </Text>
        </View>

        <View style={styles.card}>
          <Text style={styles.label}>Адрес сервера</Text>
          <TextInput
            value={urlInput}
            onChangeText={(v) => {
              setUrlInput(v);
              setAuthStatus(null);
              setError("");
            }}
            placeholder="https://assasin-jarvis.duckdns.org"
            placeholderTextColor={COLORS.textMuted}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="url"
            style={styles.input}
          />

          <View style={styles.presetRow}>
            {PRESET_URLS.map((preset) => (
              <Pressable
                key={preset}
                onPress={() => {
                  setUrlInput(preset);
                  setAuthStatus(null);
                  setError("");
                }}
                style={[
                  styles.presetChip,
                  urlInput.trim() === preset && styles.presetChipActive,
                ]}
              >
                <Text
                  numberOfLines={1}
                  style={[
                    styles.presetText,
                    urlInput.trim() === preset && styles.presetTextActive,
                  ]}
                >
                  {preset.replace(/^https?:\/\//, "")}
                </Text>
              </Pressable>
            ))}
          </View>

          <Pressable
            onPress={handleCheckServer}
            disabled={checking}
            style={({ pressed }) => [
              styles.primaryBtn,
              pressed && styles.pressed,
              checking && styles.disabledBtn,
            ]}
          >
            {checking ? (
              <ActivityIndicator color="#09090b" />
            ) : (
              <>
                <Ionicons name="link-outline" size={18} color="#09090b" />
                <Text style={styles.primaryBtnText}>
                  {authStatus ? "Перепроверить сервер" : "Подключиться к серверу"}
                </Text>
              </>
            )}
          </Pressable>
        </View>

        {authStatus && authStatus.login_required && !authStatus.authenticated && (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>
              {authStatus.has_admin ? "Вход в аккаунт" : "Создание владельца (Admin)"}
            </Text>
            <Text style={styles.hint}>
              Сервер требует авторизацию. Сессия сохранится безопасно в хранилище устройства.
            </Text>

            <Text style={styles.label}>Логин</Text>
            <TextInput
              value={username}
              onChangeText={setUsername}
              autoCapitalize="none"
              autoCorrect={false}
              placeholder="owner"
              placeholderTextColor={COLORS.textMuted}
              style={styles.input}
            />

            <Text style={styles.label}>Пароль</Text>
            <TextInput
              value={password}
              onChangeText={setPassword}
              secureTextEntry
              placeholder="Минимум 8 символов"
              placeholderTextColor={COLORS.textMuted}
              style={styles.input}
            />

            {authStatus.totp_enabled && (
              <>
                <Text style={styles.label}>Код 2FA (TOTP)</Text>
                <TextInput
                  value={totpCode}
                  onChangeText={setTotpCode}
                  keyboardType="number-pad"
                  maxLength={8}
                  placeholder="6-значный код"
                  placeholderTextColor={COLORS.textMuted}
                  style={styles.input}
                />
              </>
            )}

            <Pressable
              onPress={handleAuthSubmit}
              disabled={submitting}
              style={({ pressed }) => [
                styles.primaryBtn,
                pressed && styles.pressed,
                submitting && styles.disabledBtn,
              ]}
            >
              {submitting ? (
                <ActivityIndicator color="#09090b" />
              ) : (
                <Text style={styles.primaryBtnText}>
                  {authStatus.has_admin ? "Войти" : "Создать и войти"}
                </Text>
              )}
            </Pressable>
          </View>
        )}

        {!!error && (
          <View style={styles.errorBox}>
            <Ionicons name="alert-circle" size={18} color={COLORS.rose} />
            <Text style={styles.errorText}>{error}</Text>
          </View>
        )}

        {onCancel && (
          <Pressable onPress={onCancel} style={styles.cancelBtn}>
            <Text style={styles.cancelBtnText}>Отмена</Text>
          </Pressable>
        )}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: COLORS.bg },
  container: { flex: 1, backgroundColor: COLORS.bg },
  content: { padding: 20, paddingTop: 48, gap: 16, paddingBottom: 40 },
  hero: { alignItems: "center", gap: 10, marginBottom: 8 },
  logoBadge: {
    width: 60,
    height: 60,
    borderRadius: 18,
    backgroundColor: COLORS.amberSoft,
    borderWidth: 1,
    borderColor: COLORS.amberBorder,
    alignItems: "center",
    justifyContent: "center",
  },
  title: { fontSize: 24, fontWeight: "700", color: COLORS.text },
  subtitle: { fontSize: 14, color: COLORS.textSecondary, textAlign: "center", lineHeight: 20 },
  card: {
    backgroundColor: COLORS.card,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: COLORS.border,
    padding: 16,
    gap: 10,
  },
  cardTitle: { fontSize: 16, fontWeight: "600", color: COLORS.text },
  hint: { fontSize: 13, color: COLORS.textSecondary, lineHeight: 18 },
  label: { fontSize: 12, fontWeight: "600", color: COLORS.textSecondary, marginTop: 4 },
  input: {
    backgroundColor: COLORS.bg,
    borderWidth: 1,
    borderColor: COLORS.borderLight,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    color: COLORS.text,
    fontSize: 15,
  },
  presetRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 2 },
  presetChip: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 999,
    backgroundColor: COLORS.cardAlt,
    borderWidth: 1,
    borderColor: COLORS.borderLight,
  },
  presetChipActive: {
    backgroundColor: COLORS.amberSoft,
    borderColor: COLORS.amberBorder,
  },
  presetText: { fontSize: 12, color: COLORS.textSecondary },
  presetTextActive: { color: COLORS.amber, fontWeight: "600" },
  primaryBtn: {
    marginTop: 8,
    backgroundColor: COLORS.amber,
    borderRadius: 12,
    paddingVertical: 13,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
  },
  primaryBtnText: { color: "#09090b", fontWeight: "700", fontSize: 15 },
  pressed: { opacity: 0.85 },
  disabledBtn: { opacity: 0.6 },
  errorBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    padding: 12,
    borderRadius: 12,
    backgroundColor: COLORS.roseSoft,
    borderWidth: 1,
    borderColor: "rgba(244, 63, 94, 0.4)",
  },
  errorText: { flex: 1, color: "#fda4af", fontSize: 13 },
  cancelBtn: { alignItems: "center", paddingVertical: 12 },
  cancelBtnText: { color: COLORS.textSecondary, fontSize: 14 },
});
