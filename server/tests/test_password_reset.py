"""Password reset links: an owner or admin makes one for a member; the operator makes one for anyone
from the command line; the link works once, ends every sign-in, and a newer one replaces it."""
import asyncio
import uuid

import psycopg
import pytest

from tests.conftest import TEST_DSN


def _email(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}@example.com"


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _org_with_member(client, member_role: str = "member"):
    owner_email, member_email = _email("owner"), _email("member")
    owner = (await client.post("/v1/auth/signup", json={"email": owner_email, "password": "owner password",
                                                        "organization": f"Reset Co {uuid.uuid4().hex[:4]}"})).json()
    o = bearer(owner["token"])
    invite = (await client.post("/v1/org/invites", json={"role": member_role}, headers=o)).json()
    member = (await client.post("/v1/auth/signup", json={"email": member_email, "password": "member password",
                                                         "invite": invite["token"]})).json()
    ids = {x["email"]: x["id"] for x in (await client.get("/v1/org/members", headers=o)).json()}
    return owner, o, owner_email, ids[owner_email], member, member_email, ids[member_email]


@pytest.mark.asyncio
async def test_admin_reset_link_sets_a_new_password_once(client):
    owner, o, owner_email, owner_id, member, member_email, member_id = await _org_with_member(client)

    made = await client.post(f"/v1/org/members/{member_id}/reset", headers=o)
    assert made.status_code == 201, made.text
    link = made.json()
    assert link["token"].startswith("mv_rs_") and link["email"] == member_email
    assert (await client.post("/v1/auth/reset/describe", json={"token": link["token"]})).json() == {"email": member_email}

    # Too short a password is refused, and the link still works afterwards.
    assert (await client.post("/v1/auth/reset", json={"token": link["token"], "password": "short"})).status_code == 422
    assert (await client.post("/v1/auth/reset", json={"token": link["token"], "password": "a brand new one"})).status_code == 204

    # Every sign-in the member had is over; the old password fails and the new one works.
    assert (await client.get("/v1/org", headers=bearer(member["token"]))).status_code == 403
    assert (await client.post("/v1/auth/login", json={"email": member_email, "password": "member password"})).status_code == 401
    assert (await client.post("/v1/auth/login", json={"email": member_email, "password": "a brand new one"})).status_code == 200

    # The link is used up.
    again = await client.post("/v1/auth/reset", json={"token": link["token"], "password": "yet another one"})
    assert again.status_code == 404 and again.json()["error"]["code"] == "reset-invalid"
    assert (await client.post("/v1/auth/reset/describe", json={"token": link["token"]})).status_code == 404
    assert (await client.post("/v1/auth/reset", json={"token": "mv_rs_made-up", "password": "a brand new one"})).status_code == 404

    # Both steps are in the organization's audit log, with who made the link.
    trail = (await client.get("/v1/audit", headers=o)).json()
    assert any(e["action"] == "member.reset_link" and e["actor"] == owner_email and e["target"] == member_email for e in trail)
    assert any(e["action"] == "account.reset" and e["target"] == member_email for e in trail)


@pytest.mark.asyncio
async def test_who_may_make_a_reset_link(client):
    owner, o, owner_email, owner_id, member, member_email, member_id = await _org_with_member(client, "admin")
    admin = bearer(member["token"])  # the invited person is an admin here

    # Not for yourself (change it under Settings), not by an admin for an owner, not by a member.
    assert (await client.post(f"/v1/org/members/{owner_id}/reset", headers=o)).json()["error"]["code"] == "self-reset"
    assert (await client.post(f"/v1/org/members/{owner_id}/reset", headers=admin)).status_code == 403
    invite = (await client.post("/v1/org/invites", json={"role": "member"}, headers=o)).json()
    plain = (await client.post("/v1/auth/signup", json={"email": _email("plain"), "password": "plain password",
                                                        "invite": invite["token"]})).json()
    plain_id = next(x["id"] for x in (await client.get("/v1/org/members", headers=o)).json() if x["role"] == "member")
    assert (await client.post(f"/v1/org/members/{member_id}/reset", headers=bearer(plain["token"]))).status_code == 403
    # An admin may make one for a member; the operator key is not a person in the organization.
    assert (await client.post(f"/v1/org/members/{plain_id}/reset", headers=admin)).status_code == 201
    assert (await client.post(f"/v1/org/members/{plain_id}/reset", headers=bearer("mv_mk_test"))).status_code == 400

    # Not for someone outside the organization.
    stranger = (await client.post("/v1/auth/signup", json={"email": _email("stranger"), "password": "x" * 12, "organization": "Elsewhere"})).json()
    stranger_id = (await client.get("/v1/org", headers=bearer(stranger["token"]))).json()["user"]["id"]
    assert (await client.post(f"/v1/org/members/{stranger_id}/reset", headers=o)).status_code == 404

    # Not for someone who also belongs to another organization: that one is the operator's to make.
    other_invite = (await client.post("/v1/org/invites", json={"role": "member"}, headers=bearer(stranger["token"]))).json()
    joined = await client.post("/v1/auth/accept", json={"invite": other_invite["token"]}, headers=bearer(plain["token"]))
    assert joined.status_code == 200, joined.text
    refused = await client.post(f"/v1/org/members/{plain_id}/reset", headers=o)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "other-organizations"


@pytest.mark.asyncio
async def test_a_newer_link_replaces_the_older_and_links_expire(client):
    owner, o, owner_email, owner_id, member, member_email, member_id = await _org_with_member(client)
    first = (await client.post(f"/v1/org/members/{member_id}/reset", headers=o)).json()["token"]
    second = (await client.post(f"/v1/org/members/{member_id}/reset", headers=o)).json()["token"]
    assert (await client.post("/v1/auth/reset", json={"token": first, "password": "a brand new one"})).status_code == 404
    with psycopg.connect(TEST_DSN, autocommit=True) as conn:
        conn.execute("UPDATE password_resets SET expires_at = now() - interval '1 minute' WHERE user_id = %s", (member_id,))
    assert (await client.post("/v1/auth/reset", json={"token": second, "password": "a brand new one"})).status_code == 404


@pytest.mark.asyncio
async def test_operator_reset_link_reaches_a_locked_out_owner(client):
    from typer.testing import CliRunner

    from memvine_server.cli import app as cli

    owner, o, owner_email, *_ = await _org_with_member(client)
    # The command runs its own event loop, as it does on a server: here, off the test's loop.
    result = await asyncio.to_thread(CliRunner().invoke, cli, ["reset-link", owner_email.upper(), "--console", "https://app.example.com/"])
    assert result.exit_code == 0, result.output
    line = result.output.splitlines()[0]
    assert line.startswith("https://app.example.com/reset?token=mv_rs_")
    token = line.split("token=", 1)[1]
    assert (await client.post("/v1/auth/reset", json={"token": token, "password": "owner is back in"})).status_code == 204
    assert (await client.post("/v1/auth/login", json={"email": owner_email, "password": "owner is back in"})).status_code == 200
    assert (await asyncio.to_thread(CliRunner().invoke, cli, ["reset-link", "nobody@example.com"])).exit_code != 0
