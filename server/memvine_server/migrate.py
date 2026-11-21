"""Versioned schema migrations.

The schema lives in numbered files, migrations/NNNN_<name>.sql, applied in order and recorded in
schema_migrations with a checksum. On start (db.open_pool) the server applies what is pending, each
file in its own transaction, under an advisory lock so several processes starting together apply
it once. An applied file must never change: a checksum mismatch refuses to start.

migrations/rls.sql (row-level security) is repeatable: it runs again after any numbered migration
(new tables need its grants) and whenever its own text changes, and is recorded in
schema_repeatables.

A database created by the hash-gated schema.sql that came before (it has the tables but no
schema_migrations) is baselined: once its tables and columns are checked against 0001, 0001 is
recorded as applied without running.

A migration that cannot run inside a transaction (CREATE INDEX CONCURRENTLY) starts with the line
`-- memvine:no-transaction`; keep such a file to that one statement.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger("memvine.migrate")

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
REPEATABLES = ("rls",)
# pg_advisory_lock key held while migrating; any constant shared by every Memvine process.
LOCK_KEY = 7_320_315_120_581_901_312
NO_TRANSACTION = "-- memvine:no-transaction"
_FILE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

TRACKING = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version       int PRIMARY KEY,
    name          text NOT NULL,
    checksum      text NOT NULL,
    applied_at    timestamptz NOT NULL DEFAULT now(),
    execution_ms  int,
    baseline      boolean NOT NULL DEFAULT false   -- recorded for an existing database, not run
);
CREATE TABLE IF NOT EXISTS schema_repeatables (
    name        text PRIMARY KEY,
    checksum    text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
"""


class MigrationError(RuntimeError):
    """The database cannot be brought to this release's schema; the server must not start."""


def checksum(sql: str) -> str:
    return hashlib.sha256(sql.replace("\r\n", "\n").encode()).hexdigest()


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return checksum(self.sql)

    @property
    def label(self) -> str:
        return f"{self.version:04d}_{self.name}"

    @property
    def transactional(self) -> bool:
        return not self.sql.lstrip().startswith(NO_TRANSACTION)


@dataclass
class Status:
    applied: list[dict] = field(default_factory=list)     # schema_migrations rows
    pending: list[Migration] = field(default_factory=list)
    mismatched: list[tuple[Migration, str]] = field(default_factory=list)  # (file, recorded checksum)
    unknown: list[dict] = field(default_factory=list)     # applied rows with no file in this release
    legacy: bool = False                                   # created by the old schema.sql; needs a baseline
    repeatables_due: list[str] = field(default_factory=list)

    @property
    def up_to_date(self) -> bool:
        return not (self.pending or self.mismatched or self.unknown or self.legacy or self.repeatables_due)


def load_migrations(directory: Path | None = None) -> list[Migration]:
    directory = directory or MIGRATIONS_DIR
    found: dict[int, Migration] = {}
    for path in sorted(directory.glob("*.sql")):
        match = _FILE.match(path.name)
        if not match:
            if path.stem not in REPEATABLES:
                raise MigrationError(f"migration file {path.name} is not named NNNN_<name>.sql")
            continue
        version = int(match.group(1))
        if version in found:
            raise MigrationError(f"two migrations numbered {version:04d}: {found[version].label}.sql and {path.name}")
        found[version] = Migration(version, match.group(2), path.read_text())
    return [found[v] for v in sorted(found)]


def load_repeatables(directory: Path | None = None) -> dict[str, str]:
    directory = directory or MIGRATIONS_DIR
    return {name: (directory / f"{name}.sql").read_text() for name in REPEATABLES if (directory / f"{name}.sql").exists()}


def expected_columns(sql: str) -> dict[str, set[str]]:
    """Tables and their columns as a CREATE TABLE migration defines them (for baseline checks)."""
    tables: dict[str, set[str]] = {}
    for match in re.finditer(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+) \((.*?)\n\);", sql, re.S):
        columns = set()
        for line in match.group(2).splitlines():
            line = line.split("--", 1)[0].strip()
            word = line.split(" ", 1)[0] if line else ""
            if word and word.upper() not in ("PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT") and word.islower():
                columns.add(word)
        tables[match.group(1)] = columns
    return tables


async def _exists(conn, relation: str) -> bool:
    row = await (await conn.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (f"public.{relation}",))).fetchone()
    return bool(_get(row, "ok", 0))


def _get(row, key: str, index: int):
    return row[key] if isinstance(row, dict) else row[index]


async def _rows(conn, sql: str, params=()) -> list[dict]:
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(sql, params)
    return await cur.fetchall()


async def status(conn, directory: Path | None = None) -> Status:
    """Where the database stands against this release's migration files. Takes no locks."""
    migrations = load_migrations(directory)
    repeatables = load_repeatables(directory)
    st = Status()
    if await _exists(conn, "schema_migrations"):
        st.applied = await _rows(conn, "SELECT version, name, checksum, applied_at, execution_ms, baseline "
                                       "FROM schema_migrations ORDER BY version")
    # Tables but no migration recorded: built by the hash-gated schema.sql before migrations existed.
    st.legacy = not st.applied and await _exists(conn, "projects")
    by_version = {r["version"]: r for r in st.applied}
    files = {m.version for m in migrations}
    for m in migrations:
        row = by_version.get(m.version)
        if row is None:
            if not (st.legacy and m.version == 1):
                st.pending.append(m)
        elif row["checksum"] != m.checksum:
            st.mismatched.append((m, row["checksum"]))
    st.unknown = [r for r in st.applied if r["version"] not in files]
    recorded = {r["name"]: r["checksum"] for r in await _rows(conn, "SELECT name, checksum FROM schema_repeatables")} \
        if await _exists(conn, "schema_repeatables") else {}
    if st.pending or st.legacy:
        st.repeatables_due = list(repeatables)  # numbered migrations may add tables that need its grants
    else:
        st.repeatables_due = [n for n, sql in repeatables.items() if recorded.get(n) != checksum(sql)]
    return st


