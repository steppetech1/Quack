// Phase I5 Prep Adapter: bridges backend /overview and prep schemas with frontend UI models.

import {
  backend,
  type BackendConflict,
  type BackendExamProgress,
  type BackendExamRequirement,
  type BackendMilestone,
  type BackendOverview,
} from "@/api/backend";
import {
  EXAMS,
  type ExamId,
  type ExamOutlook,
  type ExamRequirement,
  type Milestone,
} from "./prepData";
import { programById, type Program } from "../choice/programs";

import { REMOTE_PREP } from "./remoteFlag";

export { REMOTE_PREP };

export function parseIsoDate(iso: string): Date {
  if (!iso) return new Date();
  const parts = iso.split("-");
  if (parts.length === 3) {
    const year = parseInt(parts[0], 10);
    const month = parseInt(parts[1], 10) - 1;
    const day = parseInt(parts[2], 10);
    if (!Number.isNaN(year) && !Number.isNaN(month) && !Number.isNaN(day)) {
      return new Date(year, month, day);
    }
  }
  return new Date(iso);
}

export function toFrontendExamId(backendId: string | null | undefined): ExamId | null {
  if (!backendId) return null;
  const upper = backendId.toUpperCase();
  if (upper.includes("SAT")) return "sat";
  if (upper.includes("ENT")) return "ent";
  return null;
}

export function toBackendExamId(frontendId: ExamId): "SAT_MATH" | "ENT_MATH" {
  return frontendId === "sat" ? "SAT_MATH" : "ENT_MATH";
}

export function adaptOutlook(progressList: BackendExamProgress[]): ExamOutlook {
  const result: ExamOutlook = {
    sat: { readiness: 0, forecast: EXAMS.sat.test },
    ent: { readiness: 0, forecast: EXAMS.ent.test },
  };

  for (const item of progressList) {
    const id = toFrontendExamId(item.exam_id);
    if (!id) continue;
    const readiness = Math.round(item.readiness * 100) / 100;
    const forecast = item.forecast?.ready_by
      ? parseIsoDate(item.forecast.ready_by)
      : item.forecast?.test_date
      ? parseIsoDate(item.forecast.test_date)
      : EXAMS[id].test;
    result[id] = { readiness, forecast };
  }

  return result;
}

export function adaptRequirements(
  reqs: BackendExamRequirement[],
  progressList: BackendExamProgress[],
  programs: Program[]
): ExamRequirement[] {
  const outlook = adaptOutlook(progressList);
  const progMap = new Map<string, Program>(programs.map((p) => [p.id, p]));

  return reqs.map((req) => {
    const id = toFrontendExamId(req.exam_id) ?? "sat";
    const name =
      req.exam_id === "SAT_MATH"
        ? "SAT Math"
        : req.exam_id === "ENT_MATH"
        ? "ЕНТ · математика"
        : req.exam_id;
    const target = String(Math.round(req.target_score));
    const targetNote =
      req.estimate_note && req.estimate_note !== "нет данных"
        ? req.estimate_note
        : id === "sat"
        ? `из 800 · цель по сохранённым`
        : `из 50 · профильная математика, цель на грант`;

    const testCandidates = req.test_dates.map((td) => parseIsoDate(td.date));
    const testDate = testCandidates[0] ?? (EXAMS[id] ? EXAMS[id].test : undefined);
    const matchedPrograms = req.program_ids
      .map((pid) => progMap.get(pid) ?? programById(pid))
      .filter((p): p is Program => Boolean(p));

    return {
      id,
      name,
      target,
      targetNote,
      testDate,
      testCandidates: testCandidates.length ? testCandidates : (EXAMS[id] ? [EXAMS[id].test] : []),
      programs: matchedPrograms,
      hasModel: req.has_knowledge_model,
      readiness: outlook[id]?.readiness,
      forecast: outlook[id]?.forecast,
    };
  });
}

export function adaptMilestones(
  milestones: BackendMilestone[],
  programs: Program[]
): Milestone[] {
  const progMap = new Map<string, Program>(programs.map((p) => [p.id, p]));

  return milestones.map((m) => {
    const prog = m.program_id
      ? (progMap.get(m.program_id) ?? programById(m.program_id))
      : undefined;
    const detail = prog
      ? `${prog.university} · ${prog.program}`
      : m.source?.label ?? "";
    const source = m.source?.is_demo ? "демо" : (m.source?.label ?? "план");

    return {
      id: m.key,
      key: m.key,
      date: parseIsoDate(m.date),
      title: m.title,
      detail,
      source,
      checkable: true,
      done: m.done,
      examId: toFrontendExamId(m.exam_id) ?? undefined,
      programId: m.program_id ?? undefined,
      kind: m.kind,
    };
  });
}

export type RemotePrepOverview = {
  requirements: ExamRequirement[];
  milestones: Milestone[];
  conflicts: BackendConflict[];
  outlook: ExamOutlook;
  doneMilestoneKeys: string[];
};

