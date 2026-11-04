// Stub of the Quack backend (Phase 4 API) for fast frontend work: no login, no terms, data already there.
// The frontend runs the SAME code as with the real backend (NEXT_PUBLIC_QUACK_API=1), only QUACK_API_URL differs.
//
//   npm run dev:stub            stub on :8001 + next dev pointing at it
//   npm run dev:real            next dev pointing at the real backend (QUACK_API_URL, default http://localhost:8000)
//
// Scenarios (STUB_SCENARIO=...):  full (default) | empty (no subjects: the "new subject" flow) | terms (terms gate first)
// Slow the teacher down: STUB_DELAY=ms per token (default 35). Reset state: POST /__stub/reset
// Growth of the graph (to look at the "New" indicator): `npm run dev:grow` adds the next step to Calculus, the open graph
// refreshes by itself; STUB_STAGE=n starts the stub with the first n steps already applied (the graph "grew" since the last visit).
//
// Shapes follow lib/api/openapi.json; when the backend contract changes, regenerate the types (npm run api:types)
// and adjust the fixtures here: tsc on the frontend does not check this file.
import http from "node:http";
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";

const PORT = Number(process.env.STUB_PORT ?? 8001);
const SCENARIO = process.env.STUB_SCENARIO ?? "full";
const DELAY = Number(process.env.STUB_DELAY ?? 35);
const ME = "00000000-0000-4000-8000-000000000001";
const now = () => new Date().toISOString();
const ago = (min) => new Date(Date.now() - min * 60_000).toISOString();
const id = (s) => {
  // stable readable ids: "d-calc" -> uuid-shaped, so fixtures can be referenced by name
  let h = 0;
  for (const c of s) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  const hex = h.toString(16).padStart(8, "0");
  return `${hex}-0000-4000-8000-${Buffer.from(s).toString("hex").padEnd(12, "0").slice(0, 12)}`;
};

// ---- fixtures: the same subjects, chapters (hubs), topics and links as the mocks (npm run dev:fixtures to refresh) ----
const MOCK = JSON.parse(readFileSync(new URL("./fixtures/mock-domains.json", import.meta.url), "utf8"));
const STATE = { solid: "solved_independently", review: "inconsistent", confused: "struggling", new: "not_tested" };
const cap = (x) => x.charAt(0).toUpperCase() + x.slice(1);
// Topics with no "needed before" link and in no chapter (not in the mocks; stub only)
const EXTRA_UNLINKED = {
  matan: [
    { id: "complex-numbers", title: "Complex numbers", line: "Algebraic form, modulus, argument", simple: "Numbers of the form a + bi, where i squared is minus one.", about: "Needed for Euler's formula and for series with complex terms.", state: "new", retention: 0 },
    { id: "modelling", title: "Mathematical modelling", line: "From a word problem to an equation", simple: "Turning a situation into formulas you can solve.", about: "Practice of choosing variables and writing the relations between them.", state: "solid", retention: 0.9 },
  ],
};
function toDomain(m) {
  let reviewN = 0;
  const topic = (t) => {
    const level = t.state === "solid" ? Math.min(6, 4 + Math.round(t.retention * 2)) : t.state === "review" ? 3 : t.state === "confused" ? 1 : 0;
    // review topics: the first three are due right now (they feed "Today"), the rest later
    const dueMin = t.state === "review" ? (reviewN++ < 3 ? -60 * (6 + reviewN * 20) : 60 * 24 * (2 + reviewN)) : null;
    const skills = t.line.split(/,\s*/).slice(0, 3).map(cap);
    return { key: t.id, name: t.title, state: STATE[t.state], level, dueMin, skills, line: t.line, simple: t.simple, about: t.about };
  };
  return {
    key: m.id,
    name: m.title,
    hubs: m.hubs.map((h) => ({ key: h.id, name: h.title, deadlineMs: h.deadline ? Date.parse(h.deadline.date) : null, topics: m.topics.filter((t) => t.hubId === h.id).map(topic) })),
    // free topics of the mocks plus a couple with no links at all: they stand in the "Standalone" column of the graph
    free: [...m.topics.filter((t) => !t.hubId), ...(EXTRA_UNLINKED[m.id] ?? [])].map(topic),
    requires: m.topics.flatMap((t) => t.requires.map((r) => [r, t.id])),
    sessions: m.sessions,
  };
}

