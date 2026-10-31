import type { Metadata } from "next";
import { Suspense } from "react";
import { SessionSummaryStub } from "@/components/quack/stubs/study-stubs";
import { getDict, getLocale } from "@/lib/i18n/server";

export function generateStaticParams() {
  return [{ id: "radius" }];
}

export async function generateMetadata(): Promise<Metadata> {
  const t = getDict(await getLocale());
  return { title: t.meta.title(t.stubs.summary.title) };
}

// params читаем внутри Suspense, чтобы переход оставался мгновенным
export default function SessionSummaryRoute({ params }: PageProps<"/session/[id]/summary">) {
  return (
    <Suspense fallback={<div className="flex-1" />}>
      <Inner params={params} />
    </Suspense>
  );
}

async function Inner({ params }: { params: PageProps<"/session/[id]/summary">["params"] }) {
  const { id } = await params;
  return <SessionSummaryStub id={id} />;
}
