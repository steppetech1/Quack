# Graph Report - frontend  (2026-09-19)

## Corpus Check
- 99 files · ~120,426 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 27 file(s) not represented in the graph (top: .css 23, (none) 2, .example 1)

## Summary
- 876 nodes · 1986 edges · 53 communities (46 shown, 7 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS · INFERRED: 9 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `211b3540`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- dashboardRules.ts
- journeyArt.ts
- assistant.ts
- JourneySection.tsx
- compilerOptions
- src_components_home_quack_module
- TopicWorkspace.tsx
- HeroLandscape.tsx
- UserMenu.tsx
- Globe.tsx
- PixelDuck.tsx
- Quack! — frontend
- SkillGraph.tsx
- prepData.ts
- pricing/page.tsx
- What You Must Do When Invoked
- programs.ts
- package.json
- prepAssistant.ts
- PrepView.tsx
- SetsView.tsx
- standing.ts
- AuthGate.tsx
- LoginView.tsx
- JourneySection
- glintMap
- react
- Sidebar.tsx
- store.ts
- auth.ts
- graphify reference: extra exports and benchmark
- localAuth.ts
- remoteAuth.ts
- ChoiceApp.tsx
- graphify reference: query, path, explain
- schema.d.ts
- PageSky.tsx
- graphify reference: add a URL and watch a folder
- graphify reference: commit hook and native CLAUDE.md integration
- graphify reference: incremental update and cluster-only
- localSource.ts
- DuckLane.tsx
- graphify reference: GitHub clone and cross-repo merge
- graphify reference: transcribe video and audio
- CLAUDE.md
- .claude/CLAUDE.md
- Closing.tsx
- extraction-spec.md
- StateBackend
- Overview.tsx
- CalendarTab.tsx
- GraphCanvas.tsx
- ChangesFeed.tsx

## God Nodes (most connected - your core abstractions)
1. `react` - 46 edges
2. `ChoiceApp()` - 27 edges
3. `daysBetween()` - 26 edges
4. `formatDate()` - 24 edges
5. `formatShort()` - 22 edges
6. `skillById()` - 20 edges
7. `programById()` - 19 edges
8. `computeStanding()` - 19 edges
9. `Icon()` - 17 edges
10. `evaluate()` - 16 edges

## Surprising Connections (you probably didn't know these)
- `Dashboard()` --indirect_call--> `programById()`  [INFERRED]
  src/components/dashboard/Dashboard.tsx → src/components/choice/programs.ts
- `savedPrograms()` --indirect_call--> `programById()`  [INFERRED]
  src/components/prep/prepData.ts → src/components/choice/programs.ts
- `describePrep()` --indirect_call--> `programById()`  [INFERRED]
  src/components/quack/localSource.ts → src/components/choice/programs.ts
- `computeStanding()` --indirect_call--> `programById()`  [INFERRED]
  src/components/quack/standing.ts → src/components/choice/programs.ts
- `Requirements()` --indirect_call--> `formatShort()`  [INFERRED]
  src/components/prep/Overview.tsx → src/components/prep/prepData.ts

## Import Cycles
- None detected.

## Communities (53 total, 7 thin omitted)

### Community 0 - "dashboardRules.ts"
Cohesion: 0.12
Nodes (27): ActivityGrid(), Dashboard(), src_components_dashboard_dashboard_module, Props, activityByDay(), ActivityDay, calendar(), calendarEvents() (+19 more)

### Community 1 - "journeyArt.ts"
Cohesion: 0.08
Nodes (23): ARCH, BOAT, BUSH, FOG_PALETTE, FOG_PROPS, fogIsle(), GULL, HUT (+15 more)

### Community 2 - "assistant.ts"
Cohesion: 0.07
Nodes (45): acknowledge(), CONFIRM_REPLY, editField(), EMPTY_PROFILE, extract(), FieldKey, FIELDS, FieldStatus (+37 more)

### Community 3 - "JourneySection.tsx"
Cohesion: 0.08
Nodes (22): src_components_home_journey_module, bezier(), FOG, Ghost, HALF, ISLE_1, ISLE_1_SIZE, ISLE_2 (+14 more)

### Community 4 - "compilerOptions"
Cohesion: 0.11
Nodes (18): compilerOptions, allowJs, esModuleInterop, incremental, isolatedModules, jsx, lib, module (+10 more)

### Community 6 - "TopicWorkspace.tsx"
Cohesion: 0.09
Nodes (36): downloadMarkdown(), escape(), generateCards(), inline(), markdownToHtml(), materialAsked(), MaterialKind, newId() (+28 more)

### Community 7 - "HeroLandscape.tsx"
Cohesion: 0.12
Nodes (19): Prop(), FLAG_A, FLAG_B, GROUND, HALL, HeroLandscape(), mirror(), PACE (+11 more)

### Community 8 - "UserMenu.tsx"
Cohesion: 0.10
Nodes (20): ref_next_font_google, ref_next_navigation, src_app_globals, intelOneMono, inter, metadata, raleway, useAccount() (+12 more)

### Community 9 - "Globe.tsx"
Cohesion: 0.14
Nodes (21): PROGRAMS, arcDeg(), decodeWorld(), frameCountry(), Globe(), GlobeProps, Land, src_components_home_globe_module (+13 more)

### Community 10 - "PixelDuck.tsx"
Cohesion: 0.18
Nodes (11): src_components_duck_pixel_duck_module, BEAT, BODY, COLORS, FEET, PixelDuck(), PixelDuckProps, pixels() (+3 more)

### Community 11 - "Quack! — frontend"
Cohesion: 0.22
Nodes (8): graphify, Quack! — frontend, Адаптивность, Запуск, Стек, Страницы, Структура, Что имитируется и где подключать бэкенд

### Community 12 - "SkillGraph.tsx"
Cohesion: 0.12
Nodes (22): AREAS, EXAM_IDS, ExamId, Skill, SKILLS, along(), autoLayout(), BASE (+14 more)

### Community 13 - "prepData.ts"
Cohesion: 0.09
Nodes (23): conflicts(), DEMO_SAVED, Evidence, ExamRequirement, FORECAST_BASE, IELTS_SETS, IELTS_SKILLS, Milestone (+15 more)

### Community 14 - "pricing/page.tsx"
Cohesion: 0.16
Nodes (13): ref_next_link, metadata, copy, src_components_home_footer_module, FooterDucks(), POND, TUFT, SCENE_PALETTE (+5 more)

### Community 15 - "What You Must Do When Invoked"
Cohesion: 0.07
Nodes (26): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+18 more)

