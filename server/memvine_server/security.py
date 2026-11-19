"""Request hardening for a server on the public internet: the caller's real address behind proxies,
rate limits with 429 + Retry-After, a cap on request bodies, and response security headers.

Client address. Behind a proxy (Railway's edge, a tunnel, the console calling the API) the socket
peer is the proxy, and the caller's address is in X-Forwarded-For. Anyone can send that header, so
it is read from the right: each hop added by a trusted proxy (MEMVINE_TRUSTED_PROXIES) is skipped,
and the first address a trusted proxy did not vouch for is the client. A peer that is not a trusted
proxy is the client itself, whatever the header says.

Rate limits. One in-process limiter (the API runs as one replica). Its storage sits behind the
`Backend` interface, so a shared store (Postgres, Redis) can replace `MemoryBackend` when the API
runs as several replicas, without changing the rules or their callers.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Protocol

from .config import settings

# ------------------------------------------------------------------ client address

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# "private" in MEMVINE_TRUSTED_PROXIES: addresses no internet client can connect from directly, so a
# peer holding one is infrastructure in front of the server (Railway's edge and private network,
# Docker, a local tunnel).
PRIVATE_NETWORKS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16",
    "::1/128", "fc00::/7", "fe80::/10",
)]


def parse_ip(text: str) -> IPAddress | None:
    """An address from a peer or a forwarded hop: tolerates a port ("1.2.3.4:56", "[::1]:80") and
    reads an IPv4-mapped IPv6 address (::ffff:10.0.0.1) as the IPv4 address it carries, so it cannot
    pass for (or dodge) a trusted network of the other family."""
    text = text.strip().strip('"')
    if text.startswith("["):
        text = text[1:].split("]", 1)[0]
    elif text.count(":") == 1:
        text = text.split(":", 1)[0]
    try:
        ip = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def parse_networks(spec: str) -> list[IPNetwork]:
    nets: list[IPNetwork] = []
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        if part.lower() == "private":
            nets.extend(PRIVATE_NETWORKS)
        else:
            nets.append(ipaddress.ip_network(part, strict=False))
    return nets


_trusted_cache: tuple[str, list[IPNetwork]] = ("", [])


def trusted_networks() -> list[IPNetwork]:
    global _trusted_cache
    spec = settings.trusted_proxies
    if _trusted_cache[0] != spec:
        _trusted_cache = (spec, parse_networks(spec))
    return _trusted_cache[1]


def _trusted(ip: IPAddress, nets: list[IPNetwork]) -> bool:
    return any(ip.version == n.version and ip in n for n in nets)


def resolve_client(peer: str, forwarded_for: str, nets: list[IPNetwork] | None = None) -> str:
    """The caller's address from the socket peer and the X-Forwarded-For chain (see the module doc)."""
    nets = trusted_networks() if nets is None else nets
    peer_ip = parse_ip(peer) if peer else None
    if peer_ip is None:
        return peer
    if not _trusted(peer_ip, nets):
        return str(peer_ip)
    hops = [h for h in (x.strip() for x in forwarded_for.split(",")) if h]
    current = peer_ip
    for hop in reversed(hops):
        ip = parse_ip(hop)
        if ip is None:  # garbage a trusted proxy passed along: stop at the last address we trust
            break
        current = ip
        if not _trusted(ip, nets):
            break
    return str(current)


def request_client_ip(scope) -> str:
    peer = scope.get("client")
    forwarded = ""
    for name, value in scope.get("headers") or []:
        if name == b"x-forwarded-for":
            # Several X-Forwarded-For headers read as one comma-joined chain, in order.
            forwarded = f"{forwarded},{value.decode('latin-1')}" if forwarded else value.decode("latin-1")
    return resolve_client(peer[0] if peer else "", forwarded)


# ------------------------------------------------------------------ rate limiting


class Backend(Protocol):
    """Sliding-window counters. Async, so a networked store fits behind the same calls."""

    async def hit(self, key: str, limit: int, window: float) -> float | None:
        """Count one event; seconds to wait when this one is over the limit (and is not counted)."""

    async def blocked(self, key: str, limit: int, window: float) -> float | None:
        """Seconds to wait when `limit` events are already in the window, without counting one."""

    async def add(self, key: str, window: float) -> None:
        """Count one event without checking (failures counted after the fact)."""

    async def reset(self) -> None: ...


class MemoryBackend:
    """Timestamps per key in this process. Old keys are swept as the table grows."""

    SWEEP_AT = 50_000

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = {}
        self._windows: dict[str, float] = {}

    def _queue(self, key: str, window: float, now: float) -> deque[float]:
        q = self._events.get(key)
        if q is None:
            if len(self._events) >= self.SWEEP_AT:
                self._sweep(now)
            q = self._events[key] = deque()
        self._windows[key] = window
        while q and now - q[0] >= window:
            q.popleft()
        return q

    def _sweep(self, now: float) -> None:
        for key in [k for k, q in self._events.items() if not q or now - q[-1] >= self._windows.get(k, 0)]:
            self._events.pop(key, None)
            self._windows.pop(key, None)

    @staticmethod
    def _wait(q: deque[float], limit: int, window: float, now: float) -> float | None:
        if len(q) < limit:
            return None
        return max(q[len(q) - limit] + window - now, 0.001)

    async def hit(self, key: str, limit: int, window: float) -> float | None:
        now = time.monotonic()
        q = self._queue(key, window, now)
        wait = self._wait(q, limit, window, now)
        if wait is None:
            q.append(now)
        return wait

    async def blocked(self, key: str, limit: int, window: float) -> float | None:
        now = time.monotonic()
        return self._wait(self._queue(key, window, now), limit, window, now)

    async def add(self, key: str, window: float) -> None:
        now = time.monotonic()
        self._queue(key, window, now).append(now)

    async def reset(self) -> None:
        self._events.clear()
        self._windows.clear()


