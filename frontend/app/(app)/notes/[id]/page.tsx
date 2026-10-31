import type { Metadata } from "next";
import { Suspense } from "react";
import { NoteStub } from "@/components/quack/stubs/study-stubs";
import { getDict, getLocale } from "@/lib/i18n/server";
import { getStubData } from "@/lib/mock/stubs";

export function generateStaticParams() {
  return [{ id: "new" }, ...getStubData("en").artifacts.filter((a) => a.kind === "note").map((a) => ({ id: a.id }))];
}

export async function generateMetadata(): Promise<Metadata> {
  const t = getDict(await getLocale());
  return { title: t.meta.title(t.stubs.note.title) };
}

// params читаем внутри Suspense, чтобы переход оставался мгновенным
export default function NoteRoute({ params }: PageProps<"/notes/[id]">) {
  return (
    <Suspense fallback={<div className="flex-1" />}>
      <Inner params={params} />
    </Suspense>
  );
}

async function Inner({ params }: { params: PageProps<"/notes/[id]">["params"] }) {
  const { id } = await params;
  return <NoteStub noteId={id} />;
}
