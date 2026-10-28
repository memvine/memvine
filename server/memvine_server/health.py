"""Is synthesis working? Three conditions, each of which leaves memory silently behind:

- failed:  jobs that used every attempt in the last alert_failed_hours
- stuck:   a live job (not imported history) waiting longer than alert_queue_minutes in a project
           that is not paused
- worker:  no worker process has polled the queue within alert_worker_minutes (heartbeats)

GET /v1/health/synthesis reports them, the console shows them as banners, and the worker posts each
to MEMVINE_ALERT_WEBHOOK_URL (Slack-compatible) at most once per alert_repeat_minutes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import socket
import time
import urllib.request
from uuid import UUID

from .config import settings

log = logging.getLogger("memvine.health")

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(3)}"
BEAT_SECONDS = 15.0
_last_beat = 0.0


async def beat(pool, force: bool = False) -> None:
    """Record that this process's worker is polling (at most every BEAT_SECONDS)."""
    global _last_beat
    now = time.monotonic()
    if not force and now - _last_beat < BEAT_SECONDS:
        return
    _last_beat = now
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO worker_heartbeats (worker_id, host, pid) VALUES (%s, %s, %s) "
            "ON CONFLICT (worker_id) DO UPDATE SET last_poll_at = now()",
            (WORKER_ID, socket.gethostname(), os.getpid()))
        if force:  # once per start: forget processes gone for a day
            await conn.execute("DELETE FROM worker_heartbeats WHERE last_poll_at < now() - interval '1 day'")


def minutes_text(minutes: float) -> str:
    m = int(minutes)
    if m < 120:
        return f"{m} min"
    if m < 48 * 60:
        return f"{m // 60} h {m % 60} min" if m % 60 else f"{m // 60} h"
    return f"{m // 1440} days"


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


async def synthesis_health(conn, org_id: UUID | None = None, project_id: UUID | None = None) -> dict:
    """The synthesis conditions for one project, one organization's projects, or (both None) the
    whole server. The worker condition is always server-wide: every project shares the workers."""
    scope = "project" if project_id else "organization" if org_id else "server"
    where = "(%(org)s::uuid IS NULL OR p.org_id = %(org)s) AND (%(project)s::uuid IS NULL OR p.id = %(project)s)"
    params = {"org": org_id, "project": project_id, "hours": settings.alert_failed_hours}
    failed = await (await conn.execute(
        f"""SELECT p.slug, p.name, count(DISTINCT j.episode_id) AS count, max(j.finished_at) AS last_at
            FROM jobs j JOIN projects p ON p.id = j.project_id
            WHERE j.status = 'failed' AND j.finished_at > now() - make_interval(hours => %(hours)s) AND {where}
            GROUP BY p.slug, p.name ORDER BY count DESC, p.slug""", params)).fetchall()
    waiting = await (await conn.execute(
        f"""SELECT p.slug, p.name, count(*) AS count,
                   extract(epoch FROM now() - min(j.created_at)) / 60 AS minutes
            FROM jobs j JOIN projects p ON p.id = j.project_id
            WHERE j.status = 'queued' AND NOT j.background AND NOT p.processing_paused AND {where}
            GROUP BY p.slug, p.name ORDER BY minutes DESC, p.slug""", params)).fetchall()
    worker = await (await conn.execute(
        "SELECT max(last_poll_at) AS last_poll_at, extract(epoch FROM now() - max(last_poll_at)) / 60 AS minutes, "
        "count(*) FILTER (WHERE last_poll_at > now() - make_interval(mins => %s)) AS alive FROM worker_heartbeats",
        (settings.alert_worker_minutes,))).fetchone()

    many = scope != "project"
    issues = []
    failed_count = sum(r["count"] for r in failed)
    if failed_count:
        when = "the last day" if settings.alert_failed_hours == 24 else f"the last {_plural(settings.alert_failed_hours, 'hour')}"
        listing = ", ".join(f"{r['name']}: {r['count']}" for r in failed)
        where_text = f" ({listing})" if many else ""
        issues.append({
            "condition": "failed",
            "message": f"{_plural(failed_count, 'source')} failed to synthesize in {when}{where_text} — see Sources",
            "count": failed_count,
            "projects": [{"slug": r["slug"], "name": r["name"], "count": r["count"]} for r in failed],
        })
    oldest = float(waiting[0]["minutes"]) if waiting else None
    stuck = [r for r in waiting if float(r["minutes"]) > settings.alert_queue_minutes]
    if stuck:
        top = stuck[0]
        in_text = f" in {top['name']}" if many else ""
        issues.append({
            "condition": "stuck",
            "message": f"Synthesis is stuck: the oldest source{in_text} has waited {minutes_text(float(top['minutes']))}",
            "minutes": round(float(top["minutes"]), 1),
            "projects": [{"slug": r["slug"], "name": r["name"], "count": r["count"],
                          "minutes": round(float(r["minutes"]), 1)} for r in stuck],
        })
    last_poll = worker["last_poll_at"]
    if not worker["alive"]:
        issues.append({
            "condition": "worker",
            "message": (f"The synthesis worker has not polled for {minutes_text(float(worker['minutes']))}"
                        if last_poll else "No synthesis worker has polled the queue yet"),
            "minutes": round(float(worker["minutes"]), 1) if last_poll else None,
        })
    return {
        "healthy": not issues,
        "scope": scope,
        "issues": issues,
        "failed_count": failed_count,
        "oldest_queued_minutes": round(oldest, 1) if oldest is not None else None,
        "worker_last_poll_at": last_poll,
        "thresholds": {"failed_hours": settings.alert_failed_hours, "queue_minutes": settings.alert_queue_minutes,
                       "worker_minutes": settings.alert_worker_minutes},
    }


