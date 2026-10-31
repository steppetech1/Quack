import { dictionaries } from "./i18n/dict";
import type { Locale } from "./i18n/locale";
import { MOCK_TODAY } from "./mock";

export { plural, plural2 } from "./i18n/plural";

const DAY = 86_400_000;

const MONTHS: Record<Locale, string[]> = {
  ru: ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"],
  en: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
};
const MONTHS_FULL: Record<Locale, string[]> = {
  ru: [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
  ],
  en: [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ],
};

const parse = (iso: string) => new Date(`${iso}T00:00:00`);

export function daysFromToday(iso: string) {
  return Math.round((parse(iso).getTime() - parse(MOCK_TODAY).getTime()) / DAY);
}

/** «today», «tomorrow», «in 3 days» */
export function inDays(iso: string, locale: Locale) {
  const d = daysFromToday(iso);
  const t = dictionaries[locale].time;
  if (d === 0) return t.today;
  if (d === 1) return t.tomorrow;
  return t.inDays(d);
}

/** «today», «yesterday», «12 Oct» / «12 окт» */
export function dayLabel(iso: string, locale: Locale) {
  const d = daysFromToday(iso);
  const t = dictionaries[locale].time;
  if (d === 0) return t.today;
  if (d === -1) return t.yesterday;
  const date = parse(iso);
  return locale === "en" ? `${date.getDate()} ${MONTHS.en[date.getMonth()]}` : `${date.getDate()} ${MONTHS.ru[date.getMonth()]}`;
}

/** «1 November» / «1 ноября» */
export function fullDate(iso: string, locale: Locale) {
  const date = parse(iso);
  return `${date.getDate()} ${MONTHS_FULL[locale][date.getMonth()]}`;
}
