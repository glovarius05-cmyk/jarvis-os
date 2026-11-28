export interface VoicePreset {
  id: string;
  label: string;
  gender: "male" | "female";
  styleHint: string;
}

export const VOICE_CATALOG: VoicePreset[] = [
  {
    id: "Charon",
    label: "J.A.R.V.I.S. (Charon · Мужской)",
    gender: "male",
    styleHint: "Спокойный интеллигентный баритон",
  },
  {
    id: "Orus",
    label: "Командор (Orus · Мужской)",
    gender: "male",
    styleHint: "Уверенный, глубокий, чёткий деловой голос",
  },
  {
    id: "Umbriel",
    label: "Ночной диктор (Umbriel · Мужской)",
    gender: "male",
    styleHint: "Бархатный расслабленный бас",
  },
  {
    id: "Fenrir",
    label: "Старк / Драйв (Fenrir · Мужской)",
    gender: "male",
    styleHint: "Энергичный и выразительный голос",
  },
  {
    id: "Puck",
    label: "Напарник (Puck · Мужской)",
    gender: "male",
    styleHint: "Лёгкий и быстрый молодой голос",
  },
  {
    id: "ru-RU-DmitryNeural",
    label: "Дмитрий (RU · Мужской)",
    gender: "male",
    styleHint: "Чёткий русский дикторский голос",
  },
  {
    id: "Aoede",
    label: "F.R.I.D.A.Y. (Aoede · Женский)",
    gender: "female",
    styleHint: "Тёплый, живой и естественный голос",
  },
  {
    id: "Kore",
    label: "Афина (Kore · Женский)",
    gender: "female",
    styleHint: "Собранный и чёткий тактический голос",
  },
  {
    id: "Leda",
    label: "Алиса (Leda · Женский)",
    gender: "female",
    styleHint: "Лёгкий и свежий женский голос",
  },
  {
    id: "Zephyr",
    label: "Спаркл (Zephyr · Женский)",
    gender: "female",
    styleHint: "Яркий, звонкий и жизнерадостный тембр",
  },
  {
    id: "Despina",
    label: "Ева (Despina · Женский)",
    gender: "female",
    styleHint: "Плавный и спокойный вечерний голос",
  },
  {
    id: "ru-RU-SvetlanaNeural",
    label: "Светлана (RU · Женский)",
    gender: "female",
    styleHint: "Чёткий русский женский голос",
  },
];
