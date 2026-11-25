# Критика черновика решения

> **Вывод модели, не факт.** Отчёт субагента (Claude Opus, общий агент), 2026-11-25 19:29 UTC. Сохранён без правок; оценка его выводов — в карточках гипотез.

## Задание, которое получил агент

```text
You are a skeptical senior architect reviewing a design proposal. Read-only task: do NOT edit any files in the repo.

Read the draft: <local-temp> (Russian). It proposes how Quack (an AI tutor; event-sourced backend: Postgres event journal is the truth, Neo4j is a projection written only by the projector, models never see ids and choose from short labels) should build the structure of a learner's subject: topics, skills, prerequisite edges, hubs.

Verify the draft against the real code and docs, then attack it:
- Code: <repo>\backend\quack — materials/parse.py, ops/material_parse.py, agents/reconcile.py, agents/hygiene.py, agents/topic_split.py, graph/projection/handlers.py (task markup → provisional skills, ~line 765–810 and ~1379), graph/read/neo4j_reader.py (topic_requires), graph/projection/skill_fold.py, prompts/builder/v1.md, prompts/material_parser/v1.md, and the teacher/extractor markup schemas (grep for topic_hint, "new" skill in quack/agents/schemas.py and quack/session).
- Tech doc: <repo>\documentation\Технический_документ_AI_и_архитектура_памяти_1.md §3.4, §4 (lines ~588–960), §7 (evidence, indirect evidence via REQUIRES), §8 (skill state), §13. Product doc: documentation\Продуктовая_логика_платформы_обучения_1.md §2.2–2.5, §3.2.

Questions to answer concretely, with file:line references where relevant:
1. Does the draft describe the current implementation correctly? List any factual errors.
2. For each part (4.1 topics eager, 4.2 lazy skills, 4.3 two-layer edges, 4.4 single structure writer, 4.5 material, 4.6 hubs): what breaks? Think through concrete cases: (a) learner only chats, no files, new domain; (b) learner uploads 7 unordered problem sheets; (c) learner uploads a 200-page textbook; (d) a session where the teacher needs a skill label mid-turn in a topic that has no skills yet (hot path must not wait); (e) seminar tasks that the task generator/teacher needs to tag (TESTS) — what happens with lazy skills; (f) indirect evidence and the "опора" (prerequisite support) rule that today read skill-level REQUIRES — what changes if most topics have no skills; (g) learner edits (merge/split/hide topics, §4.11); (h) Today/review/hub readiness that aggregate skill states per topic — what happens for topics with zero skills; (i) rebuild from journal (laws 1–7 in backend/CLAUDE.md).
3. Is "topic-level stored edges only for navigation, skill-level edges for the learner model" coherent, or does it recreate the two-diverging-graphs problem §3.4.5 rejected? What exactly must the read rule be?
4. Is a single debounced structure writer with the whole topic list in context realistic for latency (graph must grow "на глазах" while files are read), cost, and scale (500+ topics)? What fallback?
5. What is missing that the problem in section 1 needs but the draft does not solve?
6. Your verdict: which parts to keep, change or drop, and the 3 most important corrections. Be specific and brief; no generic advice.

Report in English, under 1200 words.
```

## Отчёт

## Review of the graph-design draft: verified against the code (read-only, no files changed)

### 1. Factual errors and omissions in the draft (§2)
- **The session path is described wrongly.** The draft follows Т-§4.4, not the code.
  - The teacher's `new_skill` is `name` only (`prompts/teacher/v3.md:118`), and `topic_hint` is kept only for old events (`events/values.py:169-174`).
  - The projector creates no temporary topic. It leaves the skill without a topic until `graph.hygiene` Topics places it (`graph/projection/handlers.py:778-781`).
  - Only the v2 extractor path attaches the session topic (`graph/evidence_validation.py:120-129`).
  - So the doc and the code already disagree here. That is an owner decision.
