import type { Metadata } from "next";
import { Suspense } from "react";
import { DomainView } from "@/components/quack/domain-view";
import { getDict, getLocale, getMocks } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  const locale = await getLocale();
  return { title: getDict(locale).meta.title(getMocks(locale).domains[0].title) };
}

export default function Home() {
  return (
    <Suspense fallback={null}>
      <DomainView />
    </Suspense>
  );
}
