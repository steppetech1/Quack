# INDEX

Правила и статусы — в [README.md](README.md). Время в UTC.

## Решения (H)

| ID | Решение | Статус | Кто решил | Наблюдения | Спека |
|---|---|---|---|---|---|
| [H-001](hypotheses/H-001-core-skill-per-topic.md) | У каждой темы ровно один центральный навык с рождения | `in-progress` | неявно, через ТЗ | [2026-11-25](observations/2026-11-25-statistics-seminars/README.md) | [4.1](specs/2026-11-25-structure-building.md) |
| [H-002](hypotheses/H-002-weak-model-knowledge-edges.md) | Связи «нужно до» по знанию модели: 0.5, без косвенных свидетельств, тусклее на графе | `in-progress` | владелец, явно | [2026-11-25](observations/2026-11-25-statistics-seminars/README.md) | [4.2, 4.4](specs/2026-11-25-structure-building.md) |
| [H-003](hypotheses/H-003-task-markup-closed-choice.md) | Разметка задач выбирает из навыков темы, `new_skill` с причиной — запасной путь | `in-progress` | владелец, явно | [2026-11-25](observations/2026-11-25-statistics-seminars/README.md) | [4.6, 4.8](specs/2026-11-25-structure-building.md) |
| [H-004](hypotheses/H-004-single-structure-writer.md) | Один писатель структуры на предмет; темы не зависят от источника и порядка файлов | `in-progress` | принцип явно, механизм неявно | [2026-11-25](observations/2026-11-25-statistics-seminars/README.md) | [4.2, 4.3](specs/2026-11-25-structure-building.md) |
| [H-005](hypotheses/H-005-no-material-hubs.md) | Хабы из материалов не создаются; силлабус и просьба остаются | `in-progress` | владелец, явно | [2026-11-25](observations/2026-11-25-statistics-seminars/README.md) | [4.7](specs/2026-11-25-structure-building.md) |
| [H-006](hypotheses/H-006-focus-topic-for-new-skills.md) | Новый навык сессии рождается из работы и встаёт в тему фокуса | `in-progress` | принцип явно, механизм неявно | данных по сессиям нет | [4.5](specs/2026-11-25-structure-building.md) |
| [H-007](hypotheses/H-007-session-end-cleanup.md) | После сессии навыки без работы сливаются в центральный | `proposed` | не решено | [2026-11-25](observations/2026-11-25-statistics-seminars/README.md) | правки, п. 2 |
| [H-008](hypotheses/H-008-tool-is-task-context.md) | Инструмент (Excel) — контекст задачи, не навык | правило `in-progress`, поле `proposed` | правило неявно, поле не решено | [2026-11-25](observations/2026-11-25-statistics-seminars/README.md) | 4.2; правки, п. 1 |
| [H-009](hypotheses/H-009-skill-definition-not-chunk-text.md) | У навыка определение и образец; вектор не по сырому тексту | `in-progress` | неявно, через ТЗ | [2026-11-25](observations/2026-11-25-statistics-seminars/README.md) | [4.1](specs/2026-11-25-structure-building.md) |

## Отвергнутое (R)

| ID | Идея | Статус | Кто отверг | Что взяли взамен |
|---|---|---|---|---|
| [R-001](hypotheses/R-001-elo-instead-of-skill-fold.md) | Одно число Эло вместо свёртки состояния навыка | `rejected` | владелец | свёртка по трём осям не меняется |
| [R-002](hypotheses/R-002-course-order-edges.md) | Связи из порядка курса и нумерации файлов | `rejected` | владелец | H-002, H-004 |
| [R-003](hypotheses/R-003-pure-lazy-skills.md) | Чисто ленивые навыки, тема создаётся пустой | `rejected` | владелец, ассистент, критик | H-001 |
| [R-004](hypotheses/R-004-fixed-skills-per-topic.md) | Заранее заданное число навыков на тему (1–6, 1–4) | `rejected` | владелец | H-001 |
| [R-005](hypotheses/R-005-stored-topic-edges.md) | Хранимые связи тем отдельно от связей навыков | `rejected` | критик; снято с H-001 | H-002 |
| [R-006](hypotheses/R-006-topic-as-root-skill-sole-basis.md) | «Тема — крупный навык» как единственная основа | `rejected`, переработано | владелец | H-001 |
| [R-007](hypotheses/R-007-topic-size-by-lecture-or-session.md) | Размер темы через лекцию или сессию | `rejected` | владелец | критерий «одно центральное умение» (H-001) |

## Наблюдения

| Дата | Что | Папка |
|---|---|---|
| 2026-11-25 | 7 листов семинаров по статистике: 0 связей, 2 пустые темы-двойника, 124 навыка на 14 тем (90 из материала + 34 от разметки задач), хаб на каждый файл | [statistics-seminars](observations/2026-11-25-statistics-seminars/README.md) |

## Спеки

| Дата | Что | Реализует | Исполнитель |
|---|---|---|---|
| 2026-11-25 | [Построение структуры предмета](specs/2026-11-25-structure-building.md) | H-001…H-006, H-008 (правило), H-009 | Antigravity; правки после проверки, возможно, не получены |

## Ждёт проверки

- **Side-by-side на тех же 7 семинарах** на новой версии ([results/](results/README.md)). Учесть смену модели Meta → OpenAI.
- **Эталонная карта связей тем от владельца** — нужна для точности H-002.
- **Наблюдение по сессиям без файлов** — нужно для H-006 и H-007.
