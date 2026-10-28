"""The console's "Check connection" step: a key's first session and recall, its creator, and the
User-Agent of its last call (the plugin's version)."""
import uuid

import pytest
from conftest import needs_db

pytestmark = needs_db

MGMT = {"Authorization": "Bearer mv_mk_test"}


@pytest.mark.asyncio
async def test_key_connection_tracks_first_session_recall_and_agent(client):
    slug = client.project_slug
    made = await client.post(f"/v1/projects/{slug}/keys", json={"name": "laptop"}, headers=MGMT)
    assert made.status_code == 201
    key_id, secret = made.json()["id"], made.json()["key"]
    assert made.json()["created_by_email"] is None  # made with the management key, not by a person

    url = f"/v1/projects/{slug}/keys/{key_id}/connection"
    before = (await client.get(url, headers=MGMT)).json()
    assert before["calls"] == 0 and before["first_session_at"] is None and before["first_recall_at"] is None
    assert before["last_user_agent"] is None

    agent = {"Authorization": f"Bearer {secret}", "User-Agent": "memvine-claude-code/0.7.1"}
    r = await client.post("/v1/sessions/conn-1/events", headers=agent, json={
        "events": [{"role": "user", "content": "Where do refunds get approved?", "at": "2026-10-26T09:00:00Z"}],
        "agent": "claude-code"})
    assert r.status_code == 202, r.text
    after_session = (await client.get(url, headers=MGMT)).json()
    assert after_session["first_session_at"] and after_session["first_recall_at"] is None
    assert after_session["last_user_agent"] == "memvine-claude-code/0.7.1"

    r = await client.post("/v1/recall", headers={**agent, "User-Agent": "memvine-claude-code/0.7.2"},
                          json={"query": "Where do refunds get approved?"})
    assert r.status_code == 200, r.text
    done = (await client.get(url, headers=MGMT)).json()
    assert done["first_recall_at"] and done["calls"] == 2 and done["last_user_agent"] == "memvine-claude-code/0.7.2"

    # The key list carries the agent too; another project's key, or an unknown one, reads as missing.
    listed = next(k for k in (await client.get(f"/v1/projects/{slug}/keys", headers=MGMT)).json() if k["id"] == key_id)
    assert listed["last_user_agent"] == "memvine-claude-code/0.7.2"
    assert (await client.get(f"/v1/projects/{slug}/keys/{uuid.uuid4()}/connection", headers=MGMT)).status_code == 404


@pytest.mark.asyncio
async def test_a_signed_in_person_owns_the_keys_they_create(client):
    email = f"conn-{uuid.uuid4().hex[:8]}@example.com"
    owner = (await client.post("/v1/auth/signup", json={"email": email, "password": "correct horse", "organization": "Conn Co"})).json()
    me = {"Authorization": f"Bearer {owner['token']}"}
    project = (await client.post("/v1/projects", json={"name": f"conn {uuid.uuid4().hex[:4]}"}, headers=me)).json()
    made = await client.post(f"/v1/projects/{project['slug']}/keys", json={"name": "laptop"}, headers=me)
    assert made.status_code == 201 and made.json()["created_by_email"] == email
    listed = (await client.get(f"/v1/projects/{project['slug']}/keys", headers=me)).json()
    assert [k["created_by_email"] for k in listed if k["id"] == made.json()["id"]] == [email]
    conn = await client.get(f"/v1/projects/{project['slug']}/keys/{made.json()['id']}/connection", headers=me)
    assert conn.status_code == 200 and conn.json()["calls"] == 0
    # Another organization cannot read it.
    other = (await client.post("/v1/auth/signup", json={"email": f"x-{email}", "password": "correct horse", "organization": "Other"})).json()
    assert (await client.get(f"/v1/projects/{project['slug']}/keys/{made.json()['id']}/connection",
                             headers={"Authorization": f"Bearer {other['token']}"})).status_code == 404
