"""T-OPS2 (ТЗ Фазы 4): `ops db-roles` is idempotent; `quack_app` has no DDL; reader reads.
The schemas are those of Phase 4: `core`, `content`, `ops` (no `auth`)."""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.errors import InsufficientPrivilege
from pydantic import SecretStr

from quack.config import Settings
from quack.db import Db, LockKeyspace, open_db
from quack.events.log import NewEvent, append_events
from quack.events.registry import REGISTRY, EventPayload
from quack.ops.commands import dbmate_url, role_dsn
from quack.ops.db_roles import (
    APP,
    MIGRATOR,
    READER,
    DbRolesRefused,
    RolePasswords,
    apply_db_roles,
)
from tests.conftest import PG_DSN, _unavailable
from tests.db.fixtures import create_learner, database_url, run_dbmate

PASSWORDS = RolePasswords(migrator="m-pass'1%x", app='a-pass"2', reader="r-pass;3")


@pytest.fixture
async def scratch(settings: Settings) -> AsyncIterator[str]:
    """A throwaway database: the roles are cluster-wide, the shared test schema stays untouched.
    Yields its DSN (as the superuser of the test server)."""
    name = f"quack_roles_{uuid4().hex[:8]}"
    try:
        admin = await psycopg.AsyncConnection.connect(PG_DSN, autocommit=True)
    except psycopg.Error as exc:  # no infrastructure: skip, or fail under REQUIRE_INFRA
        _unavailable("Postgres", exc)
        return
    await admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    dsn = make_conninfo(PG_DSN, dbname=name)
    async with await psycopg.AsyncConnection.connect(dsn) as owner:
        for schema in ("core", "content", "ops"):
            await owner.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        await owner.execute("CREATE TABLE core.events (id serial PRIMARY KEY, body text)")
        await owner.execute("CREATE TABLE ops.jobs (id serial PRIMARY KEY, kind text)")
    try:
        yield dsn
    finally:
        await admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        await admin.close()


async def superuser_db(settings: Settings, dsn: str) -> Db:
    return await open_db(settings.model_copy(update={"postgres_dsn": dsn}))


async def apply(settings: Settings, dsn: str, passwords: RolePasswords = PASSWORDS) -> list[str]:
    db = await superuser_db(settings, dsn)
    try:
        return await apply_db_roles(db, passwords)
    finally:
        await db.close()


async def as_role(dsn: str, role: str, password: str) -> psycopg.AsyncConnection[Any]:
    return await psycopg.AsyncConnection.connect(
        make_conninfo(dsn, user=role, password=password), autocommit=True
    )


async def test_roles_are_applied_twice_without_error(settings: Settings, scratch: str):
    first = await apply(settings, scratch)
    second = await apply(settings, scratch)

    assert first == second == ["content", "core", "ops"]


async def test_app_reads_and_writes_but_has_no_ddl(settings: Settings, scratch: str):
    await apply(settings, scratch)

    app = await as_role(scratch, APP, PASSWORDS.app)
    try:
        await app.execute("INSERT INTO core.events (body) VALUES ('x')")
        await app.execute("UPDATE core.events SET body = 'y'")
        cur = await app.execute("SELECT count(*) FROM ops.jobs")
        assert (await cur.fetchone()) == (0,)
        for ddl in (
            "CREATE TABLE core.sneaky (id int)",
            "DROP TABLE core.events",
            "ALTER TABLE core.events ADD COLUMN extra int",
            "CREATE SCHEMA sneaky",
        ):
            with pytest.raises(InsufficientPrivilege):
                await app.execute(ddl)  # pyright: ignore[reportArgumentType]
    finally:
        await app.close()


async def test_reader_only_reads(settings: Settings, scratch: str):
    await apply(settings, scratch)
    async with await psycopg.AsyncConnection.connect(scratch, autocommit=True) as owner:
        await owner.execute("INSERT INTO core.events (body) VALUES ('x')")

    reader = await as_role(scratch, READER, PASSWORDS.reader)
    try:
        cur = await reader.execute("SELECT count(*) FROM core.events")
        assert (await cur.fetchone()) == (1,)
        with pytest.raises(InsufficientPrivilege):
            await reader.execute("INSERT INTO core.events (body) VALUES ('y')")
    finally:
        await reader.close()


async def test_tables_created_later_by_the_migrator_reach_the_app(settings: Settings, scratch: str):
    await apply(settings, scratch)

    migrator = await as_role(scratch, MIGRATOR, PASSWORDS.migrator)
    app = await as_role(scratch, APP, PASSWORDS.app)
    try:
        await migrator.execute("CREATE SCHEMA fresh")
        await migrator.execute("CREATE TABLE core.later (id serial PRIMARY KEY, v int)")
        await app.execute("INSERT INTO core.later (v) VALUES (1)")
    finally:
        await migrator.close()
        await app.close()


async def test_a_second_run_rotates_the_password(settings: Settings, scratch: str):
    await apply(settings, scratch)
    await apply(settings, scratch, RolePasswords(migrator="m2", app="a2", reader="r2"))

    app = await as_role(scratch, APP, "a2")
    try:
        cur = await app.execute("SELECT current_user")
        assert (await cur.fetchone()) == (APP,)
    finally:
        await app.close()


