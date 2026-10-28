"""HTTP API. Routes follow the design doc's "Public API" table; v0 implements
ingest, recall, memory browsing and correction, episode deletion, and
project/key administration under the management key."""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import access, accounts, audit, billing, mcp_app
from . import code as code_ops
from . import edges as edge_ops
from . import events as event_ops
from . import health as health_ops
from . import ingest as ingest_ops
from . import instant as instant_ops
from . import review as review_ops
from . import sample as sample_ops
from . import sessions as session_ops
from . import threads as thread_ops
from .auth import (
    Admin,
    Principal,
    ensure_manager,
    find_project,
    hash_key,
    new_org_key,
    new_project_key,
    require_management,
    require_manager,
    require_operator,
    require_project,
)
from .config import settings
from .db import open_pool
from .embeddings import make_embedder
from .extraction.llm import LLMError, make_llm
from .models import (
    CodeCheck,
    EventFeed,
    GraphOut,
    IngestRequest,
    IngestResponse,
    KeyConnection,
    KeyCreate,
    KeyOut,
    MemoryCreate,
    MemoryCreated,
    MemoryEvent,
    MemoryOut,
    MemoryPatch,
    MemoryRelated,
    OrgCreate,
    ProjectCreate,
    ProjectOut,
    ProjectPatch,
    RecallRequest,
    RecallResponse,
    SessionBrief,
    SessionEventsRequest,
    SessionKept,
    SessionOut,
    ThreadDetail,
    ThreadSummary,
)
from .recall import recall as run_recall
from .remember import SupersedeError, remember
from .security import SecurityMiddleware
from .store import MEMORY_COLUMNS, get_memory, list_memories, unused_clause
from .worker import run_forever

log = logging.getLogger("memvine")


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await open_pool()
    app.state.pool = pool
    app.state.embedder = make_embedder()
    app.state.llm = make_llm()
    mcp_asgi = mcp_app.build_asgi()
    mcp_app.set_runtime(pool, app.state.embedder, app.state.llm, mcp_asgi)
    stop = asyncio.Event()
    worker = asyncio.create_task(run_forever(pool, app.state.llm, app.state.embedder, stop)) if settings.worker_enabled else None
    # The MCP transport's session manager must start and stop in one task; a background task
    # owns it so the surrounding lifespan may be driven from different tasks (as test harnesses do).
    mcp_ready, mcp_stop, mcp_task = asyncio.Event(), asyncio.Event(), None

    async def run_mcp():
        try:
            async with mcp_asgi.router.lifespan_context(mcp_asgi):
                mcp_ready.set()
                await mcp_stop.wait()
        finally:
            mcp_ready.set()  # also on failure, so startup never waits forever

    mcp_task = asyncio.create_task(run_mcp())
    await mcp_ready.wait()
    if mcp_task.done():
        mcp_task.result()  # raises the startup failure
    try:
        yield
    finally:
        mcp_stop.set()
        await mcp_task
        stop.set()
        if worker:
            # A job can take minutes of model calls; shutdown waits briefly, then cancels it. The
            # job's transaction rolls back and the job returns to the queue (worker.run_one).
            try:
                await asyncio.wait_for(asyncio.shield(worker), timeout=settings.worker_shutdown_seconds)
            except TimeoutError:
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass
        await pool.close()


app = FastAPI(title="Memvine Memory API", version="0.1.0", lifespan=lifespan)
app.mount("/mcp", mcp_app.mcp_with_auth)
app.include_router(accounts.router)
# Outermost: rate limits, the body cap and security headers apply to every route, /mcp included.
app.add_middleware(SecurityMiddleware)


@app.middleware("http")
async def request_log(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    principal: Principal | None = getattr(request.state, "principal", None)
    if principal and request.url.path.startswith("/v1/"):
        latency = int((time.perf_counter() - started) * 1000)
        try:
            async with request.app.state.pool.connection() as conn:
                await conn.execute(
                    "INSERT INTO request_log (project_id, method, path, status, latency_ms, key_id, detail) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (principal.project_id, request.method, request.url.path, response.status_code, latency, principal.key_id,
                     getattr(request.state, "log_detail", None)),
                )
        except Exception:
            log.exception("request log write failed")
    if request.method != "GET":
        try:
            # app, not request.app: a mounted app (/mcp) replaces the request's app with its own.
            await audit.record(app.state.pool, request, response.status_code)
        except Exception:
            log.exception("audit write failed")
    return response


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail}, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """422 as FastAPI words it, minus the echoed input: on account routes that is a password."""
    errors = [{k: v for k, v in e.items() if k != "input"} for e in exc.errors()]
    return JSONResponse(status_code=422, content=jsonable_encoder({"detail": errors}))


@app.get("/health")
async def health(request: Request):
    async with request.app.state.pool.connection() as conn:
        await conn.execute("SELECT 1")
    return {"ok": True, "version": app.version}


@app.get("/v1/health/synthesis")
async def synthesis_health(request: Request, admin: Admin = Depends(require_manager), project: str | None = Query(None)):
    """Whether synthesis is keeping up: jobs failed recently, a live job waiting too long in a project
    that is not paused, a worker that stopped polling. The operator sees every project; an
    organization's owners and admins see its own; `project` narrows to one."""
    async with request.app.state.pool.connection() as conn:
        project_id = None
        if project:
            found = await find_project(conn, admin, project)
            if not found:
                raise HTTPException(404, {"code": "not-found", "message": f"No project {project!r}"})
            project_id = found["id"]
        return await health_ops.synthesis_health(conn, org_id=None if admin.is_operator else admin.org_id, project_id=project_id)


# ------------------------------------------------------------------ memory


@app.post("/v1/ingest", response_model=IngestResponse, status_code=202)
async def ingest(body: IngestRequest, request: Request, response: Response, principal: Principal = Depends(require_project)):
    if body.instant and (len(body.items) != 1 or len(body.items[0].content) > instant_ops.INSTANT_MAX_CHARS):
        raise HTTPException(400, {"code": "instant-too-large", "message": (
            f"instant takes one episode of at most {instant_ops.INSTANT_MAX_CHARS:,} characters; "
            "send larger text or batches without instant and it is queued")})
    results = []
    async with request.app.state.pool.connection() as conn, conn.transaction():
        for slug in {ep.audience for ep in body.items}:
            if not await access.audience_exists(conn, principal.project_id, slug):
                raise HTTPException(400, {"code": "audience-unknown", "message": f"No audience {slug!r} in this project; create it with PUT /v1/audiences/{slug}"})
        # Charged for what is new or changed; a re-send of identical content is free.
        new_chars = 0
        for ep in body.items:
            same = await (await conn.execute(
                "SELECT 1 FROM episodes WHERE project_id = %s AND id = %s AND content = %s", (principal.project_id, ep.id, ep.content))).fetchone()
            if not same:
                new_chars += len(ep.content)
        if new_chars:
            await charge(conn, principal.project_id, "ingest", new_chars)
        for ep in body.items:
            results.append(await ingest_ops.ingest_episode(conn, principal.project_id, ep))
        if body.instant:
            now = await instant_ops.run_instant(conn, request.app.state.llm, request.app.state.embedder,
                                                principal.project_id, body.items[0].id)
            if now.status == "done":
                response.status_code = 200
                results = [r if r.outcome == "unchanged" else r.model_copy(update={"outcome": "processed"}) for r in results]
                return IngestResponse(items=results, queued=0, instant=now)
            return IngestResponse(items=results, queued=sum(r.outcome != "unchanged" for r in results), instant=now)
    return IngestResponse(items=results, queued=sum(r.outcome != "unchanged" for r in results))


