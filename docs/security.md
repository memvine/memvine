# Security

What Memvine protects and how, what an operator has to configure, and the known limits. The API
(`server/`) and the console (`console/`) are meant to sit on the public internet (Railway, see
`deploy/railway/README.md`).

## Credentials

| Credential | Prefix | Who holds it | Reaches |
| --- | --- | --- | --- |
| Project key | `mv_sk_` | an agent plugin, the SDKs, MCP clients | one project's memory routes (`/v1/ingest`, `/v1/recall`, `/v1/sessions/...`, `/v1/memories/...`, `/mcp`) |
| Sign-in session | `mv_us_` | a person in the console (cookie) | their organization, with their role (owner, admin, member) |
| Organization key | `mv_mk_` | scripts, CI | one organization, with owner rights |
| Operator key | `MEMVINE_MANAGEMENT_KEY` | the person running the server | every organization; creating organizations |

- Keys and session tokens are 256-bit random values, stored as SHA-256 hashes (`api_keys.key_hash`,
  `org_keys`, `user_sessions.token_hash`, `invites.token_hash`); a database dump holds no usable
  credential. A lookup by hash needs no constant-time compare; the operator key, the only
  credential held in configuration, is compared with `hmac.compare_digest`.
- Passwords: scrypt, N=2^14, r=8, p=5 (OWASP's 16 MiB profile), 16-byte salt, run off the event
  loop. Hashes with older parameters are upgraded at the next sign-in. A stored hash cannot ask for
  more than N=2^17. Unknown emails cost the same work as a wrong password and get the same answer.
- Sessions last `MEMVINE_SESSION_DAYS` (30). Logout, switching organization, removal from an
  organization and a password change (other sessions) revoke them on the server.
- Invites: 7 days, single use (claimed with one conditional update, so two people racing on one
  link cannot both join), optionally bound to one email, revocable.

## Authorization and tenant isolation

Every route depends on exactly one of `require_project` (project key, or a session/organization key
plus `X-Memvine-Project`), `require_management` (any session or key of an organization),
`require_manager` (owner or admin), `require_operator`, or is public (`/health`, `/v1/auth/config`,
sign-up, sign-in, invite description). Members may read and use memory and create project keys;
administrative changes need an owner or admin (`ensure_manager`).

Every query filters by the caller's project or organization, and Postgres row-level security
(`migrations/rls.sql`, role `memvine_app`) enforces the project filter underneath. Another
organization's project, memory, key or member reads as missing (404), never as forbidden.

`server/tests/test_tenant_isolation.py` enumerates every route in the app (a new route fails it until
it is listed) and aims each one, with a valid body, at organization B's project, memories, sources,
sessions, identities, audiences, keys, members, invites and jobs, as A's project key, A's owner
session and A's organization key (both naming B's project and naming their own). It asserts nothing
of B's comes back (B's data carries a marker word) and nothing of B's changes (a before/after
snapshot of B's rows), and repeats the check over MCP.

**Inside one organization.** Members of an organization can open every project of it, with two
exceptions made for people's sessions (migration 0014):

- A **personal project** (`projects.owner_user_id`) is opened only by the person who created it.
  The organization's owner and admins, and an organization key, get 404 for it; it is absent from
  their project list, their audit log and their synthesis health, and its audit lines stay its
  owner's after it is deleted. Any member may create one and manages it like an owner.
  `server/tests/test_personal_projects.py` fills one with marked data and aims every project route
  in the app at it as the organization's owner, an admin and an organization key: nothing comes
  back and nothing changes. The operator key still reads everything on the server.
- A **team space** (`projects.team_space`) refuses conversation: `POST /v1/ingest` and session
  events answer 403 `team-space`, whoever sends them, and a project that already holds sessions or
  sources cannot be turned into one. It holds memories people posted from their own projects, each
  with its author, date and one quoted line (`server/tests/test_team_space.py`).

