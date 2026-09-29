// The payloads here follow backend/app/schemas/quack.py: what `GET /quack` answers, including the
// parts the screens have to survive without — no forecast yet, an unavailable variant, an activity
// window that has not been computed.

import { describe, expect, it } from "vitest";
import type { BackendExamPace, BackendQuack, BackendRecommendation } from "@/api/backend";
import live from "./live-quack.fixture.json";
import { PACE_UNKNOWN, paceLevel, toActivity, toExamPace, toQuackView, toSignal, variantAction } from "./remoteAdapter";

const forecast = (readyBy: string | null, testDate: string | null) => ({
  exam_id: "SAT_MATH" as const,
  predicted_raw: 610,
  predicted_scaled: 610,
  coverage: 0.8,
  hours_needed: 40,
  ready_by: readyBy,
  test_date: testDate,
  on_track: readyBy !== null && testDate !== null && readyBy <= testDate,
  as_of_event_id: 12,
  note: "",
});

const examPace = (over: Partial<BackendExamPace> = {}): BackendExamPace => ({
  exam_id: "SAT_MATH",
  forecast: forecast("2026-10-20", "2026-11-07"),
  on_track: true,
  test_date: "2026-11-07",
  hours_declared: 8,
  hours_actual: 6.5,
  variants: [],
  words: "Готовность к 20 октября, тест 7 ноября — запас 18 дн.",
  ...over,
});

const rec = (over: Partial<BackendRecommendation> = {}): BackendRecommendation => ({
  id: "11111111-1111-1111-1111-111111111111",
  kind: "pace_variant",
  urgency: "high",
  position: 0,
  status: "pending",
  title: "Не успеваешь к 7 ноября",
  reason: "Прогноз готовности — 21 ноября",
  action_text: "Перенести тест на 5 декабря",
  action: { kind: "requirement_update", exam_id: "SAT_MATH", test_date: "2026-12-05" },
  reason_hash: "abc",
  created_at: "2026-09-20T09:00:00Z",
  ...over,
});

describe("paceLevel", () => {
  it("counts the margin between readiness and the test", () => {
    expect(paceLevel(examPace({ forecast: forecast("2026-11-20", "2026-11-07") }))).toBe(0);
    expect(paceLevel(examPace({ forecast: forecast("2026-11-05", "2026-11-07") }))).toBe(1);
    expect(paceLevel(examPace({ forecast: forecast("2026-10-25", "2026-11-07") }))).toBe(2);
    expect(paceLevel(examPace({ forecast: forecast("2026-09-01", "2026-11-07") }))).toBe(3);
  });

  it("falls back on on_track when there is no forecast to compare", () => {
    expect(paceLevel(examPace({ forecast: null, on_track: true }))).toBe(2);
    expect(paceLevel(examPace({ forecast: null, on_track: null }))).toBe(0);
  });

  it("uses the forecast's own test date when the exam has none", () => {
    expect(paceLevel(examPace({ test_date: null, forecast: forecast("2026-11-20", "2026-11-07") }))).toBe(0);
  });
});

describe("toExamPace", () => {
  it("renames the exam the way every screen writes it", () => {
    expect(toExamPace(examPace()).id).toBe("sat");
    expect(toExamPace(examPace()).name).toBe("SAT Math");
    expect(toExamPace(examPace({ exam_id: "ENT_MATH" })).id).toBe("ent");
  });

  it("puts the offers the student can take first, and says why the others are not on offer", () => {
    const pace = toExamPace(
      examPace({
        variants: [
          {
            kind: "remove_program",
            text: "убирать нечего — это единственная программа с порогом",
            params: {},
            forecast: forecast("2026-10-20", "2026-11-07"),
            available: false,
            unavailable_reason: "only_program",
            affected_program_ids: [],
            recommendation_id: null,
          },
          {
            kind: "move_date",
            text: "Перенести на 5 декабря",
            params: { test_date: "2026-12-05" },
            forecast: forecast("2026-10-20", "2026-12-05"),
            available: true,
            unavailable_reason: null,
            affected_program_ids: [],
            recommendation_id: null,
          },
        ],
      }),
    );
    expect(pace.advice).toEqual([
      "Перенести на 5 декабря",
      // The reason is a code; the words are already in the text
      "убирать нечего — это единственная программа с порогом",
    ]);
    expect(pace.adviceActions).toEqual([
      { kind: "pick-date", exam: "sat", key: "2026-12-05", label: "Выбрать эту дату" },
      null,
    ]);
  });

  it("keeps advice and its actions the same length", () => {
    const pace = toExamPace(examPace({ variants: [] }));
    expect(pace.advice).toHaveLength(pace.adviceActions!.length);
  });
});

