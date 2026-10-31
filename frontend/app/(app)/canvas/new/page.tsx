import type { Metadata } from "next";
import { NewCanvasView } from "@/components/quack/canvas-view";
import { getDict, getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  const t = getDict(await getLocale());
  return { title: t.meta.title(t.meta.newCanvas) };
}

export default function NewCanvasRoute() {
  return <NewCanvasView />;
}
