"""ARQ job registry: which queue each job runs on and how long it may take.

Очереди — tech-stack §2.5: `interactive` для того, чего ученик ждёт прямо
сейчас, `bulk` для фоновой подготовки. Таймаут очереди (`job_timeout`
воркера) — потолок для коротких задач (`ping`); задача, которая ходит в LLM,
объявляет свой собственный через `arq.worker.func(timeout=...)`.

Фаза 3 (docs/tz/phase3-agents.md §3.12, F19): в `interactive` —
`observe_chat` (`OBSERVER_JOB_TIMEOUT_S`) и `canonize_misconception`
(`CANON_JOB_TIMEOUT_S`).

Фаза 4 (§1.2): в `interactive` добавляется только `set_summary` — ученик
ждёт отчёт в Обзоре сразу после закрытия сета. Всё остальное фоновое и
живёт в `bulk`. `keep_result` у всех фазы 4 — `JOB_KEEP_RESULT_S` (60 с):
ARQ держит `job_id` занятым, пока хранит результат, и часовое значение по
умолчанию молча съедало бы повторные постановки того же id (§1.3).

Задачи B2 (`app.agents.jobs`) регистрируются, только если слой B2 есть в
этой копии репозитория — воркер B3 поднимается и без него.
"""

from typing import Any

from arq.worker import Function, func

from app.config import settings
from app.loader import optional_layer as _optional_layer
from app.workers.durable import durable


async def ping(ctx: dict[str, Any], request_id: str) -> str:
    return "pong"


_jobs = _optional_layer("app.agents.jobs")
_infra = _optional_layer("app.workers.jobs_infra")

INTERACTIVE: list[Any] = [ping]
BULK: list[Any] = [ping]

_KEEP = settings.JOB_KEEP_RESULT_S

# (job, queue, timeout, keep_result, max_tries) — задачи слоя B2.
_JOBS: tuple[tuple[str, str, float, float | None, int], ...] = (
    ("observe_chat", "interactive", settings.OBSERVER_JOB_TIMEOUT_S, 0, 3),
    ("canonize_misconception", "interactive", settings.CANON_JOB_TIMEOUT_S, None, 3),
    # Phase 4.
    ("set_summary", "interactive", 30, _KEEP, 3),
    # A guideline and an explanation per topic, one model call after another.
    ("pregenerate_set", "bulk", 240, _KEEP, 3),
    ("soft_match", "bulk", 90, _KEEP, 3),
    ("extract_program", "bulk", 60, _KEEP, 2),
    ("search_programs", "bulk", 30, _KEEP, 2),
    ("realism_texts", "bulk", 180, _KEEP, 2),
    ("compare_text", "bulk", 60, _KEEP, 2),
)

# Инфраструктурные задачи фазы 4 — обёртки B3 над `apply.*`.
_INFRA_JOBS: tuple[tuple[str, str, float, float | None, int], ...] = (
    ("daily_aggregates", "bulk", 90, _KEEP, 3),
    ("recommendations_batch", "bulk", 90, _KEEP, 3),
    ("outbox_replay", "bulk", 30, 0, 1),
    # Фаза 5 (§13.3): догон графовой проекции после восстановления Neo4j.
    ("recover_graph_events", "bulk", 90, _KEEP, 3),
)


def _registered(
    module: Any, name: str, timeout: float, keep_result: float | None, max_tries: int
) -> Function | None:
    """One job with its own timeout, or None when the layer is absent."""
    if module is None:
        return None
    coroutine = getattr(module, name, None)
    if coroutine is None:
        return None
    return func(
        # Фаза 5 (§13.1): долговечный жизненный цикл задачи живёт в Postgres,
        # а не в счётчике попыток ARQ. Обёртка снимает транспортные ключи,
        # доменная сигнатура задачи не меняется.
        durable(coroutine, name=name, max_tries=max_tries),
        name=name,
        timeout=min(timeout, settings.JOB_TIMEOUT_MAX_S),
        keep_result=keep_result,
        max_tries=max_tries,
    )


_INFRA_NAMES = {name for name, *_ in _INFRA_JOBS}

for _name, _queue, _timeout, _keep, _tries in (*_JOBS, *_INFRA_JOBS):
    _module = _infra if _name in _INFRA_NAMES else _jobs
    _entry = _registered(_module, _name, _timeout, _keep, _tries)
    if _entry is not None:
        (INTERACTIVE if _queue == "interactive" else BULK).append(_entry)

_ALL = (*_JOBS, *_INFRA_JOBS)
JOB_TIMEOUTS: dict[str, float] = {
    name: min(timeout, settings.JOB_TIMEOUT_MAX_S) for name, _q, timeout, _k, _t in _ALL
}
JOB_QUEUES: dict[str, str] = {name: queue for name, queue, _t, _k, _tr in _ALL}
JOB_MAX_TRIES: dict[str, int] = {name: tries for name, _q, _t, _k, tries in _ALL}
