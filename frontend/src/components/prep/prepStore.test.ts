// What «Подготовка» is allowed to keep in the browser, and what the server's plan does to the model.
//
// The flag is read when the module loads, so every case imports its modules after stubbing the env —
// that is what `load(mode)` below is for.

import { beforeEach, describe, expect, it, vi } from "vitest";

/** A stand-in for the student's store: a plain map, so nothing here touches `/state` or localStorage */
const cache = new Map<string, unknown>();

vi.mock("../account/store", () => ({
  store: {
    get: <T>(key: string): T | null => (cache.has(key) ? (cache.get(key) as T) : null),
    set: (key: string, value: unknown) => {
      if (value === null || value === undefined) cache.delete(key);
      else cache.set(key, value);
    },
  },
}));

async function load(mode: "local" | "remote") {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_DATA_SOURCE", mode);
  vi.stubEnv("NEXT_PUBLIC_SRC_PREP", "");
  return {
    store: await import("./prepStore"),
    model: await import("./prepModel"),
    data: await import("./prepData"),
    sets: await import("./remoteSets"),
  };
}

beforeEach(() => {
  cache.clear();
  vi.unstubAllEnvs();
});

describe("what is stored at remote", () => {
  it("keeps the student's own choices and the generated materials, and nothing else", async () => {
    const { store, model } = await load("remote");
    const m = {
      ...model.initialModel(),
      currentSet: "11111111-1111-4111-8111-111111111111",
      doneSets: ["22222222-2222-4222-8222-222222222222"],
      states: { linear: "solid" as const },
      milestonesDone: ["sat-reg:2026-11-07"],
      testDates: { sat: "2026-11-07" },
      materials: { linear: [{ id: "m1", kind: "notes" as const, title: "Конспект", createdAt: new Date(), markdown: "#" }] },
    };
    store.savePrepModel(m);

    expect([...cache.keys()].sort()).toEqual([store.PREP_MATERIALS_KEY, store.PREP_UI_KEY]);
    const ui = cache.get(store.PREP_UI_KEY) as Record<string, unknown>;
    expect(ui.milestonesDone).toEqual(["sat-reg:2026-11-07"]);
    expect(ui.testDates).toEqual({ sat: "2026-11-07" });
    // The domain is the server's: not a set id, not a skill state, not a piece of evidence
    expect(ui).not.toHaveProperty("currentSet");
    expect(ui).not.toHaveProperty("doneSets");
    expect(ui).not.toHaveProperty("states");
    expect(ui).not.toHaveProperty("evidence");
  });

  it("starts empty: a reload asks the server rather than redrawing the last plan", async () => {
    const { store, model } = await load("remote");
    store.savePrepModel({
      ...model.initialModel(),
      currentSet: "11111111-1111-4111-8111-111111111111",
      doneSets: ["22222222-2222-4222-8222-222222222222"],
      diagnosticDone: true,
    });

    const back = store.loadPrepModel();
    expect(back.currentSet).toBeNull();
    expect(back.doneSets).toEqual([]);
    // What no endpoint answers for survives, or the student would take the entrance test twice
    expect(back.diagnosticDone).toBe(true);
  });

  it("splits a model saved before the split and drops the old key", async () => {
    const { store } = await load("remote");
    cache.set(store.PREP_KEY, {
      currentSet: "s2",
      doneSets: ["s1", "e1"],
      reportFor: "s1",
      states: { linear: "solid" },
      milestonesDone: ["sat-test:2026-11-07"],
      materials: { linear: [{ id: "m1", kind: "notes", title: "Конспект", createdAt: "2026-09-01T00:00:00.000Z", markdown: "#" }] },
    });

    const migrated = store.loadPrepModel();
    expect(cache.has(store.PREP_KEY)).toBe(false);
    expect(migrated.currentSet).toBeNull();
    expect(migrated.doneSets).toEqual([]);
    expect(migrated.milestonesDone).toEqual(["sat-test:2026-11-07"]);
    expect(migrated.materials.linear[0].createdAt).toBeInstanceOf(Date);
  });
});

