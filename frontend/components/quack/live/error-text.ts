import { useCallback } from "react";
import { ApiError } from "@/lib/api/client";
import { useT } from "@/lib/i18n/provider";

/** Human message for a failed request */
export function useErrorText() {
  const t = useT().live;
  return useCallback((e: unknown) => {
    if (e instanceof ApiError) {
      if (e.code === "CONSENT_REQUIRED") return t.consentRequired;
      if (e.code === "LLM_DEGRADED" || e.code === "LLM_UNAVAILABLE" || e.status === 503) return t.llmDown;
      if (e.code === "RATE_LIMITED") return t.rateLimited;
      if (e.status < 500) return e.message;
    }
    return t.serverDown;
  }, [t]);
}
