-- 0001 baseline: the Memvine schema as a fresh database needs it, folded from the hash-gated
-- schema.sql it replaces (columns that schema added later with ALTER TABLE sit at the end of their
-- table, in the order they were added, so a fresh table matches one built up over time).
-- Never edit this file: an applied migration's checksum is verified on every start. Change the
-- schema in a new numbered file (see migrate.py). Row-level security lives in rls.sql.
-- Every memory table carries project_id: a project is the isolation boundary.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Tenancy. An organization owns projects; its management keys (mv_mk_, hashed) administer only
-- its own projects. The operator key from config administers every organization.
CREATE TABLE organizations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        text UNIQUE NOT NULL,
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE org_keys (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name          text NOT NULL,
    key_hash      text UNIQUE NOT NULL,
    key_suffix    text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_used_at  timestamptz,
    revoked_at    timestamptz,
    last_used_ip  text                            -- where the key was last used from
);
INSERT INTO organizations (slug, name) VALUES ('default', 'Default organization');

CREATE TABLE projects (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug                  text UNIQUE NOT NULL,
    name                  text NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    tags                  text[] NOT NULL DEFAULT '{}',    -- free labels for the console
    monthly_credit_cap    numeric(14, 2),                  -- null: uncapped
    retention_days        int,                             -- null: forever
    -- Recall abstains unless a memory is at least this relevant (recall.py); null: the embedder's default.
    recall_min_relevance  real,
    org_id                uuid REFERENCES organizations(id) ON DELETE CASCADE,
    processing_paused     boolean NOT NULL DEFAULT false,
    -- The project profile extraction judges relevance against.
    description           text
);
CREATE INDEX projects_org ON projects (org_id);

CREATE TABLE api_keys (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id    uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name          text NOT NULL,
    key_hash      text UNIQUE NOT NULL,          -- sha256 of the clear key; the clear key is never stored
    key_suffix    text NOT NULL,                 -- last 4 characters, for display
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_used_at  timestamptz,
    revoked_at    timestamptz,
    last_used_ip  text
);

-- The raw data point exactly as the client sent it. Immutable except by a
-- re-send of the same id, which replaces it and everything derived from it.
CREATE TABLE episodes (
    project_id    uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    id            text NOT NULL,                 -- client-chosen, stable across re-sends
    content       text NOT NULL,
    content_hash  text NOT NULL,
    occurred_at   timestamptz NOT NULL,
    identity      text,
    label         text,
    metadata      jsonb NOT NULL DEFAULT '{}',
    created_at    timestamptz NOT NULL DEFAULT now(),
    audience      text NOT NULL DEFAULT 'project',
    expired_at    timestamptz,                   -- raw text dropped by retention
    -- Code-change staleness (code.py): the files a conversation touched, with their git blob hashes.
    code          jsonb,
    PRIMARY KEY (project_id, id)
);

-- Threads (threads.py, synthesis.py): one living page per piece of work (a release, task, PR,
-- ticket, topic) edited in place.
-- sections: [{"label", "items": [{"text", "date", "evidence": [{"episode_id", "quote"}]}]}], labels
-- from a fixed vocabulary in a fixed order (threads.SECTION_LABELS). key: branch:<name>, pr:<n>,
-- ticket:<ID>, release:<version> or topic:<slug>; aliases are other keys naming the same work.
CREATE TABLE threads (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id        uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind              text NOT NULL CHECK (kind IN ('release','task','pr','ticket','topic')),
    key               text NOT NULL,
    title             text NOT NULL,
    status            text NOT NULL DEFAULT 'open' CHECK (status IN ('open','blocked','done','abandoned')),
    summary           text NOT NULL DEFAULT '',
    sections          jsonb NOT NULL DEFAULT '[]',
    aliases           text[] NOT NULL DEFAULT '{}',
    embedding         vector(384),
    first_seen_at     timestamptz,
    last_activity_at  timestamptz,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, key)
);
CREATE INDEX threads_aliases ON threads USING gin (aliases);
CREATE INDEX threads_activity ON threads (project_id, last_activity_at DESC);

