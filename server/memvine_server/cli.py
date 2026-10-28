"""Operator commands: `memvine-server <command>`."""
from __future__ import annotations

import asyncio
import re

import typer
import uvicorn

from .auth import hash_key, new_project_key
from .config import settings
from .db import open_pool

app = typer.Typer(no_args_is_help=True, help="Memvine memory server")


def _run(coro):
    return asyncio.run(coro)


async def _with_pool(fn):
    pool = await open_pool()
    try:
        async with pool.connection() as conn:
            return await fn(conn)
    finally:
        await pool.close()


def _redacted(dsn: str) -> str:
    return re.sub(r"//([^:/@]+):[^@]*@", r"//\1:***@", dsn)


@app.command()
def migrate(dry_run: bool = typer.Option(False, "--dry-run", help="print what would run; change nothing")):
    """Apply pending schema migrations (the server also does this on start). Take a backup first."""
    from .migrate import MigrationError, migrate_database

    try:
        steps = _run(migrate_database(settings.database_url, dry_run=dry_run))
    except MigrationError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    target = _redacted(settings.database_url)
    if not steps:
        typer.echo(f"{target}: schema is up to date")
        return
    typer.echo(f"{target}:" + (" would" if dry_run else ""))
    for step in steps:
        typer.echo(f"  {step}")


@app.command()
def migrations():
    """List schema migrations: applied (with checksum check) and pending."""
    import psycopg

    from .migrate import load_repeatables, status

    async def go():
        async with await psycopg.AsyncConnection.connect(settings.database_url, autocommit=True) as conn:
            return await status(conn)

    st = _run(go())
    mismatched = {m.version for m, _ in st.mismatched}
    for row in st.applied:
        mark = "CHANGED SINCE APPLIED" if row["version"] in mismatched else ("baseline" if row["baseline"] else "applied")
        typer.echo(f"{row['version']:04d}_{row['name']:<28} {mark:<22} {row['applied_at']:%Y-%m-%d %H:%M}")
    if st.legacy:
        typer.echo(f"{'0001_baseline':<33} {'baseline due':<22} existing database from before migrations")
    for m in st.pending:
        typer.echo(f"{m.label:<33} pending")
    for row in st.unknown:
        typer.echo(f"{row['version']:04d}_{row['name']:<28} applied by a newer release (no file here)")
    for name in load_repeatables():
        typer.echo(f"{name + '.sql':<33} {'repeatable, due' if name in st.repeatables_due else 'repeatable, current'}")
    if st.mismatched or st.unknown:
        raise typer.Exit(1)


@app.command()
def init_db():
    """Create or upgrade the schema in the configured database (same as `migrate`)."""
    migrate(dry_run=False)


@app.command()
def create_org(name: str, slug: str | None = None):
    """Create an organization and print its first management key (shown once). Give the key to
    the customer: it signs in to the console and administers only this organization's projects."""
    from .auth import new_org_key

    slug = slug or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]
    key = new_org_key()

    async def go(conn):
        org = await (await conn.execute(
            "INSERT INTO organizations (slug, name) VALUES (%s, %s) ON CONFLICT (slug) DO NOTHING RETURNING id", (slug, name))).fetchone()
        if not org:
            raise typer.BadParameter(f"an organization with slug {slug!r} exists")
        await conn.execute("INSERT INTO org_keys (org_id, name, key_hash, key_suffix) VALUES (%s, 'default', %s, %s)",
                           (org["id"], hash_key(key), key[-4:]))

    _run(_with_pool(go))
    typer.echo(f"organization: {slug}\nmanagement key: {key}\nStore the key now; it is not shown again.")


@app.command()
def add_member(email: str, org: str = "default", role: str = "owner", rename: str | None = None):
    """Make an existing account a member of an organization (the operator's way into projects
    created before accounts existed, which live in `default`). --rename also renames the org."""
    if role not in ("owner", "admin", "member"):
        raise typer.BadParameter("role is owner, admin or member")

    async def go(conn):
        user = await (await conn.execute("SELECT id FROM users WHERE lower(email) = lower(%s)", (email,))).fetchone()
        if not user:
            raise typer.BadParameter(f"no account for {email}: sign up in the console first")
        found = await (await conn.execute("SELECT id FROM organizations WHERE slug = %s", (org,))).fetchone()
        if not found:
            raise typer.BadParameter(f"no organization {org!r}")
        await conn.execute(
            "INSERT INTO memberships (user_id, org_id, role) VALUES (%s, %s, %s) ON CONFLICT (user_id, org_id) DO UPDATE SET role = EXCLUDED.role",
            (user["id"], found["id"], role))
        if rename:
            await conn.execute("UPDATE organizations SET name = %s WHERE id = %s", (rename, found["id"]))

    _run(_with_pool(go))
    typer.echo(f"{email} is now {role} of {org}. Switch to it under Settings in the console.")


@app.command()
def create_project(name: str, slug: str | None = None, key_name: str = "default", org: str = "default"):
    """Create a project (in an organization, `default` unless given) and print its first API key."""
    slug = slug or "-".join(name.lower().split())[:40].strip("-")
    key = new_project_key()

    async def go(conn):
        row = await (
            await conn.execute(
                "INSERT INTO projects (slug, name, org_id) VALUES (%s, %s, (SELECT id FROM organizations WHERE slug = %s)) "
                "ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name RETURNING id",
                (slug, name, org),
            )
        ).fetchone()
        await conn.execute(
            "INSERT INTO api_keys (project_id, name, key_hash, key_suffix) VALUES (%s, %s, %s, %s)",
            (row["id"], key_name, hash_key(key), key[-4:]),
        )

    _run(_with_pool(go))
    typer.echo(f"project: {slug}\nkey ({key_name}): {key}\nStore the key now; it is not shown again.")


@app.command()
def backfill_passages(project: str = typer.Option("", help="a project slug; all projects when empty")):
    """Index source passages for episodes stored before passages existed (embeddings only, no LLM)."""
    from .embeddings import make_embedder
    from .passages import index_episode

    async def go(conn):
        embedder = make_embedder()
        rows = await (await conn.execute(
            "SELECT e.project_id, e.id FROM episodes e JOIN projects p ON p.id = e.project_id "
            "WHERE (%s = '' OR p.slug = %s) AND NOT EXISTS "
            "(SELECT 1 FROM passages x WHERE x.project_id = e.project_id AND x.episode_id = e.id)",
            (project, project))).fetchall()
        total = 0
        for r in rows:
            total += await index_episode(conn, embedder, r["project_id"], r["id"])
        return len(rows), total

    episodes, passages = _run(_with_pool(go))
    typer.echo(f"indexed {passages} passages from {episodes} episodes")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8400, reload: bool = False):
    """Run the API with the in-process synthesis worker."""
    uvicorn.run("memvine_server.main:app", host=host, port=port, reload=reload)


@app.command()
def worker():
    """Run only the synthesis worker (use with MEMVINE_WORKER_ENABLED=false on the API)."""
    from .embeddings import make_embedder
    from .extraction.llm import make_llm
    from .worker import run_forever

    async def go():
        pool = await open_pool()
        try:
            await run_forever(pool, make_llm(), make_embedder(), asyncio.Event())
        finally:
            await pool.close()

    _run(go())
