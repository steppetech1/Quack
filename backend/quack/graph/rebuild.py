"""ops rebuild (Т-§2.7, Д-§6.3, spec §4.4): replay all events of a learner in memory and
replace the learner subgraph; one transaction when it fits into rebuild_max_tx_ops,
otherwise in chunks and only without an active session (presence key). Never calls models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from neo4j import AsyncDriver, AsyncManagedTransaction
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.config import Settings
from quack.db.locks import LockKeyspace, advisory_lock
from quack.db.neo4j import literal
from quack.db.postgres import SessionFactory
from quack.db.redis import presence_key
from quack.events.models import StoredEvent
from quack.events.reader import read_learner_events
from quack.graph.cypher import compile_mutations
from quack.graph.ids import CURRENT_RULES_VERSION
from quack.graph.memory import InMemoryGraph
from quack.graph.mutations import AdvanceWatermark, Mutation, UpsertNode, UpsertRel
from quack.graph.neo4j_store import Neo4jGraphStore
from quack.graph.projector import project_in_memory
from quack.graph.types import NODE_LABELS
from quack.graph.worker import publish_applied, record_offset
from quack.logging import get_logger

log = get_logger(__name__)

type RebuildStatus = Literal["rebuilt", "locked", "active_session"]

_WATERMARK_PROPS = ("appliedSeq", "projectorVersion")
_READ_CHUNK = 1000


@dataclass(frozen=True)
class RebuildOutcome:
    learner_id: UUID
    status: RebuildStatus
    applied_seq: int
    operations: int


def snapshot_mutations(graph: InMemoryGraph, learner_id: str) -> list[Mutation]:
    """Mutations that recreate `graph`; the watermark comes last."""
    mutations: list[Mutation] = []
    watermark: AdvanceWatermark | None = None
    for (label, node_id), props in sorted(graph.nodes.items()):
        clean = {k: v for k, v in props.items() if k != "id"}
        if label == "Learner" and node_id == learner_id:
            seq, version = clean.get("appliedSeq", 0), clean.get("projectorVersion", "")
            watermark = AdvanceWatermark(
                learner_id,
                seq if isinstance(seq, int) else 0,
                version if isinstance(version, str) and version else CURRENT_RULES_VERSION,
            )
            clean = {k: v for k, v in clean.items() if k not in _WATERMARK_PROPS}
        mutations.append(UpsertNode(label, node_id, clean))
    for (fl, fid, rt, tl, tid), props in sorted(graph.rels.items()):
        mutations.append(UpsertRel(fl, fid, rt, tl, tid, dict(props)))
    if watermark is not None:
        mutations.append(watermark)
    return mutations


async def _read_all(session_factory: SessionFactory, learner_id: UUID) -> list[StoredEvent]:
    events: list[StoredEvent] = []
    async with session_factory() as session:
        while True:
            after = events[-1].seq if events else 0
            chunk = await read_learner_events(
                session, learner_id, after_seq=after, limit=_READ_CHUNK
            )
            events += chunk
            if len(chunk) < _READ_CHUNK:
                return events


async def _replace_in_one_transaction(
    driver: AsyncDriver, learner_id: str, mutations: list[Mutation]
) -> None:
    statements = compile_mutations(mutations)

    async def work(tx: AsyncManagedTransaction) -> None:
        for label in NODE_LABELS:
            result = await tx.run(
                literal(f"MATCH (n:{label}) WHERE n.learnerId = $l DETACH DELETE n"),
                {"l": learner_id},
            )
            await result.consume()
        for st in statements:
            result = await tx.run(literal(st.query), st.params)
            await result.consume()

    async with driver.session() as session:
        await session.execute_write(work)


async def rebuild_learner(
    settings: Settings,
    engine: AsyncEngine,
    session_factory: SessionFactory,
    driver: AsyncDriver,
    redis: Redis | None,
    learner_id: UUID,
    *,
    respect_presence: bool = True,
) -> RebuildOutcome:
    async with advisory_lock(engine, LockKeyspace.LEARNER_PROJECTION, learner_id) as acquired:
        if not acquired:
            return RebuildOutcome(learner_id, "locked", 0, 0)
        events = await _read_all(session_factory, learner_id)
        graph = await project_in_memory(
            learner_id, events, settings.fold, batch_size=settings.projector_batch_max_events
        )
        lid = str(learner_id)
        mutations = snapshot_mutations(graph, lid)
        applied = events[-1].seq if events else 0
        limit = settings.rebuild_max_tx_ops
        store = Neo4jGraphStore(driver, lid)
        if len(mutations) <= limit:
            await _replace_in_one_transaction(driver, lid, mutations)
        else:
            if (
                respect_presence
                and redis is not None
                and await redis.exists(presence_key(learner_id))
            ):
                log.info("rebuild_deferred_active_session", learner_id=lid)
                return RebuildOutcome(learner_id, "active_session", applied, len(mutations))
            await store.delete_learner(chunk_size=limit)
            for start in range(0, len(mutations), limit):
                await store.apply(mutations[start : start + limit])
        await record_offset(session_factory, learner_id, applied)
        await publish_applied(redis, learner_id, applied)
        log.info("rebuild_done", learner_id=lid, applied_seq=applied, operations=len(mutations))
        return RebuildOutcome(learner_id, "rebuilt", applied, len(mutations))
