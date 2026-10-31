import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { Suspense } from "react";
import { CanvasView } from "@/components/quack/canvas-view";
import { getDict, getLocale, getMocks } from "@/lib/i18n/server";


export function generateStaticParams() {
  return getMocks("en").canvases.map((c) => ({ id: c.id }));
}

export async function generateMetadata({ params }: PageProps<"/canvas/[id]">): Promise<Metadata> {
  const { id } = await params;
  const locale = await getLocale();
  const t = getDict(locale);
  return { title: t.meta.title(getMocks(locale).getCanvas(id)?.title ?? t.meta.canvas) };
}

// params читаем внутри Suspense, чтобы переход оставался мгновенным
export default function CanvasRoute({ params }: PageProps<"/canvas/[id]">) {
  return (
    <Suspense fallback={<div className="flex-1" />}>
      <Inner params={params} />
    </Suspense>
  );
}

async function Inner({ params }: { params: PageProps<"/canvas/[id]">["params"] }) {
  const { id } = await params;
  if (!getMocks(await getLocale()).getCanvas(id)) notFound();
  return <CanvasView id={id} />;
}
