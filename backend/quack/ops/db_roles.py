"""`ops db-roles`: the three Postgres roles of Д-§7 / Т-§19.8 (ТЗ Фазы 4 WP9.6).

- `quack_migrator` owns the schemas and runs DDL (migrations);
- `quack_app` reads and writes the tables of the application schemas (`core`, `content`, `ops`),
  no DDL;
- `quack_reader` is a member of `pg_read_all_data` (dump, alert checks).

Idempotent: runs on the first start, at every rollout and before `pg_restore` (`pg_dump` does not
save roles, Д-§7). Connect as the database superuser. Passwords are set on every run, so a rotation
is a new value plus a rollout. Objects created later by `quack_migrator` get the grants through
default privileges, so the command may run before the first migration.
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg import sql

from quack.config import Settings
from quack.db import Db
from quack.logging import get_logger

log = get_logger(__name__)

MIGRATOR = "quack_migrator"
APP = "quack_app"
READER = "quack_reader"

# Д-§7 and the schema of Phase 4 (ТЗ Ф4 WP9.6): `core`, `content`, `ops`. The `auth` schema of
# Phases 1-3 is gone: sessions and links are tables of `core` (D-C-180).
APP_SCHEMAS = ("core", "content", "ops")


class DbRolesRefused(Exception):
    """A password is missing: a role without one could not be used."""


@dataclass(frozen=True)
class RolePasswords:
    migrator: str
    app: str
    reader: str

    @classmethod
    def from_settings(cls, settings: Settings) -> RolePasswords:
        values = {
            "MIGRATOR_DB_PASSWORD": settings.migrator_db_password.get_secret_value(),
            "APP_DB_PASSWORD": settings.app_db_password.get_secret_value(),
            "READER_DB_PASSWORD": settings.reader_db_password.get_secret_value(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise DbRolesRefused(f"set {', '.join(missing)}")
        return cls(
            migrator=values["MIGRATOR_DB_PASSWORD"],
            app=values["APP_DB_PASSWORD"],
            reader=values["READER_DB_PASSWORD"],
        )


_ROLE_EXISTS = "SELECT 1 AS found FROM pg_roles WHERE rolname = %(role)s"
_DATABASE = "SELECT current_database() AS name"
_SCHEMAS = "SELECT nspname FROM pg_namespace WHERE nspname = ANY(%(names)s)"
_RELATIONS = (
    "SELECT c.relkind, c.relname FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = %(schema)s AND c.relkind IN ('r', 'p', 'S', 'v', 'm') "
    # A sequence of a serial or identity column follows its table's owner.
    "AND NOT (c.relkind = 'S' AND EXISTS (SELECT 1 FROM pg_depend d "
    "WHERE d.objid = c.oid AND d.deptype IN ('a', 'i')))"
)


async def _ensure_role(db: Db, role: str, password: str) -> None:
    exists = await db.fetch_one(_ROLE_EXISTS, {"role": role})
    verb = sql.SQL("ALTER") if exists else sql.SQL("CREATE")
    # The password is a literal quoted by the driver: it cannot be a bind parameter here.
    await db.execute(
        sql.SQL("{} ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
            verb, sql.Identifier(role), sql.Literal(password)
        )
    )


async def apply_db_roles(db: Db, passwords: RolePasswords) -> list[str]:
    """Create or update the roles and grants; returns the application schemas that were found.
    `db` is connected as the database superuser."""
    ident = sql.Identifier
    async with db.transaction():
        row = await db.fetch_one(_DATABASE)
        assert row is not None
        database = ident(row["name"])
        await _ensure_role(db, MIGRATOR, passwords.migrator)
        await _ensure_role(db, APP, passwords.app)
        await _ensure_role(db, READER, passwords.reader)
        migrator, app, reader = ident(MIGRATOR), ident(APP), ident(READER)
        await db.execute(
            sql.SQL("GRANT CREATE, CONNECT ON DATABASE {} TO {}").format(database, migrator)
        )
        await db.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(database, app, reader)
        )
        await db.execute(sql.SQL("GRANT pg_read_all_data TO {}").format(reader))
        # What `quack_migrator` creates from now on (Д-§7: it owns the schemas and the tables).
        for grantee in (app, reader):
            await db.execute(
                sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} GRANT USAGE ON SCHEMAS TO {}").format(
                    migrator, grantee
                )
            )
        await db.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
            ).format(migrator, app)
        )
        await db.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} "
                "GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {}"
            ).format(migrator, app)
        )
        # What exists already (the superuser created it before the roles did).
        found = await db.fetch_all(_SCHEMAS, {"names": list(APP_SCHEMAS)})
        schemas = sorted(r["nspname"] for r in found)
        for schema in schemas:
            name = ident(schema)
            await db.execute(sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(name, migrator))
            for rel in await db.fetch_all(_RELATIONS, {"schema": schema}):
                what = sql.SQL("SEQUENCE" if rel["relkind"] == "S" else "TABLE")
                await db.execute(
                    sql.SQL("ALTER {} {} OWNER TO {}").format(
                        what, ident(schema, rel["relname"]), migrator
                    )
                )
            await db.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}, {}").format(name, app, reader)
            )
            await db.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO {}"
                ).format(name, app)
            )
            await db.execute(
                sql.SQL("GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA {} TO {}").format(
                    name, app
                )
            )
    log.info("db_roles_applied", schemas=schemas)
    return schemas
