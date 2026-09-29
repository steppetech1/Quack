"""Phase-4 background jobs (§3–§7).

Every job follows the §2.1 frame: take the lock, read the inputs, write the
`generating` row and commit, call the model *outside* the transaction, write
the result and commit, queue the children. None of them writes to the graph,
none of them dispatches an event, and re-running any of them with the same
arguments is safe.

They live in their own module and are re-exported from `agents.jobs` so the
phase-3 file stays readable; the registry only knows the names.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import openai
import structlog
from arq import Retry

from app import keys
from app.agents import texts as agent_texts
from app.config import settings
from app.db.repo import profiles as profiles_repo
from app.db.repo import programs as programs_repo
from app.db.repo import sets as sets_repo
from app.db.repo import soft_matches as soft_repo
from app.db.repo import summaries as summaries_repo
from app.db.repo import texts as texts_repo
from app.errors import LLMUnavailable, SearchUnavailable, ValidationFailed
from app.prompt_versions import version_of
from app.schemas.programs import ExtractionMeta, SearchStatusOut
from app.search import client as search_client
from app.search import extract as extractor
from app.workers.jobs_infra import record_failure, rule_deps, uuid_of

_logger = structlog.get_logger(__name__)

_TEXT_JOB_TTL_S = 600
_LOCK_TTL_S = 260
_SOFT_MATCH_PER_PROGRAM_S = 20
# `set_summary` живёт в `interactive` с job_timeout 30 с, а LLM_TIMEOUT_BULK_S
# — 90: без своего потолка вызов пережил бы задачу (§16 item 10).
_SUMMARY_CALL_S = 25
_MAX_TEXT_ATTEMPTS = 3
_SEARCH_STATUS_TTL_S = 3600


def _model() -> str:
    return settings.MODEL_BULK


def _is_rate_limit(exc: BaseException) -> bool:
    return isinstance(exc, LLMUnavailable) and "rate limit" in str(exc)


_REJECTED = "llm credentials rejected"


def _is_invalid_output(exc: BaseException) -> bool:
    """A failure of this text, not a wait: retrying it changes nothing."""
    return isinstance(exc, LLMUnavailable) and (
        "structured output failed" in str(exc) or _REJECTED in str(exc)
    )


def _llm_retry(exc: LLMUnavailable) -> Retry:
    """`down` and 429 are waits, not failures of this particular text (§11)."""
    return Retry(defer=30 if _is_rate_limit(exc) else settings.LLM_RETRY_DEFER_S)


async def _lock(redis: Any, name: str, ttl: int = _LOCK_TTL_S) -> bool:
    try:
        return bool(await redis.set(keys.lock(name), "1", nx=True, ex=ttl))
    except Exception:  # noqa: BLE001 — Redis down: unique indexes still guard
        _logger.warning("lock_redis_unavailable", key=name)
        return True


async def _unlock(redis: Any, name: str) -> None:
    try:
        await redis.delete(keys.lock(name))
    except Exception:  # noqa: BLE001
        return


async def _mark_job(redis: Any, kind: str, digest: str, job_id: str | None) -> None:
    """Tell the reader a job is alive for this text (§3.7)."""
    try:
        await redis.set(keys.text_job(kind, digest), job_id or "1", ex=_TEXT_JOB_TTL_S)
    except Exception:  # noqa: BLE001
        return


# --- pregenerate_set (§3) ---


async def pregenerate_set(
    ctx: dict, request_id: str, set_id: str | UUID, student_id: str | UUID
) -> None:
    """Guidelines and explanations for a whole set, first topic first.

    Ordered deliberately: if the 90-second budget runs out, what is ready is
    what the student opens first. The unfinished rows stay `generating` and
    the same `job_id` brings the job back.
    """
    structlog.contextvars.bind_contextvars(request_id=request_id)

    set_uuid, student = uuid_of(set_id), uuid_of(student_id)
    if not await _lock(ctx["redis"], f"pregen:{set_uuid}"):
        _logger.info("pregenerate_set", set_id=str(set_uuid), skipped="locked")
        return
    failed: list[str] = []
    try:
        async with ctx["sessionmaker"]() as session:
            set_out = await sets_repo.get_set(session, student, set_uuid)
        if set_out is None:
            _logger.info("job_skipped", job="pregenerate_set", set_id=str(set_uuid))
            return

        plan = _text_plan(set_out)
        for skill_id, kind in plan:
            try:
                await _one_text(ctx, student, set_uuid, skill_id, kind)
            except LLMUnavailable as exc:
                if _is_invalid_output(exc):
                    # Одна плохая генерация не должна ронять весь сет.
                    failed.append(f"{kind}:{skill_id}")
                    continue
                raise _llm_retry(exc) from exc
            except agent_texts.PostcheckFailed:
                failed.append(f"{kind}:{skill_id}")
    finally:
        await _unlock(ctx["redis"], f"pregen:{set_uuid}")

    if failed:
        if int(ctx.get("job_try", 1) or 1) >= 3:
            await record_failure(
                ctx,
                "pregenerate_set",
                student,
                {"set_id": str(set_uuid), "failed": failed},
                "invalid_output",
            )
            return
        raise RuntimeError(f"pregenerate_set failed for {failed}")


def _text_plan(set_out: Any) -> list[tuple[str, str]]:
    """Guideline of the first topic, its explanation, then the rest (§3.1)."""
    from app.apply.texts import TEXT_KINDS

    topics = sorted(set_out.topics, key=lambda topic: topic.position)
    return [
        (topic.skill_id, kind)
        for topic in topics
        for kind in TEXT_KINDS.get(topic.kind, ())
    ]


async def _one_text(
    ctx: dict, student_id: UUID, set_id: UUID, skill_id: str, kind: str
) -> None:
    from app.apply import texts as apply_texts

    version, model = version_of(kind), _model()
    owner = apply_texts.owner_of(kind, student_id)
    async with ctx["sessionmaker"]() as session:
        digest, inputs = await apply_texts.current_hash(
            session, rule_deps(ctx), student_id, set_id, skill_id, kind, version, model
        )
        if digest is None or inputs is None:
            # Состояний нет — генерировать по пустым входам было бы ложью.
            raise _GraphMissing(skill_id)
        row = await texts_repo.get_generated(session, kind, digest)
        if row is not None and row.status == "ready":
            return
        if (
            row is not None
            and row.status == "failed"
            and (row.attempts or 0) >= _MAX_TEXT_ATTEMPTS
        ):
            _logger.info("text_skipped", kind=kind, subject=skill_id, reason="attempts")
            return
        await texts_repo.mark(
            session,
            kind,
            digest,
            "generating",
            student_id=owner,
            subject=skill_id,
            set_id=set_id,
            model=model,
            prompt_version=version,
        )
        await session.commit()
    await _mark_job(ctx["redis"], kind, digest, ctx.get("job_id"))

    try:
        if kind == "guideline":
            text = await agent_texts.generate_guideline(ctx["llm"], inputs)
        else:
            text = await agent_texts.generate_explanation(ctx["llm"], inputs)
    except agent_texts.PostcheckFailed as exc:
        async with ctx["sessionmaker"]() as session:
            await texts_repo.mark(
                session, kind, digest, "failed", error=exc.reason, subject=skill_id
            )
            await session.commit()
        raise
    except LLMUnavailable as exc:
        if _is_invalid_output(exc):
            async with ctx["sessionmaker"]() as session:
                await texts_repo.mark(
                    session,
                    kind,
                    digest,
                    "failed",
                    error="invalid_output",
                    subject=skill_id,
                )
                await session.commit()
        raise
    except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
        # A refused key does not get better with a retry: without this the row
        # stayed `generating` for good, and the screen waited for nothing.
        _logger.error("llm_credentials_rejected", kind=kind, status=exc.status_code)
        async with ctx["sessionmaker"]() as session:
            await texts_repo.mark(
                session,
                kind,
                digest,
                "failed",
                error="llm_auth",
                subject=skill_id,
            )
            await session.commit()
        raise LLMUnavailable(_REJECTED) from exc

    async with ctx["sessionmaker"]() as session:
        await texts_repo.mark(
            session,
            kind,
            digest,
            "ready",
            text=text,
            student_id=owner,
            subject=skill_id,
            set_id=set_id,
            model=model,
            prompt_version=version,
        )
        await session.commit()
    _logger.info("text_ready", kind=kind, subject=skill_id, set_id=str(set_id))


class _GraphMissing(Exception):
    """Inputs could not be read — the text would be a guess (§2.3)."""


# --- set_summary (§4) ---


async def set_summary(
    ctx: dict, request_id: str, set_id: str | UUID, student_id: str | UUID
) -> None:
    """One call over already-computed facts; the numbers are checked."""
    structlog.contextvars.bind_contextvars(request_id=request_id)
    from app.sets.report import stats_words

    set_uuid, student = uuid_of(set_id), uuid_of(student_id)
    async with ctx["sessionmaker"]() as session:
        row = await summaries_repo.get(session, set_uuid)
    if row is None:
        _logger.info("job_skipped", job="set_summary", set_id=str(set_uuid))
        return
    if row.status == "ready" and row.text:
        return

    version = version_of("summary")
    try:
        text = await asyncio.wait_for(
            agent_texts.generate_summary(ctx["llm"], row.stats, stats_words(row.stats)),
            timeout=_SUMMARY_CALL_S,
        )
    except agent_texts.PostcheckFailed as exc:
        async with ctx["sessionmaker"]() as session:
            await summaries_repo.set_text(session, set_uuid, None, version, "failed")
            await session.commit()
        _logger.warning("set_summary_failed", set_id=str(set_uuid), reason=exc.reason)
        return
    except TimeoutError:
        raise Retry(defer=30) from None
    except LLMUnavailable as exc:
        if _is_invalid_output(exc):
            async with ctx["sessionmaker"]() as session:
                await summaries_repo.set_text(
                    session, set_uuid, None, version, "failed"
                )
                await session.commit()
            await record_failure(
                ctx, "set_summary", student, {"set_id": str(set_uuid)}, "invalid_output"
            )
            return
        raise _llm_retry(exc) from exc

    async with ctx["sessionmaker"]() as session:
        await summaries_repo.set_text(session, set_uuid, text, version, "ready")
        await session.commit()
    _logger.info("set_summary_ready", set_id=str(set_uuid))


# --- soft_match (§5) ---


async def soft_match(
    ctx: dict, request_id: str, student_id: str | UUID, program_ids: list[str]
) -> None:
    """Score the student's trait summary against each candidate program.

    One program at a time, each with its own commit: a partial result is
    already useful, and the next run skips what is done.
    """
    structlog.contextvars.bind_contextvars(request_id=request_id)
    from app.apply import soft as apply_soft

    student = uuid_of(student_id)
    deps = rule_deps(ctx)
    version, model = version_of("soft_match"), _model()

    async with ctx["sessionmaker"]() as session:
        profile = await profiles_repo.get_profile(session, student)
        summary = (profile.traits.summary or "").strip()
        if not summary:
            _logger.info("soft_match", student_id=str(student), skipped="no_summary")
            return
        digest = apply_soft.summary_hash(summary)
        programs = await apply_soft.candidates(session, deps, student, program_ids)
        existing = await soft_repo.get_many(
            session, digest, [program.id for program in programs], version
        )
    try:
        await ctx["redis"].set(keys.soft_pending(str(student)), digest, ex=300)
    except Exception:  # noqa: BLE001
        pass

    done = 0
    try:
        for program in programs:
            row = existing.get(program.id)
            if row is not None and not row.stale:
                continue
            await _one_soft_match(ctx, digest, profile, program, version, model)
            done += 1
    except LLMUnavailable as exc:
        raise _llm_retry(exc) from exc
    finally:
        try:
            await ctx["redis"].delete(keys.soft_pending(str(student)))
        except Exception:  # noqa: BLE001
            pass
    _logger.info("soft_match", student_id=str(student), scored=done)


async def _one_soft_match(
    ctx: dict,
    digest: str,
    profile: Any,
    program: Any,
    version: str,
    model: str,
) -> None:
    from app.apply import soft as apply_soft

    if not (program.environment_text or "").strip():
        # Нечего сопоставлять: нейтральный вклад без текста, без вызова.
        await _put_soft(
            ctx, digest, program.id, version, 0.5, None, model="no_environment"
        )
        return
    inputs = apply_soft.build_inputs(profile, program)
    try:
        result = await asyncio.wait_for(
            agent_texts.generate_soft_match(ctx["llm"], inputs),
            timeout=_SOFT_MATCH_PER_PROGRAM_S,
        )
    except (agent_texts.PostcheckFailed, TimeoutError):
        # Нейтральная строка вместо пустоты: иначе задача крутилась бы по
        # одной и той же программе на каждом триггере (§5.4).
        await _put_soft(ctx, digest, program.id, version, 0.5, None, model="failed")
        return
    except LLMUnavailable as exc:
        if _is_invalid_output(exc):
            await _put_soft(ctx, digest, program.id, version, 0.5, None, model="failed")
            return
        raise
    await _put_soft(
        ctx,
        digest,
        program.id,
        version,
        result.score,
        result.fit_text,
        caveat=result.caveat,
        matched_traits=result.matched_traits,
        confidence=result.confidence,
        model=model,
    )


async def _put_soft(
    ctx: dict,
    digest: str,
    program_id: str,
    version: str,
    score: float | None,
    text: str | None,
    *,
    caveat: str | None = None,
    matched_traits: list[str] | None = None,
    confidence: str | None = None,
    model: str,
) -> None:
    async with ctx["sessionmaker"]() as session:
        await soft_repo.put(
            session,
            digest,
            program_id,
            version,
            score=score,
            text=text,
            caveat=caveat,
            matched_traits=matched_traits,
            confidence=confidence,
            model=model,
        )
        await session.commit()


# --- search_programs and extract_program (§6) ---


async def search_programs(
    ctx: dict,
    request_id: str,
    query: str,
    student_id: str | UUID | None = None,
    warm: bool = False,
) -> None:
    """Search, filter the URLs, and queue one extraction per surviving page."""
    structlog.contextvars.bind_contextvars(request_id=request_id)
    student = uuid_of(student_id) if student_id else None
    search_id = ctx.get("job_id") or f"search:{query}"
    await _set_search_status(ctx, search_id, "running")
    try:
        hits = await search_client.search_programs_limited(
            ctx["redis"], query, n=settings.knowledge.extract_max_urls_per_search
        )
    except SearchUnavailable as exc:
        if int(ctx.get("job_try", 1) or 1) < 2:
            raise Retry(defer=60) from exc
        await _set_search_status(ctx, search_id, "unavailable", error=str(exc))
        return

    params = settings.knowledge
    queued = 0
    rejected = 0
    async with ctx["sessionmaker"]() as session:
        for hit in hits:
            if not search_client.allowed_url(hit.url, params):
                rejected += 1
                continue
            normalized = search_client.normalize_url(hit.url)
            known = await programs_repo.get_by_normalized_url(session, normalized)
            if known is not None and _fresh(known.checked_at, params, ctx):
                continue
            await _enqueue(
                ctx,
                "extract_program",
                f"extract:{_short(normalized)}",
                url=hit.url,
                student_id=str(student) if student else None,
                query=query,
                search_id=search_id,
            )
            queued += 1
    # «done» ставится сразу: извлечения идут своими задачами и дописывают
    # в статус найденные и отбракованные по мере готовности (§6.1).
    await _set_search_status(ctx, search_id, "done", rejected=rejected)
    _logger.info(
        "search_programs", query=query, hits=len(hits), queued=queued, warm=warm
    )


def _fresh(checked_at: date, params: Any, ctx: dict) -> bool:
    del ctx
    return (datetime.now(UTC).date() - checked_at).days < params.program_recheck_days


def _short(value: str) -> str:
    import hashlib

    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


async def extract_program(
    ctx: dict,
    request_id: str,
    url: str,
    student_id: str | UUID | None = None,
    query: str | None = None,
    search_id: str | None = None,
) -> None:
    """One page → at most one `Program`, with every number confirmed."""
    structlog.contextvars.bind_contextvars(request_id=request_id)
    student = uuid_of(student_id) if student_id else None
    normalized = search_client.normalize_url(url)
    lock_name = f"extract:{_short(normalized)}"
    if not await _lock(ctx["redis"], lock_name):
        return
    params = settings.knowledge
    try:
        async with ctx["sessionmaker"]() as session:
            known = await programs_repo.get_by_normalized_url(session, normalized)
        if known is not None:
            if _fresh(known.checked_at, params, ctx):
                _logger.info("extract_skipped", url=normalized, reason="fresh")
                return
            if not known.extracted_auto:
                # Ручной пол не перезаписывается никогда (§6.7).
                _logger.info("extract_skipped", url=normalized, reason="floor")
                return

        try:
            page = await search_client.fetch_page(url)
        except ValidationFailed:
            await _reject(ctx, search_id, url, "invalid_url")
            return
        except search_client.FetchFailed as exc:
            status = getattr(exc, "status", None)
            if status == 415:
                await _reject(ctx, search_id, url, "not_html")
                return
            if status is not None and 400 <= status < 500:
                await _reject(ctx, search_id, url, f"http_{status}")
                return
            if int(ctx.get("job_try", 1) or 1) < 2:
                raise Retry(defer=30) from exc
            await _reject(ctx, search_id, url, "fetch_failed")
            return

        reason = extractor.page_rejection(page, params)
        if reason is not None:
            await _reject(ctx, search_id, url, reason)
            return

        try:
            extracted = await extractor.extract_program(ctx["llm"], page, url)
        except LLMUnavailable as exc:
            if _is_invalid_output(exc):
                await _reject(ctx, search_id, url, "invalid_output")
                return
            raise _llm_retry(exc) from exc

        today = datetime.now(UTC).date()
        candidate = extractor.to_program(extracted, url, today)
        if isinstance(candidate, str):
            await _reject(ctx, search_id, url, candidate)
            return
        program, dropped = extractor.verify_against_source(candidate, extracted, page)
        rejection = extractor.acceptance_reason(program, dropped)
        if rejection is not None:
            await _reject(ctx, search_id, url, rejection, dropped)
            return

        notes: list[str] = []
        if not (extracted.city or "").strip():
            notes.append("city_from_country")
        meta = ExtractionMeta(
            prompt_version=version_of("extract_program"),
            model=_model(),
            page_chars=len(page),
            dropped_fields=dropped,
            notes=notes,
            search_query=query,
            requested_by=student,
            extracted_at=datetime.now(UTC),
        )
        async with ctx["sessionmaker"]() as session:
            previous = await programs_repo.get_program(session, program.id)
            outcome = await programs_repo.upsert_extracted(
                session,
                program,
                meta,
                normalized_url=normalized,
                university_slug=extractor.slugify(program.university),
                direction_slug=extractor.slugify(program.direction),
            )
            if outcome in ("skipped_floor", "lost"):
                await session.commit()
                _logger.info("extract_duplicate", url=normalized, outcome=outcome)
                await _reject(ctx, search_id, url, outcome)
                return
            if (
                previous is not None
                and previous.environment_text != program.environment_text
            ):
                # Среда изменилась — прежние мягкие оценки о ней устарели.
                await soft_repo.delete_for_program(session, program.id)
            await session.commit()

        _logger.info(
            "extract_ok", url=normalized, program_id=program.id, dropped=dropped
        )
        await _found(ctx, search_id, program.id)
        if student is not None:
            async with ctx["sessionmaker"]() as session:
                profile = await profiles_repo.get_profile(session, student)
            if (profile.traits.summary or "").strip():
                await _enqueue(
                    ctx,
                    "soft_match",
                    f"softmatch:{student}:{program.id[:12]}",
                    student_id=str(student),
                    program_ids=[program.id],
                )
    finally:
        await _unlock(ctx["redis"], lock_name)


async def _reject(
    ctx: dict,
    search_id: str | None,
    url: str,
    reason: str,
    dropped: list[str] | None = None,
) -> None:
    """A rejected page is not a failure: nothing is written, `job.failed`
    is not recorded, and the search status only counts it (§11)."""
    _logger.info("extract_rejected", url=url, reason=reason, dropped=dropped or [])
    await _update_search_status(ctx, search_id, rejected=1)


async def _found(ctx: dict, search_id: str | None, program_id: str) -> None:
    await _update_search_status(ctx, search_id, program_id=program_id)


async def _update_search_status(
    ctx: dict,
    search_id: str | None,
    *,
    program_id: str | None = None,
    rejected: int = 0,
) -> None:
    """Merge one extraction outcome into the status the route reads.

    Best effort by design: the status is a progress indicator in Redis, and
    losing it must not fail an extraction that already wrote its program.
    """
    if not search_id:
        return
    try:
        raw = await ctx["redis"].get(keys.search_status(search_id))
        payload = (
            SearchStatusOut.model_validate_json(raw)
            if raw
            else SearchStatusOut(search_id=search_id, status="running")
        )
        found = list(payload.found)
        if program_id and program_id not in found:
            found.append(program_id)
        updated = payload.model_copy(
            update={"found": found, "rejected": payload.rejected + rejected}
        )
        await ctx["redis"].set(
            keys.search_status(search_id),
            updated.model_dump_json(),
            ex=_SEARCH_STATUS_TTL_S,
        )
    except Exception:  # noqa: BLE001 — статус поиска не важнее самой записи
        _logger.info("search_status_not_updated", search_id=search_id)


async def _set_search_status(
    ctx: dict,
    search_id: str,
    status: str,
    *,
    found: list[str] | None = None,
    rejected: int = 0,
    error: str | None = None,
) -> None:
    payload = SearchStatusOut(
        search_id=search_id,
        status=status,  # type: ignore[arg-type]
        found=found or [],
        rejected=rejected,
        error=error,
    )
    try:
        await ctx["redis"].set(
            keys.search_status(search_id),
            payload.model_dump_json(),
            ex=_SEARCH_STATUS_TTL_S,
        )
    except Exception:  # noqa: BLE001
        return


# --- realism_texts and compare_text (§7) ---


async def realism_texts(
    ctx: dict, request_id: str, student_id: str | UUID, program_ids: list[str]
) -> None:
    """Put the already-computed factors of each program into words."""
    structlog.contextvars.bind_contextvars(request_id=request_id)
    from app.apply import matching as apply_matching

    student = uuid_of(student_id)
    deps = rule_deps(ctx)
    version, model = version_of("realism"), _model()
    async with ctx["sessionmaker"]() as session:
        matching = await apply_matching.run_matching(
            session, deps, student, limit=settings.knowledge.realism_texts_limit
        )
    wanted = set(program_ids)
    for item in matching.items:
        if item.program.id not in wanted:
            continue
        digest = apply_matching.realism_hash(student, item, version, model)
        async with ctx["sessionmaker"]() as session:
            row = await texts_repo.get_generated(session, "realism", digest)
            if row is not None and row.status == "ready":
                continue
            await texts_repo.mark(
                session,
                "realism",
                digest,
                "generating",
                student_id=student,
                subject=item.program.id,
                model=model,
                prompt_version=version,
            )
            await session.commit()
        try:
            text = await agent_texts.generate_realism(ctx["llm"], item)
        except agent_texts.PostcheckFailed as exc:
            await _fail_text(ctx, "realism", digest, exc.reason, item.program.id)
            continue
        except LLMUnavailable as exc:
            if _is_invalid_output(exc):
                await _fail_text(
                    ctx, "realism", digest, "invalid_output", item.program.id
                )
                continue
            raise _llm_retry(exc) from exc
        async with ctx["sessionmaker"]() as session:
            await texts_repo.mark(
                session,
                "realism",
                digest,
                "ready",
                text=text,
                student_id=student,
                subject=item.program.id,
                model=model,
                prompt_version=version,
            )
            await session.commit()


async def compare_text(
    ctx: dict, request_id: str, student_id: str | UUID, program_ids: list[str]
) -> None:
    """The «для тебя разница — …» line under a comparison table."""
    structlog.contextvars.bind_contextvars(request_id=request_id)
    from app.apply import matching as apply_matching

    student = uuid_of(student_id)
    deps = rule_deps(ctx)
    version, model = version_of("compare"), _model()
    subject = ",".join(sorted(program_ids))
    async with ctx["sessionmaker"]() as session:
        profile = await profiles_repo.get_profile(session, student)
        comparison = await apply_matching.compare_programs(
            session, deps, student, list(program_ids)
        )
        digest = apply_matching.compare_hash(
            student, list(program_ids), comparison.rows, profile, version, model
        )
        row = await texts_repo.get_generated(session, "compare", digest)
        if row is not None and row.status == "ready":
            return
        await texts_repo.mark(
            session,
            "compare",
            digest,
            "generating",
            student_id=student,
            subject=subject,
            model=model,
            prompt_version=version,
        )
        await session.commit()

    rows = [row for row in comparison.rows if row.differs and row.relevant_to_student]
    try:
        text = await agent_texts.generate_compare(
            ctx["llm"],
            rows,
            (profile.questionnaire.priorities.ranking.value or [])[:3],
            profile.traits.summary or "",
        )
    except agent_texts.PostcheckFailed as exc:
        await _fail_text(ctx, "compare", digest, exc.reason, subject)
        return
    except LLMUnavailable as exc:
        if _is_invalid_output(exc):
            await _fail_text(ctx, "compare", digest, "invalid_output", subject)
            return
        raise _llm_retry(exc) from exc
    async with ctx["sessionmaker"]() as session:
        await texts_repo.mark(
            session,
            "compare",
            digest,
            "ready",
            text=text,
            student_id=student,
            subject=subject,
            model=model,
            prompt_version=version,
        )
        await session.commit()


async def _fail_text(
    ctx: dict, kind: str, digest: str, reason: str, subject: str
) -> None:
    async with ctx["sessionmaker"]() as session:
        await texts_repo.mark(
            session, kind, digest, "failed", error=reason, subject=subject
        )
        await session.commit()
    _logger.warning("text_failed", kind=kind, subject=subject, reason=reason)


# --- enqueueing children ---


async def _enqueue(ctx: dict, fn_name: str, job_id: str, **kwargs: Any) -> None:
    from app.workers.queue import enqueue

    try:
        await enqueue(ctx["redis"], "bulk", fn_name, _job_id=job_id, **kwargs)
    except Exception:  # noqa: BLE001 — a lost child job is logged, not fatal
        _logger.warning("child_job_not_enqueued", fn=fn_name, job_id=job_id)
