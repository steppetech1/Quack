# H-008: инструмент (Excel, NORM.DIST) — контекст задачи, а не навык

| | |
|---|---|
| Статус | `in-progress` для правила в промпте, `proposed` для поля `context` |
| Дата | 2026-11-25 |
| Предложил | ассистент |
| Решение владельца | **Правило «инструмент — не навык»** вошло в ТЗ (4.2, промпт `builder@2`), принято неявно. **Поле `context` у `TESTS`** предложено при проверке ТЗ (20:27 UTC), не решено |
| Наблюдения | [2026-11-25-statistics-seminars](../observations/2026-11-25-statistics-seminars/README.md) |
| Спека | [4.2](../specs/2026-11-25-structure-building.md); [правки после проверки](../specs/2026-11-25-structure-building.md#правки-после-проверки-2026-11-25-2027-utc), пункт 1 |

## Утверждение

Способ решения («в Excel», «с SUMPRODUCT», «через NORM.DIST») не создаёт отдельный навык.

- **Задача размечается навыком темы.**
- **Инструмент записывается в необязательное поле `context` связи `TESTS`** и переходит в свидетельство. Это только предложено.

## Почему (опыт)

Навыки-варианты инструмента в предмете «statistics» ([raw/01](../observations/2026-11-25-statistics-seminars/raw/01-topics-and-skills.txt)):
- «build frequency table in Excel»;
- «compute grouped mean using SUMPRODUCT»;
- «compute mean, median and mode in Excel»;
- «compute left, right and between-areas with NORM.DIST»;
- «solve Poisson problems in Excel and compare binomial approximation by Poisson».

То же у навыков, придуманных разметкой задач ([raw/20](../observations/2026-11-25-statistics-seminars/raw/20-new-skill-names.txt)): «Writing normal-distribution probabilities in symbols and as Excel NORM.DIST formulas».

## Как проверяем

- **Метрика:** число навыков, в имени которых есть инструмент: Excel, SUMPRODUCT, NORM., «in Excel». Запрос по `skill.created` с `ilike`.
- **Подтверждение:** 0 на тех же семинарах.
- **Опровержение:** ученику и учителю нужно отдельно видеть «умеет руками, но не в Excel» — запрос владельца или учителя. Без поля `context` это не различить.

## История

- 2026-11-25 — правило `in-progress` (ТЗ, 20:26 UTC), поле `proposed` (20:27 UTC).
