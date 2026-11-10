"""Request hardening: client address behind proxies, rate limits, body caps, security headers,
password hashing, invites under races, and what errors and logs may contain."""
from __future__ import annotations

import asyncio
import logging
import uuid

import pytest
from conftest import needs_db

from memvine_server import accounts, security
from memvine_server.config import settings
from memvine_server.security import parse_networks, resolve_client

PRIVATE = parse_networks("private")


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _email(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}@example.com"


# ------------------------------------------------------------------ client address (no database)

def test_forwarded_for_is_believed_only_from_trusted_proxies():
    # A client on the internet talking to the server directly cannot claim another address.
    assert resolve_client("203.0.113.9", "1.2.3.4", PRIVATE) == "203.0.113.9"
    # Behind Railway's edge (a private peer) the edge's hop is the client.
    assert resolve_client("10.0.0.2", "198.51.100.7", PRIVATE) == "198.51.100.7"
    # A forged left part of the chain is ignored: the rightmost untrusted hop wins.
    assert resolve_client("10.0.0.2", "1.2.3.4, 198.51.100.7", PRIVATE) == "198.51.100.7"
    # The console relays the browser's chain over the private network: two trusted hops.
    assert resolve_client("fd12::5", "1.2.3.4, 198.51.100.7, 100.64.0.3", PRIVATE) == "198.51.100.7"
    # A client forging a private address still ends up as its real address.
    assert resolve_client("10.0.0.2", "10.9.9.9, 198.51.100.7", PRIVATE) == "198.51.100.7"
    # IPv4-mapped IPv6 reads as IPv4, so a public client cannot pass for a trusted network.
    assert resolve_client("::ffff:203.0.113.9", "10.0.0.1", PRIVATE) == "203.0.113.9"
    assert resolve_client("::ffff:10.0.0.2", "198.51.100.7:4431", PRIVATE) == "198.51.100.7"
    # Nothing trusted: the header is ignored.
    assert resolve_client("127.0.0.1", "198.51.100.7", []) == "127.0.0.1"
    # Garbage in the chain stops the walk at the last address a trusted proxy vouched for.
    assert resolve_client("10.0.0.2", "1.2.3.4, not-an-ip", PRIVATE) == "10.0.0.2"
    assert parse_networks("10.1.0.0/16, 192.0.2.1") == [*map(__import__("ipaddress").ip_network, ("10.1.0.0/16", "192.0.2.1/32"))]


@pytest.mark.asyncio
async def test_memory_backend_sliding_window():
    backend = security.MemoryBackend()
    assert [await backend.hit("k", 2, 60) for _ in range(2)] == [None, None]
    wait = await backend.hit("k", 2, 60)
    assert wait is not None and 0 < wait <= 60
    assert await backend.blocked("other", 1, 60) is None
    await backend.add("other", 60)
    assert await backend.blocked("other", 1, 60) is not None


# ------------------------------------------------------------------ over HTTP



@needs_db
@pytest.mark.asyncio
async def test_sign_in_routes_are_rate_limited_per_address(client, monkeypatch):
    monkeypatch.setitem(security.AUTH_LIMITS, "/v1/auth/login", [security.Limit("login", 3, 300)])
    attempt = lambda ip: client.post("/v1/auth/login", json={"email": "nobody@example.com", "password": "x" * 12},  # noqa: E731
                                     headers={"X-Forwarded-For": ip})
    codes = [(await attempt("198.51.100.1")).status_code for _ in range(3)]
    assert codes == [401, 401, 401]
    limited = await attempt("198.51.100.1")
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "rate-limited"
    assert 1 <= int(limited.headers["retry-after"]) <= 300
    # Another address has its own budget; a forged hop in front of it does not borrow one.
    assert (await attempt("198.51.100.2")).status_code == 401
    assert (await attempt("198.51.100.2, 198.51.100.1")).status_code == 429


@needs_db
@pytest.mark.asyncio
async def test_keys_are_rate_limited_per_key(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_key_per_minute", 3)
    assert [(await client.get("/v1/memories")).status_code for _ in range(3)] == [200, 200, 200]
    limited = await client.get("/v1/memories")
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) >= 1
    # Another credential is unaffected.
    assert (await client.get("/v1/projects", headers=bearer("mv_mk_test"))).status_code == 200