@app.post("/v1/recall", response_model=RecallResponse)
async def recall(body: RecallRequest, request: Request, principal: Principal = Depends(require_project)):
    async with request.app.state.pool.connection() as conn:
        audiences = await access.readable_audiences(conn, principal.project_id, body.identity, principal.key_id is None)
        # The console's playground (no project key) is a person looking, not an agent using memory.
        result = await run_recall(conn, request.app.state.embedder, principal.project_id, body, audiences,
                                  track=principal.key_id is not None)
        request.state.log_detail = recall_log_detail(result)
        if principal.key_id is not None:  # the console's playground is not billed
            await billing.charge_recall(conn, principal.project_id)
        return result


def recall_log_detail(result: RecallResponse) -> str:
    """One line for the Requests log: what a recall served, or why it abstained."""
    top = f"{result.top_relevance:.3f}" if result.top_relevance is not None else "none"
    threshold = f"{result.min_relevance:.2f}" if result.min_relevance is not None else "none"
    if result.abstained:
        return f"abstained: {result.abstain_reason} (best relevance {top}, threshold {threshold})"
    return f"served {len(result.items)} items, {result.tokens} tokens (best relevance {top}, threshold {threshold})"


@app.post("/v1/sessions/{session_id}/events", response_model=SessionOut, status_code=202)
async def session_events(session_id: str, body: SessionEventsRequest, request: Request, principal: Principal = Depends(require_project)):
    """Stream a live session's turns. The working state refreshes in the background every few turns."""
    async with request.app.state.pool.connection() as conn, conn.transaction():
        await charge(conn, principal.project_id, "session", sum(len(e.content) for e in body.events))
        return await session_ops.append_events(conn, principal.project_id, session_id, body)


@app.get("/v1/sessions", response_model=list[SessionOut])
async def sessions(request: Request, principal: Principal = Depends(require_project), limit: int = Query(50, ge=1, le=500)):
    async with request.app.state.pool.connection() as conn:
        return await session_ops.list_sessions(conn, principal.project_id, limit)


@app.delete("/v1/sessions/{session_id}")
async def delete_session(session_id: str, request: Request, principal: Principal = Depends(require_project)):
    """Delete a session and what memory drew only from it (owners and admins). A client still
    sending to it starts a new session with its next message; disconnect that folder first."""
    ensure_manager(request)
    async with request.app.state.pool.connection() as conn, conn.transaction():
        result = await session_ops.delete_session(conn, principal.project_id, session_id)
    if result is None:
        raise HTTPException(404, {"code": "not-found", "message": "No such session"})
    return {"deleted": session_id, **result}


@app.get("/v1/sessions/{session_id}", response_model=SessionOut)
async def session(session_id: str, request: Request, principal: Principal = Depends(require_project)):
    async with request.app.state.pool.connection() as conn:
        out = await session_ops.get_session(conn, principal.project_id, session_id)
    if not out:
        raise HTTPException(404, {"code": "not-found", "message": "No such session"})
    return out


@app.get("/v1/sessions/{session_id}/brief", response_model=SessionBrief)
async def session_brief(session_id: str, request: Request, principal: Principal = Depends(require_project),
                        budget: int = Query(2000, ge=200, le=32000)):
    """For an agent after compaction or a reset: the session's state brought up to date, then
    long-term memory relevant to its goal, within the budget."""
    state = request.app.state
    async with state.pool.connection() as conn:
        current = await session_ops.get_session(conn, principal.project_id, session_id)
        if not current:
            raise HTTPException(404, {"code": "not-found", "message": "No such session"})
        if current.state_through < current.turns:
            try:
                await session_ops.refresh_state(conn, state.llm, principal.project_id, session_id)
                current = await session_ops.get_session(conn, principal.project_id, session_id)
            except LLMError as exc:
                # A stale brief beats no brief for an agent mid-compaction; the brief says how far it reaches.
                log.warning("brief for a session uses a stale state: %s", type(exc).__name__)
        text, tokens = await session_ops.brief(conn, state.embedder, principal.project_id, current, budget)
    return SessionBrief(session=current, text=text, tokens=tokens)


@app.post("/v1/sessions/{session_id}/close")
async def session_close(session_id: str, request: Request, principal: Principal = Depends(require_project)):
    """End a session now: its turns become episodes and are queued for extraction."""
    async with request.app.state.pool.connection() as conn, conn.transaction():
        if not await session_ops.get_session(conn, principal.project_id, session_id):
            raise HTTPException(404, {"code": "not-found", "message": "No such session"})
        return await session_ops.synthesize_session(conn, principal.project_id, session_id)


@app.get("/v1/sessions/{session_id}/kept", response_model=SessionKept)
async def session_kept(session_id: str, request: Request, principal: Principal = Depends(require_project)):
    """What memory kept from a session: the memories it added (not ones it only restated), how many
    facts they superseded, and a summary line ("Memory kept 2 decisions and 1 gotcha; 1 fact
    superseded."). `tending` is true while synthesis of the session is not finished."""
    async with request.app.state.pool.connection() as conn:
        out = await session_ops.kept(conn, principal.project_id, session_id)
    if not out:
        raise HTTPException(404, {"code": "not-found", "message": "No such session"})
    return out


@app.put("/v1/identities/{identity}")
async def put_identity(identity: str, body: access.IdentityIn, request: Request, principal: Principal = Depends(require_project)):
    """Create an identity or replace its traits. Returns the audiences it now reaches."""
    ensure_manager(request)
    async with request.app.state.pool.connection() as conn:
        return await access.put_identity(conn, principal.project_id, identity, body)


@app.get("/v1/identities")
async def identities(request: Request, principal: Principal = Depends(require_project), limit: int = Query(200, ge=1, le=1000)):
    async with request.app.state.pool.connection() as conn:
        return await (await conn.execute(
            "SELECT identity, traits, origin, created_at, last_seen_at FROM identities WHERE project_id = %s "
            "ORDER BY last_seen_at DESC NULLS LAST, identity LIMIT %s", (principal.project_id, limit))).fetchall()


