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

Started like this, with `MEMVINE_MANAGEMENT_KEY` set, the console sends the
organization's key with every request and nobody signs in: whoever can reach it
acts as the organization's owner, so keep it to your own machine. Leave the key
unset and people sign in instead, with an email and password, an invite link or
an organization key, and see what their role allows. Either way the console
names the selected project on every request (`X-Memvine-Project`).

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

Webhooks on memory events (#13).
