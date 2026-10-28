"""People: sign-up, invites, roles, sessions. Uses the same client fixture as test_e2e."""
import uuid

import pytest

from memvine_server import accounts
from memvine_server.config import settings


def _email(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}@example.com"


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_signup_invite_roles_and_sessions(client):
    owner_email = _email("owner")
    r = await client.post("/v1/auth/signup", json={"email": owner_email, "password": "correct horse", "name": "Ola", "organization": "Ola Co"})
    assert r.status_code == 201, r.text
    owner = r.json()
    assert owner["role"] == "owner" and owner["token"].startswith("mv_us_")
    o = bearer(owner["token"])
    me = (await client.get("/v1/org", headers=o)).json()
    assert me["user"]["email"] == owner_email and me["org"]["name"] == "Ola Co" and me["role"] == "owner"

    # Duplicate email, weak password, bad email.
    assert (await client.post("/v1/auth/signup", json={"email": owner_email.upper(), "password": "x" * 12})).status_code == 409
    assert (await client.post("/v1/auth/signup", json={"email": _email("w"), "password": "short"})).status_code == 422
    assert (await client.post("/v1/auth/signup", json={"email": "nope", "password": "x" * 12})).status_code == 422

    # The owner creates a project and invites a member by link.
    project = (await client.post("/v1/projects", json={"name": f"ola app {uuid.uuid4().hex[:4]}"}, headers=o)).json()
    invite = (await client.post("/v1/org/invites", json={"role": "member"}, headers=o)).json()
    assert invite["token"].startswith("mv_in_")
    assert (await client.post("/v1/auth/invites/describe", json={"invite": invite["token"]})).json()["organization"] == "Ola Co"
    member_email = _email("member")
    m = (await client.post("/v1/auth/signup", json={"email": member_email, "password": "another pass", "invite": invite["token"]})).json()
    assert m["role"] == "member" and m["org"]["slug"] == owner["org"]["slug"]
    mh = bearer(m["token"])
    # The invite is single-use.
    assert (await client.post("/v1/auth/signup", json={"email": _email("late"), "password": "x" * 12, "invite": invite["token"]})).status_code == 404

    # A member sees and uses projects, creates a key to connect an agent, but cannot administer.
    assert [p["slug"] for p in (await client.get("/v1/projects", headers=mh)).json()] == [project["slug"]]
    assert (await client.get("/v1/memories", headers={**mh, "X-Memvine-Project": project["slug"]})).status_code == 200
    assert (await client.post(f"/v1/projects/{project['slug']}/keys", json={"name": "my laptop"}, headers=mh)).status_code == 201
    assert (await client.post("/v1/projects", json={"name": "nope"}, headers=mh)).status_code == 403
    assert (await client.delete(f"/v1/projects/{project['slug']}?confirm={project['slug']}", headers=mh)).status_code == 403
    assert (await client.post("/v1/org/invites", json={}, headers=mh)).status_code == 403
    assert (await client.put("/v1/audiences/eng", json={"kind": "fixed", "members": ["a"]}, headers={**mh, "X-Memvine-Project": project["slug"]})).status_code == 403

    # Roles: the last owner cannot step down; an owner promotes the member, who can then administer.
    members = (await client.get("/v1/org/members", headers=o)).json()
    owner_id = next(x["id"] for x in members if x["email"] == owner_email)
    member_id = next(x["id"] for x in members if x["email"] == member_email)
    assert (await client.patch(f"/v1/org/members/{owner_id}", json={"role": "admin"}, headers=o)).status_code == 409
    assert (await client.patch(f"/v1/org/members/{member_id}", json={"role": "admin"}, headers=o)).status_code == 200
    assert (await client.post("/v1/org/invites", json={"email": _email("x")}, headers=mh)).status_code == 201
    # An admin cannot make owners or remove the owner.
    assert (await client.patch(f"/v1/org/members/{member_id}", json={"role": "owner"}, headers=mh)).status_code == 403
    assert (await client.delete(f"/v1/org/members/{owner_id}", headers=mh)).status_code == 403

    # Login, switching organizations, logout.
    login = (await client.post("/v1/auth/login", json={"email": member_email.upper(), "password": "another pass"})).json()
    assert login["org"]["slug"] == owner["org"]["slug"]
    other = (await client.post("/v1/auth/signup", json={"email": _email("other"), "password": "x" * 12, "organization": "Other"})).json()
    inv2 = (await client.post("/v1/org/invites", json={}, headers=bearer(other["token"]))).json()
    joined = (await client.post("/v1/auth/accept", json={"invite": inv2["token"]}, headers=bearer(login["token"]))).json()
    assert joined["org"]["name"] == "Other"
    orgs = (await client.get("/v1/org", headers=bearer(joined["token"]))).json()["orgs"]
    assert {x["name"] for x in orgs} == {"Ola Co", "Other"}
    switched = (await client.post("/v1/auth/switch", json={"org": owner["org"]["slug"]}, headers=bearer(joined["token"]))).json()
    assert (await client.get("/v1/org", headers=bearer(joined["token"]))).status_code == 403, "switching ends the old session"
    assert (await client.post("/v1/auth/logout", headers=bearer(switched["token"]))).status_code == 204
    assert (await client.get("/v1/org", headers=bearer(switched["token"]))).status_code == 403

    # Removing a member ends their sessions.
    assert (await client.delete(f"/v1/org/members/{member_id}", headers=o)).status_code == 204
    assert (await client.get("/v1/org", headers=mh)).status_code == 403

    # Password change keeps this session and ends the others.
    second = (await client.post("/v1/auth/login", json={"email": owner_email, "password": "correct horse"})).json()["token"]
    assert (await client.post("/v1/auth/password", json={"current": "wrong", "new": "x" * 12}, headers=o)).status_code == 403
    assert (await client.post("/v1/auth/password", json={"current": "correct horse", "new": "battery staple"}, headers=o)).status_code == 204
    assert (await client.get("/v1/org", headers=o)).status_code == 200
    assert (await client.get("/v1/org", headers=bearer(second))).status_code == 403

    # The audit log names people.
    trail = (await client.get("/v1/audit", headers=o)).json()
    actions = {e["action"] for e in trail}
    assert {"account.signup", "invite.create", "member.role", "member.remove", "project.create", "account.password"} <= actions
    assert any(e["actor"] == owner_email and e["action"] == "project.create" for e in trail)

    await client.delete(f"/v1/projects/{project['slug']}?confirm={project['slug']}", headers=o)