@app.get("/v1/identities/{identity}")
async def identity_detail(identity: str, request: Request, principal: Principal = Depends(require_project)):
    async with request.app.state.pool.connection() as conn:
        row = await (await conn.execute(
            "SELECT identity, traits, origin, created_at, last_seen_at FROM identities WHERE project_id = %s AND identity = %s",
            (principal.project_id, identity))).fetchone()
        if not row:
            raise HTTPException(404, {"code": "not-found", "message": "No such identity"})
        return {**row, "audiences": await access.audiences_for(conn, principal.project_id, identity)}


@app.delete("/v1/identities/{identity}", status_code=204)
async def delete_identity(identity: str, request: Request, principal: Principal = Depends(require_project)):
    """Remove the identity and its fixed-audience memberships. Memory it wrote stays."""
    ensure_manager(request)
    async with request.app.state.pool.connection() as conn, conn.transaction():
        await conn.execute("UPDATE audiences SET members = array_remove(members, %s) WHERE project_id = %s", (identity, principal.project_id))
        await conn.execute("DELETE FROM identities WHERE project_id = %s AND identity = %s", (principal.project_id, identity))


@app.put("/v1/audiences/{slug}")
async def put_audience(slug: str, body: access.AudienceIn, request: Request, principal: Principal = Depends(require_project)):
    """Create or replace an audience: a fixed list of identities, or a rule on their traits."""
    ensure_manager(request)
    if slug == access.PROJECT:
        raise HTTPException(400, {"code": "audience-reserved", "message": "'project' is the whole project and cannot be defined"})
    try:
        async with request.app.state.pool.connection() as conn, conn.transaction():
            return await access.put_audience(conn, principal.project_id, slug, body)
    except ValueError as exc:
        raise HTTPException(400, {"code": "invalid-audience", "message": str(exc)}) from exc
    except psycopg.errors.CheckViolation as exc:
        raise HTTPException(400, {"code": "invalid-slug", "message": "2-40 lowercase letters, digits and dashes"}) from exc


@app.get("/v1/audiences")
async def audiences(request: Request, principal: Principal = Depends(require_project)):
    async with request.app.state.pool.connection() as conn:
        return await (await conn.execute(
            "SELECT a.slug, a.kind, a.members, a.conditions, a.created_at, "
            "(SELECT count(*) FROM memory_items m WHERE m.project_id = a.project_id AND m.audience = a.slug) AS memory_count "
            "FROM audiences a WHERE a.project_id = %s ORDER BY a.slug", (principal.project_id,))).fetchall()


@app.delete("/v1/audiences/{slug}", status_code=204)
async def delete_audience(slug: str, request: Request, principal: Principal = Depends(require_project)):
    ensure_manager(request)
    async with request.app.state.pool.connection() as conn:
        used = await (await conn.execute(
            "SELECT 1 FROM episodes WHERE project_id = %s AND audience = %s LIMIT 1", (principal.project_id, slug))).fetchone()
        if used:
            raise HTTPException(409, {"code": "audience-in-use", "message": "Episodes still name this audience; delete or re-send them first"})
        await conn.execute("DELETE FROM audiences WHERE project_id = %s AND slug = %s", (principal.project_id, slug))


