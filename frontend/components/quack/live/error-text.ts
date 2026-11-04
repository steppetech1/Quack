import { ApiError } from "@/lib/api/client";
import { useT } from "@/lib/i18n/provider";

/** Human message for a failed request */
export function useErrorText() {
  const t = useT().live;
  return (e: unknown) => {
    if (e instanceof ApiError) {
      if (e.code === "CONSENT_REQUIRED") return t.consentRequired;
      if (e.code === "LLM_DEGRADED" || e.code === "LLM_UNAVAILABLE") return t.llmDown;
      if (e.code === "RATE_LIMITED") return t.rateLimited;
      // the server answered but the turn broke (an `error` frame `SERVICE_UNAVAILABLE`, a 5xx): it is alive, so not "can't reach"
      if (e.code === "SERVICE_UNAVAILABLE") return t.turnFailed;
      if (e.status === 503) return t.llmDown;
      if (e.status < 500) return e.message;
      return t.serverError;
    }
    // no answer at all: fetch rejected (network, server down) or the stream broke
    return t.serverDown;
  };
}
