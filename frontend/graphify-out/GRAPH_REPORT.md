# Graph Report - frontend  (2026-09-19)

## Corpus Check
- 100 files · ~121,635 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 27 file(s) not represented in the graph (top: .css 23, (none) 2, .example 1)

## Summary
- 890 nodes · 2006 edges · 56 communities (48 shown, 8 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS · INFERRED: 9 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `01b6b383`
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
- ChoiceApp
- UserMenu.tsx
- Globe.tsx
- PixelDuck.tsx
- Quack! — frontend
- SkillGraph.tsx
- prepData.ts
- PixelSprite.tsx
- What You Must Do When Invoked
- Sidebar.tsx
- package.json
- programs.ts
- prepModel.ts
- SetsView.tsx
- standing.ts
- AuthGate.tsx
- LoginView.tsx
- JourneySection
- glintMap
- pricing/page.tsx
- Overview.tsx
- store.ts
- AuthApi
- graphify reference: extra exports and benchmark
- localAuth.ts
- ChoiceApp.tsx
- prepAssistant.ts
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
- app/page.tsx
- CalendarTab.tsx
- quack/contract.ts
- source.ts
- GraphCanvas.tsx
- react
- next

## God Nodes (most connected - your core abstractions)
1. `react` - 47 edges
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

## Communities (56 total, 8 thin omitted)

### Community 0 - "dashboardRules.ts"
Cohesion: 0.12
Nodes (30): ActivityGrid(), Dashboard(), src_components_dashboard_dashboard_module, Props, activityByDay(), ActivityDay, calendar(), calendarEvents() (+22 more)

### Community 1 - "journeyArt.ts"
Cohesion: 0.08
Nodes (23): ARCH, BOAT, BUSH, FOG_PALETTE, FOG_PROPS, fogIsle(), GULL, HUT (+15 more)

### Community 2 - "assistant.ts"
Cohesion: 0.13
Nodes (21): acknowledge(), extract(), FieldKey, FIELDS, FieldStatus, fieldValue(), labelOf(), MissingKey (+13 more)

### Community 3 - "JourneySection.tsx"
Cohesion: 0.06
Nodes (28): src_components_home_journey_module, bezier(), Box, FOG, Ghost, HALF, ISLE_1, ISLE_1_SIZE (+20 more)

### Community 4 - "compilerOptions"
Cohesion: 0.11
Nodes (18): compilerOptions, allowJs, esModuleInterop, incremental, isolatedModules, jsx, lib, module (+10 more)

### Community 6 - "TopicWorkspace.tsx"
Cohesion: 0.09
Nodes (38): downloadMarkdown(), escape(), generateCards(), generateNotes(), inline(), markdownToHtml(), materialAsked(), MaterialKind (+30 more)

### Community 7 - "ChoiceApp"
Cohesion: 0.17
Nodes (20): editField(), placeholderFor(), readiness(), ChoiceApp(), applyToProfile(), assistantSay(), handleIntent(), onConfirm() (+12 more)

### Community 8 - "UserMenu.tsx"
Cohesion: 0.17
Nodes (13): ref_next_navigation, useAccount(), Props, UserMenu(), DEMO_SHIFT, shiftDemoClock(), src_components_transition_transition_module, TransitionLink() (+5 more)

### Community 9 - "Globe.tsx"
Cohesion: 0.10
Nodes (27): PROGRAMS, DuckLane(), arcDeg(), decodeWorld(), frameCountry(), Globe(), GlobeProps, Land (+19 more)

### Community 10 - "PixelDuck.tsx"
Cohesion: 0.13
Nodes (15): src_components_duck_pixel_duck_module, BEAT, BODY, COLORS, FEET, PixelDuck(), PixelDuckProps, pixels() (+7 more)

### Community 11 - "Quack! — frontend"
Cohesion: 0.22
Nodes (8): graphify, Quack! — frontend, Адаптивность, Запуск, Стек, Страницы, Структура, Что имитируется и где подключать бэкенд

### Community 12 - "SkillGraph.tsx"
Cohesion: 0.13
Nodes (21): AREAS, EXAM_IDS, Skill, SkillState, along(), autoLayout(), BASE, depthOf() (+13 more)

### Community 13 - "prepData.ts"
Cohesion: 0.10
Nodes (21): ForecastChart(), PAD, Props, useWidth(), conflicts(), day(), DEMO_SAVED, ExamOutlook (+13 more)

### Community 14 - "PixelSprite.tsx"
Cohesion: 0.31
Nodes (7): bitmap(), cache, PixelImage(), PixelImageProps, resolve(), Palette, PixelSpriteProps

### Community 15 - "What You Must Do When Invoked"
Cohesion: 0.07
Nodes (26): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+18 more)