@app.get("/v1/memories", response_model=list[MemoryOut])
async def memories(
    request: Request,
    principal: Principal = Depends(require_project),
    status: str | None = None,
    kind: str | None = None,
    category: str | None = None,
    scope: Literal["project", "thread"] | None = None,
    review: Literal["new", "kept"] | None = Query(None, description="new: current memories no person has kept yet"),
    q: str | None = None,
    unused: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """status=expired lists items past the end they stated; unused=true lists items no recall served
    in memory_unused_days (for review: nothing is hidden or deleted for being unused)."""
    async with request.app.state.pool.connection() as conn:
        return await list_memories(conn, principal.project_id, status=status, kind=kind, q=q, limit=limit, offset=offset,
                                   category=category, scope=scope, unused=unused, review=review)


@app.get("/v1/threads", response_model=list[ThreadSummary])
async def threads(
    request: Request,
    principal: Principal = Depends(require_project),
    status: str | None = None,
    kind: str | None = None,
    q: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """The project's pieces of work, most recently active first."""
    async with request.app.state.pool.connection() as conn:
        rows = await thread_ops.list_threads(conn, principal.project_id, status=status, kind=kind, q=q, limit=limit, offset=offset)
    return [ThreadSummary(**r) for r in rows]


@app.get("/v1/threads/{thread_id}", response_model=ThreadDetail)
async def thread(thread_id: UUID, request: Request, principal: Principal = Depends(require_project)):
    """One thread's page with its related threads, the knowledge filed under it, and its sources."""
    async with request.app.state.pool.connection() as conn:
        out = await thread_ops.get_thread(conn, principal.project_id, thread_id)
    if not out:
        raise HTTPException(404, {"code": "not-found", "message": "No such thread"})
    return ThreadDetail(**out)


@app.post("/v1/memories", response_model=MemoryCreated, status_code=201)
async def create_memory(body: MemoryCreate, request: Request, principal: Principal = Depends(require_project)):
    """Store one fact directly, reconciled against what is known: a changed value supersedes the
    old one, a repeat merges into it (relation 'same'). With `supersedes`, the caller says which
    fact this replaces: no judge is asked, it supersedes that one (relation 'update') and inherits
    its kind, subjects, category, thread, scope and audience unless they are given."""
    state = request.app.state
    async with state.pool.connection() as conn, conn.transaction():
        if not await access.audience_exists(conn, principal.project_id, body.audience):
            raise HTTPException(400, {"code": "audience-unknown", "message": f"No audience {body.audience!r} in this project"})
        try:
            decision = await remember(conn, state.llm, state.embedder, principal.project_id, statement=body.statement,
                                      kind=body.kind, subjects=body.subjects, assertion=body.assertion,
                                      audience=body.audience, standing=body.standing, category=body.category, why=body.why,
                                      supersedes=body.supersedes, given=set(body.model_fields_set),
                                      actor=event_ops.actor_of(request))
        except billing.CapReached as exc:
            raise HTTPException(429, {"code": "project-cap-reached", "message": str(exc)}) from exc
        except SupersedeError as exc:
            raise HTTPException(exc.status, {"code": exc.code, "message": exc.message}) from exc
        memory = await get_memory(conn, principal.project_id, decision.memory_id)
    request.state.audit_target = str(memory.id)
    return MemoryCreated(memory=memory, relation=decision.relation, related=decision.related_id)


@app.get("/v1/memories/{memory_id}", response_model=MemoryOut)
async def memory(memory_id: UUID, request: Request, principal: Principal = Depends(require_project)):
    async with request.app.state.pool.connection() as conn:
        out = await get_memory(conn, principal.project_id, memory_id)
    if not out:
        raise HTTPException(404, {"code": "not-found", "message": "No such memory"})
    return out


@app.get("/v1/memories/{memory_id}/related", response_model=MemoryRelated)
async def memory_related(memory_id: UUID, request: Request, principal: Principal = Depends(require_project)):
    """The memory and every edge it has, both directions: supersedes, contradicts, extends and derived
    with the other memory; anchored with the file, its anchored version and whether it is current;
    decided_in with the thread."""
    async with request.app.state.pool.connection() as conn:
        out = await edge_ops.related(conn, principal.project_id, memory_id)
    if not out:
        raise HTTPException(404, {"code": "not-found", "message": "No such memory"})
    return out


@app.get("/v1/graph", response_model=GraphOut)
async def memory_graph(request: Request, principal: Principal = Depends(require_project), since: datetime | None = None,
                       thread: UUID | None = None, subject: str | None = Query(None, max_length=200),
                       everything: bool = Query(False, alias="all")):
    """A neighborhood of the memory graph for the console: facts, threads, files and people, with
    their edges, at most 300 nodes. `thread` and `subject` narrow it; without them it is the open
    threads and the most recent facts, or with `all=true` every thread whatever its status and the
    most recent facts (`truncated` when the cap left something out). Nodes added or changed since
    `since` carry grew: true."""
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    async with request.app.state.pool.connection() as conn:
        out = await edge_ops.graph(conn, principal.project_id, since=since, thread=thread, subject=subject, everything=everything)
    if out is None:
        raise HTTPException(404, {"code": "not-found", "message": "No such thread"})
    return out


@app.patch("/v1/memories/{memory_id}", response_model=MemoryOut)
async def patch_memory(memory_id: UUID, body: MemoryPatch, request: Request, principal: Principal = Depends(require_project)):
    actor = event_ops.actor_of(request)
    async with request.app.state.pool.connection() as conn, conn.transaction():
        current = await get_memory(conn, principal.project_id, memory_id)
        if not current:
            raise HTTPException(404, {"code": "not-found", "message": "No such memory"})
        if body.status == "retired":
            # A person forgets it: an older fact it replaced is current again; synthesis will not
            # learn it again; a synthesis-made one becomes a feedback case (review.py).
            await review_ops.forget(conn, principal.project_id, memory_id, actor=actor)
        elif body.status and current.status != "superseded":
            await conn.execute(
                "UPDATE memory_items SET status = %s, valid_to = CASE WHEN %s = 'retired' THEN now() ELSE NULL END WHERE id = %s",
                (body.status, body.status, memory_id),
            )
            if body.status == "active" and current.status == "retired":
                await review_ops.restore(conn, principal.project_id, memory_id, actor=actor)
            elif body.status == "active" and current.status == "stale":
                await event_ops.record(conn, principal.project_id, "revalidated", memory_id=memory_id, actor=actor,
                                       detail={"reason": "marked active"})
        if body.confirm:
            await conn.execute(
                "UPDATE memory_items SET assertion = 'observed', confidence = 'high', review = 'kept' WHERE id = %s", (memory_id,)
            )
            await event_ops.record(conn, principal.project_id, "confirmed", memory_id=memory_id, actor=actor)
            await code_ops.revalidate(conn, principal.project_id, memory_id, actor=actor)
        return await get_memory(conn, principal.project_id, memory_id)


@app.get("/v1/memories/{memory_id}/history", response_model=list[MemoryEvent])
async def memory_history(memory_id: UUID, request: Request, principal: Principal = Depends(require_project)):
    """Everything that happened to one memory, oldest first: created (with its evidence quotes),
    restated, superseded and supersedes (with the other memory's statement), contradicted, extended,
    confirmed, forgotten, restored, corrected, anchored, stale, revalidated, and expired (computed)."""
    async with request.app.state.pool.connection() as conn:
        out = await event_ops.history(conn, principal.project_id, memory_id)
    if out is None:
        raise HTTPException(404, {"code": "not-found", "message": "No such memory"})
    return out


@app.get("/v1/events", response_model=EventFeed)
async def memory_events(request: Request, principal: Principal = Depends(require_project), since: datetime | None = None,
                        kind: list[str] | None = Query(None, description="one or more event kinds"),
                        memory_id: UUID | None = None, limit: int = Query(50, ge=1, le=200),
                        cursor: str | None = Query(None, max_length=200)):
    """The project's activity: memory events, newest first. Pass `next_cursor` back as `cursor` for
    the next page."""
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    unknown = [k for k in kind or [] if k not in event_ops.KINDS]
    if unknown:
        raise HTTPException(400, {"code": "unknown-kind", "message": f"Unknown event kind {unknown[0]!r}"})
    async with request.app.state.pool.connection() as conn:
        try:
            return await event_ops.feed(conn, principal.project_id, since=since, kinds=kind, memory_id=memory_id,
                                        limit=limit, cursor=cursor)
        except ValueError as exc:
            raise HTTPException(400, {"code": "bad-cursor", "message": "Not a cursor from this feed"}) from exc


@app.get("/v1/code/anchors")
async def code_anchors(repo: str, request: Request, principal: Principal = Depends(require_project)):
    """The files of a repository that this project's memories are anchored to: what a client
    should hash and report to POST /v1/code/check."""
    async with request.app.state.pool.connection() as conn:
        return {"repo": repo, "paths": await code_ops.anchored_paths(conn, principal.project_id, repo)}


@app.post("/v1/code/check")
async def code_check(body: CodeCheck, request: Request, principal: Principal = Depends(require_project)):
    """Report the current versions of files; memories about files that changed turn stale (and back)."""
    async with request.app.state.pool.connection() as conn, conn.transaction():
        result = await code_ops.check(conn, principal.project_id, body.repo, body.files, actor=event_ops.actor_of(request))
    return {"checked": result["checked"], "stale": len(result["stale"]), "fresh": len(result["fresh"])}


@app.post("/v1/export")
async def export_project(request: Request, principal: Principal = Depends(require_project)):
    """Everything this project holds, as one JSON document: sources, memories with their evidence
    and code anchors, thread pages, sessions with their messages, identities and audiences.
    Owners and admins only; recorded in the audit log. Embeddings are left out (derived data)."""
    ensure_manager(request)
    pid = principal.project_id

    async def rows(sql: str):
        return await (await conn.execute(sql, (pid,))).fetchall()

    async with request.app.state.pool.connection() as conn:
        project = await (await conn.execute(
            "SELECT slug, name, tags, created_at, monthly_credit_cap, retention_days, description FROM projects WHERE id = %s",
            (pid,))).fetchone()
        memories = await rows(f"SELECT {MEMORY_COLUMNS} FROM memory_items WHERE project_id = %s ORDER BY recorded_at")
        evidence = await rows("SELECT memory_id, episode_id, quote FROM evidence WHERE project_id = %s")
        anchors = await rows("SELECT memory_id, repo, path, blob, anchored_at FROM memory_anchors WHERE project_id = %s")
        by_memory: dict = {}
        for e in evidence:
            by_memory.setdefault(e["memory_id"], {"evidence": [], "anchors": []})["evidence"].append({"episode_id": e["episode_id"], "quote": e["quote"]})
        for a in anchors:
            by_memory.setdefault(a["memory_id"], {"evidence": [], "anchors": []})["anchors"].append(
                {k: a[k] for k in ("repo", "path", "blob", "anchored_at")})
        events = await rows("SELECT session_id, seq, role, content, at FROM session_events WHERE project_id = %s ORDER BY session_id, seq")
        by_session: dict = {}
        for ev in events:
            by_session.setdefault(ev["session_id"], []).append({k: ev[k] for k in ("seq", "role", "content", "at")})
        document = {
            "format": "memvine-export/1",
            "exported_at": datetime.now(UTC),
            "project": project,
            "episodes": await rows("SELECT id, content, occurred_at, identity, label, metadata, audience, code, created_at "
                                   "FROM episodes WHERE project_id = %s ORDER BY occurred_at"),
            "memories": [{**m, **by_memory.get(m["id"], {"evidence": [], "anchors": []})} for m in memories],
            "memory_links": await rows("SELECT from_id, to_id, kind, created_at, episode_id FROM memory_links "
                                       "WHERE project_id = %s ORDER BY created_at"),
            "threads": await rows(
                "SELECT t.id, t.kind, t.key, t.title, t.status, t.summary, t.sections, t.aliases, t.first_seen_at, t.last_activity_at, "
                "ARRAY(SELECT episode_id FROM thread_sources s WHERE s.thread_id = t.id) AS sources, "
                "ARRAY(SELECT related_id FROM thread_links l WHERE l.thread_id = t.id) AS related "
                "FROM threads t WHERE t.project_id = %s ORDER BY t.first_seen_at"),
            "sessions": [{**s, "events": by_session.get(s["id"], [])} for s in await rows(
                "SELECT id, agent, identity, label, status, started_at, last_event_at, turns, state FROM sessions WHERE project_id = %s ORDER BY started_at")],
            "identities": await rows("SELECT identity, traits, origin, created_at, last_seen_at FROM identities WHERE project_id = %s ORDER BY identity"),
            "audiences": await rows("SELECT * FROM audiences WHERE project_id = %s"),
            "feedback_cases": await rows(f"SELECT {review_ops.CASE_COLUMNS} FROM feedback_cases WHERE project_id = %s ORDER BY created_at"),
            "memory_events": await rows("SELECT id, memory_id, kind, actor, detail, episode_id, at FROM memory_events "
                                        "WHERE project_id = %s ORDER BY at, id"),
        }
    request.state.audit_target = principal.project_slug
    filename = f"memvine-{principal.project_slug}-{datetime.now(UTC):%Y%m%d}.json"
    return JSONResponse(jsonable_encoder(document), headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/v1/feedback-cases")
async def feedback_cases(request: Request, principal: Principal = Depends(require_project),
                         limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0)):
    """Private feedback for extraction: synthesis-made memories a person forgot or corrected, each with
    its statement, category, evidence quotes and an excerpt of its source. Owners and admins only
    (they quote source text). `memvine-server feedback-export` writes the same as JSONL."""
    ensure_manager(request)
    async with request.app.state.pool.connection() as conn:
        cases = await review_ops.list_cases(conn, principal.project_id, limit=limit, offset=offset)
    return {"cases": jsonable_encoder(cases)}


@app.delete("/v1/episodes/{episode_id}", status_code=204)
async def delete_episode(episode_id: str, request: Request, principal: Principal = Depends(require_project)):
    ensure_manager(request)
    async with request.app.state.pool.connection() as conn, conn.transaction():
        found = await ingest_ops.delete_episode(conn, principal.project_id, episode_id, actor=event_ops.actor_of(request))
    if not found:
        raise HTTPException(404, {"code": "not-found", "message": "No such episode"})


@app.post("/v1/sample")
async def load_sample(request: Request, principal: Principal = Depends(require_project)):
    """Fill the project with a small sample memory (sources marked as sample) to try recall against.
    Written directly with the server's embedder: no model call, no credits. Loading again when the
    sample is already there changes nothing (`loaded: false`)."""
    ensure_manager(request)
    async with request.app.state.pool.connection() as conn, conn.transaction():
        return await sample_ops.load_sample(conn, request.app.state.embedder, principal.project_id)


@app.delete("/v1/sample")
async def delete_sample(request: Request, principal: Principal = Depends(require_project)):
    """Delete the sample sources and every memory drawn from them."""
    ensure_manager(request)
    async with request.app.state.pool.connection() as conn, conn.transaction():
        return await sample_ops.delete_sample(conn, principal.project_id)


@app.get("/v1/jobs")
async def jobs(request: Request, principal: Principal = Depends(require_project), limit: int = Query(50, ge=1, le=500)):
    """Synthesis queue for this project: what is pending, done, or failed and why."""
    async with request.app.state.pool.connection() as conn:
        rows = await (
            await conn.execute(
                "SELECT id, episode_id, status, attempts, error, result, created_at, finished_at FROM jobs "
                "WHERE project_id = %s ORDER BY id DESC LIMIT %s",
                (principal.project_id, limit),
            )
        ).fetchall()
        # Counts cover the whole project, not just the page, so a caller can wait on them.
        counts = {
            r["status"]: r["n"]
            for r in await (
                await conn.execute(
                    "SELECT status, count(*) AS n FROM jobs WHERE project_id = %s GROUP BY status", (principal.project_id,)
                )
            ).fetchall()
        }
    return {"jobs": rows, "counts": counts, "pending": counts.get("queued", 0) + counts.get("running", 0)}


@app.post("/v1/jobs/{job_id}/retry", status_code=202)
async def retry_job(job_id: int, request: Request, principal: Principal = Depends(require_project)):
    """Queue a failed synthesis job again."""
    async with request.app.state.pool.connection() as conn:
        row = await (
            await conn.execute(
                "UPDATE jobs SET status = 'queued', attempts = 0, error = NULL, run_after = now() "
                "WHERE id = %s AND project_id = %s AND status = 'failed' RETURNING id",
                (job_id, principal.project_id),
            )
        ).fetchone()
    if not row:
        raise HTTPException(404, {"code": "not-found", "message": "No failed job with that id"})
    return {"id": job_id, "status": "queued"}


@app.get("/v1/episodes")
async def episodes(request: Request, principal: Principal = Depends(require_project), limit: int = Query(50, ge=1, le=500)):
    """Sources in this project, newest first, with their synthesis status and how much memory came out.

    job_status is the latest extraction job's: queued, running (synthesizing), done or failed. A queued
    job with a job_error is waiting to retry after a failed attempt (job_attempts so far, next try at
    job_retry_at); a failed one has used every attempt. memory_count counts memory items with evidence
    in the source; thread_count the thread pages it fed."""
    async with request.app.state.pool.connection() as conn:
        rows = await (
            await conn.execute(
                """
                SELECT e.id, e.label, e.identity, e.occurred_at, e.created_at, length(e.content) AS bytes,
                       j.id AS job_id, j.status AS job_status, j.error AS job_error, j.result AS job_result,
                       j.attempts AS job_attempts, j.finished_at AS job_finished_at,
                       CASE WHEN j.status = 'queued' AND j.run_after > now() THEN j.run_after END AS job_retry_at,
                       (SELECT count(DISTINCT memory_id) FROM evidence v WHERE v.project_id = e.project_id AND v.episode_id = e.id) AS memory_count,
                       (SELECT count(*) FROM thread_sources t WHERE t.project_id = e.project_id AND t.episode_id = e.id) AS thread_count
                FROM episodes e
                LEFT JOIN LATERAL (
                    SELECT id, status, error, result, attempts, run_after, finished_at FROM jobs
                    WHERE project_id = e.project_id AND episode_id = e.id AND kind = 'extract'
                    ORDER BY id DESC LIMIT 1
                ) j ON TRUE
                WHERE e.project_id = %s ORDER BY e.occurred_at DESC LIMIT %s
                """,
                (principal.project_id, limit),
            )
        ).fetchall()
    return rows


@app.get("/v1/episodes/{episode_id}")
async def episode(episode_id: str, request: Request, principal: Principal = Depends(require_project)):
    async with request.app.state.pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT id, label, identity, occurred_at, content, metadata FROM episodes WHERE project_id = %s AND id = %s",
                (principal.project_id, episode_id),
            )
        ).fetchone()
    if not row:
        raise HTTPException(404, {"code": "not-found", "message": "No such episode"})
    return row


