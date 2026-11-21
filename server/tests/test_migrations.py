"""Versioned migrations (migrate.py): fresh databases, databases from the hash-gated schema.sql
that came before, checksum protection, and several processes starting at once."""
from __future__ import annotations

import asyncio
import hashlib
import shutil
import uuid
from pathlib import Path

import psycopg
import pytest
from conftest import ADMIN_DSN, TEST_DSN, needs_db

from memvine_server.migrate import MIGRATIONS_DIR, MigrationError, load_migrations, migrate, status

pytestmark = needs_db

LEGACY_SCHEMA = Path(__file__).with_name("fixtures") / "legacy_schema.sql"
# The last hash-gated schema.sql; live databases from before migrations carry this hash in schema_meta.
LEGACY_SCHEMA_HASH = "f6e62a17c5036cd440e14ee2e9957b33064a3e77a9e07c219dea7cd4493df265"


@pytest.fixture
def scratch_dsn():
    """An empty database of its own, dropped afterwards."""
    name = f"{TEST_DSN.rsplit('/', 1)[-1]}_mig_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    try:
        yield TEST_DSN.rsplit("/", 1)[0] + "/" + name
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


async def connect(dsn: str):
    return await psycopg.AsyncConnection.connect(dsn, autocommit=True)


async def build_legacy(conn) -> None:
    """What the server did on start before migrations (the old db.apply_schema)."""
    schema = LEGACY_SCHEMA.read_text()
    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    await conn.execute("CREATE TABLE IF NOT EXISTS schema_meta (hash text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
    await conn.execute(schema)
    await conn.execute("INSERT INTO schema_meta (hash) VALUES (%s)", (hashlib.sha256(schema.encode()).hexdigest(),))


RELEASE = load_migrations()
LATER = [f"applied {m.label}" for m in RELEASE if m.version > 1]  # what follows the baseline in this release
NEXT = f"{RELEASE[-1].version + 1:04d}"  # the number a new migration would take


def baseline_only(tmp_path: Path) -> Path:
    directory = tmp_path / "baseline"
    directory.mkdir()
    for name in ("0001_baseline.sql", "rls.sql"):
        shutil.copy(MIGRATIONS_DIR / name, directory / name)
    return directory


def migrations_copy(tmp_path: Path, extra: dict[str, str] | None = None) -> Path:
    directory = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, directory)
    for name, sql in (extra or {}).items():
        (directory / name).write_text(sql)
    return directory


async def catalog(conn) -> dict:
    """The schema as Postgres describes it, minus migration bookkeeping."""
    skip = ("schema_meta", "schema_migrations", "schema_repeatables")
    queries = {
        "columns": """SELECT table_name, ordinal_position, column_name, udt_name, is_nullable, column_default,
                             is_generated, generation_expression
                      FROM information_schema.columns WHERE table_schema = 'public' ORDER BY 1, 2""",
        "constraints": """SELECT conrelid::regclass::text AS t, conname, pg_get_constraintdef(oid) AS def
                          FROM pg_constraint WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2""",
        "indexes": "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1, 2",
        "policies": "SELECT tablename, policyname, qual FROM pg_policies WHERE schemaname = 'public' ORDER BY 1, 2",
        "rls": """SELECT relname, relrowsecurity FROM pg_class
                  WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' ORDER BY 1""",
        "organizations": "SELECT slug, name FROM organizations ORDER BY slug",
    }
    out = {}
    for key, sql in queries.items():
        rows = await (await conn.execute(sql)).fetchall()
        out[key] = [r for r in rows if r[0] not in skip]
    return out


def test_legacy_fixture_is_the_last_hash_gated_schema():
    # Merges must not touch the fixture: it stands for what live databases were built with.
    assert hashlib.sha256(LEGACY_SCHEMA.read_bytes()).hexdigest() == LEGACY_SCHEMA_HASH


async def test_fresh_database_applies_every_migration(scratch_dsn):
    async with await connect(scratch_dsn) as conn:
        steps = await migrate(conn)
        assert steps[0] == "applied 0001_baseline"
        assert steps[-1] == "applied rls.sql (repeatable)"
        assert (await status(conn)).up_to_date
        assert await migrate(conn) == []  # a second start does nothing
        rows = await (await conn.execute("SELECT version, baseline, checksum FROM schema_migrations")).fetchall()
        assert rows[0][:2] == (1, False) and len(rows[0][2]) == 64
        rls = await (await conn.execute(
            "SELECT relrowsecurity FROM pg_class WHERE relname = 'memory_items'")).fetchone()
        assert rls[0] is True


async def test_baseline_is_the_legacy_schema(scratch_dsn, tmp_path):
    """0001 + rls.sql build exactly what the hash-gated schema.sql built, down to column order."""
    async with await connect(scratch_dsn) as conn:
        await migrate(conn, baseline_only(tmp_path))
        fresh = await catalog(conn)
    legacy_dsn = scratch_dsn + "_legacy"
    admin = await connect(ADMIN_DSN)
    try:
        await admin.execute(f'CREATE DATABASE "{legacy_dsn.rsplit("/", 1)[-1]}"')
        async with await connect(legacy_dsn) as conn:
            await build_legacy(conn)
            old = await catalog(conn)
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{legacy_dsn.rsplit("/", 1)[-1]}" WITH (FORCE)')
        await admin.close()
    assert len(fresh["columns"]) > 200 and len(fresh["policies"]) == 21 and fresh["organizations"]
    for key in fresh:
        assert fresh[key] == old[key], key