### Community 16 - "programs.ts"
Cohesion: 0.15
Nodes (25): Profile, CompareView(), CompareViewProps, CompareRow, compareRows(), compareSummary(), evaluate(), Evaluation (+17 more)

### Community 17 - "package.json"
Cohesion: 0.09
Nodes (22): dependencies, next, react, react-dom, devDependencies, openapi-typescript, @types/node, @types/react (+14 more)

### Community 18 - "prepAssistant.ts"
Cohesion: 0.42
Nodes (10): addDays(), ASSISTANT_PROMPTS, assistantReply(), examOf(), findDeadline(), minutesPerDay(), openSkills(), plan() (+2 more)

### Community 19 - "PrepView.tsx"
Cohesion: 0.13
Nodes (18): react-dom, store, FirstHint(), Props, readSeen(), src_components_hints_hints_module, savedPrograms(), acceptSet() (+10 more)

### Community 20 - "SetsView.tsx"
Cohesion: 0.10
Nodes (38): useVertical(), generateNotes(), Now(), EXAMS, formatShort(), SET_STATUS_LABEL, skillById(), STATE_LABEL (+30 more)

### Community 21 - "standing.ts"
Cohesion: 0.21
Nodes (23): budgetOf(), hardConflicts(), Important(), Requirements(), daysBetween(), formatDate(), readiness(), addDays() (+15 more)

### Community 22 - "AuthGate.tsx"
Cohesion: 0.18
Nodes (8): nextConfig, next, metadata, src_components_account_account_module, Account, AccountContext, AuthGate(), ChoiceRoot()

### Community 23 - "LoginView.tsx"
Cohesion: 0.20
Nodes (8): metadata, AuthErrorCode, EMAIL_RE, PASSWORD_MIN, ERRORS, LoginView(), Mode, safeNext()

### Community 24 - "JourneySection"
Cohesion: 0.21
Nodes (12): signalArc(), boatDistance(), clamp01(), JourneySection(), lerp(), place(), pointAt(), routeAt() (+4 more)

### Community 25 - "glintMap"
Cohesion: 0.38
Nodes (7): glintMap(), islandSlice(), islandTop(), noise(), radius(), IslandBody(), Water()

### Community 26 - "react"
Cohesion: 0.14
Nodes (15): react, src_components_home_aura_module, Closing(), CursorAura(), RADIUS, HeroTitle(), measurePath(), Point (+7 more)

### Community 27 - "Sidebar.tsx"
Cohesion: 0.20
Nodes (11): ChatSummary, Sidebar(), SidebarProps, SidebarTab, TABS, timeLabel(), DASH_TABS, DashTab (+3 more)

### Community 28 - "store.ts"
Cohesion: 0.24
Nodes (10): adoptLegacy(), cache, DEVICE, flush(), flushOnExit(), localBackend, pending, prefix() (+2 more)

### Community 29 - "auth.ts"
Cohesion: 0.20
Nodes (4): auth, AuthApi, localAuth, remoteAuth

### Community 30 - "graphify reference: extra exports and benchmark"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 31 - "localAuth.ts"
Cohesion: 0.28
Nodes (5): Account, derive(), publicPart(), startSession(), toHex()

### Community 32 - "remoteAuth.ts"
Cohesion: 0.32
Nodes (5): AuthError, User, call(), fail(), StudentCtx

### Community 33 - "ChoiceApp.tsx"
Cohesion: 0.10
Nodes (24): ChatMessage(), ChatMessageProps, ChatMsg, src_components_choice_choice_module, ChatItem, GREETINGS, LeftPanel, RightPanel (+16 more)

