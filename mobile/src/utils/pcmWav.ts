const B64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

export function base64ToBytes(b64: string): Uint8Array {
  const clean = b64.replace(/[^A-Za-z0-9+/=]/g, "");
  const len = clean.length;
  let padding = 0;
  if (clean.endsWith("==")) padding = 2;
  else if (clean.endsWith("=")) padding = 1;

  const byteLen = Math.floor((len * 3) / 4) - padding;
  const bytes = new Uint8Array(Math.max(0, byteLen));

  let p = 0;
  for (let i = 0; i < len; i += 4) {
    const c1 = B64_CHARS.indexOf(clean[i]);
    const c2 = B64_CHARS.indexOf(clean[i + 1]);
    const c3 = clean[i + 2] === "=" ? 0 : B64_CHARS.indexOf(clean[i + 2]);
    const c4 = clean[i + 3] === "=" ? 0 : B64_CHARS.indexOf(clean[i + 3]);

    const trip = (c1 << 18) | (c2 << 12) | (c3 << 6) | c4;
    if (p < byteLen) bytes[p++] = (trip >> 16) & 0xff;
    if (p < byteLen) bytes[p++] = (trip >> 8) & 0xff;
    if (p < byteLen) bytes[p++] = trip & 0xff;
  }
  return bytes;
}

export function bytesToBase64(bytes: Uint8Array): string {
  let out = "";
  for (let i = 0; i < bytes.length; i += 3) {
    const b1 = bytes[i];
    const has2 = i + 1 < bytes.length;
    const has3 = i + 2 < bytes.length;
    const b2 = has2 ? bytes[i + 1] : 0;
    const b3 = has3 ? bytes[i + 2] : 0;
    const trip = (b1 << 16) | (b2 << 8) | b3;

    out += B64_CHARS[(trip >> 18) & 0x3f];
    out += B64_CHARS[(trip >> 12) & 0x3f];
    out += has2 ? B64_CHARS[(trip >> 6) & 0x3f] : "=";
    out += has3 ? B64_CHARS[trip & 0x3f] : "=";
  }
  return out;
}

/**
 * Resample mono Int16 PCM to 16 kHz for Gemini Live when hardware delivers 44.1/48 kHz.
 */
export function resampleInt16To16k(input: Int16Array, sourceRate: number): Int16Array {
  if (!sourceRate || sourceRate === 16000 || input.length === 0) {
    return input;
  }
  const ratio = sourceRate / 16000;
  const outLength = Math.max(1, Math.floor(input.length / ratio));
  const out = new Int16Array(outLength);
  for (let i = 0; i < outLength; i++) {
    const srcIdx = i * ratio;
    const idx0 = Math.floor(srcIdx);
    const idx1 = Math.min(input.length - 1, idx0 + 1);
    const frac = srcIdx - idx0;
    out[i] = Math.round(input[idx0] * (1 - frac) + input[idx1] * frac);
  }
  return out;
}

/**
 * Wrap concatenated raw 16-bit mono PCM chunks into a standard WAV container
 * so expo-audio can play streamed Gemini Live responses natively on iOS & Android.
 */
export function pcm16ChunksToWavBase64(base64Chunks: string[], sampleRate = 24000): string {
  const decoded = base64Chunks.map(base64ToBytes);
  const dataLength = decoded.reduce((acc, arr) => acc + arr.length, 0);
  const wav = new Uint8Array(44 + dataLength);
  const view = new DataView(wav.buffer);

  const writeStr = (offset: number, str: string) => {
    for (let i = 0; i < str.length; i++) {
      wav[offset + i] = str.charCodeAt(i);
    }
  };

  writeStr(0, "RIFF");
  view.setUint32(4, 36 + dataLength, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true); // PCM chunk size
  view.setUint16(20, 1, true); // Linear PCM
  view.setUint16(22, 1, true); // Mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byteRate = rate * channels * 2
  view.setUint16(32, 2, true); // blockAlign
  view.setUint16(34, 16, true); // bitsPerSample
  writeStr(36, "data");
  view.setUint32(40, dataLength, true);

  let offset = 44;
  for (const chunk of decoded) {
    wav.set(chunk, offset);
    offset += chunk.length;
  }

  return bytesToBase64(wav);
}
