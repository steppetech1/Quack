import type { Metadata } from "next";
import { Suspense } from "react";
import { DomainView } from "@/components/quack/domain-view";
import { API_MODE } from "@/lib/api/client";
import { getDict, getLocale, getMocks } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  const locale = await getLocale();
  // На бэкенде предмет известен только в браузере: заголовок ставит сам вид домена
  return { title: API_MODE ? "Quack!" : getDict(locale).meta.title(getMocks(locale).domains[0].title) };
}

export default function Home() {
  return (
    <Suspense fallback={null}>
      <DomainView />
    </Suspense>
  );
}
