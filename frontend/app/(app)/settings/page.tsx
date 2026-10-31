import type { Metadata } from "next";
import { Suspense } from "react";
import { SettingsPage } from "@/components/quack/settings-page";
import { getDict, getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  const t = getDict(await getLocale());
  return { title: t.meta.title(t.meta.settings) };
}

export default function SettingsRoute() {
  return (
    <Suspense fallback={<div className="flex-1" />}>
      <SettingsPage />
    </Suspense>
  );
}