def _post(url: str, text: str) -> None:
    if not url.lower().startswith(("https://", "http://")):
        raise ValueError("the alert webhook must be an http(s) URL")  # urllib would also open file:// and ftp://
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as res:  # noqa: S310 - the operator's own URL
        res.read()


async def check_and_alert(conn, url: str | None = None) -> list[str]:
    """Post each unhealthy condition to the webhook unless it was posted within alert_repeat_minutes
    (by any process). Returns the conditions posted."""
    url = settings.alert_webhook_url if url is None else url
    if not url:
        return []
    report = await synthesis_health(conn)
    posted = []
    for issue in report["issues"]:
        # Claim the condition first, so two processes noticing it together post it once.
        claimed = await (await conn.execute(
            "INSERT INTO alert_state (condition, last_sent_at, message) VALUES (%s, now(), %s) "
            "ON CONFLICT (condition) DO UPDATE SET last_sent_at = now(), message = EXCLUDED.message "
            "WHERE alert_state.last_sent_at < now() - make_interval(mins => %s) RETURNING condition",
            (issue["condition"], issue["message"], settings.alert_repeat_minutes))).fetchone()
        if not claimed:
            continue
        try:
            await asyncio.to_thread(_post, url, f"Memvine: {issue['message']}")
        except Exception as exc:  # noqa: BLE001 - try again at the next check
            log.warning("alert webhook failed for %s: %s", issue["condition"], exc)
            await conn.execute("DELETE FROM alert_state WHERE condition = %s", (issue["condition"],))
            continue
        posted.append(issue["condition"])
    return posted


async def alert_loop(pool, stop: asyncio.Event) -> None:
    """The worker's alert check, beside (not inside) its job loops, so hung jobs do not silence it."""
    if not settings.alert_webhook_url:
        return
    while not stop.is_set():
        try:
            async with pool.connection() as conn:
                await check_and_alert(conn)
        except Exception:
            log.exception("alert check error")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.alert_check_seconds)
        except TimeoutError:
            pass