@dataclass(frozen=True)
class Limit:
    name: str
    limit: int
    window: float  # seconds


# Unauthenticated account routes, per client address. Failed sign-ins and failed password checks
# are also limited per account (accounts.py).
AUTH_LIMITS: dict[str, list[Limit]] = {
    "/v1/auth/login": [Limit("login", 30, 300)],
    "/v1/auth/signup": [Limit("signup", 10, 3600)],
    "/v1/auth/accept": [Limit("accept", 20, 600)],
    "/v1/auth/invites/describe": [Limit("invite", 30, 600)],
    "/v1/auth/password": [Limit("password", 20, 600)],
    "/v1/auth/reset": [Limit("reset", 20, 600)],
    "/v1/auth/reset/describe": [Limit("reset-describe", 30, 600)],
}
LOGIN_FAILURES = Limit("login-fail", 10, 900)       # per account (email) and per address
PASSWORD_FAILURES = Limit("password-fail", 5, 900)  # per signed-in person


class RateLimited(Exception):
    def __init__(self, retry_after: float, message: str = "Too many requests. Slow down and try again.") -> None:
        super().__init__(message)
        self.retry_after = max(1, math.ceil(retry_after))
        self.message = message


class Limiter:
    def __init__(self, backend: Backend | None = None) -> None:
        self.backend: Backend = backend or MemoryBackend()

    async def check(self, key: str, limit: Limit) -> None:
        if not settings.rate_limit_enabled:
            return
        wait = await self.backend.hit(f"{limit.name}:{key}", limit.limit, limit.window)
        if wait is not None:
            raise RateLimited(wait)

    async def blocked(self, key: str, limit: Limit) -> float | None:
        if not settings.rate_limit_enabled:
            return None
        return await self.backend.blocked(f"{limit.name}:{key}", limit.limit, limit.window)

    async def failed(self, key: str, limit: Limit) -> None:
        if settings.rate_limit_enabled:
            await self.backend.add(f"{limit.name}:{key}", limit.window)

    async def reset(self) -> None:
        await self.backend.reset()


limiter = Limiter()


def token_bucket_key(token: str) -> str:
    """A key's bucket is named by a hash, so the limiter never holds a usable credential."""
    return hashlib.sha256(token.encode()).hexdigest()[:32]


# ------------------------------------------------------------------ the middleware

BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Routes that take conversation text in bulk get the larger cap (their items have limits of their own).
BULK_PREFIXES = ("/v1/ingest", "/v1/sessions/")

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
    # Browsers ignore this over plain http, so a local server is unaffected.
    (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
]


def body_cap(path: str) -> int:
    if path.startswith(BULK_PREFIXES):
        return settings.max_bulk_body_bytes
    return settings.max_body_bytes


async def _send_error(send, status: int, code: str, message: str, extra_headers: list | None = None) -> None:
    body = json.dumps({"error": {"code": code, "message": message}}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
               *SECURITY_HEADERS, *(extra_headers or [])]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _bearer(scope) -> str:
    for name, value in scope.get("headers") or []:
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            return token.strip() if scheme.lower() == "bearer" else ""
    return ""


class SecurityMiddleware:
    """Outermost layer: rate limits, then the body cap, then security headers on every response."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path, method = scope.get("path", ""), scope.get("method", "GET")
        ip = request_client_ip(scope)

        try:
            for limit in AUTH_LIMITS.get(path, []) if method == "POST" else []:
                await limiter.check("ip:" + ip, limit)
            token = _bearer(scope)
            if token:
                await limiter.check(token_bucket_key(token), Limit("key", settings.rate_limit_key_per_minute, 60))
            if path != "/health":
                await limiter.check("ip:" + ip, Limit("ip", settings.rate_limit_ip_per_minute, 60))
        except RateLimited as exc:
            await _send_error(send, 429, "rate-limited", exc.message, [(b"retry-after", str(exc.retry_after).encode())])
            return

        if method in BODY_METHODS:
            cap = body_cap(path)
            declared = next((v for n, v in scope.get("headers") or [] if n == b"content-length"), None)
            try:
                too_big = declared is not None and int(declared) > cap
            except ValueError:
                await _send_error(send, 400, "bad-request", "Invalid Content-Length")
                return
            if too_big:
                await _send_error(send, 413, "body-too-large", f"Request bodies are limited to {cap:,} bytes")
                return
            # Read the body here, stopping at the cap, so an oversized chunked upload never reaches
            # the app; the app then reads the buffered copy.
            chunks, size, more = [], 0, True
            while more:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > cap:
                    await _send_error(send, 413, "body-too-large", f"Request bodies are limited to {cap:,} bytes")
                    return
                chunks.append(chunk)
                more = message.get("more_body", False)
            body, replayed = b"".join(chunks), False

            async def replay():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            app_receive = replay
        else:
            app_receive = receive

        async def with_headers(message):
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                message = {**message, "headers": [*message.get("headers", []),
                                                  *(h for h in SECURITY_HEADERS if h[0] not in present)]}
            await send(message)

        await self.app(scope, app_receive, with_headers)