What this does not do: a person's sessions are still stored on the server their own project is
on, readable by whoever runs that server (the operator key, the database). Running that project
on a server of one's own is the only way to keep them off someone else's machine. A shared memory
carries a quoted line of the session, which the plugin shows before sharing.

## Rate limits

`server/memvine_server/security.py`, an in-process sliding window (the API runs as one replica). The
storage sits behind a small async `Backend` interface (`hit`, `blocked`, `add`, `reset`), so a
Postgres or Redis backend can replace `MemoryBackend` for several replicas without touching the rules.

| What | Limit | Key |
| --- | --- | --- |
| `POST /v1/auth/login` | 30 per 5 min | client address |
| failed sign-ins | 10 per 15 min | email, and client address |
| `POST /v1/auth/signup` | 10 per hour | client address |
| `POST /v1/auth/accept` | 20 per 10 min | client address |
| `POST /v1/auth/invites/describe` | 30 per 10 min | client address |
| `POST /v1/auth/password` | 20 per 10 min; 5 wrong current passwords per 15 min | client address; person |
| any request with a credential | `MEMVINE_RATE_LIMIT_KEY_PER_MINUTE` (1200) | the credential (by hash) |
| any request but `/health` | `MEMVINE_RATE_LIMIT_IP_PER_MINUTE` (3000) | client address |

Over a limit: `429` with `Retry-After` (seconds) and `{"error": {"code": "rate-limited"}}` (or
`too-many-attempts` for failed sign-ins). `MEMVINE_RATE_LIMIT_ENABLED=false` turns them off.
Counters live in memory: a restart clears them.

## The client address behind proxies

The socket peer behind Railway is Railway's edge (public traffic) or the console (private network),
so the address comes from `X-Forwarded-For`, which anyone can send. The API reads the chain from the
right: hops added by a trusted proxy are skipped, and the first address a trusted proxy did not
vouch for is the client. A peer that is not a trusted proxy is the client, whatever the header says.
IPv4-mapped IPv6 addresses are read as IPv4.

`MEMVINE_TRUSTED_PROXIES` (default `private`): comma-separated addresses and CIDR ranges; `private`
stands for loopback, 10/8, 172.16/12, 192.168/16, 100.64/10 (CGNAT), 169.254/16, fc00::/7 and
fe80::/10. That fits Railway (its edge and private network use private ranges, and a container is
not reachable from the internet directly), Docker and a local tunnel. Set it to `""` when the API
is exposed directly on a network where untrusted hosts have private addresses.

The console calls the API server-side, so it relays the browser's `X-Forwarded-For`
(`console/lib/forwarded.ts`); the API believes it because the console is a trusted (private) peer.

To check a deployment: make a call with a project key from a known address and look at the key's
`last_used_ip` (console → API keys). If it shows a Railway address instead of yours, add that range
to `MEMVINE_TRUSTED_PROXIES`; until then every client shares one per-address budget.

## Request size

The API refuses bodies over `MEMVINE_MAX_BODY_BYTES` (2 MB) with `413` before the app reads them,
from `Content-Length` or, for chunked uploads, by counting as the body arrives. Ingest and session
events (`/v1/ingest`, `/v1/sessions/...`) take `MEMVINE_MAX_BULK_BODY_BYTES` (25 MB); their items
have their own limits (an episode 200,000 characters and 100 per request; a session event 100,000
characters and 500 per request), and recall queries 4,000 characters.

## Outbound requests (SSRF)

The server fetches one URL: `MEMVINE_ALERT_WEBHOOK_URL`, set by the operator (http and https only).
No route fetches a URL a customer supplies, so there is no customer-controlled server-side request.
A future feature that does (customer webhooks, URL import) must resolve the host and refuse
loopback, private, link-local and metadata addresses (127/8, 10/8, 172.16/12, 192.168/16,
169.254.169.254, fc00::/7, fe80::/10, `*.railway.internal`), re-checking after redirects.

## Responses and logs