def test_missing_passwords_are_refused():
    settings = Settings(_env_file=None, app_db_password=SecretStr("only-app"))  # type: ignore[call-arg]

    with pytest.raises(DbRolesRefused, match="MIGRATOR_DB_PASSWORD") as info:
        RolePasswords.from_settings(settings)
    assert "APP_DB_PASSWORD" not in str(info.value)


# --- a database the superuser created and migrated (a stand, production before the roles) -------


@pytest.fixture
async def legacy(settings: Settings) -> AsyncIterator[str]:
    """A throwaway database migrated by dbmate AS THE SUPERUSER, so that every object belongs to
    `quack` and `public.schema_migrations` too. Yields its name."""
    name = f"quack_legacy_{uuid4().hex[:8]}"
    try:
        admin = await psycopg.AsyncConnection.connect(PG_DSN, autocommit=True)
    except psycopg.Error as exc:
        _unavailable("Postgres", exc)
        return
    await admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        run_dbmate(database_url(name, for_dbmate=True), "up")
    except subprocess.CalledProcessError as exc:
        await admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        await admin.close()
        _unavailable("dbmate", RuntimeError(exc.stderr))
        return
    try:
        yield name
    finally:
        await admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
        await admin.close()


class Noted(EventPayload):
    text: str


async def test_the_app_role_writes_events_but_cannot_change_the_schema(
    settings: Settings, legacy: str, monkeypatch: pytest.MonkeyPatch
):
    """The services connect as `quack_app` (WP9.1): what the application does passes on a
    database created by the superuser, and no DDL does."""
    monkeypatch.setitem(REGISTRY, ("test.noted", 1), Noted)
    dsn = make_conninfo(PG_DSN, dbname=legacy)
    await apply(settings, dsn)
    app_url = role_dsn(database_url(legacy), APP, PASSWORDS.app)
    db = await open_db(settings.model_copy(update={"postgres_dsn": app_url}))
    try:
        who = await db.fetch_one(
            "SELECT current_user AS name, usesuper FROM pg_user WHERE usename = current_user"
        )
        assert who == {"name": APP, "usesuper": False}
        learner = await create_learner(db)
        [stored] = await append_events(
            db,
            learner,
            [
                NewEvent(
                    type="test.noted", payload={"text": "hi"}, actor="learner", idempotency_key="k"
                )
            ],
        )
        assert stored.seq == 1
        # What the processes lean on besides plain reads and writes.
        async with db.advisory_lock(LockKeyspace.LEARNER_PROJECTION, learner) as taken:
            assert taken
        await db.execute("CREATE TEMP TABLE scratch_rows (id int)")
        await db.fetch_all("SELECT job_key FROM ops.jobs LIMIT 1 FOR UPDATE SKIP LOCKED")
        for ddl in (
            "CREATE TABLE core.sneaky (id int)",
            "CREATE TABLE public.sneaky (id int)",
            "ALTER TABLE core.events ADD COLUMN extra int",
            "ALTER TABLE ops.turn_manifests DROP COLUMN frozen_slots",
            "DROP TABLE content.sessions",
            "TRUNCATE core.events",
            "CREATE SCHEMA sneaky",
            "UPDATE public.schema_migrations SET version = version",
        ):
            with pytest.raises(InsufficientPrivilege):
                await db.execute(ddl)  # pyright: ignore[reportArgumentType]
    finally:
        await db.close()


async def test_old_and_new_migrations_run_as_the_migrator_on_a_database_of_the_superuser(
    settings: Settings, legacy: str
):
    """Ownership of the tables the superuser created passes to `quack_migrator`: the last
    migration is rolled back and applied again under it (an ALTER of an old table and a write to
    the journal of dbmate, which the superuser created)."""
    await apply(settings, make_conninfo(PG_DSN, dbname=legacy))
    url = dbmate_url(role_dsn(database_url(legacy), MIGRATOR, PASSWORDS.migrator))

    run_dbmate(url, "rollback")
    run_dbmate(url, "up")

    owner = await psycopg.AsyncConnection.connect(make_conninfo(PG_DSN, dbname=legacy))
    async with owner:
        cur = await owner.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_roles r ON r.oid = c.relowner WHERE n.nspname IN ('core', 'content', 'ops') "
            "AND c.relkind IN ('r', 'p') AND r.rolname <> %s",
            (MIGRATOR,),
        )
        assert (await cur.fetchone()) == (0,)
        cur = await owner.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = 'ops' AND table_name = 'turn_manifests' "
            "AND column_name = 'frozen_slots'"
        )
        assert (await cur.fetchone()) == (1,)


async def test_a_second_run_on_the_legacy_database_changes_nothing_and_locks_nothing(
    settings: Settings, legacy: str
):
    """Only objects of another owner are handed over: a rerun (every rollout) finds nothing to
    ALTER, so it takes no exclusive lock on the tables the live release uses."""
    dsn = make_conninfo(PG_DSN, dbname=legacy)
    await apply(settings, dsn)
    async with await psycopg.AsyncConnection.connect(dsn) as holder:
        # A running release holds a row lock on a table of the application.
        await holder.execute("LOCK TABLE core.events IN ROW SHARE MODE")
        # Would wait for the lock if it ALTERed a table (ACCESS EXCLUSIVE).
        again = await asyncio.wait_for(apply(settings, dsn), timeout=20)

    assert again == ["content", "core", "ops"]
