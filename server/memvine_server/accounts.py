"""People and their sign-in: accounts with email and password, organization membership with a role
(owner, admin, member), invite links, and sign-in sessions (mv_us_ tokens, stored hashed).

Sign-up follows MEMVINE_SIGNUP: open (anyone who can reach the server; without an invite they get
their own organization), invite (an invite link is required), closed. Passwords are hashed with
scrypt. Failed logins are rate-limited per email and per address."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import SESSION_PREFIX, Admin, client_ip, hash_key, require_management, require_manager
from .config import settings

router = APIRouter()

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 10
INVITE_DAYS = 7
# Failed logins allowed per email and per client address in the window, before 429.
LOGIN_FAILURES = 10
LOGIN_WINDOW_SECONDS = 15 * 60
_failures: dict[str, deque] = defaultdict(deque)


# ------------------------------------------------------------------ passwords and tokens

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def check_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def _throttled(*keys: str) -> bool:
    now = time.monotonic()
    for key in keys:
        q = _failures[key]
        while q and now - q[0] > LOGIN_WINDOW_SECONDS:
            q.popleft()
        if len(q) >= LOGIN_FAILURES:
            return True
    return False


def _failed(*keys: str) -> None:
    now = time.monotonic()
    for key in keys:
        _failures[key].append(now)


def _client(request: Request) -> str:
    return client_ip(request) or "?"


async def _new_session(conn, user_id: UUID, org_id: UUID) -> str:
    token = SESSION_PREFIX + secrets.token_urlsafe(32)
    await conn.execute(
        "INSERT INTO user_sessions (user_id, org_id, token_hash, expires_at) VALUES (%s, %s, %s, %s)",
        (user_id, org_id, hash_key(token), datetime.now(timezone.utc) + timedelta(days=settings.session_days)))
    return token


async def _unique_org_slug(conn, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "org"
    slug = base
    while await (await conn.execute("SELECT 1 FROM organizations WHERE slug = %s", (slug,))).fetchone():
        slug = f"{base}-{secrets.token_hex(2)}"
    return slug


async def _valid_invite(conn, token: str):
    invite = await (await conn.execute(
        "SELECT i.id, i.org_id, i.email, i.role, o.slug, o.name FROM invites i JOIN organizations o ON o.id = i.org_id "
        "WHERE i.token_hash = %s AND i.accepted_at IS NULL AND i.revoked_at IS NULL AND i.expires_at > now()",
        (hash_key(token),))).fetchone()
    if not invite:
        raise HTTPException(404, {"code": "invite-invalid", "message": "This invite link is used, revoked or expired. Ask for a new one."})
    return invite


async def _join(conn, invite, user_id: UUID, email: str) -> None:
    if invite["email"] and invite["email"].lower() != email.lower():
        raise HTTPException(403, {"code": "invite-email-mismatch", "message": f"This invite is for {invite['email']}"})
    await conn.execute("INSERT INTO memberships (user_id, org_id, role) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                       (user_id, invite["org_id"], invite["role"]))
    await conn.execute("UPDATE invites SET accepted_at = now(), accepted_by = %s WHERE id = %s", (user_id, invite["id"]))


def _session_admin(request: Request, user, org, role: str) -> None:
    """Lets the audit log name who signed up or joined, though the request carried no token."""
    request.state.admin = Admin(org_id=org["id"], org_slug=org["slug"], user_id=user["id"], user_email=user["email"], role=role)
    request.state.audit_target = user["email"]


# ------------------------------------------------------------------ sign-up, login, sessions

class SignUp(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)
    name: str = Field("", max_length=100)
    organization: str = Field("", max_length=100)
    invite: str | None = None


class Login(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)
    org: str | None = None


async def _first_account(request: Request) -> bool:
    """On a server with no accounts yet, the first person may sign up without an invite: in
    invite mode that is how the server's owner gets in."""
    async with request.app.state.pool.connection() as conn:
        return not await (await conn.execute("SELECT 1 FROM users LIMIT 1")).fetchone()


@router.get("/v1/auth/config")
async def auth_config(request: Request):
    """What the sign-in page offers. Public."""
    mode = settings.signup
    if mode == "invite" and await _first_account(request):
        mode = "open"
    return {"signup": mode}


class InviteLookup(BaseModel):
    invite: str = Field(max_length=200)