- **The main cause of the empty topics is missing.**
  - Every chunk's `topic` mark becomes a topic subject, whether or not the chunk teaches anything (`materials/parse.py:816-817,828`).
  - Any topic decision other than `link` creates a topic (`agents/reconcile.py:400-405`).
  - A free-text `topic_hint` also creates one (`reconcile.py:366-376,433-438`).
  - Problem sheets with teaches=0 therefore produce chunk topics that never get skills.
- **"Hygiene moves skills between topics" is wrong.** Topics mode places only skills that have no `COVERS` (`agents/hygiene.py:74-80,585-636`), and it is the only writer of `skill.placed`. 86 placements out of 90 skills is unexplained by the code. Investigate it before redesigning.
- **Hygiene already sees all topics, with no cap** (`hygiene.py:7,481-487`). So 4.4 extends existing hygiene; it does not replace a race. Its scale problem exists today.
- **Builder topic candidates are the vector top-k, not the full list.** `candidate_k=15` (`config.py:596`). Topics and skills go in separate batches, topics first (`parse.py:1027-1037`).
- **Omitted:** syllabus hubs with deadlines (`parse.py:866-941`).
- **Confirmed:** `reconcile.py:524-527` is the only writer of `edge.added` in the whole backend. Your points on the 300-character summary (`reconcile.py:452`), REQUIRES only within one call (`reconcile.py:475-482`) and the material hub (`parse.py:1063-1068`) are correct.

### 2. What breaks, part by part
**4.1 Topics created eagerly, facets as plain text**
- **(a) Chat only.** The teacher has no "new topic" markup field; `topicLabel` exists only for `split_topic` (`teacher/v3.md:105`). Adding one is a prompt and schema change on the hot path.
- **(c) 200-page textbook.** "Section → candidate topic" at chunk level, with parallel batches that cannot see each other, still produces fragments. You need an outline pass over the whole document (table of contents and headings) before chunks are assigned.
- **(i) Rebuild.** `TopicCreated` holds only id, domain, name and origin (`events/event_types.py:285-289`). Description, facets and sources need new events and new projector handlers.
- **(g) Split.** `agents/topic_split.py:6-15` spreads skills. A topic with zero skills has nothing to split.

**4.2 Lazy skills (the weakest part)**
- **(d) Hot path.** The projector cannot call a model (law 2), so "match against the topic's facets" can only be an exact name match there. The real matching is pushed to the background, and until then the skill has no topic and drops out of every topic aggregate.
- **(e) Seminar tasks and TESTS.** Task markup must have tests (`practice/markup.py:124`, `no_tests`), and a seminar task needs a `skill_id` (`practice/seminar_tasks.py`, `parse.py:944-979`). "No TESTS until the first use" breaks the form check of Т-§5.3. Generating tasks in parallel with `new_skill` brings back the duplicates, only later: the only dedup is by normalized name.
- **Material retrieval.** `chunk.indexed` stores skill ids, and `materials_prefetch` walks `Chunk-[:TEACHES]->Skill` (`graph/read/materials.py:39-53`). With no skills from material, retrieval for the teacher returns nothing.
- **(h) Readiness and Today.**
  - A topic with 1 of 5 facets made into a skill and proven reads "решаешь сам" / ready (`graph/projection/topic_fold.py:72-79`, `neo4j_reader.py:740-743`). That over-reports, against law 8.
  - `review/today.py:105-110` uses a non-optional `MATCH (t)-[:COVERS]->(s)`, so topics with zero skills drop out of the pace count and the warning is missed.
  - Today candidates are skills only (`today.py:237-254`).
  - A topic moves from proposed to active only when its skills are touched (`topic_fold.py:98-103`).

**4.3 Two layers of edges**
- **(f) Indirect evidence and the "опора" rule.**
  - Indirect evidence is created only when the evidence arrives and is never applied back: "a relation added later derives nothing from the past" (`handlers.py:1123-1127`).
  - With lazy skills the prerequisite skill usually does not exist yet, so indirect evidence never happens.
  - 4.5 removes teaches/uses, which is the only source of skill REQUIRES. The skill layer will be nearly empty.
  - So "the contract does not change" is true only because nothing feeds it. The "опора" guard (`session/guards.py:16-19`) and the plan order (`session/plan_view.py:8`) go silent.

