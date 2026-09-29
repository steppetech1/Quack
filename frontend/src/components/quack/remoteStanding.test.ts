// The halves of the standing that /quack does not answer for, against the shapes
// backend/app/schemas/roadmap.py and matching.py actually send.

import { describe, expect, it } from "vitest";
import type { BackendMatching, BackendOverview } from "@/api/backend";
import { alertsOf, chancesOf, nextDateOf, readinessOf } from "./remoteStanding";

const TODAY = new Date("2026-09-20T09:00:00Z");

const milestone = (over: Partial<BackendOverview["milestones"][number]>) => ({
  key: "m1",
  kind: "registration" as const,
  date: "2026-09-25",
  title: "Регистрация на ЕНТ",
  exam_id: "ENT_MATH" as const,
  program_id: null,
  source: { kind: "demo" as const, url: null, checked_at: null },
  done: false,
  ...over,
});

const overview = (over: Partial<BackendOverview> = {}): BackendOverview =>
  ({
    requirements: [],
    milestones: [],
    conflicts: [],
    progress: [],
    last_set_summary: null,
    ...over,
  }) as BackendOverview;

describe("readinessOf", () => {
  it("shows the worst exam, not an average that flatters", () => {
    const view = overview({
      progress: [
        { exam_id: "SAT_MATH", readiness: 0.8, forecast: null, milestones_done: 0, milestones_total: 0 },
        { exam_id: "ENT_MATH", readiness: 0.2, forecast: null, milestones_done: 0, milestones_total: 0 },
      ],
    } as Partial<BackendOverview>);
    expect(readinessOf(view)).toBe(20);
  });

  it("is zero when nothing has been measured", () => {
    expect(readinessOf(overview())).toBe(0);
  });
});

describe("alertsOf", () => {
  it("raises a date a week out as a notice and two days out as urgent", () => {
    const soon = alertsOf(overview({ milestones: [milestone({ date: "2026-09-26" })] } as Partial<BackendOverview>), TODAY);
    expect(soon[0].level).toBe("notice");
    const urgent = alertsOf(overview({ milestones: [milestone({ date: "2026-09-21" })] } as Partial<BackendOverview>), TODAY);
    expect(urgent[0].level).toBe("urgent");
  });

  it("keeps the id in its bucket, so a day passing does not make the button glow again", () => {
    const a = alertsOf(overview({ milestones: [milestone({ date: "2026-09-26" })] } as Partial<BackendOverview>), TODAY);
    const b = alertsOf(overview({ milestones: [milestone({ date: "2026-09-25" })] } as Partial<BackendOverview>), TODAY);
    expect(a[0].id).toBe(b[0].id);
  });

  it("says nothing about a date already ticked", () => {
    expect(alertsOf(overview({ milestones: [milestone({ done: true })] } as Partial<BackendOverview>), TODAY)).toEqual([]);
  });

  it("stops calling a date missed once it is long past", () => {
    const recent = alertsOf(overview({ milestones: [milestone({ date: "2026-09-10" })] } as Partial<BackendOverview>), TODAY);
    expect(recent[0].kind).toBe("missed");
    const ancient = alertsOf(overview({ milestones: [milestone({ date: "2026-01-10" })] } as Partial<BackendOverview>), TODAY);
    expect(ancient).toEqual([]);
  });

  it("turns a conflict's sentences into the buttons the feed offers", () => {
    const view = overview({
      conflicts: [
        {
          kind: "same_day_applications",
          milestone_keys: ["a", "b"],
          text: "Два дедлайна в один день",
          options: ["Подать раньше", "Убрать одну программу"],
        },
      ],
    } as Partial<BackendOverview>);
    const [alert] = alertsOf(view, TODAY);
    expect(alert.kind).toBe("conflict");
    expect(alert.conflict?.options.map((o) => o.label)).toEqual(["Подать раньше", "Убрать одну программу"]);
  });
});

describe("nextDateOf", () => {
  it("picks the nearest date still ahead", () => {
    const view = overview({
      milestones: [
        milestone({ key: "far", date: "2026-12-01", title: "Подача" }),
        milestone({ key: "near", date: "2026-10-01", title: "Тест" }),
        milestone({ key: "past", date: "2026-08-01", title: "Прошло" }),
      ],
    } as Partial<BackendOverview>);
    expect(nextDateOf(view, TODAY)).toEqual({ title: "Тест", date: "2026-10-01", daysLeft: 11 });
  });

  it("has nothing to show when every date is behind or done", () => {
    expect(nextDateOf(overview({ milestones: [milestone({ date: "2026-01-01" })] } as Partial<BackendOverview>), TODAY)).toBeUndefined();
  });
});

describe("chancesOf", () => {
  const SAVED = ["p1"];

  const matching = (realism: "possible" | "try" | "impossible"): BackendMatching =>
    ({
      items: [
        {
          program: { id: "p1", university: "TU Delft" },
          realism,
          factors: [
            { id: "f1", kind: "hard", status: "below", text: "IELTS 6.0, нужно 6.5", source: null, weight: 1 },
            { id: "f2", kind: "hard", status: "unknown", text: "SAT неизвестен", source: null, weight: 1 },
            { id: "f3", kind: "soft", status: "in_range", text: "Город подходит", source: null, weight: 0.5 },
          ],
          assumptions: [],
          score: 0.72,
          fits_text: null,
          soft_pending: false,
        },
      ],
      total: 1,
      profile_readiness: 0.5,
      forecast_used: false,
      empty_reason: null,
    }) as unknown as BackendMatching;

  it("takes the level from the backend's realism", () => {
    expect(chancesOf(matching("possible"), SAVED)[0].level).toBe("realistic");
    expect(chancesOf(matching("try"), SAVED)[0].level).toBe("try");
    expect(chancesOf(matching("impossible"), SAVED)[0].level).toBe("unlikely");
  });

  it("keeps only the hard factors, and reads `unknown` as unknown rather than failed", () => {
    const [chance] = chancesOf(matching("try"), SAVED);
    expect(chance.facts.map((f) => f.label)).toEqual(["IELTS 6.0, нужно 6.5", "SAT неизвестен"]);
    expect(chance.facts.map((f) => f.ok)).toEqual([false, null]);
  });

  it("leaves out programs the student has not saved: this card is about their plan", () => {
    expect(chancesOf(matching("try"), [])).toEqual([]);
    expect(chancesOf(matching("try"), ["other"])).toEqual([]);
  });

  it("orders by the server's score", () => {
    expect(chancesOf(matching("try"), SAVED)[0].index).toBe(72);
  });
});
