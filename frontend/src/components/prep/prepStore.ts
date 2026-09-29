// Where the state of «Подготовки» is kept, and — at `remote` — what is no longer kept at all.
//
// Until the backend owned the section, the whole model lived in the student's store under one key:
// skill states, recall, evidence, misconceptions, which sets are passed, which one is in work, which
// report is open. With the backend behind us that is a second copy of the domain, and a copy is the
// one thing a source of truth cannot have: it goes stale, it survives a «начать заново», and it keeps
// ids («s2», «e1») the server has never heard of.
//
// So at `remote` this module writes two narrow keys instead of the model:
//
//   quack-prep-ui         — what the student chose on the screen and no endpoint speaks for
//   quack-prep-materials  — notes and flashcards generated in the browser (ТЗ §5.7)
//
// Everything else is read back from `GET /sets`, `GET /knowledge` and `GET /overview` on every load
// and whenever `GET /prep/knowledge/version` moves. At `local` nothing changes: the demo mode keeps
// the whole model under the old key, because there it *is* the source of truth.

import { store } from "../account/store";
import { initialModel, reviveModel, type Material, type PrepModel } from "./prepModel";
import { REMOTE_PREP } from "./remoteFlag";

/** Demo mode: the whole model, as before */
export const PREP_KEY = "quack-prep";
/** Remote: the student's own choices on the screen */
export const PREP_UI_KEY = "quack-prep-ui";
/** Remote: what the assistant generated for a topic at the student's request */
export const PREP_MATERIALS_KEY = "quack-prep-materials";

/**
 * The slice that stays in the browser at `remote`, field by field, with the reason it is allowed —
 * the same rule the `/state` whitelist follows (account/store.ts).
 *
 * `testDates`, `targets`, `resolvedConflicts` — the student's choices; the backend has no endpoint
 *   for them yet (see docs/sync-log.md), so losing them on reload would lose the student's input.
 * `milestonesDone` — a cache of the server's marks: `GET /overview` brings `done` on every load and
 *   `POST /overview/milestones/{key}` writes it, this only keeps the ticks visible in between.
 * `diagnosticDone`, `diagnosticSkipped` — whether the entrance test was passed or put off; the
 *   backend reports an *active* run only, so there is nothing to read this back from yet.
 * `demo` — a screen switch: show the section on demo programs while nothing is saved.
 */
export type PrepUi = Pick<
  PrepModel,
  | "milestonesDone"
  | "testDates"
  | "targets"
  | "resolvedConflicts"
  | "demo"
  | "diagnosticDone"
  | "diagnosticSkipped"
>;

const uiOf = (model: PrepModel): PrepUi => ({
  milestonesDone: model.milestonesDone,
  testDates: model.testDates,
  targets: model.targets,
  resolvedConflicts: model.resolvedConflicts,
  demo: model.demo,
  diagnosticDone: model.diagnosticDone,
  diagnosticSkipped: model.diagnosticSkipped,
});

/** Materials come back from JSON with string dates */
function reviveMaterials(raw: unknown): Record<string, Material[]> {
  if (!raw || typeof raw !== "object") return {};
  return Object.fromEntries(
    Object.entries(raw as Record<string, Material[]>).map(([id, list]) => [
      id,
      (Array.isArray(list) ? list : []).map((m) => ({ ...m, createdAt: new Date(m.createdAt) })),
    ])
  );
}

/**
 * An account that used the section before this split still has the fat key. Its UI slice is worth
 * keeping — the ticks and the chosen dates are the student's own — the domain half is not, so it is
 * read once, split, and the old key dropped. Without this the stale `currentSet: "s2"` would be read
 * for as long as the account lives.
 */
function migrateLegacy(): { ui: Partial<PrepUi>; materials: Record<string, Material[]> } | null {
  const legacy = store.get<Partial<PrepModel>>(PREP_KEY);
  if (!legacy || typeof legacy !== "object") return null;
  const ui: Partial<PrepUi> = {
    milestonesDone: legacy.milestonesDone,
    testDates: legacy.testDates,
    targets: legacy.targets,
    resolvedConflicts: legacy.resolvedConflicts,
    demo: legacy.demo,
    diagnosticDone: legacy.diagnosticDone,
    diagnosticSkipped: legacy.diagnosticSkipped,
  };
  const materials = reviveMaterials(legacy.materials);
  store.set(PREP_UI_KEY, ui);
  store.set(PREP_MATERIALS_KEY, materials);
  store.set(PREP_KEY, null); // `set(null)` is how the store drops a key
  return { ui, materials };
}

/** The model the screens start from: the stored one at `local`, an empty one plus the UI slice at `remote`. */
export function loadPrepModel(): PrepModel {
  if (!REMOTE_PREP) return reviveModel(store.get(PREP_KEY)) ?? initialModel();

  const migrated = migrateLegacy();
  const ui = migrated?.ui ?? store.get<Partial<PrepUi>>(PREP_UI_KEY) ?? {};
  const materials = migrated?.materials ?? reviveMaterials(store.get(PREP_MATERIALS_KEY));
  const fresh = initialModel();
  return {
    ...fresh,
    ...ui,
    // Never from storage: the server answers for all of it, and a stale copy outlives the truth
    milestonesDone: ui.milestonesDone ?? fresh.milestonesDone,
    resolvedConflicts: ui.resolvedConflicts ?? fresh.resolvedConflicts,
    materials,
  };
}

export function savePrepModel(model: PrepModel): void {
  if (!REMOTE_PREP) {
    store.set(PREP_KEY, model);
    return;
  }
  store.set(PREP_UI_KEY, uiOf(model));
  store.set(PREP_MATERIALS_KEY, model.materials);
}

/**
 * The stored slice for screens outside «Подготовки» (the dashboard, the choice chat, the local Quack):
 * they read ticks, dates and targets without reviving the whole model.
 */
export function readPrepUi(): Partial<PrepUi> {
  if (!REMOTE_PREP) return store.get<Partial<PrepModel>>(PREP_KEY) ?? {};
  return store.get<Partial<PrepUi>>(PREP_UI_KEY) ?? {};
}

/** The raw value behind `readPrepUi`, for readers that only re-render when it changes by identity. */
export function rawPrepUi(): unknown {
  return store.get(REMOTE_PREP ? PREP_UI_KEY : PREP_KEY);
}

/** Read, change, write — the one path for screens that change preparation from outside the section. */
export function updatePrepModel(change: (model: PrepModel) => PrepModel): PrepModel {
  const next = change(loadPrepModel());
  savePrepModel(next);
  return next;
}
