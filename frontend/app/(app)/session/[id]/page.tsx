import type { Metadata } from "next";
import { getDict, getLocale, getMocks } from "@/lib/i18n/server";
import { Suspense } from "react";
import { SessionView } from "@/components/quack/session-view";
import { NewSessionLive, SessionLive } from "@/components/quack/live/session-live";
import { SessionWorkspace } from "@/components/quack/workspace/workspace-pane";
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
  // a session has the workspace pane beside its chat (documents the learner opens); a session that is just being made has none
  if (id === "new") return API_MODE ? <NewSessionLive /> : <SessionView id={id} />;
  return (
    <SessionWorkspace key={id} sessionId={id}>
      {API_MODE ? <SessionLive id={id} /> : <SessionView id={id} />}
    </SessionWorkspace>
  );
}