CREATE TABLE memory_items (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind            text NOT NULL CHECK (kind IN ('episodic','semantic','procedural','prospective','preference')),
    statement       text NOT NULL,
    subjects        text[] NOT NULL DEFAULT '{}',
    valid_from      timestamptz NOT NULL,
    valid_to        timestamptz,
    recorded_at     timestamptz NOT NULL,
    status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active','superseded','retired','stale')),
    supersedes      uuid REFERENCES memory_items(id) ON DELETE SET NULL,
    superseded_by   uuid REFERENCES memory_items(id) ON DELETE SET NULL,
    assertion       text NOT NULL CHECK (assertion IN ('observed','stated','claimed','inferred')),
    confidence      text NOT NULL DEFAULT 'medium' CHECK (confidence IN ('high','medium','low')),
    audience        text NOT NULL DEFAULT 'project',
    embedding       vector(384),
    tsv             tsvector GENERATED ALWAYS AS (to_tsvector('english', statement)) STORED,
    created_at      timestamptz NOT NULL DEFAULT now(),
    -- Items that make incompatible claims about the same thing with no sign of a change over time.
    -- Both stay active; recall shows them together so the reader can see the conflict.
    conflicts_with  uuid[] NOT NULL DEFAULT '{}',
    -- A lasting rule for how the assistant should respond or work. Recall includes every active
    -- standing item, whatever the question, because no question resembles the rule it should follow.
    standing        boolean NOT NULL DEFAULT false,
    stale_reason    text,
    -- Knowledge: decision, rule, gotcha, fact or preference, why it matters, and the thread it belongs to.
    category        text CHECK (category IN ('decision','rule','gotcha','fact','preference')),
    why             text,
    thread_id       uuid REFERENCES threads(id) ON DELETE SET NULL,
    -- 'project' for anyone in the project, 'thread' for what matters only inside its thread.
    -- NULL (older items) reads as project.
    scope           text CHECK (scope IN ('project','thread'))
);
CREATE INDEX memory_items_project_status ON memory_items (project_id, status);
CREATE INDEX memory_items_subjects ON memory_items USING gin (subjects);
CREATE INDEX memory_items_tsv ON memory_items USING gin (tsv);
CREATE INDEX memory_items_audience ON memory_items (project_id, audience);
CREATE INDEX memory_items_standing ON memory_items (project_id) WHERE standing AND status = 'active';
CREATE INDEX memory_items_thread ON memory_items (thread_id) WHERE thread_id IS NOT NULL;

-- Which episode text a memory was drawn from. Deleting an episode deletes its
-- evidence, and a memory with no evidence left is deleted by the worker.
CREATE TABLE evidence (
    memory_id   uuid NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
    project_id  uuid NOT NULL,
    episode_id  text NOT NULL,
    quote       text NOT NULL,
    PRIMARY KEY (memory_id, episode_id, quote),
    FOREIGN KEY (project_id, episode_id) REFERENCES episodes(project_id, id) ON DELETE CASCADE
);
CREATE INDEX evidence_episode ON evidence (project_id, episode_id);

-- A live agent session: its turns as they arrive and a working state kept current
-- by a fast pass. When the session goes idle its turns become episodes and enter
-- the long-term pipeline.
CREATE TABLE sessions (
    project_id         uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    id                 text NOT NULL,
    agent              text,
    identity           text,
    label              text,
    status             text NOT NULL DEFAULT 'live' CHECK (status IN ('live','synthesized')),
    started_at         timestamptz NOT NULL DEFAULT now(),
    last_event_at      timestamptz NOT NULL DEFAULT now(),
    turns              int NOT NULL DEFAULT 0,
    state              jsonb NOT NULL DEFAULT '{}',
    state_through      int NOT NULL DEFAULT 0,          -- the last event seq the state reflects
    state_updated_at   timestamptz,
    synthesized_at     timestamptz,
    -- Rolling checkpoints (checkpoints.py): the last message sealed into an episode, and the last
    -- message whose extraction has committed.
    sealed_through     int NOT NULL DEFAULT 0,
    processed_through  int NOT NULL DEFAULT 0,
    -- Imported history (backfilled sessions, bulk imports) runs after live work of the same project.
    imported           boolean NOT NULL DEFAULT false,
    PRIMARY KEY (project_id, id)
);
CREATE INDEX sessions_live ON sessions (project_id, last_event_at) WHERE status = 'live';