async def test_legacy_database_is_baselined_then_migrates(scratch_dsn, tmp_path):
    async with await connect(scratch_dsn) as conn:
        await build_legacy(conn)
        await conn.execute("INSERT INTO projects (slug, name) VALUES ('kept', 'Kept')")

        st = await status(conn)
        assert st.legacy and [m.version for m in st.pending] == [m.version for m in RELEASE if m.version > 1]
        assert await migrate(conn, dry_run=True) == [
            "baseline 0001_baseline (existing database; recorded, not run)",
            *[s.replace("applied", "apply") for s in LATER], "apply rls.sql (repeatable)"]
        assert not (await status(conn)).applied  # a dry run changes nothing

        steps = await migrate(conn)
        assert steps == ["baselined 0001_baseline (existing database; recorded, not run)", *LATER, "applied rls.sql (repeatable)"]
        row = await (await conn.execute("SELECT version, baseline FROM schema_migrations ORDER BY version")).fetchone()
        assert row == (1, True)
        assert await (await conn.execute("SELECT name FROM projects WHERE slug = 'kept'")).fetchone() == ("Kept",)

        directory = migrations_copy(tmp_path, {f"{NEXT}_widget_note.sql": "ALTER TABLE projects ADD COLUMN widget_note text;"})
        assert await migrate(conn, directory) == [f"applied {NEXT}_widget_note", "applied rls.sql (repeatable)"]
        await conn.execute("SELECT widget_note FROM projects")
        assert await migrate(conn, directory) == []


async def test_legacy_database_missing_columns_is_not_baselined(scratch_dsn):
    async with await connect(scratch_dsn) as conn:
        await build_legacy(conn)
        await conn.execute("ALTER TABLE memory_items DROP COLUMN scope")
        with pytest.raises(MigrationError, match=r"memory_items\.scope"):
            await migrate(conn)
        assert (await status(conn)).legacy  # nothing recorded; the next start tries again


async def test_changed_migration_refuses_to_start(scratch_dsn, tmp_path):
    directory = migrations_copy(tmp_path)
    async with await connect(scratch_dsn) as conn:
        await migrate(conn, directory)
        baseline = directory / "0001_baseline.sql"
        baseline.write_text(baseline.read_text() + "\n-- edited after it ran\n")
        with pytest.raises(MigrationError, match="0001_baseline.sql: recorded checksum"):
            await migrate(conn, directory)


async def test_a_database_that_is_not_utf8_refuses_to_start_and_says_why(scratch_dsn):
    """A cluster made under the C locale creates SQL_ASCII databases. Text read from one is bytes, so a
    second start used to say that every applied migration had changed."""
    name = scratch_dsn.rsplit("/", 1)[-1] + "_ascii"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f"CREATE DATABASE \"{name}\" ENCODING 'SQL_ASCII' TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C'")
    try:
        async with await connect(scratch_dsn.rsplit("/", 1)[0] + "/" + name) as conn:
            with pytest.raises(MigrationError, match="encoding is SQL_ASCII, and Memvine needs UTF8"):
                await migrate(conn)
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


async def test_database_from_a_newer_release_refuses_to_start(scratch_dsn, tmp_path):
    directory = migrations_copy(tmp_path, {f"{NEXT}_newer.sql": "CREATE TABLE newer (id int);"})
    async with await connect(scratch_dsn) as conn:
        await migrate(conn, directory)
        with pytest.raises(MigrationError, match=f"{NEXT}_newer"):
            await migrate(conn)


async def test_failed_migration_rolls_back(scratch_dsn, tmp_path):
    directory = migrations_copy(tmp_path, {
        f"{NEXT}_broken.sql": "CREATE TABLE half_done (id int);\nSELECT no_such_column FROM projects;"})
    async with await connect(scratch_dsn) as conn:
        with pytest.raises(MigrationError, match=f"{NEXT}_broken failed"):
            await migrate(conn, directory)
        assert await (await conn.execute("SELECT to_regclass('half_done')")).fetchone() == (None,)
        st = await status(conn, directory)
        assert [m.label for m in st.pending] == [f"{NEXT}_broken"]
        assert [r["version"] for r in st.applied] == [m.version for m in RELEASE]


async def test_concurrent_starts_apply_once(scratch_dsn):
    conns = [await connect(scratch_dsn) for _ in range(5)]
    try:
        results = await asyncio.gather(*(migrate(c) for c in conns))
    finally:
        for c in conns:
            await c.close()
    assert sum("applied 0001_baseline" in steps for steps in results) == 1
    async with await connect(scratch_dsn) as conn:
        assert await (await conn.execute("SELECT count(*) FROM schema_migrations")).fetchone() == (len(RELEASE),)


async def test_concurrent_starts_of_a_legacy_database_baseline_once(scratch_dsn):
    async with await connect(scratch_dsn) as conn:
        await build_legacy(conn)
    conns = [await connect(scratch_dsn) for _ in range(4)]
    try:
        results = await asyncio.gather(*(migrate(c) for c in conns))
    finally:
        for c in conns:
            await c.close()
    assert sum(any(s.startswith("baselined") for s in steps) for steps in results) == 1


async def test_legacy_database_that_ran_later_blocks_migrates(scratch_dsn):
    """Databases that ran a newer schema.sql (with the memory aging and review loop blocks that
    became 0002 and 0003) are baselined at 0001 and run those files again without harm."""
    async with await connect(scratch_dsn) as conn:
        await build_legacy(conn)
        for m in RELEASE:
            if m.name in ("memory_aging", "review_loop"):
                await conn.execute(m.sql)
        steps = await migrate(conn)
        assert steps[0].startswith("baselined 0001") and steps[1:] == [*LATER, "applied rls.sql (repeatable)"]
        assert (await status(conn)).up_to_date