### Community 16 - "Sidebar.tsx"
Cohesion: 0.12
Nodes (18): IconName, LevelDot(), ProgramActions, ChatSummary, Mode, Sidebar(), SidebarProps, SidebarTab (+10 more)

### Community 17 - "package.json"
Cohesion: 0.07
Nodes (25): dependencies, next, react, react-dom, devDependencies, openapi-typescript, @types/node, @types/react (+17 more)

### Community 18 - "programs.ts"
Cohesion: 0.15
Nodes (25): Profile, CompareView(), CompareViewProps, Icon(), CompareRow, compareRows(), compareSummary(), evaluate() (+17 more)

### Community 19 - "prepModel.ts"
Cohesion: 0.13
Nodes (21): src_components_prep_prep_module, Evidence, ExamId, Misconception, savedPrograms(), SetStatus, acceptSet(), AnswerResult (+13 more)

### Community 20 - "SetsView.tsx"
Cohesion: 0.16
Nodes (22): useVertical(), Overview(), forecastSeries(), formatShort(), monthStarts(), disputeMisconception(), rankSets(), readiness() (+14 more)

### Community 21 - "standing.ts"
Cohesion: 0.27
Nodes (15): budgetOf(), addDays(), alertsFor(), chanceOf(), clamp(), computeStanding(), Ctx, iso() (+7 more)

### Community 22 - "AuthGate.tsx"
Cohesion: 0.22
Nodes (7): metadata, src_components_account_account_module, Account, AccountContext, AuthGate(), User, ChoiceRoot()

### Community 23 - "LoginView.tsx"
Cohesion: 0.20
Nodes (11): auth, AuthError, AuthErrorCode, EMAIL_RE, PASSWORD_MIN, ERRORS, Mode, call() (+3 more)

### Community 24 - "JourneySection"
Cohesion: 0.18
Nodes (13): signalArc(), boatDistance(), clamp01(), JourneySection(), lerp(), place(), pointAt(), routeAt() (+5 more)

### Community 25 - "glintMap"
Cohesion: 0.32
Nodes (8): glintMap(), islandSlice(), islandTop(), noise(), radius(), IslandBody(), Water(), usePoolImage()

### Community 26 - "pricing/page.tsx"
Cohesion: 0.21
Nodes (10): ref_next_link, metadata, copy, src_components_home_footer_module, FooterDucks(), src_components_home_pricing_module, Billing, PricingPlans() (+2 more)

### Community 27 - "Overview.tsx"
Cohesion: 0.14
Nodes (26): Important(), Now(), PaceCard(), Props, Requirements(), daysBetween(), formatDate(), SET_STATUS_LABEL (+18 more)

### Community 28 - "store.ts"
Cohesion: 0.15
Nodes (15): adoptLegacy(), cache, DEVICE, flush(), flushOnExit(), localBackend, pending, prefix() (+7 more)

### Community 30 - "graphify reference: extra exports and benchmark"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 31 - "localAuth.ts"
Cohesion: 0.24
Nodes (6): Account, derive(), localAuth, publicPart(), startSession(), toHex()

### Community 32 - "ChoiceApp.tsx"
Cohesion: 0.13
Nodes (17): CONFIRM_REPLY, EMPTY_PROFILE, ChatMessage(), ChatMessageProps, ChatMsg, src_components_choice_choice_module, ChatItem, GREETINGS (+9 more)

### Community 33 - "prepAssistant.ts"
Cohesion: 0.25
Nodes (14): addDays(), ASSISTANT_PROMPTS, assistantReply(), examOf(), findDeadline(), minutesPerDay(), openSkills(), plan() (+6 more)

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
Cohesion: 0.17
Nodes (18): milestones(), glowOf(), ProgramChance, describeChange(), describePrep(), KEYS, Known, localSource() (+10 more)

