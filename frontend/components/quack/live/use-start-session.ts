"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useData } from "@/lib/api/data";
import { startSession } from "@/lib/api/sessions";
import { useNotice } from "../soon";
import { useErrorText } from "./domain-setup-live";

type Start = { domainId: string; topicId?: string | null; mode: "learn" | "practice" | "review"; goal?: string | null };

/** Start a session on the backend and open it; a failure shows as a toast */
export function useStartSession() {
  const router = useRouter();
  const notice = useNotice();
  const errorText = useErrorText();
  const { setActive } = useData();
  const [busy, setBusy] = useState(false);

  const start = async ({ domainId, topicId = null, mode, goal = null }: Start) => {
    if (busy) return;
    setBusy(true);
    try {
      const s = await startSession({ domain_id: domainId, topic_id: topicId, mode, goal });
      const now = new Date().toISOString();
      setActive({
        session_id: s.session_id,
        domain_id: s.domain_id,
        topic_id: s.topic_id,
        mode: s.mode,
        status: s.status,
        started_at: now,
        last_activity_at: now,
        last_turn_index: -1,
        last_checkpoint_turn: -1,
      });
      router.push(`/session/${s.session_id}`);
    } catch (e) {
      notice(errorText(e));
      setBusy(false);
    }
  };

  return { start, busy };
}
