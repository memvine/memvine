# Memvine console

The customer web app for the Memvine memory platform: create and revoke API keys,
browse and correct memories (with evidence and the history of each fact), see every
source sent and what synthesis made of it, try recall as an agent would receive it,
and see requests and usage.

Design: see the platform design doc, section "Customer console".

## Run against the server

```sh
cd server && docker compose up -d && MEMVINE_MANAGEMENT_KEY=mv_mk_dev .venv/bin/memvine-server serve   # :8400
cd console && npm install
MEMVINE_API_URL=http://127.0.0.1:8400 MEMVINE_MANAGEMENT_KEY=mv_mk_dev npm run dev                      # :3000
```

The console authenticates to the server with the organization's management key
and names the selected project on every request (`X-Memvine-Project`). There is
no sign-in yet: whoever can reach the console acts as the organization owner, so
run it locally or behind your own auth until that lands.

## Run on mock data

Leave `MEMVINE_API_URL` unset and every screen reads from an in-memory mock
seeded with two demo projects (`lib/mock-data.ts`); the sidebar shows a
"mock data" badge. Useful for UI work without a database.

## Code layout

- `lib/types.ts`: the console's view of the API, including the `MemvineApi`
  interface every backend implements.
- `lib/api-live.ts`: the HTTP client for the server.
- `lib/api-mock.ts`: the in-memory backend.
- `lib/api.ts`: picks a backend and is the only module pages import.
- `app/*`: one folder per screen; `app/actions.ts` holds every write.

## Not built yet

Sign-in and members, audiences and identities, entity pages, live sessions,
webhooks.
