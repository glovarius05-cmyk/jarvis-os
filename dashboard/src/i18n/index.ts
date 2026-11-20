import ru from "./ru.json";
import en from "./en.json";

export type UiLang = "ru" | "en";

const DICTS: Record<UiLang, Record<string, string>> = { ru, en };

export function translate(lang: UiLang, key: string): string {
  return DICTS[lang]?.[key] ?? DICTS.en[key] ?? key;
}