### Community 34 - "graphify reference: query, path, explain"
Cohesion: 0.33
Nodes (5): For /graphify explain, For /graphify path, graphify reference: query, path, explain, Step 0 — Constrained query expansion (REQUIRED before traversal), Step 1 — Traversal

### Community 35 - "schema.d.ts"
Cohesion: 0.33
Nodes (5): components, $defs, operations, paths, webhooks

### Community 36 - "PageSky.tsx"
Cohesion: 0.18
Nodes (9): Cloud, CLOUD_A, CLOUD_B, CLOUDS, PageSky(), PALETTE, Pass, WEDGE (+1 more)

### Community 37 - "graphify reference: add a URL and watch a folder"
Cohesion: 0.50
Nodes (3): For /graphify add, For --watch, graphify reference: add a URL and watch a folder

### Community 38 - "graphify reference: commit hook and native CLAUDE.md integration"
Cohesion: 0.50
Nodes (3): For git commit hook, For native CLAUDE.md integration, graphify reference: commit hook and native CLAUDE.md integration

### Community 39 - "graphify reference: incremental update and cluster-only"
Cohesion: 0.50
Nodes (3): For --cluster-only, For --update (incremental re-extraction), graphify reference: incremental update and cluster-only

### Community 40 - "localSource.ts"
Cohesion: 0.09
Nodes (32): ChancesCard(), examWord(), Props, initialModel(), reviveModel(), ChanceFact, EMPTY_STATE, ExamPace (+24 more)

### Community 41 - "DuckLane.tsx"
Cohesion: 0.14
Nodes (13): src_components_home_duck_lane_module, BALLOON, COLORS, DuckLane(), DuckLaneProps, FLAME, Flight, PARACHUTE (+5 more)

### Community 46 - "Closing.tsx"
Cohesion: 0.13
Nodes (13): ANGLER, FLOAT, GROUND_WIDE, LAKE, src_components_home_closing_module, PALETTE, ROD_BENT, ROD_STRAIGHT (+5 more)

### Community 49 - "Overview.tsx"
Cohesion: 0.15
Nodes (19): Program, ForecastChart(), PAD, Props, useWidth(), Overview(), PaceCard(), Props (+11 more)

### Community 50 - "CalendarTab.tsx"
Cohesion: 0.24
Nodes (14): downloadIcs(), escape(), googleCalendarUrl(), icsFile(), nextDay(), pad(), stamp(), CalendarTab() (+6 more)

### Community 51 - "GraphCanvas.tsx"
Cohesion: 0.22
Nodes (12): Beacon, Beacons(), clamp(), Drag, dropSelection(), GraphCanvas(), Popover, PopoverCard() (+4 more)

### Community 52 - "ChangesFeed.tsx"
Cohesion: 0.33
Nodes (6): ago(), ChangesFeed(), Props, Target, TARGET_LABEL, TONE_ICON

## Knowledge Gaps
- **290 isolated node(s):** `nextConfig`, `name`, `version`, `private`, `dev` (+285 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 363 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `react` connect `react` to `dashboardRules.ts`, `assistant.ts`, `JourneySection.tsx`, `TopicWorkspace.tsx`, `HeroLandscape.tsx`, `UserMenu.tsx`, `Globe.tsx`, `PixelDuck.tsx`, `SkillGraph.tsx`, `pricing/page.tsx`, `programs.ts`, `package.json`, `PrepView.tsx`, `SetsView.tsx`, `AuthGate.tsx`, `LoginView.tsx`, `JourneySection`, `Sidebar.tsx`, `ChoiceApp.tsx`, `PageSky.tsx`, `localSource.ts`, `DuckLane.tsx`, `Closing.tsx`, `Overview.tsx`, `CalendarTab.tsx`, `GraphCanvas.tsx`?**
  _High betweenness centrality (0.364) - this node is a cross-community bridge._
- **Why does `ChoiceApp()` connect `assistant.ts` to `dashboardRules.ts`, `ChoiceApp.tsx`, `PrepView.tsx`, `AuthGate.tsx`?**
  _High betweenness centrality (0.020) - this node is a cross-community bridge._
- **Why does `PixelDuck()` connect `PixelDuck.tsx` to `ChoiceApp.tsx`, `assistant.ts`, `JourneySection.tsx`, `PageSky.tsx`, `HeroLandscape.tsx`, `UserMenu.tsx`, `DuckLane.tsx`, `Closing.tsx`, `pricing/page.tsx`, `PrepView.tsx`, `AuthGate.tsx`, `LoginView.tsx`, `react`?**
  _High betweenness centrality (0.018) - this node is a cross-community bridge._
- **What connects `nextConfig`, `name`, `version` to the rest of the system?**
  _290 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `dashboardRules.ts` be split into smaller, more focused modules?**
  _Cohesion score 0.12473118279569892 - nodes in this community are weakly interconnected._
- **Should `journeyArt.ts` be split into smaller, more focused modules?**
  _Cohesion score 0.07692307692307693 - nodes in this community are weakly interconnected._
- **Should `assistant.ts` be split into smaller, more focused modules?**
  _Cohesion score 0.07294117647058823 - nodes in this community are weakly interconnected._