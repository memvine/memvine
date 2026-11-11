from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from MEMVINE_* environment variables or server/.env."""

    model_config = SettingsConfigDict(env_prefix="MEMVINE_", env_file=".env", extra="ignore")

    database_url: str = "postgresql://memvine:memvine@localhost:5433/memvine"
    # Organization-level key for creating projects and project keys over HTTP.
    # Empty disables those routes; the CLI still works against the database directly.
    management_key: str = ""

    # Who may create an account: open (anyone who can reach the server; a new user gets their own
    # organization), invite (only with an invite link from an organization admin), closed.
    signup: str = "open"
    # Apply pending schema migrations on start (migrate.py). Off: a server whose database needs
    # migrations refuses to start until `memvine-server migrate` has run.
    auto_migrate: bool = True
    # Postgres row-level security beneath the application's own project filters (migrations/rls.sql).
    row_security: bool = True
    # A console sign-in lasts this long.
    session_days: int = 30

    # Request hardening (security.py; docs/security.md).
    # Proxies whose X-Forwarded-For hops are believed: comma-separated addresses or CIDR ranges, and
    # "private" for loopback, RFC 1918, CGNAT (100.64/10), link-local and IPv6 ULA ranges (Railway's
    # edge and private network, Docker, a local tunnel). Empty: X-Forwarded-For is ignored and the
    # socket peer is the client.
    trusted_proxies: str = "private"
    rate_limit_enabled: bool = True
    # Requests per minute per credential (project key, organization key, sign-in session) and per
    # client address. Sign-in, sign-up, invite and password routes have tighter limits of their own.
    rate_limit_key_per_minute: int = 1200
    rate_limit_ip_per_minute: int = 3000
    # Largest request body accepted, in bytes; ingest and session events (bulk conversation text,
    # each item capped by its own model) get the larger cap.
    max_body_bytes: int = 2_000_000
    max_bulk_body_bytes: int = 25_000_000

    # LLM backend for extraction and reconciliation:
    #   anthropic  - the Anthropic SDK; needs ANTHROPIC_API_KEY (or an `ant auth login` profile)
    #   claude-cli - `claude -p` on the local machine, billed to a Claude subscription; dev only
    #   fake       - deterministic canned answers, for tests
    llm_backend: str = "anthropic"
    llm_model: str = "claude-sonnet-5-5"
    # High-volume, simple judgements (reconciliation, session state) run on a fast model;
    # thread pages and knowledge extraction keep llm_model.
    llm_model_fast: str = "claude-haiku-5-5"
    # Model names handed to `claude --model` when llm_backend is claude-cli.
    claude_cli_model: str = "sonnet"
    claude_cli_model_fast: str = "haiku"
    # Simultaneous `claude -p` calls. Rate limits pause the job (worker.py), so more than one is safe.
    claude_cli_concurrency: int = 1

    # Embeddings: fastembed (local BGE-small, 384 dims, downloaded on first use) or fake (tests).
    embeddings_backend: str = "fastembed"

    # The in-process worker polls the jobs table this often.
    worker_poll_seconds: float = 1.0
    worker_enabled: bool = True
    # Jobs processed at once, across projects; one project never runs two jobs together.
    worker_concurrency: int = 4
    # On shutdown, how long a running job may finish before it is cancelled and requeued.
    worker_shutdown_seconds: float = 10.0

    # Synthesis health (health.py, GET /v1/health/synthesis, console banners). Unhealthy when a job
    # failed in the last alert_failed_hours, when a project's oldest waiting live job is older than
    # alert_queue_minutes (paused projects excepted), or when no worker polled within alert_worker_minutes.
    alert_failed_hours: int = 24
    alert_queue_minutes: int = 30
    alert_worker_minutes: int = 10
    # Slack-compatible incoming webhook ({"text": ...}); the worker posts each unhealthy condition
    # at most once per alert_repeat_minutes. Empty: no webhook.
    alert_webhook_url: str = ""
    alert_repeat_minutes: int = 60
    alert_check_seconds: float = 60.0

    # A live session with no event for this long is over: its turns become episodes.
    session_idle_minutes: int = 30
    # Refresh a session's working state after this many new turns (or on explicit request).
    session_state_every_turns: int = 4
    # Rolling checkpoints (checkpoints.py): seal a completed stretch into an episode once its text
    # crosses session_soft_chars (cut at the next completed exchange), or once its oldest message
    # has waited session_max_age_minutes. session_max_tokens caps what one extraction reads.
    session_soft_chars: int = 6000
    session_max_tokens: int = 8000
    session_max_age_minutes: int = 10

    recall_default_budget: int = 2000
    recall_max_budget: int = 32000
    # Server-wide relevance threshold for recall's abstention (recall.py). Unset: each embedding model's
    # calibrated default (embeddings.py). A project's own recall_min_relevance overrides both.
    recall_min_relevance: float | None = None
    # Recency in recall's ranking (recall.py: recency_factor): a fact or gotcha's weight halves toward
    # the floor every this many days since it was last said. 0 turns it off. A project's own
    # recall_half_life_days overrides it.
    recall_half_life_days: float = 90.0
    # Within a project every memory looks similar to every task, so the fast model picks what a task
    # needs from the closest candidates (recall.py: gate). Past the timeout, or with this off, recall
    # falls back to the similarity margin. The plugins' prompt hook gives recall 8 seconds in all.
    recall_gate: bool = True
    # Conversation mode (recall.py): the share of a recall's budget that kept details may take; the
    # source excerpts fill the rest. On a first BEAM conversation (20 questions, Claude Sonnet answering
    # and judging) a half scored 0.77, a quarter 0.85 and none 0.86: the original words answer better
    # than many derived lines, which mostly earn their place by saying what changed and what conflicts.
    recall_detail_fact_share: float = 0.25
    # Conversation mode (rerank.py): the local cross-encoder that orders the candidate details against
    # the question. Empty turns it off. On a 500k-token BEAM conversation (2,888 details) the line that
    # answered a question sat 135th by vector and 41st by keyword; this model put it first.
    recall_rerank_model: str = "jinaai/jina-reranker-v1-turbo-en"
    recall_gate_timeout: float = 3.0
    # A memory this many days old that no briefing served in this many days is listed for review
    # (GET /v1/memories?unused=true, the project's unused_count). Never hidden or deleted for it.
    memory_unused_days: int = 60


settings = Settings()