// What "New" has to show: each step is one of the four kinds of change it lights
const mk = (key, name, state = "not_tested", line = "") => ({ key, name, state, level: 0, dueMin: null, skills: [name], line, simple: `${name}.`, about: "" });
const GROWTH = [
  { say: "a new topic in the Series chapter: Taylor polynomials", apply: (d) => {
    d.hubs.find((h) => h.key === "series").topics.push(mk("taylor-poly", "Taylor polynomials"));
    d.requires.push(["radius", "taylor-poly"]);
  } },
  { say: "a new link between known topics: Standard limits -> Comparison tests", apply: (d) => d.requires.push(["remarkable", "comparison"]) },
  { say: "a topic that had no links got one: Complex numbers needs Limit of a function", apply: (d) => d.requires.push(["lim-fn", "complex-numbers"]) },
  { say: "a new chapter with three topics: Differential equations", apply: (d) => {
    d.hubs.push({ key: "diffeq", name: "Differential equations", deadlineMs: null, topics: [mk("ode-first", "First-order equations"), mk("ode-sep", "Separable equations"), mk("ode-lin", "Linear equations")] });
    d.requires.push(["taylor-poly", "ode-first"], ["ode-first", "ode-sep"], ["ode-first", "ode-lin"]);
  } },
  { say: "a new topic with no links: Fourier series (preview)", apply: (d) => d.free.push(mk("fourier", "Fourier series (preview)")) },
];


const TEACHER_LINES = [
  "Good question. Think of it this way: you are not asking what the function equals at the point, you are asking where it is heading as you get closer. ",
  "Let me give you one to try. ",
];

// ---- state ----------------------------------------------------------------------------------------------------------
let S;
function fresh() {
  const domains = SCENARIO === "empty" ? [] : MOCK.domains.map(toDomain).map((d) => ({ ...d, did: id("d-" + d.key), createdSeq: 1, createdAt: ago(60 * 24 * 14) }));
  S = { terms: SCENARIO !== "terms", settings: {}, domains, sessions: [], events: {}, seq: 100, notes: {}, cheats: {}, stage: 0 };
  const first = domains[0];
  if (!first) return;
  // history: the sessions of the mocks, finished
  first.sessions.forEach((m, i) => {
    const sid = randomUUID();
    const min = 60 * 24 * (i + 1);
    S.sessions.push({ domain_id: first.did, ended_at: ago(min - 20), hub_id: null, intent: "learn", last_activity_at: ago(min - 20), session_id: sid, start_seq: 1, started_at: ago(min), status: "ended", topic_id: null });
    S.events[sid] = [
      { seq: 1, type: "session.started", actor: "system", occurred_at: ago(min), payload: { intent: "learn" } },
      { seq: 2, type: "message.sent", actor: "learner", occurred_at: ago(min), payload: { text: m.title } },
      { seq: 3, type: "turn.committed", actor: "teacher", occurred_at: ago(min), payload: { text: `We went through: ${m.topics.join(", ")}.`, degraded_mode: null } },
    ];
  });
  // notes: the mock notes where there are some, else the topic's own short text; none for topics not studied yet
  for (const t of [...first.hubs.flatMap((h) => h.topics), ...first.free]) {
    const rich = MOCK.notes[t.key];
    if (rich) S.notes[t.key] = [{ block_id: id("b-" + t.key + "-s"), kind: "text", content: rich.summary, owner: "system" }, ...rich.notes.map((n, i) => ({ block_id: id(`b-${t.key}-${i}`), kind: "text", content: n.text, owner: "system" }))];
    else if (t.state !== "not_tested") S.notes[t.key] = [{ block_id: id("b-" + t.key), kind: "text", content: `## ${t.name}\n\n${t.simple}\n\n${t.about}`, owner: "system" }];
  }
  // one ready cheat sheet (the series chapter in the mocks); the others are built on demand
  const sheet = MOCK.cheatSheets.series;
  if (sheet) S.cheats["series"] = [{ block_id: id("b-ch-series"), kind: "text", content: "## Series cheat sheet\n\n" + sheet.formulas.map((f) => `- **${f.name}**: $${f.tex}$`).join("\n"), owner: "system" }];
}
function grow() {
  const d = S.domains[0];
  const step = GROWTH[S.stage];
  if (!d || !step) return null;
  step.apply(d);
  S.stage += 1;
  S.seq += 5;
  for (const r of streams) r.write(`event: projection_advanced\ndata: ${JSON.stringify({ applied_seq: S.seq })}\n\n`);
  return step.say;
}
const streams = new Set();
fresh();
for (let i = 0; i < Number(process.env.STUB_STAGE ?? 0); i++) grow();

