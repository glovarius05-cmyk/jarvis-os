import { createAudioPlayer, setAudioModeAsync, type AudioPlayer } from "expo-audio";
import { File, Paths } from "expo-file-system";
import * as Speech from "expo-speech";
import { useCallback, useEffect, useRef, useState } from "react";
import type { JarvisApiClient } from "../api/client";
import { isFemaleVoice } from "../utils/voiceHelpers";

export function useVoicePlayer(client: JarvisApiClient | null) {
  const [speakingId, setSpeakingId] = useState<string | null>(null);
  const playerRef = useRef<AudioPlayer | null>(null);
  const tempFileRef = useRef<File | null>(null);

  const stop = useCallback(() => {
    try {
      Speech.stop();
    } catch {}
    if (playerRef.current) {
      try {
        playerRef.current.pause();
        playerRef.current.remove();
      } catch {}
      playerRef.current = null;
    }
    if (tempFileRef.current) {
      try {
        if (tempFileRef.current.exists) tempFileRef.current.delete();
      } catch {}
      tempFileRef.current = null;
    }
    setSpeakingId(null);
  }, []);

  useEffect(() => stop, [stop]);

  const playAudioBytes = useCallback(
    async (bytes: Uint8Array, filename: string, itemId: string): Promise<void> => {
      stop();
      setSpeakingId(itemId);
      await setAudioModeAsync({
        playsInSilentMode: true,
        allowsRecording: false,
      });

      const file = new File(Paths.cache, filename);
      if (file.exists) file.delete();
      file.create();
      await file.write(bytes);
      tempFileRef.current = file;

      const player = createAudioPlayer(file.uri);
      playerRef.current = player;

      await new Promise<void>((resolve) => {
        const sub = player.addListener("playbackStatusUpdate", (st) => {
          if (st.didJustFinish || st.error) {
            sub.remove();
            if (playerRef.current === player) {
              stop();
            }
            resolve();
          }
        });
        player.play();
      });
    },
    [stop]
  );

  const speakText = useCallback(
    async (text: string, itemId: string, voiceId?: string): Promise<void> => {
      if (!client || !text.trim()) return;
      if (speakingId === itemId) {
        stop();
        return;
      }

      setSpeakingId(itemId);
      try {
        const res = await client.synthesizeTtsBytes(text, voiceId);
        if (res.bytes && res.bytes.length > 0) {
          await playAudioBytes(res.bytes, `tts_${Date.now()}.mp3`, itemId);
          return;
        }
        const spoken = res.spoken_text || text;
        const female = isFemaleVoice(voiceId || "");
        await new Promise<void>((resolve) => {
          Speech.speak(spoken, {
            language: "ru-RU",
            pitch: female ? 1.1 : 0.94,
            rate: 1.0,
            onDone: () => {
              setSpeakingId(null);
              resolve();
            },
            onStopped: () => {
              setSpeakingId(null);
              resolve();
            },
            onError: () => {
              setSpeakingId(null);
              resolve();
            },
          });
        });
      } catch {
        setSpeakingId(null);
      }
    },
    [client, playAudioBytes, speakingId, stop]
  );

  return { speakingId, speakText, playAudioBytes, stop };
}
