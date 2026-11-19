import { useCallback } from "react";
import { ApiError } from "@/lib/api/client";
import { useT } from "@/lib/i18n/provider";

/** Human message for a failed request */
export function useErrorText() {
  const tx = useT();
  const t = tx.live;
  const ga = tx.apiGaps;
  return useCallback((e: unknown) => {
    if (e instanceof ApiError) {
      if (e.code === "CONSENT_REQUIRED") return t.consentRequired;
      if (e.code === "LLM_DEGRADED" || e.code === "LLM_UNAVAILABLE") return t.llmDown;
      if (e.code === "RATE_LIMITED") return t.rateLimited;
      // the server answered but the turn broke: the `error` frame of a turn arrives as `SERVICE_UNAVAILABLE` with status 500
      // (the client makes it so). The same code on a real HTTP 503 is a dependency that is down, not a failed answer.
      if (e.code === "SERVICE_UNAVAILABLE" && e.status === 500) return t.turnFailed;
      if (e.status === 503) return /llm/i.test(e.code ?? "") ? t.llmDown : ga.serviceDown;
      if (e.status < 500) return e.message;
      return t.serverError;
    }
    // no answer at all: fetch rejected (network, server down) or the stream broke
    return t.serverDown;
  }, [t, ga]);
}