const CACHE_STORAGE_KEY = "quack:prep:overview-cache";
let memoryCachedOverview: RemotePrepOverview | null = null;
let inflightOverviewPromise: Promise<RemotePrepOverview> | null = null;

function serializeOverview(data: RemotePrepOverview): string {
  return JSON.stringify({
    requirements: data.requirements.map((r) => ({
      ...r,
      testDate: r.testDate ? r.testDate.toISOString() : undefined,
      testCandidates: r.testCandidates.map((d) => d.toISOString()),
      forecast: r.forecast ? r.forecast.toISOString() : undefined,
    })),
    milestones: data.milestones.map((m) => ({
      ...m,
      date: m.date.toISOString(),
    })),
    conflicts: data.conflicts,
    outlook: {
      sat: {
        readiness: data.outlook.sat.readiness,
        forecast: data.outlook.sat.forecast.toISOString(),
      },
      ent: {
        readiness: data.outlook.ent.readiness,
        forecast: data.outlook.ent.forecast.toISOString(),
      },
    },
    doneMilestoneKeys: data.doneMilestoneKeys,
  });
}

function deserializeOverview(rawJson: string): RemotePrepOverview | null {
  try {
    const parsed = JSON.parse(rawJson);
    if (!parsed || !Array.isArray(parsed.requirements) || !Array.isArray(parsed.milestones)) {
      return null;
    }
    return {
      requirements: parsed.requirements.map((r: any) => ({
        ...r,
        testDate: r.testDate ? new Date(r.testDate) : undefined,
        testCandidates: (r.testCandidates ?? []).map((d: string) => new Date(d)),
        forecast: r.forecast ? new Date(r.forecast) : undefined,
      })),
      milestones: parsed.milestones.map((m: any) => ({
        ...m,
        date: new Date(m.date),
      })),
      conflicts: parsed.conflicts ?? [],
      outlook: {
        sat: {
          readiness: parsed.outlook?.sat?.readiness ?? 0,
          forecast: parsed.outlook?.sat?.forecast ? new Date(parsed.outlook.sat.forecast) : EXAMS.sat.test,
        },
        ent: {
          readiness: parsed.outlook?.ent?.readiness ?? 0,
          forecast: parsed.outlook?.ent?.forecast ? new Date(parsed.outlook.ent.forecast) : EXAMS.ent.test,
        },
      },
      doneMilestoneKeys: parsed.doneMilestoneKeys ?? [],
    };
  } catch {
    return null;
  }
}

export function getCachedRemoteOverview(): RemotePrepOverview | null {
  if (memoryCachedOverview) return memoryCachedOverview;
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(CACHE_STORAGE_KEY);
    if (raw) {
      memoryCachedOverview = deserializeOverview(raw);
      return memoryCachedOverview;
    }
  } catch {}
  return null;
}

export function saveCachedRemoteOverview(data: RemotePrepOverview): void {
  memoryCachedOverview = data;
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(CACHE_STORAGE_KEY, serializeOverview(data));
  } catch {}
}

export async function fetchRemoteOverview(programs: Program[] = []): Promise<RemotePrepOverview> {
  if (inflightOverviewPromise) return inflightOverviewPromise;

  inflightOverviewPromise = (async () => {
    try {
      const raw = await backend.overview.get();
      const outlook = adaptOutlook(raw.progress);
      const reqs = adaptRequirements(raw.requirements, raw.progress, programs);
      const milestones = adaptMilestones(raw.milestones, programs);
      const doneMilestoneKeys = raw.milestones.filter((m) => m.done).map((m) => m.key);

      const overview: RemotePrepOverview = {
        requirements: reqs,
        milestones,
        conflicts: raw.conflicts,
        outlook,
        doneMilestoneKeys,
      };

      saveCachedRemoteOverview(overview);
      return overview;
    } finally {
      inflightOverviewPromise = null;
    }
  })();

  return inflightOverviewPromise;
}

export function prefetchRemoteOverview(programs: Program[] = []): void {
  if (!REMOTE_PREP) return;
  fetchRemoteOverview(programs).catch(() => undefined);
}

export async function markRemoteMilestone(key: string, done: boolean): Promise<BackendMilestone> {
  const result = await backend.overview.markMilestone(key, done);
  if (memoryCachedOverview) {
    const nextDoneKeys = done
      ? Array.from(new Set([...memoryCachedOverview.doneMilestoneKeys, key]))
      : memoryCachedOverview.doneMilestoneKeys.filter((k) => k !== key);
    const nextMilestones = memoryCachedOverview.milestones.map((m) =>
      (m.key === key || m.id === key) ? { ...m, done } : m
    );
    const updated: RemotePrepOverview = {
      ...memoryCachedOverview,
      milestones: nextMilestones,
      doneMilestoneKeys: nextDoneKeys,
    };
    saveCachedRemoteOverview(updated);
  }
  return result;
}

