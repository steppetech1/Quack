# Graph Report - frontend  (2026-09-19)

## Corpus Check
- 99 files · ~119,351 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 27 file(s) not represented in the graph (top: .css 23, (none) 2, .example 1)

## Summary
- 858 nodes · 1959 edges · 50 communities (43 shown, 7 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS · INFERRED: 9 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `a41b8bb6`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- dashboardRules.ts
- ChoiceApp.tsx
- ChoiceApp
- JourneySection.tsx
- compilerOptions
- src_components_home_quack_module
- TopicWorkspace.tsx
- HeroLandscape.tsx
- react
- Globe.tsx
- PixelDuck.tsx
- Quack! — frontend
- SkillGraph.tsx
- prepData.ts
- prepModel.ts
- What You Must Do When Invoked
- programs.ts
- package.json
- prepAssistant.ts
- CalendarTab.tsx
- SetsView.tsx
- UserMenu.tsx
- AuthGate.tsx
- standing.ts
- SetDetail.tsx
- next
- app/page.tsx
- Sidebar.tsx
- store.ts
- AuthApi
- graphify reference: extra exports and benchmark
- localAuth.ts
- LoginView.tsx
- assistant.ts
- graphify reference: query, path, explain
- schema.d.ts
- source.ts
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
- quack/contract.ts
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
- `computeStanding()` --indirect_call--> `programById()`  [INFERRED]
  src/components/quack/standing.ts → src/components/choice/programs.ts
- `Requirements()` --indirect_call--> `formatShort()`  [INFERRED]
  src/components/prep/Overview.tsx → src/components/prep/prepData.ts
- `call()` --calls--> `AuthError`  [EXTRACTED]
  src/components/account/remoteAuth.ts → src/components/account/contract.ts

## Import Cycles
- None detected.

## Communities (50 total, 7 thin omitted)

### Community 0 - "dashboardRules.ts"
Cohesion: 0.12
Nodes (28): ActivityGrid(), Dashboard(), src_components_dashboard_dashboard_module, Props, activityByDay(), ActivityDay, calendarEvents(), CalendarKind (+20 more)

### Community 1 - "ChoiceApp.tsx"
Cohesion: 0.15
Nodes (14): ChatMessage(), ChatMessageProps, ChatMsg, src_components_choice_choice_module, ChatItem, GREETINGS, LeftPanel, RightPanel (+6 more)

### Community 2 - "ChoiceApp"
Cohesion: 0.14
Nodes (26): editField(), extract(), fieldValue(), nextMissing(), placeholderFor(), planReply(), readiness(), summaryText() (+18 more)

### Community 3 - "JourneySection.tsx"
Cohesion: 0.06
Nodes (50): src_components_home_journey_module, BOAT, BUSH, glintMap(), HUT, islandSlice(), islandTop(), JOURNEY_PALETTE (+42 more)

### Community 4 - "compilerOptions"
Cohesion: 0.11
Nodes (18): compilerOptions, allowJs, esModuleInterop, incremental, isolatedModules, jsx, lib, module (+10 more)

### Community 6 - "TopicWorkspace.tsx"
Cohesion: 0.09
Nodes (36): downloadMarkdown(), escape(), generateCards(), generateNotes(), inline(), markdownToHtml(), materialAsked(), MaterialKind (+28 more)

### Community 7 - "HeroLandscape.tsx"
Cohesion: 0.10
Nodes (22): Prop(), src_components_home_footer_module, FooterDucks(), POND, TUFT, FLAG_A, FLAG_B, HALL (+14 more)

### Community 8 - "react"
Cohesion: 0.14
Nodes (13): ref_next_font_google, ref_next_navigation, react, src_app_globals, intelOneMono, inter, metadata, raleway (+5 more)

### Community 9 - "Globe.tsx"
Cohesion: 0.13
Nodes (22): Program, PROGRAMS, arcDeg(), decodeWorld(), frameCountry(), Globe(), GlobeProps, Land (+14 more)

### Community 10 - "PixelDuck.tsx"
Cohesion: 0.09
Nodes (23): src_components_duck_pixel_duck_module, BEAT, BODY, COLORS, FEET, PixelDuck(), PixelDuckProps, pixels() (+15 more)

### Community 11 - "Quack! — frontend"
Cohesion: 0.22
Nodes (8): graphify, Quack! — frontend, Адаптивность, Запуск, Стек, Страницы, Структура, Что имитируется и где подключать бэкенд

### Community 12 - "SkillGraph.tsx"
Cohesion: 0.14
Nodes (19): AREAS, Skill, along(), autoLayout(), BASE, depthOf(), edgePath(), edgePathDown() (+11 more)

### Community 13 - "prepData.ts"
Cohesion: 0.10
Nodes (26): ForecastChart(), PAD, Props, useWidth(), Overview(), Props, src_components_prep_prep_module, day() (+18 more)

### Community 14 - "prepModel.ts"
Cohesion: 0.12
Nodes (24): Now(), Evidence, ExamId, Misconception, savedPrograms(), setById(), SetStatus, SkillState (+16 more)

### Community 15 - "What You Must Do When Invoked"
Cohesion: 0.07
Nodes (26): For /graphify add and --watch, For /graphify query, For the commit hook and native CLAUDE.md integration, For --update and --cluster-only, /graphify, Honesty Rules, Interpreter guard for subcommands, Part A - Structural extraction for code files (+18 more)

### Community 16 - "programs.ts"
Cohesion: 0.16
Nodes (25): Profile, CompareView(), CompareViewProps, Icon(), src_components_choice_layout_module, budgetOf(), CompareRow, compareRows() (+17 more)

### Community 17 - "package.json"
Cohesion: 0.07
Nodes (25): dependencies, next, react, react-dom, devDependencies, openapi-typescript, @types/node, @types/react (+17 more)

### Community 18 - "prepAssistant.ts"
Cohesion: 0.17
Nodes (24): calendar(), hardConflicts(), Important(), Requirements(), addDays(), ASSISTANT_PROMPTS, assistantReply(), examOf() (+16 more)

### Community 19 - "CalendarTab.tsx"
Cohesion: 0.24
Nodes (14): downloadIcs(), escape(), googleCalendarUrl(), icsFile(), nextDay(), pad(), stamp(), CalendarTab() (+6 more)

### Community 20 - "SetsView.tsx"
Cohesion: 0.16
Nodes (22): useVertical(), forecastSeries(), formatShort(), monthStarts(), closed(), disputeMisconception(), MISCONCEPTION_LABEL(), rankSets() (+14 more)

### Community 21 - "UserMenu.tsx"
Cohesion: 0.25
Nodes (9): ref_next_link, useAccount(), Props, UserMenu(), DEMO_SHIFT, shiftDemoClock(), TransitionLink(), TransitionLinkProps (+1 more)

### Community 22 - "AuthGate.tsx"
Cohesion: 0.24
Nodes (6): metadata, src_components_account_account_module, Account, AccountContext, AuthGate(), ChoiceRoot()

### Community 23 - "standing.ts"
Cohesion: 0.27
Nodes (14): Conflict, addDays(), alertsFor(), clamp(), computeStanding(), Ctx, iso(), lowerFirst() (+6 more)

### Community 24 - "SetDetail.tsx"
Cohesion: 0.21
Nodes (13): SET_STATUS_LABEL, skillById(), STATE_LABEL, Due, DUE_TEXT, dueOf(), plannedDates(), Props (+5 more)

### Community 25 - "next"
Cohesion: 0.20
Nodes (5): nextConfig, next, metadata, LoginView(), safeNext()

### Community 26 - "app/page.tsx"
Cohesion: 0.14
Nodes (17): metadata, src_components_home_aura_module, copy, CursorAura(), RADIUS, src_components_home_home_module, src_components_home_layers_module, LayerStack() (+9 more)

### Community 27 - "Sidebar.tsx"
Cohesion: 0.10
Nodes (21): IconName, LevelDot(), ProgramActions, ChatSummary, Mode, Sidebar(), SidebarProps, SidebarTab (+13 more)

### Community 28 - "store.ts"
Cohesion: 0.08
Nodes (28): StateBackend, adoptLegacy(), cache, DEVICE, flush(), flushOnExit(), localBackend, pending (+20 more)

### Community 30 - "graphify reference: extra exports and benchmark"
Cohesion: 0.22
Nodes (8): graphify reference: extra exports and benchmark, Step 6b - Wiki (only if --wiki flag), Step 7 - Neo4j export (only if --neo4j or --neo4j-push flag), Step 7a - FalkorDB export (only if --falkordb or --falkordb-push flag), Step 7b - SVG export (only if --svg flag), Step 7c - GraphML export (only if --graphml flag), Step 7d - MCP server (only if --mcp flag), Step 8 - Token reduction benchmark (only if total_words > 5000)

### Community 31 - "localAuth.ts"
Cohesion: 0.28
Nodes (5): Account, derive(), publicPart(), startSession(), toHex()

### Community 32 - "LoginView.tsx"
Cohesion: 0.18
Nodes (13): auth, AuthError, AuthErrorCode, EMAIL_RE, PASSWORD_MIN, User, localAuth, ERRORS (+5 more)

### Community 33 - "assistant.ts"
Cohesion: 0.13
Nodes (18): acknowledge(), CONFIRM_REPLY, EMPTY_PROFILE, FieldKey, FIELDS, FieldStatus, labelOf(), MissingKey (+10 more)

### Community 34 - "graphify reference: query, path, explain"
Cohesion: 0.33
Nodes (5): For /graphify explain, For /graphify path, graphify reference: query, path, explain, Step 0 — Constrained query expansion (REQUIRED before traversal), Step 1 — Traversal

### Community 35 - "schema.d.ts"
Cohesion: 0.33
Nodes (5): components, $defs, operations, paths, webhooks

### Community 36 - "source.ts"
Cohesion: 0.19
Nodes (9): PaceCard(), EMPTY_STATE, QuackState, EVENT_TYPE, remoteSource(), quackSource, serverSnapshot(), useQuack() (+1 more)

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
Cohesion: 0.21
Nodes (14): glowOf(), KEYS, Known, localSource(), recompute(), read(), write(), Draft (+6 more)

### Community 41 - "DuckLane.tsx"
Cohesion: 0.14
Nodes (13): src_components_home_duck_lane_module, BALLOON, COLORS, DuckLane(), DuckLaneProps, FLAME, Flight, PARACHUTE (+5 more)

### Community 46 - "Closing.tsx"
Cohesion: 0.12
Nodes (14): ANGLER, Closing(), FLOAT, GROUND_WIDE, LAKE, src_components_home_closing_module, PALETTE, ROD_BENT (+6 more)

### Community 48 - "quack/contract.ts"
Cohesion: 0.19
Nodes (10): Level, ChancesCard(), examWord(), Props, ChanceFact, ExamPace, PaceLevel, ProgramChance (+2 more)

### Community 49 - "ChangesFeed.tsx"
Cohesion: 0.29
Nodes (7): ago(), ChangesFeed(), Props, Target, TARGET_LABEL, TONE_ICON, Signal

## Knowledge Gaps
- **283 isolated node(s):** `nextConfig`, `name`, `version`, `private`, `dev` (+278 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 354 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `react` connect `react` to `dashboardRules.ts`, `ChoiceApp.tsx`, `JourneySection.tsx`, `TopicWorkspace.tsx`, `HeroLandscape.tsx`, `Globe.tsx`, `PixelDuck.tsx`, `SkillGraph.tsx`, `prepData.ts`, `prepModel.ts`, `programs.ts`, `package.json`, `CalendarTab.tsx`, `SetsView.tsx`, `UserMenu.tsx`, `AuthGate.tsx`, `SetDetail.tsx`, `next`, `app/page.tsx`, `Sidebar.tsx`, `store.ts`, `LoginView.tsx`, `assistant.ts`, `source.ts`, `DuckLane.tsx`, `Closing.tsx`?**
  _High betweenness centrality (0.354) - this node is a cross-community bridge._
- **Why does `ChoiceApp()` connect `ChoiceApp` to `ChoiceApp.tsx`, `source.ts`, `prepModel.ts`, `AuthGate.tsx`?**
  _High betweenness centrality (0.021) - this node is a cross-community bridge._
- **Why does `PixelDuck()` connect `PixelDuck.tsx` to `LoginView.tsx`, `assistant.ts`, `JourneySection.tsx`, `HeroLandscape.tsx`, `react`, `DuckLane.tsx`, `Closing.tsx`, `AuthGate.tsx`, `app/page.tsx`, `Sidebar.tsx`, `store.ts`?**
  _High betweenness centrality (0.017) - this node is a cross-community bridge._
- **What connects `nextConfig`, `name`, `version` to the rest of the system?**
  _283 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `dashboardRules.ts` be split into smaller, more focused modules?**
  _Cohesion score 0.125 - nodes in this community are weakly interconnected._
- **Should `ChoiceApp` be split into smaller, more focused modules?**
  _Cohesion score 0.13756613756613756 - nodes in this community are weakly interconnected._
- **Should `JourneySection.tsx` be split into smaller, more focused modules?**
  _Cohesion score 0.06140350877192982 - nodes in this community are weakly interconnected._