CREATE TABLE session_events (
    project_id  uuid NOT NULL,
    session_id  text NOT NULL,
    seq         int NOT NULL,
    role        text NOT NULL,
    content     text NOT NULL,
    at          timestamptz NOT NULL,
    PRIMARY KEY (project_id, session_id, seq),
    FOREIGN KEY (project_id, session_id) REFERENCES sessions(project_id, id) ON DELETE CASCADE
);

-- Work queue for synthesis. One row per episode to extract; the worker polls it.
CREATE TABLE jobs (
    id          bigserial PRIMARY KEY,
    project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind        text NOT NULL,
    episode_id  text NOT NULL,
    status      text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
    attempts    int NOT NULL DEFAULT 0,
    error       text,
    run_after   timestamptz NOT NULL DEFAULT now(),
    created_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    result      jsonb,
    -- When a worker claimed the job. A running job past its lease belongs to a worker that died
    -- and is handed out again.
    claimed_at  timestamptz,
    background  boolean NOT NULL DEFAULT false
);
CREATE INDEX jobs_queue ON jobs (status, run_after) WHERE status = 'queued';

CREATE TABLE request_log (
    id          bigserial PRIMARY KEY,
    project_id  uuid NOT NULL,
    at          timestamptz NOT NULL DEFAULT now(),
    method      text NOT NULL,
    path        text NOT NULL,
    status      int NOT NULL,
    latency_ms  int NOT NULL,
    key_id      uuid,
    detail      text
);
CREATE INDEX request_log_project ON request_log (project_id, at DESC);

-- Who reads and writes memory. An identity is created the first time it is seen
-- (on an episode, in a fixed audience, or through the identity routes).
CREATE TABLE identities (
    project_id    uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    identity      text NOT NULL,
    traits        jsonb NOT NULL DEFAULT '{}',
    origin        text NOT NULL DEFAULT 'api' CHECK (origin IN ('api','ingest','audience','console')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz,
    PRIMARY KEY (project_id, identity)
);

-- A named scope inside a project. A memory names the audience that may read it;
-- 'project' (implicit, never stored) reaches every identity.
--   fixed: an explicit list of identities
--   rule:  every identity whose traits match all conditions
CREATE TABLE audiences (
    project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    slug        text NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$' AND slug <> 'project'),
    kind        text NOT NULL CHECK (kind IN ('fixed','rule')),
    members     text[] NOT NULL DEFAULT '{}',
    conditions  jsonb NOT NULL DEFAULT '[]',
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, slug)
);

-- The source text itself, cut into passages and indexed. Recall fills what its budget has
-- left after facts with the passages behind them.
CREATE TABLE passages (
    project_id   uuid NOT NULL,
    episode_id   text NOT NULL,
    ord          int NOT NULL,
    content      text NOT NULL,
    occurred_at  timestamptz NOT NULL,
    audience     text NOT NULL DEFAULT 'project',
    embedding    vector(384),
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    PRIMARY KEY (project_id, episode_id, ord),
    FOREIGN KEY (project_id, episode_id) REFERENCES episodes(project_id, id) ON DELETE CASCADE
);
CREATE INDEX passages_tsv ON passages USING gin (tsv);
CREATE INDEX passages_project ON passages (project_id, occurred_at);

-- Metering (billing.py). One row per billable call: ingest is charged by characters received,
-- recall per call. A project's monthly cap refuses new ingestion once reached; recall is never refused.
CREATE TABLE usage_events (
    id          bigserial PRIMARY KEY,
    project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    at          timestamptz NOT NULL DEFAULT now(),
    kind        text NOT NULL CHECK (kind IN ('ingest', 'session', 'recall')),
    units       bigint NOT NULL,          -- characters for ingest/session, calls for recall
    credits     numeric(14, 2) NOT NULL
);
CREATE INDEX usage_events_project_at ON usage_events (project_id, at);

