import type { Metadata } from "next";
import { Suspense } from "react";
import { TopicLive } from "@/components/quack/live/topic-live";
import { TopicPage } from "@/components/quack/topic-page";
import { API_MODE } from "@/lib/api/client";
import { getDict, getLocale, getMocks } from "@/lib/i18n/server";

export function generateStaticParams() {
  return getMocks("en").domains.flatMap((d) => d.topics.map((x) => ({ id: x.id })));
}

export async function generateMetadata({ params }: PageProps<"/topic/[id]">): Promise<Metadata> {
  const { id } = await params;
  const locale = await getLocale();
  const t = getDict(locale);
  const topic = getMocks(locale).domains.flatMap((d) => d.topics).find((x) => x.id === id);
  return { title: t.meta.title(topic ? topic.title.replace(/­/g, "") : t.meta.topic) };
}

// params читаем внутри Suspense, чтобы переход оставался мгновенным
export default function TopicRoute({ params }: PageProps<"/topic/[id]">) {
  return (
    <Suspense fallback={<div className="flex-1" />}>
      <Inner params={params} />
    </Suspense>
  );
}

async function Inner({ params }: { params: PageProps<"/topic/[id]">["params"] }) {
  const { id } = await params;
  return API_MODE ? <TopicLive id={id} /> : <TopicPage id={id} />;
}
