import type { Metadata } from "next";
import { StubsIndex } from "@/components/quack/stubs/stubs-index";
import { getDict, getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  const t = getDict(await getLocale());
  return { title: t.meta.title(t.stubs.index.title) };
}

export default function StubsRoute() {
  return <StubsIndex />;
}
