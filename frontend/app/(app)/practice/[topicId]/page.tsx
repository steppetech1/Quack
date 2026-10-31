import type { Metadata } from "next";
import { Suspense } from "react";
import { PracticeStub } from "@/components/quack/stubs/study-stubs";
import { getDict, getLocale, getMocks } from "@/lib/i18n/server";

export function generateStaticParams() {
  return getMocks("en").domains.flatMap((d) => d.topics.map((x) => ({ topicId: x.id })));
}

export async function generateMetadata({ params }: PageProps<"/practice/[topicId]">): Promise<Metadata> {
  const { topicId } = await params;
  const locale = await getLocale();
  const t = getDict(locale);
  const topic = getMocks(locale).domains.flatMap((d) => d.topics).find((x) => x.id === topicId);
  return { title: t.meta.title(t.stubs.practice.title(topic ? topic.title.replace(/­/g, "") : t.meta.topic)) };
}

// params читаем внутри Suspense, чтобы переход оставался мгновенным
export default function PracticeRoute({ params }: PageProps<"/practice/[topicId]">) {
  return (
    <Suspense fallback={<div className="flex-1" />}>
      <Inner params={params} />
    </Suspense>
  );
}

async function Inner({ params }: { params: PageProps<"/practice/[topicId]">["params"] }) {
  const { topicId } = await params;
  return <PracticeStub topicId={topicId} />;
}