describe("variantAction", () => {
  const variant = (over: Partial<Parameters<typeof variantAction>[0]>) =>
    ({
      kind: "move_date",
      text: "t",
      params: {},
      forecast: forecast("2026-10-20", "2026-11-07"),
      available: true,
      unavailable_reason: null,
      affected_program_ids: [],
      recommendation_id: null,
      ...over,
    }) as Parameters<typeof variantAction>[0];

  it("gives nothing to press for a variant that is not on offer", () => {
    expect(variantAction(variant({ available: false, params: { test_date: "2026-12-05" } }), "SAT_MATH")).toBeNull();
  });

  it("gives nothing to press for a move without a date", () => {
    expect(variantAction(variant({ params: {} }), "SAT_MATH")).toBeNull();
  });

  it("trims a datetime down to the date key the calendar uses", () => {
    expect(variantAction(variant({ params: { test_date: "2026-12-05T00:00:00Z" } }), "ENT_MATH")).toMatchObject({
      kind: "pick-date",
      exam: "ent",
      key: "2026-12-05",
    });
  });
});

describe("toSignal", () => {
  it("carries the recommendation so the feed can accept or decline it", () => {
    expect(toSignal(rec()).recommendation).toEqual({
      id: "11111111-1111-1111-1111-111111111111",
      actionText: "Перенести тест на 5 декабря",
      status: "pending",
    });
  });

  it("reads urgency as the glow level", () => {
    expect(toSignal(rec({ urgency: "urgent" })).level).toBe("urgent");
    expect(toSignal(rec({ urgency: "high" })).level).toBe("urgent");
    expect(toSignal(rec({ urgency: "normal" })).level).toBe("notice");
    expect(toSignal(rec({ urgency: "low" })).level).toBe("notice");
  });

  it("sends the student where the action would land", () => {
    expect(toSignal(rec({ action: { kind: "set_open", set_id: "s1" } })).target).toBe("prep");
    expect(toSignal(rec({ action: { kind: "program_remove", program_id: "p1" } })).target).toBe("programs");
    expect(toSignal(rec({ action: { kind: "milestone_open", milestone_key: "m" } })).target).toBe("calendar");
    expect(toSignal(rec({ action: { kind: "acknowledge" } })).target).toBeUndefined();
  });

  it("dates the signal from when it was shown, falling back to when it was made", () => {
    expect(toSignal(rec()).at).toBe("2026-09-20T09:00:00Z");
    expect(toSignal(rec({ shown_at: "2026-09-20T10:00:00Z" })).at).toBe("2026-09-20T10:00:00Z");
  });
});

describe("toActivity", () => {
  it("says nothing was computed rather than inventing a zero", () => {
    const view = toActivity({
      days: [],
      window_days: 14,
      active_days: 0,
      hours_per_week_actual: null,
      hours_per_week_declared: null,
      computed_at: null,
      tz: "Asia/Almaty",
    });
    expect(view.computedAt).toBeNull();
    expect(view.hoursActual).toBeNull();
  });
});