const domainByKey = (k) => S.domains.find((d) => d.key === k);
const domainById = (did) => S.domains.find((d) => d.did === did);
const allTopics = (d) => [...d.hubs.flatMap((h) => h.topics.map((t) => ({ ...t, hub: h }))), ...d.free.map((t) => ({ ...t, hub: null }))];
const topicById = (tid) => {
  for (const d of S.domains) for (const t of allTopics(d)) if (id("t-" + t.key) === tid) return { d, t };
  return null;
};
const hubById = (hid) => {
  for (const d of S.domains) {
    for (const h of d.hubs) if (id("h-" + h.key) === hid) return { d, h };
    if (id("h-all-" + d.key) === hid) return { d, h: { key: "all-" + d.key, name: d.name, topics: allTopics(d) } };
  }
  return null;
};
const display = (t) => ({ display_state: t.state, display_level: t.level, display_reason: "stub", display_domain_id: undefined });
const dueMs = (t) => (t.dueMin === null ? 0 : Date.now() + t.dueMin * 60_000);

// ---- views ----------------------------------------------------------------------------------------------------------
const graphOf = (d) => ({
  domain_id: d.did,
  projected_seq: S.seq,
  lag_degraded: false,
  topics: allTopics(d).map((t) => ({ topic_id: id("t-" + t.key), name: t.name, status: "active", visibility: "shown", display: display(t), nearest_due_at_ms: dueMs(t) || undefined, skill_count: t.skills.length })),
  requires: d.requires.map(([a, b]) => ({ from_topic_id: id("t-" + a), to_topic_id: id("t-" + b), weight: 1 })),
});
const hubsOf = (d) => [
  // like the real backend: every subject has a `domain_all` hub over all its topics; it is not a chapter
  { hub_id: id("h-all-" + d.key), name: d.name, kind: "domain_all", control_type: "no_goal", deadline_at_ms: null, topics: allTopics(d).map((t) => ({ topic_id: id("t-" + t.key), name: t.name, required_levels: [3] })) },
  ...d.hubs.map((h) => ({
    hub_id: id("h-" + h.key),
    name: h.name,
    kind: "material",
    control_type: "tasks",
    deadline_at_ms: h.deadlineMs,
    topics: h.topics.map((t) => ({ topic_id: id("t-" + t.key), name: t.name, required_levels: [3] })),
  })),
];
const topicView = ({ d, t }) => ({
  topic_id: id("t-" + t.key),
  domain_id: d.did,
  name: t.name,
  status: "active",
  visibility: "shown",
  display: display(t),
  nearest_due_at_ms: dueMs(t) || undefined,
  places: [],
  skills: t.skills.map((n, i) => ({ skill_id: id(`s-${t.key}-${i}`), name: n, levels: [], ret_due_at_ms: t.dueMin !== null && i === 0 ? dueMs(t) : 0 })),
});
const sessionView = (s) => ({ ...s });

// ---- http helpers ---------------------------------------------------------------------------------------------------
const json = (res, code, body) => {
  res.writeHead(code, { "Content-Type": "application/json" });
  res.end(body === undefined ? "" : JSON.stringify(body));
};
const err = (res, code, status, message) => json(res, status, { error: { code, message, request_id: randomUUID(), details: {} } });
const readBody = (req) =>
  new Promise((r) => {
    let b = "";
    req.on("data", (c) => (b += c));
    req.on("end", () => {
      try {
        r(b ? JSON.parse(b) : {});
      } catch {
        r({});
      }
    });
  });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- the teacher ----------------------------------------------------------------------------------------------------
