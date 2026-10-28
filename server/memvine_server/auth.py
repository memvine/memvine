"""Bearer-key authentication. A project key (mv_sk_) scopes a request to one project. An
organization's admin side is reached three ways: a person's sign-in session (mv_us_, carrying their
role), an organization key (mv_mk_, for scripts and CI), or the operator key (MEMVINE_MANAGEMENT_KEY)."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Request

from .config import settings
from .db import request_project

PROJECT_PREFIX = "mv_sk_"
MANAGEMENT_PREFIX = "mv_mk_"
SESSION_PREFIX = "mv_us_"
ADMIN_PREFIXES = (MANAGEMENT_PREFIX, SESSION_PREFIX)
MANAGER_ROLES = ("owner", "admin")


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_project_key() -> str:
    return PROJECT_PREFIX + secrets.token_urlsafe(32)


@dataclass
class Principal:
    project_id: UUID
    project_slug: str
    key_id: UUID | None  # None when the management key acts on a project (the console)


def client_ip(request: Request) -> str:
    """The caller's address. Behind a tunnel or proxy the peer is the proxy; the first forwarded
    address is the client's."""
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "")


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, {"code": "unauthorized", "message": "Send Authorization: Bearer <key>"})
    return token.strip()


def is_management(token: str) -> bool:
    return bool(settings.management_key) and hmac.compare_digest(token, settings.management_key)


@dataclass
class Admin:
    """Who acts on an organization: a signed-in person with a role, an organization key, or the
    operator (org_id None)."""
    org_id: UUID | None
    org_slug: str | None
    key_name: str | None = None
    user_id: UUID | None = None
    user_email: str | None = None
    role: str = "owner"  # organization keys and the operator act with full rights

    @property
    def is_operator(self) -> bool:
        return self.org_id is None

    @property
    def can_manage(self) -> bool:
        return self.role in MANAGER_ROLES

    @property
    def actor(self) -> str:
        if self.is_operator:
            return "operator"
        return self.user_email if self.user_id else f"org key {self.key_name}"


def new_org_key() -> str:
    return MANAGEMENT_PREFIX + secrets.token_urlsafe(32)


async def resolve_admin(pool, token: str, ip: str = "") -> Admin | None:
    if token.startswith(SESSION_PREFIX):
        async with pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT s.id, s.user_id, s.org_id, u.email, o.slug, m.role FROM user_sessions s "
                "JOIN users u ON u.id = s.user_id JOIN organizations o ON o.id = s.org_id "
                "JOIN memberships m ON m.user_id = s.user_id AND m.org_id = s.org_id "
                "WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > now()",
                (hash_key(token),))).fetchone()
            if not row:
                return None
            await conn.execute("UPDATE user_sessions SET last_used_at = now() WHERE id = %s", (row["id"],))
        return Admin(org_id=row["org_id"], org_slug=row["slug"], user_id=row["user_id"], user_email=row["email"], role=row["role"])
    if not token.startswith(MANAGEMENT_PREFIX):
        return None
    if is_management(token):
        return Admin(org_id=None, org_slug=None)
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "SELECT k.id, k.name, k.org_id, k.revoked_at, o.slug FROM org_keys k JOIN organizations o ON o.id = k.org_id WHERE k.key_hash = %s",
            (hash_key(token),))).fetchone()
        if not row or row["revoked_at"]:
            return None
        await conn.execute("UPDATE org_keys SET last_used_at = now(), last_used_ip = %s WHERE id = %s", (ip or None, row["id"]))
    return Admin(org_id=row["org_id"], org_slug=row["slug"], key_name=row["name"])


async def require_management(request: Request) -> Admin:
    admin = await resolve_admin(request.app.state.pool, _bearer(request), client_ip(request))
    if admin is None:
        raise HTTPException(403, {"code": "management-key-required", "message": "This route needs an organization or operator key"})
    request.state.admin = admin
    return admin


async def require_manager(request: Request) -> Admin:
    """An owner or admin (or a key): members may read and use memory but not change the organization."""
    admin = await require_management(request)
    if not admin.can_manage:
        raise HTTPException(403, {"code": "admin-role-required", "message": "Ask an owner or admin of this organization"})
    return admin


def ensure_manager(request: Request) -> None:
    """For project-scoped routes: a signed-in member may not make administrative changes. Project keys
    keep their rights (they belong to systems the admins set up)."""
    admin = getattr(request.state, "admin", None)
    if admin is not None and not admin.can_manage:
        raise HTTPException(403, {"code": "admin-role-required", "message": "Ask an owner or admin of this organization"})


async def require_operator(request: Request) -> Admin:
    admin = await require_management(request)
    if not admin.is_operator:
        raise HTTPException(403, {"code": "operator-key-required", "message": "Only the operator key can manage organizations"})
    return admin


async def find_project(conn, admin: Admin, slug: str):
    """A project the admin may administer, or None (another org's project reads as missing)."""
    if admin.is_operator:
        return await (await conn.execute("SELECT id, slug, org_id FROM projects WHERE slug = %s", (slug,))).fetchone()
    return await (await conn.execute(
        "SELECT id, slug, org_id FROM projects WHERE slug = %s AND org_id = %s", (slug, admin.org_id))).fetchone()


async def require_project(request: Request) -> Principal:
    token = _bearer(request)
    pool = request.app.state.pool

    # The console holds a sign-in session or organization key and names the project per request.
    if token.startswith(ADMIN_PREFIXES):
        slug = request.headers.get("x-memvine-project") or request.query_params.get("project")
        admin = await resolve_admin(pool, token, client_ip(request))
        if admin is None:
            raise HTTPException(403, {"code": "management-key-required", "message": "Invalid management key"})
        if not slug:
            raise HTTPException(400, {"code": "project-required", "message": "Send X-Memvine-Project: <slug> with the management key"})
        async with pool.connection() as conn:
            row = await find_project(conn, admin, slug)
        if not row:
            raise HTTPException(404, {"code": "not-found", "message": "No such project"})
        principal = Principal(project_id=row["id"], project_slug=row["slug"], key_id=None)
        request.state.principal = principal
        request.state.admin = admin
        request_project.set(str(principal.project_id))
        return principal

    if not token.startswith(PROJECT_PREFIX):
        raise HTTPException(401, {"code": "malformed-api-key", "message": "Project keys start with mv_sk_"})
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                """
                SELECT k.id AS key_id, k.revoked_at, p.id AS project_id, p.slug
                FROM api_keys k JOIN projects p ON p.id = k.project_id
                WHERE k.key_hash = %s
                """,
                (hash_key(token),),
            )
        ).fetchone()
        if not row:
            raise HTTPException(401, {"code": "unknown-api-key", "message": "No such key"})
        if row["revoked_at"]:
            raise HTTPException(401, {"code": "revoked-api-key", "message": "This key was revoked"})
        await conn.execute("UPDATE api_keys SET last_used_at = now(), last_used_ip = %s WHERE id = %s",
                           (client_ip(request) or None, row["key_id"]))
    principal = Principal(project_id=row["project_id"], project_slug=row["slug"], key_id=row["key_id"])
    request.state.principal = principal
    request_project.set(str(principal.project_id))
    return principal


ProjectDep = Depends(require_project)
ManagementDep = Depends(require_management)
