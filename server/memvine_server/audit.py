"""The audit log: one row per successful administrative change or memory edit, written by the
request middleware after the route succeeded. Request bodies are never stored (they can hold keys
and customer text); the action and its target are enough to answer who did what, when."""
from __future__ import annotations

import re

# (method, path pattern) -> action; the first capture group is the target.
AUDITED: list[tuple[str, re.Pattern, str]] = [
    ("POST", re.compile(r"^/v1/projects$"), "project.create"),
    ("PATCH", re.compile(r"^/v1/projects/([^/]+)$"), "project.update"),
    ("DELETE", re.compile(r"^/v1/projects/([^/]+)$"), "project.delete"),
    ("POST", re.compile(r"^/v1/projects/([^/]+)/keys$"), "key.create"),
    ("DELETE", re.compile(r"^/v1/projects/([^/]+/keys/[^/]+)$"), "key.revoke"),
    ("POST", re.compile(r"^/v1/projects/([^/]+)/categories$"), "category.create"),
    ("PATCH", re.compile(r"^/v1/projects/([^/]+/categories/[^/]+)$"), "category.update"),
    ("DELETE", re.compile(r"^/v1/projects/([^/]+/categories/[^/]+)$"), "category.delete"),
    ("POST", re.compile(r"^/v1/projects/([^/]+)/consolidate$"), "project.consolidate"),
    ("POST", re.compile(r"^/v1/orgs$"), "org.create"),
    ("POST", re.compile(r"^/v1/org/keys$"), "org_key.create"),
    ("DELETE", re.compile(r"^/v1/org/keys/([^/]+)$"), "org_key.revoke"),
    ("POST", re.compile(r"^/v1/memories$"), "memory.create"),
    ("POST", re.compile(r"^/v1/export$"), "project.export"),
    ("PATCH", re.compile(r"^/v1/memories/([^/]+)$"), "memory.update"),
    ("DELETE", re.compile(r"^/v1/episodes/(.+)$"), "source.delete"),
    ("DELETE", re.compile(r"^/v1/sessions/([^/]+)$"), "session.delete"),
    ("POST", re.compile(r"^/v1/jobs/([^/]+)/retry$"), "job.retry"),
    ("PUT", re.compile(r"^/v1/audiences/([^/]+)$"), "audience.put"),
    ("DELETE", re.compile(r"^/v1/audiences/([^/]+)$"), "audience.delete"),
    ("PUT", re.compile(r"^/v1/identities/(.+)$"), "identity.put"),
    ("DELETE", re.compile(r"^/v1/identities/(.+)$"), "identity.delete"),
    ("POST", re.compile(r"^/v1/auth/signup$"), "account.signup"),
    ("POST", re.compile(r"^/v1/auth/login$"), "account.login"),
    ("POST", re.compile(r"^/v1/auth/accept$"), "member.join"),
    ("POST", re.compile(r"^/v1/auth/password$"), "account.password"),
    ("PATCH", re.compile(r"^/v1/org/members/([^/]+)$"), "member.role"),
    ("DELETE", re.compile(r"^/v1/org/members/([^/]+)$"), "member.remove"),
    ("POST", re.compile(r"^/v1/org/invites$"), "invite.create"),
    ("DELETE", re.compile(r"^/v1/org/invites/([^/]+)$"), "invite.revoke"),
]


def match(method: str, path: str) -> tuple[str, str] | None:
    for m, pattern, action in AUDITED:
        if m == method and (found := pattern.match(path)):
            return action, (found.group(1) if found.groups() else path)
    return None


def _admin_actor(admin) -> str:
    return admin.actor


async def record(pool, request, status: int) -> None:
    hit = match(request.method, request.url.path)
    if not hit or status >= 400:
        return
    action, target = hit
    target = getattr(request.state, "audit_target", None) or target
    principal = getattr(request.state, "principal", None)
    admin = getattr(request.state, "admin", None)
    async with pool.connection() as conn:
        org_id = project_id = None
        if principal is not None:
            project_id = principal.project_id
            row = await (await conn.execute("SELECT org_id FROM projects WHERE id = %s", (project_id,))).fetchone()
            org_id = row["org_id"] if row else None
            actor = _admin_actor(admin) if principal.key_id is None and admin else f"project key {principal.key_id}"
        elif admin is not None:
            org_id = admin.org_id
            actor = _admin_actor(admin)
            if org_id is None and action.startswith(("project.", "key.")):
                slug = target.split("/")[0]
                row = await (await conn.execute("SELECT id, org_id FROM projects WHERE slug = %s", (slug,))).fetchone()
                if row:
                    project_id, org_id = row["id"], row["org_id"]
        else:
            return
        await conn.execute(
            "INSERT INTO audit_events (org_id, project_id, actor, action, target, status) VALUES (%s, %s, %s, %s, %s, %s)",
            (org_id, project_id, actor, action, target[:300], status))