async function act(res, session, body) {
  res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
  const send = (event, data) => res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
  const ev = S.events[session.session_id] ?? (S.events[session.session_id] = []);
  const push = (type, actor, payload) => {
    const e = { seq: ++S.seq, type, actor, occurred_at: now(), payload };
    ev.push(e);
    return e.seq;
  };
  const recorded = push(body.kind === "message" ? "message.sent" : body.kind === "element_response" ? "answer.submitted" : "intent.set", "learner", body.kind === "message" ? { text: body.text ?? "" } : body);
  send("action_recorded", { seq: recorded });
  send("status", { state: "thinking" });
  await sleep(500);

  let text;
  let element = null;
  if (body.kind === "element_response") {
    send("verdict", { outcome: "correct" });
    text = "Right, and notice why it works: the inner function is what you substitute into. Want a harder one?";
  } else if (body.kind === "intent") {
    text = body.intent === "practice" ? "Let's practice. Here is the first one. " : body.intent === "review" ? "Quick review first. " : "Let's start. ";
    if (body.intent === "practice" || body.intent === "review") element = { kind: "task", element_id: randomUUID(), prompt: "Compute $\\lim_{x\\to 2} (x^2 - 4)/(x - 2)$ and say why you can not just plug in $x = 2$.", ask_confidence: false, canvas: false };
  } else {
    text = TEACHER_LINES.join("");
    element = { kind: "task", element_id: randomUUID(), prompt: "What is $\\lim_{x\\to 0} \\frac{\\sin x}{x}$? Explain in one sentence.", ask_confidence: false, canvas: false };
  }
  for (const w of text.match(/\S+\s*/g) ?? []) {
    send("token", { text: w });
    await sleep(DELAY);
  }
  if (element) send("element", { block_id: randomUUID(), element });
  const seq = push("turn.committed", "teacher", { text, degraded_mode: null });
  send("turn_committed", { seq, degraded_mode: null, effort: "low", role: "teacher" });
  session.last_activity_at = now();
  res.end();
}

