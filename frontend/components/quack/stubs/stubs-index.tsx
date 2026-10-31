"use client";

import Link from "next/link";
import { useT } from "@/lib/i18n/provider";
import { BackLink, PageShell } from "../page-parts";
import { StubHeader } from "./stub-parts";

/** One list of all stub screens, so they can be reached before they are linked from the app */
export function StubsIndex() {
  const tx = useT();
  const s = tx.stubs;
  const items: { href: string; title: string; api: string }[] = [
    { href: "/login", title: s.auth.loginTitle, api: "POST /v1/auth/login" },
    { href: "/signup", title: s.auth.signupTitle, api: "POST /v1/auth/register" },
    { href: "/domains/new", title: s.newDomain.title, api: "POST /v1/domains, …/structure/propose" },
    { href: "/domains/matan/structure", title: s.structure.title(tx.stubs.newDomain.namePh), api: "…/structure/confirm, PATCH …/structure, …/archive" },
    { href: "/domains/matan/diagnostic", title: s.diagnostic.title, api: "…/diagnostic/start|answer|finish" },
    { href: "/practice/radius", title: s.practice.title("…"), api: "…/tasks/next|hint|submit" },
    { href: "/session/radius/summary", title: s.summary.title, api: "POST /v1/sessions/{id}/finish" },
    { href: "/library", title: s.library.title, api: "GET /v1/artifacts, /artifacts/search, …/export" },
    { href: "/notes/new", title: s.note.newTitle, api: "POST|PATCH|DELETE /v1/artifacts/notes" },
  ];
  return (
    <PageShell>
      <BackLink href="/">{tx.shell.subjects}</BackLink>
      <StubHeader title={s.index.title} body={s.index.body} />
      <ul className="mt-8 border-t border-seam-soft">
        {items.map((i) => (
          <li key={i.href} className="border-b border-seam-soft">
            <Link href={i.href} className="flex min-h-12 flex-wrap items-center gap-x-4 gap-y-1 rounded-option px-1 py-2 transition-colors hover:bg-cloth-hover">
              <span className="min-w-0 flex-1 text-body text-paper">{i.title}</span>
              <span className="num text-small text-paper-dim">{i.api}</span>
            </Link>
          </li>
        ))}
      </ul>
    </PageShell>
  );
}
