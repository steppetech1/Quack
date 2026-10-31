# Mock data and locales

The app has no backend, so all content (topics, notes, sessions, cards, canvases, chat replies, seed skills)
lives here. Interface strings are NOT mock data: they live in `lib/i18n/en.ts` and `lib/i18n/ru.ts`.

## Layout

- `types.ts` – shared types, the same for every locale.
- `data.ts` – `MockData`: the full set of locale-dependent content one locale module must provide.
- `ru/*.ts` – the Russian content (the original data).
- `ru-data.ts` – assembles `ru/*` into a `MockData`.
- `en/index.ts` – the English content. Currently `{}`, so English falls back to Russian field by field.
- `index.ts` – `getMocks(locale)`: the single seam. It returns `{ ...ru, ...en }` for `en` and `ru` for `ru`,
  plus helpers built on top (`canvasesOf`, `getCanvas`, `getTopicDetails`).
- `skill-draft.ts` – the fake "skill assistant". It already has keyword packs for both `ru` and `en`
  (bindings, templates, refinement phrases); extend the `en` pack there if you want more templates.
- `MOCK_TODAY` (in `index.ts`) is the fake "today" for all relative dates. It is not locale-dependent.

## How consumers get data

- Client components: `const { domains, getCanvas } = useMocks()` from `@/lib/i18n/provider`.
- Server code (metadata, `notFound()` checks): `getMocks(await getLocale())` from `@/lib/i18n/server`.
- `generateStaticParams` uses `getMocks("en")`; ids must be identical across locales.

## Adding the English data

1. Create files next to `ru/` in `en/`, mirroring `ru/`: `domains.ts`, `topic-texts.ts`, `topic-details.ts`,
   `canvases.ts`, `session.ts`, `canvas-chat.ts`, `skills.ts`. Copy the Russian file and translate it.
   Types come from `../types`, so the compiler tells you what is missing.
2. Keep every `id`, `href`, `topicId`, `requires`, `hubId`, `sessionId`, `afterLine` and similar reference identical
   to the Russian data. Only translate the human-readable text. Keep soft hyphens (`­`) out of English titles.
3. `domains.ts` joins `topicTexts` into each topic, so import the English `topic-texts` there.
   `titleDat` is the dative form used in the sentence "ask something about …": for English write the plain phrase,
   e.g. `"calculus"`.
4. Export the pieces from `en/index.ts` under the `MockData` keys, for example:

   ```ts
   import { domains } from "./domains";
   export const en: Partial<MockData> = { domains /* , canvases, radiusSession, ... */ };
   ```

   You can add pieces one at a time. Whatever is missing falls back to Russian.
   Note that `domains`, `canvases` and `topicDetails` reference each other by id and by title
   (`topic-page.tsx` matches a canvas to a topic detail by `title`), so translate those together.
5. Dates in the data are ISO strings, nothing to translate. Math is LaTeX inside `$...$`, keep it as is.

## Known leftovers for phase 2

- `Hub.deadline.label` ("Коллоквиум по рядам") is used in sentences; `hub-page.tsx` takes its first word.
  Check the English wording reads well there.
- Mock user prefs and skills seeded for the current locale are replaced by whatever the user saved in `localStorage`
  (`quack.settings`, `quack.skills.v1`), so switching language will not translate already saved skills.
