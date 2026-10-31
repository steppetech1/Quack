import type { Metadata } from "next";
import { getDict, getLocale, getMocks } from "@/lib/i18n/server";
import { Suspense } from "react";
import { SessionView } from "@/components/quack/session-view";
import { NewSessionLive, SessionLive } from "@/components/quack/live/session-live";
import { API_MODE } from "@/lib/api/client";

export function generateStaticParams() {
  return [{ id: "radius" }, { id: "new" }];
}

export async function generateMetadata({ params }: PageProps<"/session/[id]">): Promise<Metadata> {
  const { id } = await params;
  const locale = await getLocale();
  const t = getDict(locale);
  return { title: t.meta.title(id === "new" || API_MODE ? t.meta.newSession : getMocks(locale).radiusSession.title) };
}

// params читаем внутри Suspense, чтобы переход в сессию оставался мгновенным
export default function SessionPage({ params }: PageProps<"/session/[id]">) {
  return (
    <Suspense fallback={<div className="flex-1" />}>
      <Session params={params} />
    </Suspense>
  );
}

async function Session({ params }: { params: PageProps<"/session/[id]">["params"] }) {
  const { id } = await params;
  if (API_MODE) return id === "new" ? <NewSessionLive /> : <SessionLive key={id} id={id} />;
  return <SessionView id={id} />;
}