@router.post("/v1/auth/invites/describe")
async def describe_invite(body: InviteLookup, request: Request):
    """What an invite link joins, for the sign-up page. Public; reveals only the organization's
    name. A POST, so the invite code never lands in an access log."""
    async with request.app.state.pool.connection() as conn:
        invite = await _valid_invite(conn, body.invite)
    return {"organization": invite["name"], "role": invite["role"], "email": invite["email"]}


@router.post("/v1/auth/signup", status_code=201)
async def signup(body: SignUp, request: Request):
    if settings.signup == "invite" and not body.invite and not await _first_account(request):
        raise HTTPException(403, {"code": "signup-closed", "message": "Sign-up needs an invite link"})
    if settings.signup == "closed":
        raise HTTPException(403, {"code": "signup-closed",
                                  "message": "Sign-up is closed on this server"})
    email = body.email.strip()
    if not EMAIL.match(email):
        raise HTTPException(422, {"code": "invalid-email", "message": "Enter a valid email address"})
    if len(body.password) < MIN_PASSWORD:
        raise HTTPException(422, {"code": "weak-password", "message": f"Use at least {MIN_PASSWORD} characters"})
    async with request.app.state.pool.connection() as conn, conn.transaction():
        if await (await conn.execute("SELECT 1 FROM users WHERE lower(email) = lower(%s)", (email,))).fetchone():
            raise HTTPException(409, {"code": "account-exists", "message": "An account with this email exists. Sign in instead."})
        user = await (await conn.execute(
            "INSERT INTO users (email, name, password_hash, last_login_at) VALUES (%s, %s, %s, now()) RETURNING id, email, name",
            (email, body.name.strip(), hash_password(body.password)))).fetchone()
        if body.invite:
            invite = await _valid_invite(conn, body.invite)
            await _join(conn, invite, user["id"], email)
            org, role = {"id": invite["org_id"], "slug": invite["slug"], "name": invite["name"]}, invite["role"]
        else:
            name = body.organization.strip() or f"{body.name.strip() or email.split('@')[0]}'s organization"
            org = await (await conn.execute("INSERT INTO organizations (slug, name) VALUES (%s, %s) RETURNING id, slug, name",
                                            (await _unique_org_slug(conn, name), name))).fetchone()
            role = "owner"
            await conn.execute("INSERT INTO memberships (user_id, org_id, role) VALUES (%s, %s, 'owner')", (user["id"], org["id"]))
        token = await _new_session(conn, user["id"], org["id"])
    _session_admin(request, user, org, role)
    return {"token": token, "user": user, "org": org, "role": role}


@router.post("/v1/auth/login")
async def login(body: Login, request: Request):
    email, client = body.email.strip().lower(), _client(request)
    if _throttled("email:" + email, "ip:" + client):
        raise HTTPException(429, {"code": "too-many-attempts", "message": "Too many failed sign-ins. Wait 15 minutes."})
    async with request.app.state.pool.connection() as conn:
        user = await (await conn.execute("SELECT id, email, name, password_hash FROM users WHERE lower(email) = %s", (email,))).fetchone()
        if not user or not check_password(body.password, user["password_hash"]):
            _failed("email:" + email, "ip:" + client)
            raise HTTPException(401, {"code": "bad-credentials", "message": "Wrong email or password"})
        org = await (await conn.execute(
            "SELECT o.id, o.slug, o.name, m.role FROM memberships m JOIN organizations o ON o.id = m.org_id "
            "WHERE m.user_id = %s AND (%s::text IS NULL OR o.slug = %s) ORDER BY m.created_at LIMIT 1",
            (user["id"], body.org, body.org))).fetchone()
        if not org:
            raise HTTPException(403, {"code": "no-organization", "message": "You are not a member of any organization. Ask for an invite."})
        await conn.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (user["id"],))
        token = await _new_session(conn, user["id"], org["id"])
    _session_admin(request, user, org, org["role"])
    return {"token": token, "user": {k: user[k] for k in ("id", "email", "name")}, "org": org, "role": org["role"]}


def _bearer(request: Request) -> str:
    return request.headers.get("authorization", "").partition(" ")[2].strip()


async def require_session(request: Request) -> Admin:
    admin = await require_management(request)
    if admin.user_id is None:
        raise HTTPException(400, {"code": "session-required", "message": "Sign in as a person for this route"})
    return admin


@router.post("/v1/auth/logout", status_code=204)
async def logout(request: Request, admin: Admin = Depends(require_session)):
    async with request.app.state.pool.connection() as conn:
        await conn.execute("UPDATE user_sessions SET revoked_at = now() WHERE token_hash = %s", (hash_key(_bearer(request)),))


