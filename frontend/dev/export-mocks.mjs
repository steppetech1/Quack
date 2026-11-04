// Exports the English mock data (lib/mock/en) to dev/fixtures/mock-domains.json, so the stub API serves the SAME
// subjects, chapters (hubs), topics and links as the mocks: the graph looks identical in stub and mock mode.
//   npm run dev:fixtures        (re-run after the mocks change; the JSON is committed)
import { execFileSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";

const out = join(tmpdir(), "quack-mockjs");
rmSync(out, { recursive: true, force: true });
execFileSync("npx", ["tsc", "lib/mock/en/index.ts", "--outDir", out, "--module", "commonjs", "--moduleResolution", "node", "--target", "es2022", "--skipLibCheck", "--esModuleInterop"], { stdio: "inherit", shell: true });

const require = createRequire(join(out, "x.js"));
const { domains } = require(join(out, "en/domains.js"));
const { topicDetails, cheatSheets } = require(join(out, "en/topic-details.js"));

const fixture = {
  domains: domains.map((d) => ({
    id: d.id,
    title: d.title,
    hubs: d.hubs.map((h) => ({ id: h.id, num: h.num, title: h.title, about: h.about, deadline: h.deadline ?? null })),
    topics: d.topics.map((t) => ({ id: t.id, hubId: t.hubId ?? null, title: t.title, line: t.line, simple: t.simple ?? "", about: t.about ?? "", state: t.state, requires: t.requires, retention: t.retention })),
    sessions: (d.sessions ?? []).map((s) => ({ title: s.title, date: s.date, topics: s.topics })),
  })),
  // notes of the topics that have them in the mocks: blocks of {kind, text, src}
  notes: Object.fromEntries(Object.entries(topicDetails).map(([id, v]) => [id, { summary: v.summary, notes: v.notes }])),
  cheatSheets,
};

mkdirSync("dev/fixtures", { recursive: true });
writeFileSync("dev/fixtures/mock-domains.json", JSON.stringify(fixture, null, 1));
console.log(`dev/fixtures/mock-domains.json: ${fixture.domains.length} subjects, ${fixture.domains.reduce((n, d) => n + d.topics.length, 0)} topics`);