- API responses carry `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, `Cache-Control: no-store` and `Strict-Transport-Security` (one year,
  subdomains; browsers ignore it over plain http, so a local server is unaffected). The API sends no CORS headers, so
  browsers on other origins cannot read it.
- Validation errors (422) do not echo the submitted input (it can be a password).
- Logs: request bodies are never logged or stored; the request log and the audit log keep method,
  path, status and actor only. Job and instant-ingest failures log the error type, not its message
  (which can quote model output drawn from customer text); the message stays on the job, visible to
  the project's own people. Keys travel only in the `Authorization` header, never in a URL. A key's
  last User-Agent is stored trimmed to 200 printable characters and is not logged.

## Console

- The sign-in token is in an `HttpOnly`, `SameSite=Lax` cookie, `Secure` in production
  (`MEMVINE_INSECURE_COOKIES=1` drops `Secure` for a plain-HTTP container on a laptop). Browser code
  never sees a key: every API call is made server-side.
- Content-Security-Policy with a per-request nonce (`middleware.ts`): scripts only from the console
  with the nonce (`strict-dynamic`), nothing from other origins, `frame-ancestors 'none'`,
  `form-action 'self'`, `object-src 'none'`. Styles allow `'unsafe-inline'` (React style attributes).
- `next.config.ts`: `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy:
  strict-origin-when-cross-origin`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, and
  `Strict-Transport-Security` (one year, subdomains) in production over HTTPS.
- CSRF: server actions check that `Origin` matches the request's host (Next.js); Railway keeps the
  `Host` header, so both the custom domain and the `*.up.railway.app` domain work. If a proxy rewrites
  `Host`, list the public origins in `MEMVINE_CONSOLE_ALLOWED_ORIGINS` (`app.example.com,...`). The two
  GET routes that act with the cookie (`/export`, `/logout`) refuse `Sec-Fetch-Site: cross-site`.
- Client bundles: a production build's `.next/static` contains no key and no reference to
  `MEMVINE_MANAGEMENT_KEY` (checked with
  `grep -rEo "mv_(mk|sk|us|in)_[A-Za-z0-9_-]{16,}|MEMVINE_MANAGEMENT_KEY" .next/static`).

## What an operator must configure

1. `MEMVINE_MANAGEMENT_KEY` on the **API only**: `mv_mk_` plus 32+ random characters, kept in
   Railway and a password manager. **Never set it on a public console**: a console holding it serves
   every page as the operator, with no sign-in (that mode exists for a console on localhost).
2. `MEMVINE_SIGNUP=invite` (or `closed`) on a public API: in `open` mode anyone can create an
   organization. The first account may always sign up.
3. `MEMVINE_TRUSTED_PROXIES`: keep `private` on Railway and verify with `last_used_ip` (above).
4. Postgres without a public domain or TCP proxy; the API reaches it on the private network.
5. HTTPS only (Railway terminates TLS); leave `MEMVINE_INSECURE_COOKIES` unset.
6. A monthly limit on the Anthropic key and credit caps per project (model spend is the cost an
   abuser can run up with a stolen project key).

## Known limitations

- Rate-limit counters are per process and reset on restart; several replicas need a shared backend.
- The API does not see the browser's address when the console is not behind a proxy that sets
  `X-Forwarded-For` (then the console's own address is the client for sign-ins made through it).
- Sign-up answers "an account with this email exists" (account enumeration, rate-limited).
- No multi-factor sign-in, no SSO, no email verification or password reset by email.
- Project keys do not expire; they are revoked by hand. Session tokens are bearer tokens: a stolen
  cookie works until it expires or the person signs out.
- Access logs of the hosting platform (uvicorn) include request paths, which can name an identity
  (`/v1/identities/{identity}`) or a session id; never a key or content.
- Tracebacks of unexpected errors (`log.exception`) can include a database error's detail.
- The server image installs the exact versions in `server/requirements.lock`; the lock does not
  update itself. Refresh and audit it before a release (`server/README.md`, Docker). The pins carry
  no hashes. The root `memvine` npm package
  still depends on zod 3 (GHSA-mrhx-c7xc-m3w5, fixed only in zod 4, a breaking upgrade).