@app.get("/v1/requests")
async def requests(request: Request, principal: Principal = Depends(require_project), limit: int = Query(100, ge=1, le=500)):
    """This project's API calls, newest first. `detail` says what a call did where that matters: for a
    recall, how many items it served or why it abstained."""
    async with request.app.state.pool.connection() as conn:
        rows = await (
            await conn.execute(
                "SELECT r.id, r.at, r.method, r.path, r.status, r.latency_ms, k.name AS key_name, r.detail "
                "FROM request_log r LEFT JOIN api_keys k ON k.id = r.key_id "
                "WHERE r.project_id = %s ORDER BY r.id DESC LIMIT %s",
                (principal.project_id, limit),
            )
        ).fetchall()
    return rows


@app.get("/v1/usage")
async def usage(request: Request, principal: Principal = Depends(require_project), days: int = Query(30, ge=1, le=365)):
    """Billable calls and credits per day over the window, every day present."""
    async with request.app.state.pool.connection() as conn:
        rows = await (
            await conn.execute(
                """
                SELECT d::date AS date,
                       count(u.id) FILTER (WHERE u.kind IN ('ingest', 'session')) AS ingest,
                       count(u.id) FILTER (WHERE u.kind = 'recall') AS recall,
                       coalesce(sum(u.credits) FILTER (WHERE u.kind IN ('ingest', 'session')), 0) AS ingest_credits,
                       coalesce(sum(u.credits) FILTER (WHERE u.kind = 'recall'), 0) AS recall_credits
                FROM generate_series((now() AT TIME ZONE 'UTC')::date - (%s - 1), (now() AT TIME ZONE 'UTC')::date, '1 day') d
                LEFT JOIN usage_events u ON u.project_id = %s AND (u.at AT TIME ZONE 'UTC')::date = d::date
                GROUP BY d ORDER BY d
                """,
                (days, principal.project_id),
            )
        ).fetchall()
    return rows