**4.4 Single structure writer**
- Its retry concern is already handled by `append_unwritten` (`reconcile.py:546-560`).
- It also takes the `domain:{id}` lock that the material structure step and split already use. See point 4 for latency.

**4.6 Hubs**
- "Hubs only on request" contradicts П-2.3 (material hubs) and Т-§4.8.
- It also drops syllabus deadline hubs and the teacher's `new_hub_name` deadline hubs (`teacher/v3.md:139`).
- This needs an owner decision and a doc change (backend/CLAUDE.md, rule 4).

### 3. Does "topic edges for navigation, skill edges for the learner model" hold together?
Only with an explicit read rule. As written, it is the two diverging graphs that Т-§3.4.5 and §4.6 rejected; Т-§4.6 says topic REQUIRES is not stored, so this is an owner decision. The rule should be:
- Displayed topic edges = learner edges, then skill-derived edges, then stored model or material edges. A stored edge that contradicts a skill-derived or learner edge is suppressed when read.
- A learner deleting a topic edge must also write `edge.removed` for the skill edges underneath. Otherwise the derived edge brings it back.
- A topic edge never creates evidence and never blocks a turn.
- When a skill appears in topic B and B→A is stored, the writer proposes skill edges to A's skills (closed choice). Edges from model knowledge alone get confidence below `requires_min_confidence` (0.6, `config.py:349`), so they only warn. Flow is one way only: topic to skill.
- The plan order must use the same rule. Today it orders skills by skill REQUIRES.

### 4. Latency, cost and scale of the single writer
- About 3k input tokens holds only up to about 50 topics. At 500 topics it is about 25–30k per call, and a closed choice among 500 labels gets worse.
- A 200-page book puts 100+ new topics into one debounce window.
- Fallback:
  - Full list only below a threshold (about 80 topics). Above it, a shortlist: the existing `find_candidates` vector top-k, plus graph neighbours, plus the topics of the same material.
  - Batches of about 15 new topics, in order, each batch seeing the previous results, with events committed per batch so the graph grows as files are read.
  - Lock per batch, not for the whole book.
  - Show material topics at once as `proposed`; merges later are acceptable.

### 5. What section 1 needs that the draft does not solve
- **Stability.** The same file gives different names; nothing deterministic fixes this (outline first, then chunk assignment against a fixed outline).
- **Problem sheets with teaches=0.** Assign each problem to an existing topic, or infer the topic per problem.
- **Root cause.** The 86 `skill.placed` events are not explained.
- **Existing data.** No plan for journals that already hold material skills.
- **Model accuracy on prerequisites** is unmeasured, and the confidence values are not set.
- **No list of doc changes.** Needed: Т-§3.4.3, §4.3, §4.4, §4.6, §4.8, П-2.3.
- **Teacher labels.** How the teacher gets labels for a topic that has zero skills is not covered.

### 6. Verdict
- **Keep:** one topic decision against a list (shortlist when large); skill name plus definition as the embedding, never raw chunk text; tool variants and meta-skills not becoming skills; 4.4 as an extension of hygiene; the 4.8 evaluation plan.
- **Change:** 4.2, 4.3 and 4.4 as in the corrections below.
- **Drop:** 4.6 as written.

**Three most important corrections:**
1. **Make facets nodes, not text.** Use latent skills with deterministic ids (`uuid5(topic, facet)`): not shown, no state until evidence, readiness counts them as unknown. That gives labels for closed choice, targets for TESTS and seminar tasks, ends for REQUIRES, TEACHES links for retrieval, and deterministic rebuild. It also fixes the readiness over-reporting and the `today.py` MATCH. "Lazy" then means lazy state, not lazy identity.
2. **Give skill REQUIRES a source after 4.5.** Use topic-to-skill promotion with the read precedence in point 3, and accept that indirect evidence is not applied to the past.
3. **Fix the facts before redesigning.** Correct the session-path description and the real cause of the empty topics, explain the 86 placements, and take 4.6 and the doc conflicts to the owner instead of deciding them.