@needs_db
@pytest.mark.asyncio
async def test_addresses_are_rate_limited_across_keys(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_ip_per_minute", 2)
    headers = {"X-Forwarded-For": "198.51.100.40"}
    assert (await client.get("/v1/memories", headers=headers)).status_code == 200
    assert (await client.get("/v1/memories", headers={**headers, **bearer("mv_sk_guess1")})).status_code == 401
    assert (await client.get("/v1/memories", headers={**headers, **bearer("mv_sk_guess2")})).status_code == 429
    assert (await client.get("/health", headers=headers)).status_code == 200  # health checks are never limited


@needs_db
@pytest.mark.asyncio
async def test_oversized_bodies_are_refused_before_the_app(client, monkeypatch):
    monkeypatch.setattr(settings, "max_body_bytes", 1000)
    monkeypatch.setattr(settings, "max_bulk_body_bytes", 5000)
    big = {"query": "x" * 2000}
    r = await client.post("/v1/recall", json=big)
    assert r.status_code == 413 and r.json()["error"]["code"] == "body-too-large"

    async def chunks():  # no Content-Length: the cap counts what arrives
        for _ in range(4):
            yield b"x" * 400
    r = await client.post("/v1/recall", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413

    # Bulk routes take the larger cap.
    item = {"id": "big", "content": "Northwind invoices close monthly. " * 100, "timestamp": "2026-10-01T10:00:00Z"}
    assert (await client.post("/v1/ingest", json={"items": [item]})).status_code == 202
    assert (await client.post("/v1/ingest", json={"items": [item, {**item, "id": "big2"}]})).status_code == 413
    # Small bodies pass through intact.
    assert (await client.post("/v1/recall", json={"query": "invoices"})).status_code == 200


@needs_db
@pytest.mark.asyncio
async def test_security_headers_on_every_response(client):
    for r in [await client.get("/health"), await client.get("/v1/memories"),
              await client.get("/v1/memories", headers=bearer("mv_sk_nope"))]:
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


@needs_db
@pytest.mark.asyncio
async def test_passwords_hashing_and_upgrade(client):
    assert accounts.hash_password("pw").startswith("scrypt$16384$8$5$")
    email = _email("hash")
    assert (await client.post("/v1/auth/signup", json={"email": email, "password": "x" * 12})).status_code == 201
    # An account hashed with the old, cheaper parameters is upgraded at its next sign-in.
    import base64
    import hashlib

    from memvine_server.main import app
    salt = b"s" * 16
    old = "scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(
        hashlib.scrypt(b"y" * 12, salt=salt, n=2**14, r=8, p=1, dklen=32)).decode()
    async with app.state.pool.connection() as conn:
        await conn.execute("UPDATE users SET password_hash = %s WHERE email = %s", (old, email))
    assert (await client.post("/v1/auth/login", json={"email": email, "password": "y" * 12})).status_code == 200
    async with app.state.pool.connection() as conn:
        stored = (await (await conn.execute("SELECT password_hash FROM users WHERE email = %s", (email,))).fetchone())["password_hash"]
    assert stored.startswith("scrypt$16384$8$5$") and accounts.check_password("y" * 12, stored)
    # A stored hash cannot make the server do unbounded work.
    assert not accounts.check_password("y", "scrypt$1048576$8$1$c2FsdA==$ZGlnZXN0")
    # Unknown account and wrong password answer alike.
    unknown = await client.post("/v1/auth/login", json={"email": _email("none"), "password": "x" * 12})
    wrong = await client.post("/v1/auth/login", json={"email": email, "password": "z" * 12})
    assert unknown.status_code == wrong.status_code == 401 and unknown.json() == wrong.json()


@needs_db
@pytest.mark.asyncio
async def test_wrong_current_password_is_throttled_per_person(client, monkeypatch):
    monkeypatch.setattr(accounts, "PASSWORD_FAILURES", security.Limit("password-fail", 2, 900))
    s = (await client.post("/v1/auth/signup", json={"email": _email("pw"), "password": "x" * 12})).json()
    change = lambda current: client.post("/v1/auth/password", json={"current": current, "new": "n" * 12},  # noqa: E731
                                         headers=bearer(s["token"]))
    assert [(await change("wrong")).status_code for _ in range(2)] == [403, 403]
    r = await change("x" * 12)
    assert r.status_code == 429 and "retry-after" in r.headers


@needs_db
@pytest.mark.asyncio
async def test_an_invite_admits_one_person_even_under_a_race(client):
    owner = (await client.post("/v1/auth/signup", json={"email": _email("own"), "password": "x" * 12})).json()
    invite = (await client.post("/v1/org/invites", json={"role": "member"}, headers=bearer(owner["token"]))).json()
    a = (await client.post("/v1/auth/signup", json={"email": _email("ra"), "password": "x" * 12})).json()
    b = (await client.post("/v1/auth/signup", json={"email": _email("rb"), "password": "x" * 12})).json()
    results = await asyncio.gather(*[client.post("/v1/auth/accept", json={"invite": invite["token"]}, headers=bearer(t["token"]))
                                     for t in (a, b)])
    assert sorted(r.status_code for r in results) == [200, 404]
    members = (await client.get("/v1/org/members", headers=bearer(owner["token"]))).json()
    assert len(members) == 2


@needs_db
@pytest.mark.asyncio
async def test_validation_errors_do_not_echo_passwords(client):
    secret = "hunter2-" + "p" * 300  # over the length limit
    r = await client.post("/v1/auth/login", json={"email": "a@example.com", "password": secret})
    assert r.status_code == 422 and "hunter2" not in r.text


@needs_db
@pytest.mark.asyncio
async def test_failed_jobs_do_not_log_customer_text(client, caplog):
    from memvine_server.main import app
    from memvine_server.worker import run_one

    class Broken:
        async def complete(self, system, user, *, max_tokens=4096):
            raise ValueError(f"no JSON found in model output: {user[-200:]!r}")

    await client.post("/v1/ingest", json={"items": [{"id": "s1", "content": "The Northwind vault code is 4417-SECRET.",
                                                     "timestamp": "2026-10-01T10:00:00Z"}]})
    async with app.state.pool.connection() as conn:
        project_id = (await (await conn.execute("SELECT id FROM projects WHERE slug = %s", (client.project_slug,))).fetchone())["id"]
    with caplog.at_level(logging.WARNING):
        assert await run_one(app.state.pool, Broken(), app.state.embedder, project_id)
    assert "failed" in caplog.text and "4417-SECRET" not in caplog.text
    # The project's own job record keeps the detail for its people.
    assert "4417-SECRET" in (await client.get("/v1/jobs")).json()["jobs"][0]["error"]


def test_user_agent_is_trimmed_and_printable():
    from memvine_server.auth import user_agent
    assert user_agent("memvine-claude-code/0.7.1\r\nX-Evil: 1") == "memvine-claude-code/0.7.1X-Evil: 1"
    assert len(user_agent("a" * 1000)) == 200
    assert user_agent("  ") is None
