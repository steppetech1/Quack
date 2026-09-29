// The selection chat against the backend agent (backend/app/agents/selection.py). The agent keeps
// the profile itself (tool update_profile), so what the panel shows is read back from /profile after
// each answer. Cards, comparison and saving arrive as tool results in the same stream.

import { api, ApiError } from "@/api/client";
import { backend, type BackendProfile } from "@/api/backend";
import { postSSE } from "@/api/stream";
import { PRIORITY_KEYS, type Profile, type PriorityKey } from "./assistant";

/* ---------- Profile: backend questionnaire -> the panel's slots ---------- */

const EUROPE = new Set(["DE", "NL", "PL", "FR"]);
const COUNTRY_NAME: Record<string, string> = { KZ: "Казахстан", US: "США", SG: "Сингапур", JP: "Япония", CA: "Канада" };
const GRANT: Record<string, string> = { only_grant: "только грант", preferred: "желательно", not_needed: "не нужен" };
const DEPTH: Record<string, string> = { short: "коротко", normal: "обычная", deep: "глубоко" };
const HINT: Record<string, string> = { minimal: "минимум", normal: "обычный", generous: "щедро" };

/** The generated schema marks every defaulted field optional, so each level is read defensively */
type Field<T> = { value?: T | null } | undefined;
const val = <T,>(f: Field<T>): T | undefined => f?.value ?? undefined;

/** What the panel shows for a backend profile. Slots the backend does not know stay empty. */
export function backendToProfile(p: BackendProfile): Profile {
  const q = p.questionnaire;
  const out: Profile = { soft: [...(p.traits?.verbatim ?? [])], priorities: [...PRIORITY_KEYS] };

  const grade = val(q?.level?.grade);
  if (grade) out.grade = `${grade} класс`;
  const direction = val(q?.direction?.field);
  if (direction) out.direction = direction;

  const countries = val(q?.preferences?.countries) ?? [];
  if (countries.length) {
    out.location = countries.every((c) => EUROPE.has(c)) ? "Европа" : countries.map((c) => COUNTRY_NAME[c] ?? c).join(", ");
  }
  const budget = val(q?.preferences?.budget_per_year);
  if (budget) out.budget = `до ${budget}${val(q?.preferences?.currency) === "USD" ? "$" : "€"} в год`;
  const grant = val(q?.preferences?.grant_need);
  if (grant) out.grant = GRANT[grant];

  // The panel thinks in the 1600 total; `academics.sat_score` is the math section, max 800. The
  // conversion is a fixed doubling, not a guess from the size of the number: both writers keep the
  // backend's scale — the agent normalises anything above 800 (agents/selection.py) and the panel
  // halves before PATCH — so a value that does not fit the section is stale data, not a total.
  const sat = val(q?.academics?.sat_score);
  if (sat) out.sat = String(sat * 2);
  const ielts = val(q?.academics?.ielts_score);
  if (ielts) out.ielts = String(ielts);
  const ent = val(q?.academics?.ent_trial_score);
  if (ent) out.ent = String(ent);

  const required = val(q?.constraints?.required) ?? [];
  if (required.length) out.requiredNote = required.join(", ");
  const excluded = val(q?.constraints?.excluded) ?? [];
  if (excluded.length) out.excludedNote = excluded.join(", ");

  const ranking = val(q?.priorities?.ranking) as PriorityKey[] | undefined;
  if (ranking?.length === PRIORITY_KEYS.length) out.priorities = ranking;

  const hours = val(q?.pace?.hours_per_week);
  if (hours) out.paceHours = `${hours} ч/нед`;
  const depth = val(q?.pace?.explanation_depth);
  if (depth) out.paceDepth = DEPTH[depth] ?? depth;
  const hint = val(q?.pace?.hint_level);
  if (hint) out.paceHint = HINT[hint] ?? hint;

  return out;
}

export const loadProfile = async (): Promise<Profile> => backendToProfile(await backend.profile.get());

/* ---------- History ---------- */

export type HistoryItem = { role: "user" | "assistant"; text: string; at: number };

export async function loadHistory(): Promise<HistoryItem[]> {
  const list = await api.get<{ role: "user" | "assistant"; text: string; created_at: string }[]>("/chat/selection/messages?limit=200");
  return list.map((m) => ({ role: m.role, text: m.text, at: Date.parse(m.created_at) }));
}

/* ---------- One turn ---------- */

export type TurnHandlers = {
  /** A piece of the answer text */
  onText: (delta: string) => void;
  /** run_matching returned these programs, best first */
  onPrograms: (ids: string[]) => void;
  /** compare was asked for these programs */
  onCompare: (ids: string[]) => void;
  /** The profile, the saved list or both changed on the server during the turn */
  onChanged: (what: { profile?: boolean; saved?: boolean }) => void;
};

type MatchingData = { items?: { program_id: string }[] };
type CompareData = { program_ids?: string[] };

/**
 * Sends one message and feeds the stream to the handlers. Resolves when the answer is complete;
 * rejects with ApiError before the stream starts (503 llm_unavailable, 409 still answering) and
 * with an Error carrying the server's message when the stream itself reports one.
 *
 * Returns whether the answer actually finished. The backend closes every turn with a `done` frame
 * (backend/app/api/chat.py), so a stream that ends without one was cut — a dropped connection ends
 * the body without an error, and the half-written answer would otherwise pass for a whole one.
 */
export async function sendSelectionMessage(
  text: string,
  h: TurnHandlers,
  signal?: AbortSignal
): Promise<{ complete: boolean }> {
  const failure: { code?: string; message?: string } = {};
  let done = false;
  await postSSE(
    "/chat/selection/messages",
    { text },
    (event) => {
      if (event.type === "text_delta") h.onText(event.text);
      else if (event.type === "done") done = true;
      else if (event.type === "error") {
        failure.code = event.code;
        failure.message = event.message;
      } else if (event.type === "tool_result" && !event.error) {
        if (event.tool === "run_matching") {
          const ids = ((event.data as MatchingData | null)?.items ?? []).map((i) => i.program_id);
          if (ids.length) h.onPrograms(ids);
        } else if (event.tool === "compare") {
          const ids = (event.data as CompareData | null)?.program_ids ?? [];
          if (ids.length >= 2) h.onCompare(ids);
        } else if (event.tool === "update_profile") h.onChanged({ profile: true });
        else if (event.tool === "save_program") h.onChanged({ saved: true });
      }
    },
    signal
  );
  if (failure.message) throw new ApiError(500, failure.code ?? "internal", failure.message);
  return { complete: done };
}
