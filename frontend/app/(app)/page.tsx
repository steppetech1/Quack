import type { Metadata } from "next";
import { AllGraphs } from "@/components/quack/all-graphs";
import { getDict, getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  const locale = await getLocale();
  return { title: getDict(locale).meta.title(getDict(locale).allGraphs.title) };
}

export default function Home() {
  return <AllGraphs />;
}
