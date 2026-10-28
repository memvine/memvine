import pytest

from memvine_server.redaction import redact


@pytest.mark.parametrize("text,expected", [
    ("key ghp_abcdefghijklmnopqrstuvwxyz123456 here", "key [redacted] here"),
    ("export ANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnop", "export ANTHROPIC_API_KEY=[redacted]"),
    ("DB_PASSWORD: hunter2hunter2", "DB_PASSWORD=[redacted]"),
    ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz.123", "Authorization: [redacted]"),
    ("use mv_sk_AbCdEfGhIjKlMnOpQrStUv for the project", "use [redacted] for the project"),
    ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0", "jwt [redacted]"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----", "[redacted]"),
    ("We chose Postgres; the token budget is 4000.", "We chose Postgres; the token budget is 4000."),
])
def test_redact(text, expected):
    assert redact(text) == expected


@pytest.mark.asyncio
async def test_secrets_never_reach_storage(client):
    from memvine_server.main import app
    secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    await client.post("/v1/ingest", json={"items": [{"id": "leak-1", "content": f"the deploy token is {secret}", "timestamp": "2026-10-01T10:00:00Z"}]})
    await client.post("/v1/sessions/leak-s/events", json={"events": [{"role": "user", "content": f"try {secret}"}]})
    async with app.state.pool.connection() as conn:
        for table, column in (("episodes", "content"), ("session_events", "content")):
            rows = await (await conn.execute(f"SELECT {column} FROM {table} WHERE {column} LIKE %s", (f"%{secret}%",))).fetchall()
            assert rows == [], table
