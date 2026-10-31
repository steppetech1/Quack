import type { Metadata } from "next";
import { LibraryStub } from "@/components/quack/stubs/study-stubs";
import { getDict, getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  const t = getDict(await getLocale());
  return { title: t.meta.title(t.stubs.library.title) };
}

export default function LibraryRoute() {
  return <LibraryStub />;
}
