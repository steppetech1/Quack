import type { Metadata } from "next";
import { AuthForm } from "@/components/quack/stubs/auth-form";
import { getDict, getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  const t = getDict(await getLocale());
  return { title: t.meta.title(t.stubs.auth.signupTitle) };
}

export default function SignupRoute() {
  return <AuthForm mode="signup" />;
}
