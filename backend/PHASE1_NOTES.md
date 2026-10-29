# Фаза 1: принятые решения и вопросы к команде

Работа выполнена автономно (по просьбе владельца), поэтому ниже — всё, что пришлось решить
самостоятельно, и все найденные расхождения документов. Каждый пункт нужно подтвердить или
поправить; числа — стартовые значения в `quack/config.py` (`FoldParams`, `Settings`).

## 1. Конфликты документов (выбор сделан в пользу Спецификации и ТЗ)

| # | Тема | Спецификация / ТЗ | Т-§ / Д-§ | Что сделано |
| :- | :--- | :--- | :--- | :--- |
| 1 | Модель навыка | `pMastery`, `confidence`, `distinctFamilies`, `stability`, `nextReviewAt`, статус `mastered` (spec §4.3, ТЗ) | Т-§8: 6 уровней `l1…l6`, статусы proven/failed/…, FSRS через py-fsrs | Свёртка по полям спецификации; внутри — правила Т-§7.2–7.4, §8.2 (ярусы, ворота T1/T2, λ=0.6, перевес 2×, пороги 0.8) и кривая FSRS (R(S)=0.9). py-fsrs не подключён (нет в списке зависимостей spec §1.1) |
| 2 | Коды `displayState` | `locked, available, in_progress, solid, rusty` (spec §4.3, ТЗ) | Т-§22.2: `confused_with, persistent_error, struggling, …` | По спецификации |
| 3 | Нумерация разделов | spec ссылается на «Т-§7–9 формулы», «Т-§13 очередь», «Т-§20 константы», «Т-§21 инварианты» | В Т-документе: §7 свидетельства, §8 навык, §9 заблуждения, §13 очередь, §21.3 параметры; формул pMastery/потолков нет нигде | Формулы пришлось вывести (раздел 2) |
| 4 | `rebuild_max_tx_ops` | 5000 (spec §1.3) | 200 000 (Т-§21.3) | 5000 |
| 5 | Миграции и драйвер | Alembic + asyncpg (spec §1.1, ТЗ) | dbmate + psycopg 3 (Д-§5.1, Д-§6.2) | Alembic + asyncpg |
| 6 | Опечатки регистра в spec | `topic.Status_changed`, `session.Checkpointed`, `artifact.cheatsheet_Updated`, `ops.deletion_Tombstones`, `llm_Semaphore_limit`, `daily_token_Limit_per_learner`, `'Completed'` | Т-§0.4: snake_case | Везде нижний регистр |
| 7 | Каталог событий | 36 типов spec §5.2 | Т-§2.2 другие имена (`turn.committed`, `verdict.recorded`, …) | По спецификации |
| 8 | Пересчёт ссылок Д-§ | spec: «Д-§3.2 переменные», «Д-§5.3 health» | В Д-документе §3.2 — сети Docker, §5.3 — секреты | Имена переменных — по spec §1.3 |

## 2. Формулы, которых нет в документах (решено самостоятельно)

Свёртка навыка (`quack/graph/folds.py`), действующие свидетельства, сортировка `(at, seq)`:

- вес `w = base[tier] × independence × (0.6 + 0.4·tested_depth)`, `base = 1.0/0.8/0.4/0.15` (Т-§7.4);
- ворота: в `pMastery` входят только T1/T2 (Т-§7.3); `w_eff = w·0.6^k` (Т-§8.2);
  `S = Σ w_eff·score`, `F = Σ w_eff·(1−score)`, `W = Σ w`;
- `confidence = W/(W+0.5)`, `pMastery = S/(S+F) × confidence`;
- потолок без семейного разнообразия: `distinctFamilies < 2 ⇒ pMastery ≤ 0.6`;
- без T1/T2: только показы (`0.1·depth`, потолок 0.3) + положительные T3/T4 (потолок 0.4)
  + априор диагностики (`prior·confidence`, потолок 0.5); отрицательные T3/T4 состояние не
  ухудшают (закон 4);
- `mastered`: `pMastery ≥ 0.7`, ≥ 2 семейства, `S ≥ 0.8`, `S ≥ 2F`, последний успех после
  последнего провала; `struggling`: `F ≥ 0.8`, `F ≥ 2S`, провал последним;
- удержание: `R(t) = (1 + 19/81·t/S)^−0.5`; первая успешная попытка T1/T2 — `S = 1 сут`;
  успех «через ночь» (> 20 ч от прошлой практики/показа) — `S × (1 + 1.5·score·independence)`;
  провал через ночь — `S × 0.5` (не ниже 0.5); `nextReviewAt = lastReviewedAt + S`
  (момент R = 0.9); `decayRate = 1 − R(1 сут)`;
- «эффективное мастерство» `pMastery × R(now − lastReviewedAt)` считается при чтении
  (`effective_mastery`), проектор время не читает (Т-§2.5).

Заблуждения (Т-§9.2): `suspected` → `confirmed` (≥ 2 проявления из разных источников, одно
из них T1/T2) → `watching` (2 «чистых» успеха T1/T2 разных семейств после последнего
проявления: score ≥ 0.8, independence ≥ 0.8) → `fixed` (ещё сопротивление ≥ 3 сут спустя);
`dismissed` для неподтверждённых; `resolved` по `misconception.resolved`; новое проявление
возвращает в `confirmed`/`suspected`. Сопротивление засчитывается по связанному навыку
(ТЗ), а не только по задаче с ловушкой (Т-§9.1).

