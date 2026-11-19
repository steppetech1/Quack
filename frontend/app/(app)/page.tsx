import type { Metadata } from "next";
import { AllGraphs } from "@/components/quack/all-graphs";
import { getDict, getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  const locale = await getLocale();
  // На бэкенде предмет известен только в браузере: заголовок ставит сам вид домена
  return { title: API_MODE ? "Quack!" : getDict(locale).meta.title(getMocks(locale).domains[0].title) };
}

export default function Home() {
  return <AllGraphs />;
}
