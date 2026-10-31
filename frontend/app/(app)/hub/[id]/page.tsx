import type { Metadata } from "next";
import { Suspense } from "react";
import { HubPage } from "@/components/quack/hub-page";
import { getDict, getLocale, getMocks } from "@/lib/i18n/server";

export function generateStaticParams() {
  return getMocks("en").domains.flatMap((d) => d.hubs.map((x) => ({ id: x.id })));
}

export async function generateMetadata({ params }: PageProps<"/hub/[id]">): Promise<Metadata> {
  const { id } = await params;
  const locale = await getLocale();
  const t = getDict(locale);
  const hub = getMocks(locale).domains.flatMap((d) => d.hubs).find((h) => h.id === id);
  return { title: t.meta.title(hub ? `§ ${hub.num} ${hub.title.replace(/­/g, "")}` : t.meta.chapter) };
}

// params читаем внутри Suspense, чтобы переход оставался мгновенным
export default function HubRoute({ params }: PageProps<"/hub/[id]">) {
  return (
    <Suspense fallback={<div className="flex-1" />}>
      <Inner params={params} />
    </Suspense>
  );
}

async function Inner({ params }: { params: PageProps<"/hub/[id]">["params"] }) {
  const { id } = await params;
  return <HubPage id={id} />;
}
