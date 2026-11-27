export interface VoicePreset {
  id: string;
  label: string;
  gender: "male" | "female";
  styleHint: string;
}

export const VOICE_CATALOG: VoicePreset[] = [
  // Male voices
  {
    id: "Charon",
    label: "J.A.R.V.I.S. (Charon · Мужской)",
    gender: "male",
    styleHint: "Спокойный интеллигентный баритон в духе классического Джарвиса",
  },
  {
    id: "Orus",
    label: "TARS / Командор (Orus · Мужской)",
    gender: "male",
    styleHint: "Уверенный, глубокий, чёткий деловой голос (Интерстеллар / командир)",
  },
  {
    id: "Umbriel",
    label: "Ночной диктор (Umbriel · Мужской)",
    gender: "male",
    styleHint: "Бархатный расслабленный бас, спокойный рассказчик в стиле Моргана Фримена",
  },
  {
    id: "Fenrir",
    label: "Старк / Драйв (Fenrir · Мужской)",
    gender: "male",
    styleHint: "Энергичный, харизматичный и выразительный голос",
  },
  {
    id: "Puck",
    label: "Спайди / Напарник (Puck · Мужской)",
    gender: "male",
    styleHint: "Лёгкий, быстрый и дружелюбный молодой голос",
  },
  {
    id: "ru-RU-DmitryNeural",
    label: "Дмитрий (Классика RU · Мужской)",
    gender: "male",
    styleHint: "Чёткий русский дикторский голос (Edge Neural / Charon в Live)",
  },

  // Female voices
  {
    id: "Aoede",
    label: "F.R.I.D.A.Y. / Саманта (Aoede · Женский)",
    gender: "female",
    styleHint: "Тёплый, живой и естественный голос в духе F.R.I.D.A.Y. / фильма «Она»",
  },
  {
    id: "Kore",
    label: "Кортана / Афина (Kore · Женский)",
    gender: "female",
    styleHint: "Собранный, уверенный и чёткий тактический голос",
  },
  {
    id: "Leda",
    label: "Алиса / Юный ИИ (Leda · Женский)",
    gender: "female",
    styleHint: "Лёгкий, свежий и энергичный молодой женский голос",
  },
  {
    id: "Zephyr",
    label: "Спаркл (Zephyr · Женский)",
    gender: "female",
    styleHint: "Яркий, звонкий и жизнерадостный тембр",
  },
  {
    id: "Despina",
    label: "Ева / Мягкий ИИ (Despina · Женский)",
    gender: "female",
    styleHint: "Плавный, спокойный и мягкий вечерний голос",
  },
  {
    id: "ru-RU-SvetlanaNeural",
    label: "Светлана (Классика RU · Женский)",
    gender: "female",
    styleHint: "Чёткий русский женский голос (Edge Neural / Aoede в Live)",
  },
];

const FEMALE_IDS = new Set([
  "aoede",
  "kore",
  "leda",
  "zephyr",
  "despina",
  "autonoe",
  "erinome",
  "callirrhoe",
  "ru-ru-svetlananeural",
  "ru-ru-dariyaneural",
  "browser-female",
]);

export function isFemaleVoice(voiceId: string): boolean {
  const key = (voiceId || "").trim().toLowerCase();
  if (FEMALE_IDS.has(key)) return true;
  return key.includes("svetlana") || key.includes("dariya") || key.includes("female");
}

export function getSavedVoice(): string {
  try {
    return localStorage.getItem("jarvis_selected_voice") || "Charon";
  } catch {
    return "Charon";
  }
}