-- Administrative actions and memory edits: who did what to which target. Bodies are never stored.
CREATE TABLE audit_events (
    id          bigserial PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    org_id      uuid,
    project_id  uuid,
    actor       text NOT NULL,      -- "project key <name>", "org key <name>", "operator"
    action      text NOT NULL,      -- e.g. project.delete, key.create, memory.update
    target      text NOT NULL,
    status      int NOT NULL
);
CREATE INDEX audit_events_org ON audit_events (org_id, at DESC);

-- People. Each signs in with email and password, belongs to one or more organizations with a
-- role, and holds sign-in sessions (mv_us_ tokens, stored hashed). Organization keys stay for CI.
CREATE TABLE users (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email          text NOT NULL,
    name           text NOT NULL DEFAULT '',
    password_hash  text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    last_login_at  timestamptz
);
CREATE UNIQUE INDEX users_email ON users (lower(email));
CREATE TABLE memberships (
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    role        text NOT NULL CHECK (role IN ('owner','admin','member')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, org_id)
);
CREATE TABLE user_sessions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    token_hash    text UNIQUE NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    last_used_at  timestamptz,
    revoked_at    timestamptz
);
CREATE TABLE invites (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    token_hash   text UNIQUE NOT NULL,
    email        text,
    role         text NOT NULL CHECK (role IN ('admin','member')),
    created_by   text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    accepted_at  timestamptz,
    accepted_by  uuid REFERENCES users(id) ON DELETE SET NULL,
    revoked_at   timestamptz
);

-- Code-change staleness (code.py). Clients report the files a conversation touched with their git
-- blob hashes; facts about current code that name such a file are anchored to that version; a
-- later report of a different version marks them stale.
CREATE TABLE session_code (
    project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    session_id  text NOT NULL,
    repo        text NOT NULL,
    path        text NOT NULL,
    blob        text NOT NULL,
    seen_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, session_id, repo, path)
);
CREATE TABLE memory_anchors (
    project_id   uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    memory_id    uuid NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
    repo         text NOT NULL,
    path         text NOT NULL,
    blob         text NOT NULL,
    anchored_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (memory_id, repo, path)
);
CREATE INDEX memory_anchors_file ON memory_anchors (project_id, repo, path);
CREATE TABLE code_files (
    project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    repo        text NOT NULL,
    path        text NOT NULL,
    blob        text,              -- NULL: the file was reported deleted
    seen_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, repo, path)
);

-- The episodes a thread page was written from. Deleting an episode drops its row; a thread left
-- with no source is deleted (threads.forget_episode).
CREATE TABLE thread_sources (
    thread_id   uuid NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    project_id  uuid NOT NULL,
    episode_id  text NOT NULL,
    PRIMARY KEY (thread_id, episode_id),
    FOREIGN KEY (project_id, episode_id) REFERENCES episodes(project_id, id) ON DELETE CASCADE
);
CREATE INDEX thread_sources_episode ON thread_sources (project_id, episode_id);
CREATE TABLE thread_links (
    project_id  uuid NOT NULL,
    thread_id   uuid NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    related_id  uuid NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    PRIMARY KEY (thread_id, related_id)
);

-- Edges between facts that the item columns do not already hold (supersedes, conflicts_with,
-- thread_id and memory_anchors do). extends: the newer fact (from) adds detail to an older one
-- (to); both stay active. derived: a fact the model inferred (from) cites one it was drawn from (to).
-- episode_id: the evidence that created the edge, when one episode did.
CREATE TABLE memory_links (
    project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    from_id     uuid NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
    to_id       uuid NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
    kind        text NOT NULL CHECK (kind IN ('extends','derived')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    episode_id  text,
    PRIMARY KEY (from_id, to_id, kind),
    CHECK (from_id <> to_id)
);
CREATE INDEX memory_links_to ON memory_links (to_id);
CREATE INDEX memory_links_project ON memory_links (project_id, created_at);
