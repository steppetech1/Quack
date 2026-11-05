"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useData } from "@/lib/api/data";
import { type Intent, startSession } from "@/lib/api/sessions";
import { useNotice } from "../soon";
import { useErrorText } from "./error-text";

type Start = { domainId: string; topicId?: string | null; hubId?: string | null; intent: Intent };

/** Start a session on the backend and open it; a failure shows as a toast */
export function useStartSession() {
  const router = useRouter();
  const notice = useNotice();
  const errorText = useErrorText();
  const { setActive } = useData();
  const [busy, setBusy] = useState(false);

  const start = async ({ domainId, topicId = null, hubId = null, intent }: Start) => {
    if (busy) return;
    setBusy(true);
    try {
      const s = await startSession({ domain_id: domainId, topic_id: topicId, hub_id: hubId, intent });
      setActive(s);
      router.push(`/session/${s.session_id}`);
    } catch (e) {
      notice(errorText(e));
      setBusy(false);
    }
  };

  return { start, busy };
}
