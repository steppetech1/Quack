import type { Metadata } from "next";
import { Suspense } from "react";
import { StructureStub } from "@/components/quack/stubs/domain-setup";
import { getDict, getLocale, getMocks } from "@/lib/i18n/server";

export function generateStaticParams() {
  return getMocks("en").domains.map((d) => ({ id: d.id }));
}

export async function generateMetadata({ params }: PageProps<"/domains/[id]/structure">): Promise<Metadata> {
  const { id } = await params;
  const locale = await getLocale();
  const t = getDict(locale);
  const domain = getMocks(locale).domains.find((d) => d.id === id);
  return { title: t.meta.title(domain ? t.stubs.structure.title(domain.title) : t.stubs.structure.topics) };
}

// params читаем внутри Suspense, чтобы переход оставался мгновенным
export default function StructureRoute({ params }: PageProps<"/domains/[id]/structure">) {
  return (
    <Suspense fallback={<div className="flex-1" />}>
      <Inner params={params} />
    </Suspense>
  );
}

async function Inner({ params }: { params: PageProps<"/domains/[id]/structure">["params"] }) {
  const { id } = await params;
  return <StructureStub domainId={id} />;
}
