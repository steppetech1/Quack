import type { Metadata } from "next";
import { LandingPage } from "@/components/quack/landing/landing-page";
import { getDict, getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  const t = getDict(await getLocale());
  return { title: "Qvack!", description: t.meta.description };
}

export default function LandingRoute() {
  return <LandingPage />;
}