@pytest.mark.asyncio
async def test_failed_logins_are_throttled(client, monkeypatch):
    monkeypatch.setattr(accounts, "LOGIN_FAILURES", 3)
    email = _email("t")
    await client.post("/v1/auth/signup", json={"email": email, "password": "x" * 12})
    codes = [(await client.post("/v1/auth/login", json={"email": email, "password": "wrong"})).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]
    assert (await client.post("/v1/auth/login", json={"email": email, "password": "x" * 12})).status_code == 429
    accounts._failures.clear()


@pytest.mark.asyncio
async def test_signup_modes(client, monkeypatch):
    monkeypatch.setattr(settings, "signup", "invite")
    assert (await client.post("/v1/auth/signup", json={"email": _email("i"), "password": "x" * 12})).status_code == 403
    assert (await client.get("/v1/auth/config")).json() == {"signup": "invite"}
    monkeypatch.setattr(settings, "signup", "closed")
    assert (await client.post("/v1/auth/signup", json={"email": _email("c"), "password": "x" * 12, "invite": "mv_in_x"})).status_code == 403


@pytest.mark.asyncio
async def test_project_names_are_per_organization(client):
    a = (await client.post("/v1/auth/signup", json={"email": _email("pa"), "password": "x" * 12})).json()
    b = (await client.post("/v1/auth/signup", json={"email": _email("pb"), "password": "x" * 12})).json()
    name = f"Shared name {uuid.uuid4().hex[:4]}"
    pa = await client.post("/v1/projects", json={"name": name}, headers=bearer(a["token"]))
    pb = await client.post("/v1/projects", json={"name": name}, headers=bearer(b["token"]))
    assert pa.status_code == pb.status_code == 201
    assert pa.json()["slug"] != pb.json()["slug"]
    again = await client.post("/v1/projects", json={"name": name.upper()}, headers=bearer(a["token"]))
    assert again.status_code == 409 and "already has" in again.json()["error"]["message"]