def _refuse(st: Status) -> None:
    if st.mismatched:
        lines = [f"  {m.label}.sql: recorded checksum {rec[:12]}…, file now {m.checksum[:12]}…" for m, rec in st.mismatched]
        raise MigrationError(
            "refusing to start: applied migration(s) changed since they ran:\n" + "\n".join(lines) +
            "\nAn applied migration must never be edited. Restore the file to what was applied and put the "
            "change in a new numbered migration.")
    if st.unknown:
        names = ", ".join(f"{r['version']:04d}_{r['name']}" for r in st.unknown)
        raise MigrationError(
            f"refusing to start: the database has migration(s) this release does not know ({names}). "
            "It was migrated by a newer Memvine; run that release, or restore a backup taken before it.")


async def _verify_legacy(conn, baseline: Migration) -> None:
    expected = expected_columns(baseline.sql)
    have: dict[str, set[str]] = {}
    for r in await _rows(conn, "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public'"):
        have.setdefault(r["table_name"], set()).add(r["column_name"])
    missing = []
    for table, columns in expected.items():
        if table not in have:
            missing.append(f"table {table}")
        else:
            missing += [f"column {table}.{c}" for c in sorted(columns - have[table])]
    if missing:
        raise MigrationError(
            "refusing to baseline: this database was created by an older Memvine schema and lacks "
            + ", ".join(missing) + ". Start the release before versioned migrations once (it brings the old "
            "schema up to date), then this one.")


async def migrate(conn, directory: Path | None = None, dry_run: bool = False) -> list[str]:
    """Apply pending migrations on an autocommit connection. Returns what was (or, with dry_run,
    would be) done, one line per step; empty when the database is current."""
    # From any other encoding text does not come back as it went in (from SQL_ASCII, what a cluster
    # made under the C locale gives, it comes back as bytes), and the first thing to break is the
    # checksum comparison below, which then blames the migration files.
    encoding = conn.info.parameter_status("server_encoding") or "unknown"
    if encoding != "UTF8":
        raise MigrationError(
            f"the database's encoding is {encoding}, and Memvine needs UTF8. Create the database again as UTF8 "
            "(createdb -E UTF8 -T template0 --locale=C.UTF-8 <name>) and start the server on that one.")
    st = await status(conn, directory)
    _refuse(st)
    if st.up_to_date:
        return []
    if dry_run:
        steps = []
        if st.legacy:
            steps.append("baseline 0001_baseline (existing database; recorded, not run)")
        steps += [f"apply {m.label}" for m in st.pending]
        steps += [f"apply {n}.sql (repeatable)" for n in st.repeatables_due]
        return steps

    migrations = load_migrations(directory)
    repeatables = load_repeatables(directory)
    started = time.monotonic()
    await conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
    try:
        waited = time.monotonic() - started
        if waited > 1:
            log.info("waited %.1fs for another process's migration", waited)
        await conn.execute(TRACKING)
        # Another process may have migrated while this one waited for the lock: look again.
        st = await status(conn, directory)
        _refuse(st)
        steps: list[str] = []
        if st.legacy:
            baseline = next((m for m in migrations if m.version == 1), None)
            if baseline is None:
                raise MigrationError("no 0001 migration to baseline an existing database against")
            await _verify_legacy(conn, baseline)
            await conn.execute(
                "INSERT INTO schema_migrations (version, name, checksum, baseline) VALUES (%s, %s, %s, true)",
                (baseline.version, baseline.name, baseline.checksum))
            steps.append(f"baselined {baseline.label} (existing database; recorded, not run)")
            log.info("existing database baselined at %s", baseline.label)
        for m in st.pending:
            t0 = time.monotonic()
            log.info("applying migration %s", m.label)
            try:
                if m.transactional:
                    async with conn.transaction():
                        await conn.execute(m.sql)
                        await _record(conn, m, t0)
                else:
                    await conn.execute(m.sql)
                    await _record(conn, m, t0)
            except psycopg.Error as exc:
                raise MigrationError(f"migration {m.label} failed and was rolled back: {exc}") from exc
            steps.append(f"applied {m.label}")
        for name in st.repeatables_due:
            sql = repeatables[name]
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_repeatables (name, checksum) VALUES (%s, %s) "
                    "ON CONFLICT (name) DO UPDATE SET checksum = EXCLUDED.checksum, applied_at = now()",
                    (name, checksum(sql)))
            steps.append(f"applied {name}.sql (repeatable)")
        return steps
    finally:
        try:
            await conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
        except psycopg.Error:
            pass  # the connection is gone, and its session lock with it


async def _record(conn, m: Migration, t0: float) -> None:
    await conn.execute(
        "INSERT INTO schema_migrations (version, name, checksum, execution_ms) VALUES (%s, %s, %s, %s)",
        (m.version, m.name, m.checksum, int((time.monotonic() - t0) * 1000)))


async def migrate_database(dsn: str, directory: Path | None = None, dry_run: bool = False) -> list[str]:
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        return await migrate(conn, directory, dry_run)
