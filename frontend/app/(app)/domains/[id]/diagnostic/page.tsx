import type { Metadata } from "next";
import { Suspense } from "react";
import { DiagnosticStub } from "@/components/quack/stubs/domain-setup";
import { getDict, getLocale, getMocks } from "@/lib/i18n/server";

export function generateStaticParams() {
  return getMocks("en").domains.map((d) => ({ id: d.id }));
}

export async function generateMetadata(): Promise<Metadata> {
  const t = getDict(await getLocale());
  return { title: t.meta.title(t.stubs.diagnostic.title) };
}

// params читаем внутри Suspense, чтобы переход оставался мгновенным
export default function DiagnosticRoute({ params }: PageProps<"/domains/[id]/diagnostic">) {
  return (
    <Suspense fallback={<div className="flex-1" />}>
      <Inner params={params} />
    </Suspense>
  );
}

async function Inner({ params }: { params: PageProps<"/domains/[id]/diagnostic">["params"] }) {
  const { id } = await params;
  return <DiagnosticStub domainId={id} />;
}