Темы: вес навыка `1 + 0.5·hub`, `hub` = число рёбер `DEPENDS_ON`, **входящих** в навык
(сколько навыков от него зависят; в ТЗ сказано «исходящих» — направление ребра
`(skill)-[:DEPENDS_ON]->(dependency)`, проверить смысл); `coverage` — доля навыков со
свидетельствами; `solid`: mastery ≥ 0.7, coverage ≥ 0.8, нет `struggling`; `rusty`: был бы
`solid`, но `as_of > nearestReviewAt`; `locked`: нет активности и есть активная тема-предпосылка
с mastery < 0.6; иначе `available`/`in_progress`. `as_of` в проекторе = последнее время
практики/показа навыков темы (детерминированно); интерфейс подставляет текущее время.

Ярусы источников (Т-§7.2): `task.evaluated` compute → T1, llm/hybrid → T2;
`diagnostic.question_answered` → T2 (класс проверки в событии не указан);
`turn.extracted` → T3.

## 3. Прочие решения по контрактам

- Не описанные в spec вложенные модели (`TopicDraft`, `SkillDraft`, `PrereqEdge`,
  `SkillDepEdge`, `MisconceptionDraft`, `TopicPatch`, `SkillPatch`, `MisconceptionEvalHit`):
  поля в `quack/events/payloads.py`. `MisconceptionEvalHit = {skill_id, name, misconception_id?}`.
- Индекс inline-навыка `skill:{idx}` — порядковый номер SkillRef в событии в порядке
  payload (в `turn.extracted` сквозной по observations, затем misconceptions), иначе ids
  двух списков совпадали бы. Проявления заблуждений — узлы Evidence с `kind = manifestation`
  и id `manifest:{j}` (экстрактор) / `hit:{j}` (оценка задачи).
- Направления рёбер: `(Domain)-[:HAS_TOPIC]->(Topic)`, `(prereq)-[:PREREQ_OF]->(topic)`,
  `(Skill)-[:PART_OF]->(Topic)`, `(Skill)-[:DEPENDS_ON]->(dependency)`,
  `(Misconception)-[:ABOUT_SKILL]->(Skill)`, `(Task)-[:TESTS]->(Skill)`,
  `(Evidence)-[:EVIDENCE_FOR]->(Skill)`, `(Exposure)-[:EXPOSED_TO]->(Skill)`,
  `(Evidence)-[:OBSERVED_MISCONCEPTION]->(Misconception)`,
  `(Task)-[:TRIGGERS_MISCONCEPTION]->(Misconception)`, `(Topic|Domain)-[:HAS_ARTIFACT]->(Artifact)`,
  `(Learner)-[:OWNS]->(Domain)`.
- Времена в Neo4j — int64 мс, 0 = «не было» (Т-§3.4.4); `null` не пишется никогда.
- При слиянии вычисляемые свойства исходного узла сбрасываются в пустые значения — иначе
  результат зависел бы от размера пачки.
- Добавлены таблицы `ops.projector_offsets` (Т-§2.4) и `ops.event_types_seen` (Т-§2.3);
  в spec §3.3 их нет.
- Подпись `build_session_overlay` дополнена `session` и `params`, `read_consistent_slice` —
  `deps` (пулы); `append_events` — `redis`. Без этого функции не могут работать.
- Протухшие задачи (нет пульса 60 с) возвращаются в очередь сразу, без backoff; backoff
  `min(300, 2^attempt·5)` — для ошибок обработчика. Повтор склеиваемой задачи при наличии
  более новой в очереди → `cancelled`.
- Шаблоны промптов: `{{ name }}`; хеши — `quack/prompts/manifest.json`.
- Добавлена переменная `LLM_API_BASE_URL` (в spec §1.3 нет адреса провайдера).

## 4. Пункты «Проверить» (не подтверждены, в коде заменяемы)

- HTTP-провайдер LLM (`quack/llm/provider.py`) рассчитан на OpenAI-совместимый
  `/chat/completions` + `response_format: json_schema`; Т-§11.1/§11.8 требуют спайка
  (Responses API, structured output Spark/Wonder). Имена моделей `muse-wonder`/`muse-spark` —
  условные.
- Тесты гонялись на Neo4j `2026.06-community` (в Д-§5.1 — 2026.10.x).

## 5. Правки тестов после Этапа II

- `test_memory_mutation_semantics`: ожидание поправлено — узел содержит свойство `id`, как
  требует буквальный `MERGE (n:Label {id: $id})` (spec §4.2). Утверждения не ослаблялись.
- Косметика под ruff/pyright: `pass` → вызов в `test_logging_context_and_secret_masking`,
  неиспользуемая переменная в `test_chunked_rebuild_respects_presence`.
- `tests/test_worker_and_ops.py` (воркер, rebuild, replay-dlq, purge, раннер задач) написан
  в Этапе III, но до реализации этих модулей — по их заглушкам; в списке ТЗ его не было.