describe("the demo mode is untouched", () => {
  it("keeps the whole model under one key and reads it back", async () => {
    const { store, model } = await load("local");
    const m = { ...model.initialModel(), currentSet: "s2", doneSets: ["s1"] };
    store.savePrepModel(m);

    expect([...cache.keys()]).toEqual([store.PREP_KEY]);
    expect(store.loadPrepModel().currentSet).toBe("s2");
    expect(store.loadPrepModel().doneSets).toEqual(["s1"]);
  });

  it("still starts on the demo route", async () => {
    const { model } = await load("local");
    expect(model.initialModel().currentSet).toBe("s2");
  });
});

describe("the demo plan does not mix with the server's", () => {
  it("leaves no demo sets or skills to rank at remote", async () => {
    const { data } = await load("remote");
    expect(data.allSets()).toEqual([]);
    expect(data.allSkills()).toEqual([]);
    expect(data.knownSet("s2")).toBeNull();
  });

  it("still has them without a backend", async () => {
    const { data } = await load("local");
    expect(data.allSets().length).toBeGreaterThan(0);
    expect(data.knownSet("s2")?.id).toBe("s2");
  });
});

describe("the server's plan against the model on screen", () => {
  const set = (id: string, exam: "sat" | "ent" = "sat") => ({
    id,
    exam,
    number: 1,
    title: id,
    area: "Алгебра",
    skills: [],
    start: new Date(),
    deadline: new Date(),
    status: "upcoming" as const,
    why: "",
  });

  it("takes the current set and the passed ones from the plan", async () => {
    const { sets } = await load("remote");
    const next = sets.applyRemoteSetsToModel(
      { currentSet: "old", doneSets: ["old", "gone"] },
      { current: set("now"), upcoming: [set("later")], done: [set("old")] }
    );
    expect(next.currentSet).toBe("now");
    expect(next.doneSets).toEqual(["gone", "old"]);
  });

  it("does not forget the other exam while reading one", async () => {
    const { sets } = await load("remote");
    const next = sets.applyRemoteSetsToModel(
      { currentSet: "ent-set", doneSets: ["ent-done"] },
      { current: set("sat-now"), upcoming: [], done: [] }
    );
    expect(next.currentSet).toBe("ent-set");
    expect(next.doneSets).toEqual(["ent-done"]);
  });

  it("clears a set the plan no longer has", async () => {
    const { sets } = await load("remote");
    const next = sets.applyRemoteSetsToModel(
      { currentSet: "now", doneSets: [] },
      { current: null, upcoming: [set("now")], done: [] }
    );
    expect(next.currentSet).toBeNull();
  });
});

describe("the entrance test, once the server has measured the student", () => {
  const skill = (level: string) =>
    ({
      skill_id: "ent.alg.inequalities",
      name: "Неравенства",
      area_id: "area.ent.algebra",
      weight: 1,
      level,
      p_recall: 0.5,
      is_root: false,
    }) as never;
  const knowledge = (level: string) =>
    ({ skills: [skill(level)], misconceptions: [], roots: [] }) as never;

  it("is done when any skill has left low_data, wherever the test was taken", async () => {
    vi.resetModules();
    vi.stubEnv("NEXT_PUBLIC_DATA_SOURCE", "remote");
    const { applyRemoteKnowledgeToModel } = await import("./remoteKnowledge");
    const { initialModel } = await import("./prepModel");
    const measured = applyRemoteKnowledgeToModel(initialModel(), knowledge("shaky"), "ent");
    expect(measured.diagnosticDone).toBe(true);
    const unmeasured = applyRemoteKnowledgeToModel(initialModel(), knowledge("low_data"), "ent");
    expect(unmeasured.diagnosticDone).toBe(false);
  });
});
