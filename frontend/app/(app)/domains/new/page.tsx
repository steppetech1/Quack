import type { Metadata } from "next";
import { NewDomainStub } from "@/components/quack/stubs/domain-setup";
import { getDict, getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  const t = getDict(await getLocale());
  return { title: t.meta.title(t.stubs.newDomain.title) };
}

export default function NewDomainRoute() {
  return <NewDomainStub />;
}
