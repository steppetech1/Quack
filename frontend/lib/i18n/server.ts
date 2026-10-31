import { cookies } from "next/headers";
import { getMocks } from "@/lib/mock";
import { dictionaries } from "./dict";
import { LOCALE_COOKIE, parseLocale, type Locale } from "./locale";

/** Server-side locale from the cookie. Call inside a Suspense boundary. */
export async function getLocale(): Promise<Locale> {
  return parseLocale((await cookies()).get(LOCALE_COOKIE)?.value);
}

export const getDict = (locale: Locale) => dictionaries[locale];
export { getMocks };