### Community 41 - "DuckLane.tsx"
Cohesion: 0.13
Nodes (15): Tempo, src_components_home_duck_lane_module, BALLOON, COLORS, DuckLaneProps, FLAME, Flight, PARACHUTE (+7 more)

### Community 46 - "Closing.tsx"
Cohesion: 0.09
Nodes (23): ANGLER, FLOAT, GROUND_WIDE, LAKE, src_components_home_closing_module, PALETTE, ROD_BENT, ROD_STRAIGHT (+15 more)

### Community 49 - "app/page.tsx"
Cohesion: 0.15
Nodes (12): src_components_home_aura_module, Closing(), CursorAura(), RADIUS, HeroTitle(), measurePath(), Point, src_components_home_home_module (+4 more)

### Community 50 - "CalendarTab.tsx"
Cohesion: 0.24
Nodes (14): downloadIcs(), escape(), googleCalendarUrl(), icsFile(), nextDay(), pad(), stamp(), CalendarTab() (+6 more)

### Community 51 - "quack/contract.ts"
Cohesion: 0.13
Nodes (16): Level, ChancesCard(), examWord(), Props, ago(), ChangesFeed(), Props, Target (+8 more)

### Community 52 - "source.ts"
Cohesion: 0.21
Nodes (8): EMPTY_STATE, QuackState, EVENT_TYPE, remoteSource(), quackSource, serverSnapshot(), useQuack(), QuackInputs

### Community 53 - "GraphCanvas.tsx"
Cohesion: 0.22
Nodes (12): Beacon, Beacons(), clamp(), Drag, dropSelection(), GraphCanvas(), Popover, PopoverCard() (+4 more)

### Community 54 - "react"
Cohesion: 0.20
Nodes (8): ref_next_font_google, react, src_app_globals, intelOneMono, inter, metadata, raleway, ScrollToTopOnReload()

### Community 55 - "next"
Cohesion: 0.20
Nodes (5): nextConfig, next, metadata, LoginView(), safeNext()

## Knowledge Gaps
- **298 isolated node(s):** `nextConfig`, `name`, `version`, `private`, `dev` (+293 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 371 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **8 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `react` connect `react` to `dashboardRules.ts`, `assistant.ts`, `JourneySection.tsx`, `TopicWorkspace.tsx`, `UserMenu.tsx`, `Globe.tsx`, `PixelDuck.tsx`, `SkillGraph.tsx`, `prepData.ts`, `PixelSprite.tsx`, `Sidebar.tsx`, `package.json`, `programs.ts`, `prepModel.ts`, `SetsView.tsx`, `AuthGate.tsx`, `LoginView.tsx`, `JourneySection`, `pricing/page.tsx`, `Overview.tsx`, `store.ts`, `ChoiceApp.tsx`, `PageSky.tsx`, `DuckLane.tsx`, `Closing.tsx`, `app/page.tsx`, `CalendarTab.tsx`, `source.ts`, `GraphCanvas.tsx`, `next`?**
  _High betweenness centrality (0.373) - this node is a cross-community bridge._
- **Why does `ChoiceApp()` connect `ChoiceApp` to `ChoiceApp.tsx`, `assistant.ts`, `programs.ts`, `prepModel.ts`, `source.ts`, `AuthGate.tsx`?**
  _High betweenness centrality (0.020) - this node is a cross-community bridge._
- **Why does `PixelDuck()` connect `PixelDuck.tsx` to `assistant.ts`, `JourneySection.tsx`, `PageSky.tsx`, `UserMenu.tsx`, `DuckLane.tsx`, `Closing.tsx`, `Sidebar.tsx`, `app/page.tsx`, `AuthGate.tsx`, `LoginView.tsx`, `store.ts`?**
  _High betweenness centrality (0.019) - this node is a cross-community bridge._
- **What connects `nextConfig`, `name`, `version` to the rest of the system?**
  _298 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `dashboardRules.ts` be split into smaller, more focused modules?**
  _Cohesion score 0.11764705882352941 - nodes in this community are weakly interconnected._
- **Should `journeyArt.ts` be split into smaller, more focused modules?**
  _Cohesion score 0.07692307692307693 - nodes in this community are weakly interconnected._
- **Should `assistant.ts` be split into smaller, more focused modules?**
  _Cohesion score 0.12666666666666668 - nodes in this community are weakly interconnected._