class Switch(BaseModel):
    org: str


@router.post("/v1/auth/switch")
async def switch_org(body: Switch, request: Request, admin: Admin = Depends(require_session)):
    """A session for another organization the person belongs to; the current one ends."""
    async with request.app.state.pool.connection() as conn, conn.transaction():
        org = await (await conn.execute(
            "SELECT o.id, o.slug, o.name, m.role FROM memberships m JOIN organizations o ON o.id = m.org_id WHERE m.user_id = %s AND o.slug = %s",
            (admin.user_id, body.org))).fetchone()
        if not org:
            raise HTTPException(404, {"code": "not-found", "message": "You are not a member of that organization"})
        await conn.execute("UPDATE user_sessions SET revoked_at = now() WHERE token_hash = %s", (hash_key(_bearer(request)),))
        token = await _new_session(conn, admin.user_id, org["id"])
    return {"token": token, "org": org, "role": org["role"]}


class Accept(BaseModel):
    invite: str


@router.post("/v1/auth/accept")
async def accept_invite(body: Accept, request: Request, admin: Admin = Depends(require_session)):
    """A signed-in person joins another organization with an invite; returns a session for it."""
    async with request.app.state.pool.connection() as conn, conn.transaction():
        invite = await _valid_invite(conn, body.invite)
        await _join(conn, invite, admin.user_id, admin.user_email)
        token = await _new_session(conn, admin.user_id, invite["org_id"])
    request.state.admin = Admin(org_id=invite["org_id"], org_slug=invite["slug"], user_id=admin.user_id,
                                user_email=admin.user_email, role=invite["role"])
    request.state.audit_target = admin.user_email
    return {"token": token, "org": {"id": invite["org_id"], "slug": invite["slug"], "name": invite["name"]}, "role": invite["role"]}


class PasswordChange(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)


@router.post("/v1/auth/password", status_code=204)
async def change_password(body: PasswordChange, request: Request, admin: Admin = Depends(require_session)):
    if len(body.new) < MIN_PASSWORD:
        raise HTTPException(422, {"code": "weak-password", "message": f"Use at least {MIN_PASSWORD} characters"})
    async with request.app.state.pool.connection() as conn, conn.transaction():
        user = await (await conn.execute("SELECT password_hash FROM users WHERE id = %s", (admin.user_id,))).fetchone()
        if not check_password(body.current, user["password_hash"]):
            raise HTTPException(403, {"code": "bad-credentials", "message": "The current password is wrong"})
        await conn.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hash_password(body.new), admin.user_id))
        # Every other sign-in ends; this one stays.
        await conn.execute("UPDATE user_sessions SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL AND token_hash <> %s",
                           (admin.user_id, hash_key(_bearer(request))))


# ------------------------------------------------------------------ members and invites

@router.get("/v1/org/members")
async def list_members(request: Request, admin: Admin = Depends(require_management)):
    if admin.is_operator:
        raise HTTPException(400, {"code": "org-required", "message": "Call as a member of an organization"})
    async with request.app.state.pool.connection() as conn:
        return await (await conn.execute(
            "SELECT u.id, u.email, u.name, m.role, m.created_at AS joined_at, u.last_login_at FROM memberships m "
            "JOIN users u ON u.id = m.user_id WHERE m.org_id = %s ORDER BY m.created_at", (admin.org_id,))).fetchall()


class RoleChange(BaseModel):
    role: str = Field(pattern="^(owner|admin|member)$")


async def _owners_left(conn, org_id: UUID, without: UUID) -> int:
    row = await (await conn.execute("SELECT count(*) AS n FROM memberships WHERE org_id = %s AND role = 'owner' AND user_id <> %s",
                                    (org_id, without))).fetchone()
    return row["n"]


async def _member(conn, org_id: UUID, user_id: UUID):
    row = await (await conn.execute("SELECT m.role, u.email FROM memberships m JOIN users u ON u.id = m.user_id "
                                    "WHERE m.org_id = %s AND m.user_id = %s", (org_id, user_id))).fetchone()
    if not row:
        raise HTTPException(404, {"code": "not-found", "message": "No such member"})
    return row