# ------------------------------------------------------------------ administration


async def charge(conn, project_id, kind: str, chars: int) -> None:
    try:
        await billing.charge_ingest(conn, project_id, kind, chars)
    except billing.CapReached as exc:
        raise HTTPException(429, {"code": "project-cap-reached", "message": f"This project's monthly credit cap is reached ({exc.used} of {exc.cap}); raise it or wait for the month to reset"}) from exc


PROJECT_SELECT = """
    SELECT p.id, p.slug, p.name, p.created_at, p.tags, p.monthly_credit_cap, p.retention_days, p.processing_paused,
           p.description, p.recall_min_relevance,
           (SELECT coalesce(sum(u.credits), 0) FROM usage_events u WHERE u.project_id = p.id
             AND u.at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') AS month_credits,
           (SELECT count(*) FROM memory_items m WHERE m.project_id = p.id) AS memory_count,
           (SELECT count(*) FROM memory_items m WHERE m.project_id = p.id AND """ + unused_clause(settings.memory_unused_days) + """) AS unused_count,
           """ + str(int(settings.memory_unused_days)) + """ AS unused_days,
           (SELECT count(*) FROM memory_items m WHERE m.project_id = p.id AND m.review = 'new'
             AND m.status IN ('active', 'stale')) AS review_count,
           (SELECT count(*) FROM episodes e WHERE e.project_id = p.id) AS source_count,
           (SELECT count(*) FROM episodes e WHERE e.project_id = p.id AND e.metadata->>'source' = 'sample') AS sample_source_count,
           (SELECT count(*) FROM api_keys k WHERE k.project_id = p.id AND k.revoked_at IS NULL) AS key_count,
           -- calls made with the project's own keys; the console's management-key reads don't count
           (SELECT max(r.at) FROM request_log r WHERE r.project_id = p.id AND r.key_id IS NOT NULL) AS last_activity_at
    FROM projects p
"""