// ---- routes ---------------------------------------------------------------------------------------------------------
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", "http://stub");
  const p = url.pathname;
  const m = req.method ?? "GET";
  const body = m === "GET" || m === "DELETE" ? {} : await readBody(req);
  console.log(`${m} ${p}${url.search}`);
  let g;

  if (p === "/__stub/grow" && m === "POST") {
    const said = grow();
    return json(res, 200, said ? { ok: true, stage: S.stage, added: said } : { ok: false, message: "nothing more to add, POST /__stub/reset starts over" });
  }
  if (p === "/__stub/reset" && m === "POST") {
    fresh();
    S.seq += 5;
    for (const r of streams) r.write(`event: projection_advanced
data: ${JSON.stringify({ applied_seq: S.seq })}

`);
    return json(res, 200, { ok: true, scenario: SCENARIO });
  }

  // auth: the stub never asks for a login
  if (p === "/auth/link") return json(res, 200, { ok: true });
  if (p === "/auth/logout") return json(res, 204);
  if (p === "/auth/google/start") {
    res.writeHead(302, { Location: "/" });
    return res.end();
  }
  if (p === "/v1/me") {
    return json(res, 200, { learner_id: ME, email: "anya@uni.edu", settings: S.settings, terms_version: "2026-11", terms_accepted: S.terms, client: { capture_still_ms: 700, line_pause_ms: 1200, stroke_batch_ms: 300 } });
  }
  if (p === "/v1/consents") {
    S.terms = true;
    return json(res, 200, { request_id: randomUUID() });
  }
  if (p === "/v1/learner/preferences" && m === "PATCH") {
    S.settings = { ...S.settings, [body.key]: body.value };
    return json(res, 200, { settings: S.settings });
  }
  if (p === "/v1/learner/stream") {
    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
    res.write(`event: projection_advanced\ndata: ${JSON.stringify({ applied_seq: S.seq })}\n\n`);
    streams.add(res);
    const t = setInterval(() => res.write(": ping\n\n"), 15000);
    req.on("close", () => {
      clearInterval(t);
      streams.delete(res);
    });
    return;
  }

  // subjects
  if (p === "/v1/domains" && m === "GET") return json(res, 200, { domains: S.domains.map((d) => ({ domain_id: d.did, name: d.name, created_seq: d.createdSeq, created_at: d.createdAt })) });
  if (p === "/v1/domains" && m === "POST") {
    const name = String(body.name ?? "New subject");
    const d = { key: "n" + S.domains.length, name, hubs: [], free: [], requires: [], sessions: [], did: randomUUID(), createdSeq: ++S.seq, createdAt: now() };
    S.domains.push(d);
    return json(res, 201, { domain_id: d.did, name, seq: d.createdSeq });
  }
  if (p === "/v1/domains/classify") {
    const text = String(body.text ?? "").toLowerCase();
    if (text.trim().length < 6) return json(res, 200, { decision: "unclear", name: null, domain_id: null, confidence: 0.2 });
    const hit = S.domains.find((d) => (d.key === "matan" && /calc|limit|deriv|integral/.test(text)) || (d.key === "linal" && /linear|matri|vector|eigen/.test(text)));
    if (hit) return json(res, 200, { decision: "existing", name: hit.name, domain_id: hit.did, confidence: 0.93 });
    const name = String(body.text).trim().split(/\s+/).slice(0, 3).join(" ");
    return json(res, 200, { decision: "new", name: name.charAt(0).toUpperCase() + name.slice(1), domain_id: null, confidence: 0.8 });
  }
  if ((g = p.match(/^\/v1\/domains\/([^/]+)\/today$/))) {
    const d = domainById(g[1]);
    if (!d) return err(res, "NOT_FOUND", 404, "no such subject");
    const endOfDay = new Date().setHours(24, 0, 0, 0);
    // t.state here is already STATE[...] (e.g. "inconsistent" for the mock's "review"), not the mock's own word
    const items = allTopics(d)
      .filter((t) => t.state === STATE.review && dueMs(t) > 0 && dueMs(t) < endOfDay)
      .sort((a, b) => dueMs(a) - dueMs(b))
      .map((t, i, arr) => ({ skill_id: id(`s-${t.key}-0`), topic_id: id("t-" + t.key), score: arr.length - i, reasons: ["due"] }));
    // one hub past its deadline with unfinished topics gets a pace warning, so the stub exercises that card too
    const overdue = d.hubs.find((h) => h.deadlineMs && h.deadlineMs < Date.now() && h.topics.some((t) => t.state !== STATE.solid));
    const pace_warnings = overdue
      ? [{ hub_id: id("h-" + overdue.key), ready: overdue.topics.filter((t) => t.state === STATE.solid).length, not_ready: overdue.topics.filter((t) => t.state !== STATE.solid).length, forecast: 3 }]
      : [];
    return json(res, 200, { items, minutes: items.length * 10, grade_hub_ids: [], pace_warnings });
  }
  if ((g = p.match(/^\/v1\/domains\/([^/]+)\/(graph|hubs)$/))) {
    const d = domainById(g[1]);
    if (!d) return err(res, "NOT_FOUND", 404, "no such subject");
    if (g[2] === "hubs") return json(res, 200, hubsOf(d));
    if (!d.hubs.length && !d.free.length) return err(res, "NOT_FOUND", 404, "no graph yet");
    return json(res, 200, graphOf(d));
  }

  // topics, notes, cards, cheat sheets
  if ((g = p.match(/^\/v1\/topics\/([^/]+)(?:\/(notes|cards))?$/))) {
    const hit = topicById(g[1]);
    if (!hit) return err(res, "NOT_FOUND", 404, "no such topic");
    if (!g[2]) return json(res, 200, topicView(hit));
    if (g[2] === "notes") {
      const blocks = S.notes[hit.t.key];
      return blocks ? json(res, 200, { artifact_id: id("a-notes-" + hit.t.key), kind: "notes", topic_id: g[1], hub_id: null, version: 1, blocks }) : err(res, "NOT_FOUND", 404, "no notes yet");
    }
    if (hit.t.state === "not_tested") return err(res, "NOT_FOUND", 404, "no cards yet");
    // like the backend: {card_id, kind, prompt, owner}; the answer stays in the task behind the card and the API does not show it.
    // STUB_CARD_ANSWERS=1 adds an `answer` to every card, to see how the deck looks when the back of a card is known
    const KINDS = ["definition", "sign_to_method", "distinction", "against_misconception"];
    const ask = {
      definition: (n) => `Define: ${n}.`,
      sign_to_method: (n) => `What in a problem tells you to reach for "${n}"?`,
      distinction: (n) => `How is "${n}" different from what it is usually mistaken for?`,
      against_misconception: (n) => `What is the usual mistake with "${n}", and why is it wrong?`,
    };
    const skills = hit.t.skills?.length ? hit.t.skills : [hit.t.name];
    const withAnswers = process.env.STUB_CARD_ANSWERS === "1";
    const cards = skills.flatMap((sk, i) => [KINDS[i % 4], KINDS[(i + 2) % 4]].map((kind, j) => ({ card_id: id(`c-${hit.t.key}-${i}-${j}`), kind, prompt: ask[kind](sk), owner: "system", ...(withAnswers ? { answer: hit.t.about || hit.t.simple || hit.t.line } : {}) })));
    return json(res, 200, { artifact_id: id("a-cards-" + hit.t.key), topic_id: g[1], version: 1, cards });
  }
  if ((g = p.match(/^\/v1\/hubs\/([^/]+)\/cheatsheet$/))) {
    const hit = hubById(g[1]);
    if (!hit) return err(res, "NOT_FOUND", 404, "no such chapter");
    if (m === "POST") {
      S.cheats[hit.h.key] = [{ block_id: id("b-ch-" + hit.h.key), kind: "text", content: `## ${hit.h.name}: cheat sheet\n\n${hit.h.topics.map((t) => `- **${t.name}**: ${t.skills.join(", ")}`).join("\n")}`, owner: "system" }];
    }
    const blocks = S.cheats[hit.h.key];
    return blocks ? json(res, m === "POST" ? 201 : 200, { artifact_id: id("a-ch-" + hit.h.key), kind: "cheatsheet", topic_id: null, hub_id: g[1], version: 1, offer: false, blocks }) : err(res, "NOT_FOUND", 404, "no cheat sheet yet");
  }
  if ((g = p.match(/^\/v1\/artifacts\/([^/]+)\/blocks\/([^/]+)$/))) {
    for (const [key, blocks] of Object.entries(S.notes)) {
      if (id("a-notes-" + key) !== g[1]) continue;
      const i = blocks.findIndex((b) => b.block_id === g[2]);
      if (i < 0) return err(res, "NOT_FOUND", 404, "no such block");
      if (m === "PUT") blocks[i] = { ...blocks[i], content: String(body.content ?? ""), owner: "learner" };
      if (m === "DELETE") blocks.splice(i, 1);
      return json(res, 200, { artifact_id: g[1], kind: "notes", topic_id: id("t-" + key), hub_id: null, version: 2, blocks });
    }
    return err(res, "NOT_FOUND", 404, "no such artifact");
  }
  if (p === "/v1/search") {
    const q = (url.searchParams.get("q") ?? "").toLowerCase();
    const hits = [];
    for (const [sid, evs] of Object.entries(S.events)) for (const e of evs) if (typeof e.payload.text === "string" && e.payload.text.toLowerCase().includes(q)) hits.push({ seq: e.seq, session_id: sid, snippet: e.payload.text.slice(0, 160), type: e.type });
    return json(res, 200, { hits });
  }

  // sessions
  if (p === "/v1/sessions" && m === "GET") return json(res, 200, { sessions: [...S.sessions].reverse().map(sessionView) });
  if (p === "/v1/sessions/active") return json(res, 200, S.sessions.findLast((s) => s.status === "active") ?? null);
  if (p === "/v1/sessions" && m === "POST") {
    if (!S.terms) return err(res, "CONSENT_REQUIRED", 403, "accept the terms first");
    const s = { domain_id: body.domain_id, ended_at: null, hub_id: body.hub_id ?? null, intent: body.intent ?? "free", last_activity_at: now(), session_id: randomUUID(), start_seq: ++S.seq, started_at: now(), status: "active", topic_id: body.topic_id ?? null };
    S.sessions.push(s);
    S.events[s.session_id] = [{ seq: ++S.seq, type: "session.started", actor: "system", occurred_at: now(), payload: { intent: s.intent } }];
    return json(res, 201, s);
  }
  if ((g = p.match(/^\/v1\/sessions\/([^/]+)(?:\/(actions|heartbeat|end|summary))?$/))) {
    const s = S.sessions.find((x) => x.session_id === g[1]);
    if (!s) return err(res, "NOT_FOUND", 404, "no such session");
    if (!g[2]) return json(res, 200, { ...s, events: S.events[s.session_id] ?? [] });
    if (g[2] === "heartbeat") return json(res, 200, {});
    if (g[2] === "actions") return act(res, s, body);
    if (g[2] === "end") {
      s.status = "ended";
      s.ended_at = now();
      return json(res, 200, s);
    }
    const topic = s.topic_id ? topicById(s.topic_id) : null;
    return json(res, 200, { before_seq: s.start_seq ?? 1, domain_id: s.domain_id, intent: s.intent ?? "free", session_id: s.session_id, started_seq: s.start_seq ?? 1, topic_id: s.topic_id, skills: (topic?.t.skills ?? ["Continuity at a point"]).map((_, i) => ({ skill_id: id("s" + i), changed: i === 0, topic_id: s.topic_id })), tasks: [{ task_id: randomUUID(), answers: 2, hint_step: 0 }, { task_id: randomUUID(), answers: 1, hint_step: 1 }] });
  }

  err(res, "NOT_FOUND", 404, `stub: no route ${m} ${p}`);
});

server.listen(PORT, () => console.log(`Quack stub API on http://localhost:${PORT}  scenario=${SCENARIO}`));