@router.patch("/v1/org/members/{user_id}")
async def change_role(user_id: UUID, body: RoleChange, request: Request, admin: Admin = Depends(require_manager)):
    async with request.app.state.pool.connection() as conn, conn.transaction():
        member = await _member(conn, admin.org_id, user_id)
        if "owner" in (member["role"], body.role) and admin.role != "owner":
            raise HTTPException(403, {"code": "owner-required", "message": "Only an owner can grant or remove the owner role"})
        if member["role"] == "owner" and body.role != "owner" and await _owners_left(conn, admin.org_id, user_id) == 0:
            raise HTTPException(409, {"code": "last-owner", "message": "Make someone else an owner first"})
        await conn.execute("UPDATE memberships SET role = %s WHERE org_id = %s AND user_id = %s", (body.role, admin.org_id, user_id))
    request.state.audit_target = f"{member['email']} -> {body.role}"
    return {"id": user_id, "role": body.role}


@router.delete("/v1/org/members/{user_id}", status_code=204)
async def remove_member(user_id: UUID, request: Request, admin: Admin = Depends(require_management)):
    """Managers remove members; anyone may leave. The last owner cannot go."""
    if admin.is_operator or (admin.user_id != user_id and not admin.can_manage):
        raise HTTPException(403, {"code": "admin-role-required", "message": "Ask an owner or admin of this organization"})
    async with request.app.state.pool.connection() as conn, conn.transaction():
        member = await _member(conn, admin.org_id, user_id)
        if member["role"] == "owner" and admin.user_id != user_id and admin.role != "owner":
            raise HTTPException(403, {"code": "owner-required", "message": "Only an owner can remove an owner"})
        if member["role"] == "owner" and await _owners_left(conn, admin.org_id, user_id) == 0:
            raise HTTPException(409, {"code": "last-owner", "message": "Make someone else an owner first"})
        await conn.execute("DELETE FROM memberships WHERE org_id = %s AND user_id = %s", (admin.org_id, user_id))
        await conn.execute("UPDATE user_sessions SET revoked_at = now() WHERE org_id = %s AND user_id = %s AND revoked_at IS NULL",
                           (admin.org_id, user_id))
    request.state.audit_target = member["email"]


class InviteCreate(BaseModel):
    email: str | None = Field(None, max_length=254)
    role: str = Field("member", pattern="^(admin|member)$")


@router.get("/v1/org/invites")
async def list_invites(request: Request, admin: Admin = Depends(require_manager)):
    async with request.app.state.pool.connection() as conn:
        return await (await conn.execute(
            "SELECT i.id, i.email, i.role, i.created_by, i.created_at, i.expires_at, i.accepted_at, u.email AS accepted_by "
            "FROM invites i LEFT JOIN users u ON u.id = i.accepted_by "
            "WHERE i.org_id = %s AND i.revoked_at IS NULL ORDER BY i.created_at DESC LIMIT 100", (admin.org_id,))).fetchall()


@router.post("/v1/org/invites", status_code=201)
async def create_invite(body: InviteCreate, request: Request, admin: Admin = Depends(require_manager)):
    """An invite link token (shown once). The console turns it into <console>/signup?invite=<token>."""
    if admin.is_operator:
        raise HTTPException(400, {"code": "org-required", "message": "Call as a member of an organization"})
    email = (body.email or "").strip() or None
    if email and not EMAIL.match(email):
        raise HTTPException(422, {"code": "invalid-email", "message": "Enter a valid email address"})
    token = "mv_in_" + secrets.token_urlsafe(24)
    async with request.app.state.pool.connection() as conn:
        row = await (await conn.execute(
            "INSERT INTO invites (org_id, token_hash, email, role, created_by, expires_at) VALUES (%s, %s, %s, %s, %s, %s) "
            "RETURNING id, email, role, created_at, expires_at",
            (admin.org_id, hash_key(token), email, body.role, admin.actor,
             datetime.now(timezone.utc) + timedelta(days=INVITE_DAYS)))).fetchone()
    request.state.audit_target = f"{email or 'anyone with the link'} as {body.role}"
    return {**row, "token": token}


@router.delete("/v1/org/invites/{invite_id}", status_code=204)
async def revoke_invite(invite_id: UUID, request: Request, admin: Admin = Depends(require_manager)):
    async with request.app.state.pool.connection() as conn:
        row = await (await conn.execute(
            "UPDATE invites SET revoked_at = now() WHERE org_id = %s AND id = %s AND accepted_at IS NULL RETURNING id",
            (admin.org_id, invite_id))).fetchone()
    if not row:
        raise HTTPException(404, {"code": "not-found", "message": "No such open invite"})
