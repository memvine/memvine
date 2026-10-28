"""Identities and audiences: who may read which memory.

Every episode names an audience (default 'project'), and every memory drawn from it
inherits that audience. A recall names the identity it answers for and reaches the
'project' audience plus every audience that identity belongs to: listed in a fixed
audience, or matching all conditions of a rule audience on its traits.

A recall that names no identity reads project-visible memory only, unless the caller
holds the management key (the console), which reads everything.
"""
from __future__ import annotations

import json
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

PROJECT = "project"
OPS = {"is", "is_not", "is_one_of", "is_not_one_of", "exists", "does_not_exist"}


class Condition(BaseModel):
    trait: str = Field(min_length=1, max_length=200)
    op: str
    value: str | list[str] | None = None

    @field_validator("op")
    @classmethod
    def known_op(cls, v: str) -> str:
        if v not in OPS:
            raise ValueError(f"op must be one of {sorted(OPS)}")
        return v


class AudienceIn(BaseModel):
    kind: str = Field(pattern="^(fixed|rule)$")
    members: list[str] = Field(default_factory=list, max_length=10_000)
    conditions: list[Condition] = Field(default_factory=list, max_length=20)


class IdentityIn(BaseModel):
    traits: dict[str, str] = Field(default_factory=dict)

    @field_validator("traits")
    @classmethod
    def bounded(cls, v: dict[str, str]) -> dict[str, str]:
        for key, value in v.items():
            if not (1 <= len(key) <= 200) or len(value) > 2000:
                raise ValueError("trait keys are 1-200 characters and values at most 2,000")
        return v


def matches(conditions: list[dict], traits: dict[str, str]) -> bool:
    """True when the traits satisfy every condition. A rule with no conditions matches no one."""
    if not conditions:
        return False
    for c in conditions:
        op, has, value = c["op"], c["trait"] in traits, traits.get(c["trait"])
        expected = c.get("value")
        ok = {
            "exists": has,
            "does_not_exist": not has,
            "is": has and value == expected,
            "is_not": has and value != expected,
            "is_one_of": has and value in (expected or []),
            "is_not_one_of": has and value not in (expected or []),
        }[op]
        if not ok:
            return False
    return True


async def audience_exists(conn, project_id: UUID, slug: str) -> bool:
    if slug == PROJECT:
        return True
    row = await (await conn.execute("SELECT 1 FROM audiences WHERE project_id = %s AND slug = %s", (project_id, slug))).fetchone()
    return row is not None


async def audiences_for(conn, project_id: UUID, identity: str) -> list[str]:
    """Every audience the identity reaches, 'project' first."""
    row = await (
        await conn.execute("SELECT traits FROM identities WHERE project_id = %s AND identity = %s", (project_id, identity))
    ).fetchone()
    traits = row["traits"] if row else {}
    reached = [PROJECT]
    for a in await (await conn.execute("SELECT slug, kind, members, conditions FROM audiences WHERE project_id = %s", (project_id,))).fetchall():
        if (a["kind"] == "fixed" and identity in a["members"]) or (a["kind"] == "rule" and row and matches(a["conditions"], traits)):
            reached.append(a["slug"])
    return reached


async def readable_audiences(conn, project_id: UUID, identity: str | None, is_management: bool) -> list[str] | None:
    """The audiences a recall may read; None means all of them."""
    if identity:
        await touch_identity(conn, project_id, identity, origin="api")
        return await audiences_for(conn, project_id, identity)
    return None if is_management else [PROJECT]


async def touch_identity(conn, project_id: UUID, identity: str, *, origin: str) -> None:
    await conn.execute(
        """
        INSERT INTO identities (project_id, identity, origin, last_seen_at) VALUES (%s, %s, %s, now())
        ON CONFLICT (project_id, identity) DO UPDATE SET last_seen_at = now()
        """,
        (project_id, identity, origin),
    )


async def put_identity(conn, project_id: UUID, identity: str, body: IdentityIn) -> dict:
    row = await (
        await conn.execute(
            """
            INSERT INTO identities (project_id, identity, traits, origin) VALUES (%s, %s, %s, 'api')
            ON CONFLICT (project_id, identity) DO UPDATE SET traits = EXCLUDED.traits
            RETURNING identity, traits, origin, created_at, last_seen_at
            """,
            (project_id, identity, json.dumps(body.traits)),
        )
    ).fetchone()
    return {**row, "audiences": await audiences_for(conn, project_id, identity)}


async def put_audience(conn, project_id: UUID, slug: str, body: AudienceIn) -> dict:
    if body.kind == "rule" and not body.conditions:
        raise ValueError("a rule audience needs at least one condition")
    members = sorted(set(body.members)) if body.kind == "fixed" else []
    for m in members:
        await conn.execute(
            "INSERT INTO identities (project_id, identity, origin) VALUES (%s, %s, 'audience') ON CONFLICT DO NOTHING",
            (project_id, m),
        )
    return await (
        await conn.execute(
            """
            INSERT INTO audiences (project_id, slug, kind, members, conditions) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (project_id, slug) DO UPDATE SET kind = EXCLUDED.kind, members = EXCLUDED.members, conditions = EXCLUDED.conditions
            RETURNING slug, kind, members, conditions, created_at
            """,
            (project_id, slug, body.kind, members, json.dumps([c.model_dump() for c in body.conditions] if body.kind == "rule" else [])),
        )
    ).fetchone()
