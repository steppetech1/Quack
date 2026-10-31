import { redirect } from "next/navigation";

const MAP: Record<string, string> = { skills: "skills", subjects: "domains", data: "account" };

export default async function ProfileRoute({ searchParams }: PageProps<"/profile">) {
  const tab = (await searchParams).tab;
  const to = typeof tab === "string" ? MAP[tab] : undefined;
  redirect(to ? `/settings?tab=${to}` : "/settings");
}