describe("toQuackView", () => {
  const quack = (over: Partial<BackendQuack> = {}): BackendQuack => ({
    pace: { exams: [examPace()], as_of: "2026-09-20T09:00:00Z" },
    items: [],
    activity: {
      days: [],
      window_days: 14,
      active_days: 3,
      hours_per_week_actual: 6.5,
      hours_per_week_declared: 8,
      computed_at: "2026-09-20T08:00:00Z",
      tz: "Asia/Almaty",
    },
    new_batch: false,
    batch_at: null,
    n_new: 0,
    ...over,
  });

  it("only makes the button glow for items the student has not been shown", () => {
    const view = toQuackView(
      quack({
        items: [
          rec({ id: "a", status: "pending" }),
          rec({ id: "b", status: "shown" }),
        ],
      }),
    );
    expect(view.open.map((s) => s.id)).toEqual(["a", "b"]);
    expect(view.fresh.map((s) => s.id)).toEqual(["a"]);
  });

  it("keeps the server's order instead of re-sorting the feed", () => {
    const view = toQuackView(
      quack({
        items: [
          rec({ id: "low", urgency: "low", position: 0 }),
          rec({ id: "urgent", urgency: "urgent", position: 1 }),
        ],
      }),
    );
    expect(view.open.map((s) => s.id)).toEqual(["low", "urgent"]);
  });

  it("lets the worst exam speak for the pace card", () => {
    const view = toQuackView(
      quack({
        pace: {
          as_of: "2026-09-20T09:00:00Z",
          exams: [
            examPace(),
            examPace({ exam_id: "ENT_MATH", forecast: forecast("2027-02-01", "2027-01-20"), test_date: "2027-01-20" }),
          ],
        },
      }),
    );
    expect(view.pace?.id).toBe("ent");
    expect(view.pace?.level).toBe(0);
  });

  it("survives a backend that sends no feed and no exams at all", () => {
    const view = toQuackView(quack({ pace: { exams: [], as_of: "2026-09-20T09:00:00Z" } }));
    expect(view.pace).toBeNull();
    expect(view.exams).toEqual([]);
    expect(view.fresh).toEqual([]);
  });
});

// A payload recorded off the local stack (see live-quack.fixture.json). Hand-written fixtures agree
// with what the code expects; this one only agrees with what the backend actually sent.
describe("a recorded GET /quack", () => {
  const view = toQuackView(live as unknown as BackendQuack);

  it("says the pace cannot be computed instead of claiming the student is behind", () => {
    // forecast.ready_by and on_track are both null: nothing is known, and "Не успеваешь" would be a claim
    expect(view.pace?.verdict).toBe(PACE_UNKNOWN);
    expect(view.pace?.summary).toBe("ЕНТ математика: готовность не посчитать — часы не указаны, тест 20 мая");
  });

  it("offers the two variants the student can act on, and explains the two it cannot", () => {
    expect(view.pace?.advice).toEqual([
      "1 ч/нед → готов 20 сентября",
      "перенести тест на 20 мая (регистрация до 20 апреля)",
      "убрать E.A. Buketov Karaganda University (порог 18 остаётся максимальным) → готов к сроку — всё равно позже теста",
      "снижение цели до 12 не помогает — ниже опускать не станем",
    ]);
    expect(view.pace?.adviceActions).toEqual([
      { kind: "open-prep", label: "Открыть подготовку" },
      { kind: "pick-date", exam: "ent", key: "2027-05-20", label: "Выбрать эту дату" },
      null,
      null,
    ]);
  });

  it("reports activity as not computed rather than as a week of zero hours", () => {
    expect(view.activity.computedAt).toBeNull();
    expect(view.activity.hoursActual).toBeNull();
    expect(view.activity.days).toHaveLength(2);
    expect(view.activity.tz).toBe("Asia/Almaty");
  });

  it("has an empty feed and nothing to glow about", () => {
    expect(view.open).toEqual([]);
    expect(view.fresh).toEqual([]);
  });
});
