"""Metering, monthly caps, and retention.

Credits: ingesting costs one credit per CHARS_PER_CREDIT characters received (episodes and live
session messages alike, counted once when they arrive); a recall costs RECALL_CREDITS. A project
with a monthly credit cap refuses new ingestion once the calendar month (UTC) reaches it; recall
is never refused, so an agent never loses its memory mid-task.

Retention: a project with retention_days drops the raw text of episodes older than that, and
their passages. The memories drawn from them, with their short evidence quotes, are kept: the
retention window governs transcripts, not what was learned.
"""
from __future__ import annotations

import math
from decimal import Decimal
from uuid import UUID

CHARS_PER_CREDIT = 1_000
RECALL_CREDITS = Decimal("0.1")


def ingest_credits(chars: int) -> Decimal:
    return Decimal(max(1, math.ceil(chars / CHARS_PER_CREDIT)))


async def month_credits(conn, project_id: UUID) -> Decimal:
    row = await (await conn.execute(
        "SELECT coalesce(sum(credits), 0) AS c FROM usage_events WHERE project_id = %s "
        "AND at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'", (project_id,))).fetchone()
    return Decimal(row["c"])


class CapReached(Exception):
    def __init__(self, used: Decimal, cap: Decimal) -> None:
        super().__init__(f"monthly credit cap reached: {used} of {cap}")
        self.used, self.cap = used, cap


async def charge_ingest(conn, project_id: UUID, kind: str, chars: int) -> Decimal:
    """Refuse when the project's cap is already reached, else record the charge."""
    row = await (await conn.execute("SELECT monthly_credit_cap FROM projects WHERE id = %s", (project_id,))).fetchone()
    cap = row["monthly_credit_cap"] if row else None
    if cap is not None:
        used = await month_credits(conn, project_id)
        if used >= cap:
            raise CapReached(used, Decimal(cap))
    credits = ingest_credits(chars)
    await conn.execute("INSERT INTO usage_events (project_id, kind, units, credits) VALUES (%s, %s, %s, %s)",
                       (project_id, kind, chars, credits))
    return credits


async def charge_recall(conn, project_id: UUID) -> None:
    await conn.execute("INSERT INTO usage_events (project_id, kind, units, credits) VALUES (%s, 'recall', 1, %s)",
                       (project_id, RECALL_CREDITS))


async def apply_retention(conn) -> int:
    """Drop raw text of episodes past their project's retention window. Returns how many."""
    rows = await (await conn.execute(
        """
        UPDATE episodes e SET content = '[expired by retention]', expired_at = now()
        FROM projects p
        WHERE p.id = e.project_id AND p.retention_days IS NOT NULL AND e.expired_at IS NULL
          AND e.occurred_at < now() - make_interval(days => p.retention_days)
          AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.project_id = e.project_id AND j.episode_id = e.id
                          AND j.status IN ('queued', 'running'))
        RETURNING e.project_id, e.id
        """)).fetchall()
    for r in rows:
        await conn.execute("DELETE FROM passages WHERE project_id = %s AND episode_id = %s", (r["project_id"], r["id"]))
        # A feedback case's excerpt is source text too (review.py).
        await conn.execute("UPDATE feedback_cases SET excerpt = '' WHERE project_id = %s AND episode_id = %s", (r["project_id"], r["id"]))
    return len(rows)