def slug_from(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-") or "project"


async def _owned(conn, admin: Admin, slug: str):
    project = await find_project(conn, admin, slug)
    if not project:
        raise HTTPException(404, {"code": "not-found", "message": "No such project"})
    return project


@app.post("/v1/projects", response_model=ProjectOut, status_code=201)
async def create_project(body: ProjectCreate, request: Request, admin: Admin = Depends(require_manager)):
    """Create a project in the caller's organization (the operator may name one with `org`)."""
    slug = body.slug or slug_from(body.name)
    async with request.app.state.pool.connection() as conn:
        org_slug = body.org if admin.is_operator and body.org else None
        if admin.is_operator:
            org = await (await conn.execute("SELECT id FROM organizations WHERE slug = %s", (org_slug or "default",))).fetchone()
            if not org:
                raise HTTPException(404, {"code": "not-found", "message": f"No organization {org_slug!r}"})
            org_id = org["id"]
        else:
            org_id = admin.org_id
        # Names are unique within an organization. Slugs are unique on the server (they name the
        # project in X-Memvine-Project), so a name another organization also uses gets a suffixed
        # slug, and nothing reveals that the other project exists.
        if await (await conn.execute("SELECT 1 FROM projects WHERE org_id = %s AND (slug = %s OR lower(name) = lower(%s))",
                                     (org_id, slug, body.name))).fetchone():
            raise HTTPException(409, {"code": "slug-taken", "message": "This organization already has a project with that name"})
        row = None
        for attempt in range(5):
            candidate = slug if attempt == 0 else f"{slug}-{secrets.token_hex(2)}"
            row = await (
                await conn.execute(
                    "INSERT INTO projects (slug, name, tags, org_id, description) VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (slug) DO NOTHING RETURNING id",
                    (candidate, body.name, sorted(set(body.tags)), org_id, body.description),
                )
            ).fetchone()
            if row or body.slug:
                break
        if not row:
            raise HTTPException(409, {"code": "slug-taken", "message": f"The slug {slug!r} is not available; choose another"})
        if body.sample:
            await sample_ops.load_sample(conn, request.app.state.embedder, row["id"])
        created = await (await conn.execute(PROJECT_SELECT + " WHERE p.id = %s", (row["id"],))).fetchone()
    request.state.audit_target = created["slug"]
    return ProjectOut(**created)


@app.get("/v1/projects", response_model=list[ProjectOut])
async def projects(request: Request, admin: Admin = Depends(require_management)):
    async with request.app.state.pool.connection() as conn:
        if admin.is_operator:
            rows = await (await conn.execute(PROJECT_SELECT + " ORDER BY p.created_at")).fetchall()
        else:
            rows = await (await conn.execute(PROJECT_SELECT + " WHERE p.org_id = %s ORDER BY p.created_at", (admin.org_id,))).fetchall()
    return [ProjectOut(**r) for r in rows]


@app.patch("/v1/projects/{slug}", response_model=ProjectOut)
async def patch_project(slug: str, body: ProjectPatch, request: Request, admin: Admin = Depends(require_manager)):
    """Rename, retag, pause, describe, or change the cap, retention or recall
    relevance threshold of a project. The slug never changes."""
    async with request.app.state.pool.connection() as conn:
        project = await _owned(conn, admin, slug)
        await conn.execute(
            """
            UPDATE projects SET name = COALESCE(%s, name), tags = COALESCE(%s, tags),
                monthly_credit_cap = CASE WHEN %s THEN %s ELSE monthly_credit_cap END,
                retention_days = CASE WHEN %s THEN %s ELSE retention_days END,
                processing_paused = COALESCE(%s, processing_paused),
                description = CASE WHEN %s THEN %s ELSE description END,
                recall_min_relevance = CASE WHEN %s THEN %s ELSE recall_min_relevance END
            WHERE id = %s
            """,
            (body.name, sorted(set(body.tags)) if body.tags is not None else None,
             "monthly_credit_cap" in body.model_fields_set, body.monthly_credit_cap,
             "retention_days" in body.model_fields_set, body.retention_days, body.processing_paused,
             "description" in body.model_fields_set, (body.description or "").strip() or None,
             "recall_min_relevance" in body.model_fields_set, body.recall_min_relevance, project["id"]),
        )
        updated = await (await conn.execute(PROJECT_SELECT + " WHERE p.id = %s", (project["id"],))).fetchone()
    return ProjectOut(**updated)


@app.delete("/v1/projects/{slug}", status_code=204)
async def delete_project(slug: str, request: Request, admin: Admin = Depends(require_manager),
                         confirm: str = Query("", description="must equal the slug")):
    """Delete a project and everything in it: sources, memories, thread pages, sessions, keys. Irreversible,
    so the caller repeats the slug in ?confirm=."""
    if confirm != slug:
        raise HTTPException(400, {"code": "confirm-required", "message": "Repeat the project slug in ?confirm= to delete it"})
    async with request.app.state.pool.connection() as conn:
        project = await _owned(conn, admin, slug)
        await conn.execute("DELETE FROM projects WHERE id = %s", (project["id"],))
        # Tables without a foreign key to projects.
        for table in ("passages", "session_events", "request_log", "evidence"):
            await conn.execute(f"DELETE FROM {table} WHERE project_id = %s", (project["id"],))


@app.get("/v1/projects/{slug}/keys", response_model=list[KeyOut])
async def keys(slug: str, request: Request, admin: Admin = Depends(require_management)):
    async with request.app.state.pool.connection() as conn:
        project = await _owned(conn, admin, slug)
        rows = await (
            await conn.execute(
                "SELECT k.id, k.name, k.key_suffix AS suffix, k.created_at, k.last_used_at, k.last_used_ip, k.revoked_at, "
                "k.last_user_agent, u.email AS created_by_email FROM api_keys k LEFT JOIN users u ON u.id = k.created_by "
                "WHERE k.project_id = %s ORDER BY k.created_at DESC",
                (project["id"],),
            )
        ).fetchall()
    return [KeyOut(**r) for r in rows]


@app.post("/v1/projects/{slug}/keys", response_model=KeyOut, status_code=201)
async def create_key(slug: str, body: KeyCreate, request: Request, admin: Admin = Depends(require_management)):
    key = new_project_key()
    async with request.app.state.pool.connection() as conn:
        project = await _owned(conn, admin, slug)
        row = await (
            await conn.execute(
                "INSERT INTO api_keys (project_id, name, key_hash, key_suffix, created_by) VALUES (%s, %s, %s, %s, %s) "
                "RETURNING id, name, key_suffix AS suffix, created_at, last_used_at, last_used_ip, revoked_at",
                (project["id"], body.name, hash_key(key), key[-4:], admin.user_id),
            )
        ).fetchone()
    request.state.audit_target = f"{slug}/keys/{row['id']}"
    return KeyOut(**row, key=key, created_by_email=admin.user_email if admin.user_id else None)


@app.get("/v1/projects/{slug}/keys/{key_id}/connection", response_model=KeyConnection)
async def key_connection(slug: str, key_id: UUID, request: Request, admin: Admin = Depends(require_management)):
    """Whether an agent has connected with this key: its first call, first conversation sent and first
    recall (from the request log), and the User-Agent of its last call (the plugin's version)."""
    async with request.app.state.pool.connection() as conn:
        project = await _owned(conn, admin, slug)
        row = await (
            await conn.execute(
                """
                SELECT k.id AS key_id, k.last_used_at, k.last_user_agent,
                       min(r.at) AS first_call_at,
                       min(r.at) FILTER (WHERE r.method = 'POST' AND r.path LIKE '/v1/sessions/%%/events' AND r.status < 300) AS first_session_at,
                       min(r.at) FILTER (WHERE (r.path = '/v1/recall' OR r.path LIKE '/v1/sessions/%%/brief') AND r.status < 300) AS first_recall_at,
                       count(r.id) AS calls
                FROM api_keys k LEFT JOIN request_log r ON r.key_id = k.id AND r.project_id = k.project_id
                WHERE k.project_id = %s AND k.id = %s
                GROUP BY k.id
                """,
                (project["id"], key_id),
            )
        ).fetchone()
    if not row:
        raise HTTPException(404, {"code": "not-found", "message": "No such key"})
    return KeyConnection(**row)


@app.delete("/v1/projects/{slug}/keys/{key_id}", status_code=204)
async def revoke_key(slug: str, key_id: UUID, request: Request, admin: Admin = Depends(require_manager)):
    async with request.app.state.pool.connection() as conn:
        project = await _owned(conn, admin, slug)
        row = await (
            await conn.execute(
                "UPDATE api_keys SET revoked_at = COALESCE(revoked_at, now()) WHERE project_id = %s AND id = %s RETURNING id",
                (project["id"], key_id),
            )
        ).fetchone()
    if not row:
        raise HTTPException(404, {"code": "not-found", "message": "No such key"})


# ------------------------------------------------------------------ organizations


@app.get("/v1/org")
async def current_org(request: Request, admin: Admin = Depends(require_management)):
    """Who is calling: a signed-in person (with their role and organizations), an organization key,
    or the operator."""
    if admin.is_operator:
        return {"operator": True, "org": None, "user": None, "role": "owner", "orgs": []}
    async with request.app.state.pool.connection() as conn:
        org = await (await conn.execute("SELECT id, slug, name, created_at FROM organizations WHERE id = %s", (admin.org_id,))).fetchone()
        user = orgs = None
        if admin.user_id:
            user = await (await conn.execute("SELECT id, email, name FROM users WHERE id = %s", (admin.user_id,))).fetchone()
            orgs = await (await conn.execute(
                "SELECT o.slug, o.name, m.role FROM memberships m JOIN organizations o ON o.id = m.org_id "
                "WHERE m.user_id = %s ORDER BY m.created_at", (admin.user_id,))).fetchall()
    return {"operator": False, "org": org, "user": user, "role": admin.role, "orgs": orgs or []}


@app.post("/v1/orgs", status_code=201)
async def create_org(body: OrgCreate, request: Request, admin: Admin = Depends(require_operator)):
    """Operator only: create an organization and its first management key (shown once)."""
    slug = body.slug or slug_from(body.name)
    key = new_org_key()
    async with request.app.state.pool.connection() as conn, conn.transaction():
        org = await (await conn.execute(
            "INSERT INTO organizations (slug, name) VALUES (%s, %s) ON CONFLICT (slug) DO NOTHING RETURNING id, slug, name, created_at",
            (slug, body.name))).fetchone()
        if not org:
            raise HTTPException(409, {"code": "slug-taken", "message": f"An organization with slug {slug!r} exists"})
        await conn.execute("INSERT INTO org_keys (org_id, name, key_hash, key_suffix) VALUES (%s, 'default', %s, %s)",
                           (org["id"], hash_key(key), key[-4:]))
    request.state.audit_target = org["slug"]
    return {**org, "key": key}


@app.get("/v1/orgs")
async def list_orgs(request: Request, admin: Admin = Depends(require_operator)):
    async with request.app.state.pool.connection() as conn:
        return await (await conn.execute(
            "SELECT o.id, o.slug, o.name, o.created_at, (SELECT count(*) FROM projects p WHERE p.org_id = o.id) AS project_count "
            "FROM organizations o ORDER BY o.created_at")).fetchall()


@app.get("/v1/audit")
async def audit_log(request: Request, admin: Admin = Depends(require_manager),
                    limit: int = Query(100, ge=1, le=1000), project: str | None = None):
    """Administrative changes and memory edits, newest first: the caller's organization, or everything for the operator."""
    where, args = [], []
    if not admin.is_operator:
        where.append("a.org_id = %s")
        args.append(admin.org_id)
    if project:
        where.append("p.slug = %s")
        args.append(project)
    sql = ("SELECT a.at, a.actor, a.action, a.target, p.slug AS project FROM audit_events a "
           "LEFT JOIN projects p ON p.id = a.project_id"
           + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY a.at DESC, a.id DESC LIMIT %s")
    async with request.app.state.pool.connection() as conn:
        return await (await conn.execute(sql, (*args, limit))).fetchall()


@app.get("/v1/org/keys")
async def org_keys(request: Request, admin: Admin = Depends(require_manager)):
    if admin.is_operator:
        raise HTTPException(400, {"code": "org-key-required", "message": "Call with an organization key"})
    async with request.app.state.pool.connection() as conn:
        return await (await conn.execute(
            "SELECT id, name, key_suffix AS suffix, created_at, last_used_at, last_used_ip, revoked_at FROM org_keys WHERE org_id = %s "
            "ORDER BY created_at DESC", (admin.org_id,))).fetchall()


@app.post("/v1/org/keys", status_code=201)
async def create_org_key(body: KeyCreate, request: Request, admin: Admin = Depends(require_manager)):
    if admin.is_operator:
        raise HTTPException(400, {"code": "org-key-required", "message": "Call with an organization key"})
    key = new_org_key()
    async with request.app.state.pool.connection() as conn:
        row = await (await conn.execute(
            "INSERT INTO org_keys (org_id, name, key_hash, key_suffix) VALUES (%s, %s, %s, %s) "
            "RETURNING id, name, key_suffix AS suffix, created_at", (admin.org_id, body.name, hash_key(key), key[-4:]))).fetchone()
    request.state.audit_target = str(row["id"])
    return {**row, "key": key}


@app.delete("/v1/org/keys/{key_id}", status_code=204)
async def revoke_org_key(key_id: UUID, request: Request, admin: Admin = Depends(require_manager)):
    if admin.is_operator:
        raise HTTPException(400, {"code": "org-key-required", "message": "Call with an organization key"})
    async with request.app.state.pool.connection() as conn:
        live = await (await conn.execute(
            "SELECT count(*) AS n FROM org_keys WHERE org_id = %s AND revoked_at IS NULL AND id <> %s", (admin.org_id, key_id))).fetchone()
        if live["n"] == 0:
            raise HTTPException(409, {"code": "last-key", "message": "Create another key before revoking the last one"})
        row = await (await conn.execute(
            "UPDATE org_keys SET revoked_at = COALESCE(revoked_at, now()) WHERE org_id = %s AND id = %s RETURNING id",
            (admin.org_id, key_id))).fetchone()
    if not row:
        raise HTTPException(404, {"code": "not-found", "message": "No such key"})
