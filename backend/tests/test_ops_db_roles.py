"""T-OPS2 (ТЗ Фазы 4): `ops db-roles` is idempotent; `quack_app` has no DDL; reader reads.
The schemas are those of Phase 4: `core`, `content`, `ops` (no `auth`)."""

from __future__ import annotations

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
from quack.db import Db, open_db
from quack.ops.db_roles import (
    APP,
    MIGRATOR,
    READER,
    DbRolesRefused,
    RolePasswords,
    apply_db_roles,
)
from tests.conftest import PG_DSN, _unavailable

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
