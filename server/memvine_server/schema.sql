-- Memvine platform schema, v0. Applied idempotently at startup (see db.py).
-- Every memory table carries project_id: a project is the isolation boundary.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS projects (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        text UNIQUE NOT NULL,
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS api_keys (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id    uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name          text NOT NULL,
    key_hash      text UNIQUE NOT NULL,          -- sha256 of the clear key; the clear key is never stored
    key_suffix    text NOT NULL,                 -- last 4 characters, for display
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_used_at  timestamptz,
    revoked_at    timestamptz
);

-- The raw data point exactly as the client sent it. Immutable except by a
-- re-send of the same id, which replaces it and everything derived from it.
CREATE TABLE IF NOT EXISTS episodes (
    project_id    uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    id            text NOT NULL,                 -- client-chosen, stable across re-sends
    content       text NOT NULL,
    content_hash  text NOT NULL,
    occurred_at   timestamptz NOT NULL,
    identity      text,
    label         text,
    metadata      jsonb NOT NULL DEFAULT '{}',
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, id)
);

CREATE TABLE IF NOT EXISTS memory_items (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id     uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind           text NOT NULL CHECK (kind IN ('episodic','semantic','procedural','prospective','preference')),
    statement      text NOT NULL,
    subjects       text[] NOT NULL DEFAULT '{}',
    valid_from     timestamptz NOT NULL,
    valid_to       timestamptz,
    recorded_at    timestamptz NOT NULL,
    status         text NOT NULL DEFAULT 'active' CHECK (status IN ('active','superseded','retired','stale')),
    supersedes     uuid REFERENCES memory_items(id) ON DELETE SET NULL,
    superseded_by  uuid REFERENCES memory_items(id) ON DELETE SET NULL,
    assertion      text NOT NULL CHECK (assertion IN ('observed','stated','claimed','inferred')),
    confidence     text NOT NULL DEFAULT 'medium' CHECK (confidence IN ('high','medium','low')),
    audience       text NOT NULL DEFAULT 'project',
    embedding      vector(384),
    tsv            tsvector GENERATED ALWAYS AS (to_tsvector('english', statement)) STORED,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS memory_items_project_status ON memory_items (project_id, status);
CREATE INDEX IF NOT EXISTS memory_items_subjects ON memory_items USING gin (subjects);
CREATE INDEX IF NOT EXISTS memory_items_tsv ON memory_items USING gin (tsv);

-- Which episode text a memory was drawn from. Deleting an episode deletes its
-- evidence, and a memory with no evidence left is deleted by the worker.
CREATE TABLE IF NOT EXISTS evidence (
    memory_id   uuid NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
    project_id  uuid NOT NULL,
    episode_id  text NOT NULL,
    quote       text NOT NULL,
    PRIMARY KEY (memory_id, episode_id, quote),
    FOREIGN KEY (project_id, episode_id) REFERENCES episodes(project_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS evidence_episode ON evidence (project_id, episode_id);

-- A live agent session: its turns as they arrive and a working state kept current
-- by a fast pass. When the session goes idle its turns become episodes and enter
-- the long-term pipeline.
CREATE TABLE IF NOT EXISTS sessions (
    project_id       uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    id               text NOT NULL,
    agent            text,
    identity         text,
    label            text,
    status           text NOT NULL DEFAULT 'live' CHECK (status IN ('live','synthesized')),
    started_at       timestamptz NOT NULL DEFAULT now(),
    last_event_at    timestamptz NOT NULL DEFAULT now(),
    turns            int NOT NULL DEFAULT 0,
    state            jsonb NOT NULL DEFAULT '{}',
    state_through    int NOT NULL DEFAULT 0,          -- the last event seq the state reflects
    state_updated_at timestamptz,
    synthesized_at   timestamptz,
    PRIMARY KEY (project_id, id)
);
CREATE INDEX IF NOT EXISTS sessions_live ON sessions (project_id, last_event_at) WHERE status = 'live';

CREATE TABLE IF NOT EXISTS session_events (
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
CREATE TABLE IF NOT EXISTS jobs (
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
    result      jsonb
);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs (status, run_after) WHERE status = 'queued';

CREATE TABLE IF NOT EXISTS request_log (
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
CREATE INDEX IF NOT EXISTS request_log_project ON request_log (project_id, at DESC);

-- Who reads and writes memory. An identity is created the first time it is seen
-- (on an episode, in a fixed audience, or through the identity routes).
CREATE TABLE IF NOT EXISTS identities (
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
CREATE TABLE IF NOT EXISTS audiences (
    project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    slug        text NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$' AND slug <> 'project'),
    kind        text NOT NULL CHECK (kind IN ('fixed','rule')),
    members     text[] NOT NULL DEFAULT '{}',
    conditions  jsonb NOT NULL DEFAULT '[]',
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, slug)
);

ALTER TABLE episodes ADD COLUMN IF NOT EXISTS audience text NOT NULL DEFAULT 'project';
CREATE INDEX IF NOT EXISTS memory_items_audience ON memory_items (project_id, audience);

-- Items that make incompatible claims about the same thing with no sign of a change over time.
-- Both stay active; recall shows them together so the reader can see the conflict.
ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS conflicts_with uuid[] NOT NULL DEFAULT '{}';

-- When a worker claimed a job. A running job past its lease belongs to a worker that died
-- (killed, crashed, machine slept) and is handed out again.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS claimed_at timestamptz;

-- The source text itself, cut into passages and indexed. Recall fills what its budget has
-- left after facts with the passages behind them, so wording that extraction condensed away
-- (preferences, exact phrasing, order of events) still reaches the reader.
CREATE TABLE IF NOT EXISTS passages (
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
CREATE INDEX IF NOT EXISTS passages_tsv ON passages USING gin (tsv);
CREATE INDEX IF NOT EXISTS passages_project ON passages (project_id, occurred_at);

-- A lasting rule for how the assistant should respond or work ("always format code with
-- syntax highlighting", "never use semicolons"). Recall includes every active standing item,
-- whatever the question, because no question resembles the rule it should follow.
ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS standing boolean NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS memory_items_standing ON memory_items (project_id) WHERE standing AND status = 'active';

-- Free labels for organizing projects in the console (team, environment, customer).
ALTER TABLE projects ADD COLUMN IF NOT EXISTS tags text[] NOT NULL DEFAULT '{}';

-- Rolling checkpoints (checkpoints.py): the last message sealed into an episode, and the last
-- message whose extraction has committed.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS sealed_through int NOT NULL DEFAULT 0;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS processed_through int NOT NULL DEFAULT 0;
-- Sessions closed before checkpoints existed were already turned into episodes whole.
UPDATE sessions SET sealed_through = turns, processed_through = turns
WHERE status = 'synthesized' AND sealed_through = 0;

-- Metering (billing.py). One row per billable call: ingest is charged by characters received,
-- recall per call. A project's monthly cap refuses new ingestion once reached; recall is never refused.
CREATE TABLE IF NOT EXISTS usage_events (
    id          bigserial PRIMARY KEY,
    project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    at          timestamptz NOT NULL DEFAULT now(),
    kind        text NOT NULL CHECK (kind IN ('ingest', 'session', 'recall')),
    units       bigint NOT NULL,          -- characters for ingest/session, calls for recall
    credits     numeric(14, 2) NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_events_project_at ON usage_events (project_id, at);

ALTER TABLE projects ADD COLUMN IF NOT EXISTS monthly_credit_cap numeric(14, 2);  -- null: uncapped
ALTER TABLE projects ADD COLUMN IF NOT EXISTS retention_days int;                 -- null: forever
-- Recall abstains unless a memory is at least this relevant (recall.py); null: the embedder's default.
ALTER TABLE projects ADD COLUMN IF NOT EXISTS recall_min_relevance real;
ALTER TABLE episodes ADD COLUMN IF NOT EXISTS expired_at timestamptz;             -- raw text dropped by retention

-- Tenancy. An organization owns projects; its management keys (mv_mk_, hashed) administer only
-- its own projects. The operator key from config administers every organization.
CREATE TABLE IF NOT EXISTS organizations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        text UNIQUE NOT NULL,
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS org_keys (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name          text NOT NULL,
    key_hash      text UNIQUE NOT NULL,
    key_suffix    text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_used_at  timestamptz,
    revoked_at    timestamptz
);
INSERT INTO organizations (slug, name) VALUES ('default', 'Default organization') ON CONFLICT (slug) DO NOTHING;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS org_id uuid REFERENCES organizations(id) ON DELETE CASCADE;
UPDATE projects SET org_id = (SELECT id FROM organizations WHERE slug = 'default') WHERE org_id IS NULL;
CREATE INDEX IF NOT EXISTS projects_org ON projects (org_id);

-- Administrative actions and memory edits: who did what to which target. Bodies are never stored.
CREATE TABLE IF NOT EXISTS audit_events (
    id          bigserial PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    org_id      uuid,
    project_id  uuid,
    actor       text NOT NULL,      -- "project key <name>", "org key <name>", "operator"
    action      text NOT NULL,      -- e.g. project.delete, key.create, memory.update
    target      text NOT NULL,
    status      int NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_events_org ON audit_events (org_id, at DESC);

-- People. Each signs in with email and password, belongs to one or more organizations with a
-- role, and holds sign-in sessions (mv_us_ tokens, stored hashed). Organization keys stay for CI.
CREATE TABLE IF NOT EXISTS users (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email          text NOT NULL,
    name           text NOT NULL DEFAULT '',
    password_hash  text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    last_login_at  timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS users_email ON users (lower(email));
CREATE TABLE IF NOT EXISTS memberships (
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    org_id      uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    role        text NOT NULL CHECK (role IN ('owner','admin','member')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, org_id)
);
CREATE TABLE IF NOT EXISTS user_sessions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    org_id        uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    token_hash    text UNIQUE NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    last_used_at  timestamptz,
    revoked_at    timestamptz
);
CREATE TABLE IF NOT EXISTS invites (
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
ALTER TABLE projects ADD COLUMN IF NOT EXISTS processing_paused boolean NOT NULL DEFAULT false;

-- Code-change staleness (code.py). Clients report the files a conversation touched with their git
-- blob hashes; facts about current code that name such a file are anchored to that version; a
-- later report of a different version marks them stale.
ALTER TABLE episodes ADD COLUMN IF NOT EXISTS code jsonb;
ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS stale_reason text;
CREATE TABLE IF NOT EXISTS session_code (
    project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    session_id  text NOT NULL,
    repo        text NOT NULL,
    path        text NOT NULL,
    blob        text NOT NULL,
    seen_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, session_id, repo, path)
);
CREATE TABLE IF NOT EXISTS memory_anchors (
    project_id   uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    memory_id    uuid NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
    repo         text NOT NULL,
    path         text NOT NULL,
    blob         text NOT NULL,
    anchored_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (memory_id, repo, path)
);
CREATE INDEX IF NOT EXISTS memory_anchors_file ON memory_anchors (project_id, repo, path);
CREATE TABLE IF NOT EXISTS code_files (
    project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    repo        text NOT NULL,
    path        text NOT NULL,
    blob        text,              -- NULL: the file was reported deleted
    seen_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, repo, path)
);

-- Where each key was last used from (the console shows it next to the time).
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS last_used_ip text;
ALTER TABLE org_keys ADD COLUMN IF NOT EXISTS last_used_ip text;

-- Background work (imported history: backfilled sessions, bulk imports) runs after live work of
-- the same project, so what people are doing now becomes memory first.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS imported boolean NOT NULL DEFAULT false;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS background boolean NOT NULL DEFAULT false;

-- Threads (threads.py, synthesis.py): one living page per piece of work (a release, task, PR,
-- ticket, topic) edited in place, plus a small set of durable knowledge items with a category. The
-- description is the project profile extraction judges relevance against.
ALTER TABLE projects ADD COLUMN IF NOT EXISTS description text;

-- sections: [{"label", "items": [{"text", "date", "evidence": [{"episode_id", "quote"}]}]}], labels
-- from a fixed vocabulary in a fixed order (threads.SECTION_LABELS). key: branch:<name>, pr:<n>,
-- ticket:<ID>, release:<version> or topic:<slug>; aliases are other keys naming the same work.
CREATE TABLE IF NOT EXISTS threads (
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
CREATE INDEX IF NOT EXISTS threads_aliases ON threads USING gin (aliases);
CREATE INDEX IF NOT EXISTS threads_activity ON threads (project_id, last_activity_at DESC);
-- The episodes a thread page was written from. Deleting an episode drops its row; a thread left
-- with no source is deleted (threads.forget_episode).
CREATE TABLE IF NOT EXISTS thread_sources (
    thread_id   uuid NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    project_id  uuid NOT NULL,
    episode_id  text NOT NULL,
    PRIMARY KEY (thread_id, episode_id),
    FOREIGN KEY (project_id, episode_id) REFERENCES episodes(project_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS thread_sources_episode ON thread_sources (project_id, episode_id);
CREATE TABLE IF NOT EXISTS thread_links (
    project_id  uuid NOT NULL,
    thread_id   uuid NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    related_id  uuid NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    PRIMARY KEY (thread_id, related_id)
);
-- Knowledge: decision, rule, gotcha, fact or preference, why it matters, and the thread it belongs to.
ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS category text CHECK (category IN ('decision','rule','gotcha','fact','preference'));
ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS why text;
ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS thread_id uuid REFERENCES threads(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS memory_items_thread ON memory_items (thread_id) WHERE thread_id IS NOT NULL;
-- Knowledge scope: 'project' for anyone in the project, 'thread' for what matters only inside
-- its thread (recall shows it beside that page). NULL (older items) reads as project.
ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS scope text CHECK (scope IN ('project','thread'));

-- Edges between facts that the item columns do not already hold (supersedes, conflicts_with,
-- thread_id and memory_anchors do). extends: the newer fact (from) adds detail to an older one
-- (to); both stay active. derived: a fact the model inferred (from) cites one it was drawn from (to).
-- episode_id: the evidence that created the edge, when one episode did.
CREATE TABLE IF NOT EXISTS memory_links (
    project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    from_id     uuid NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
    to_id       uuid NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
    kind        text NOT NULL CHECK (kind IN ('extends','derived')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    episode_id  text,
    PRIMARY KEY (from_id, to_id, kind),
    CHECK (from_id <> to_id)
);
CREATE INDEX IF NOT EXISTS memory_links_to ON memory_links (to_id);
CREATE INDEX IF NOT EXISTS memory_links_project ON memory_links (project_id, created_at);

-- One memory model. Projects once chose between a flat-facts model with one summary page per
-- subject and the thread-and-knowledge model above; only the latter remains. On a database from
-- before, drop what only the old model used: the per-project switch, the subject pages, and any
-- job still queued to rebuild them. Idempotent, and a no-op on a fresh database.
ALTER TABLE projects DROP COLUMN IF EXISTS memory_model;
DROP TABLE IF EXISTS entity_pages;
DELETE FROM jobs WHERE kind = 'consolidate';

-- Row-level security. The application runs as memvine_app (not the owner, not a superuser, so
-- policies apply to it). Each pooled connection carries memvine.project_id: the project a
-- request is confined to (a project key, or the console acting on one project), or '' for work
-- that spans projects (the worker, organization administration). A confined connection sees and
-- writes only its project's rows, whatever a query forgets to filter. Keep this block last.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'memvine_app') THEN
        CREATE ROLE memvine_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;
    EXECUTE format('GRANT memvine_app TO %I', current_user);
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'memvine: cannot create role memvine_app; row-level security stays off';
END $$;

DO $$
DECLARE
    t text;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'memvine_app') THEN
        RETURN;
    END IF;
    GRANT USAGE ON SCHEMA public TO memvine_app;
    GRANT ALL ON ALL TABLES IN SCHEMA public TO memvine_app;
    GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO memvine_app;
    FOREACH t IN ARRAY ARRAY['api_keys', 'episodes', 'memory_items', 'evidence', 'sessions',
                             'session_events', 'jobs', 'request_log', 'identities', 'audiences', 'passages',
                             'usage_events', 'audit_events', 'session_code', 'memory_anchors', 'code_files',
                             'threads', 'thread_sources', 'thread_links', 'memory_links'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS project_scope ON %I', t);
        EXECUTE format($p$CREATE POLICY project_scope ON %I
            USING (coalesce(current_setting('memvine.project_id', true), '') = ''
                   OR project_id = nullif(current_setting('memvine.project_id', true), '')::uuid)$p$, t);
    END LOOP;
    ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS project_scope ON projects;
    CREATE POLICY project_scope ON projects
        USING (coalesce(current_setting('memvine.project_id', true), '') = ''
               OR id = nullif(current_setting('memvine.project_id', true), '')::uuid);
END $$;

-- review loop ------------------------------------------------------------------------------------
-- (review.py) Kept together so it can move into a migration as one piece.
-- review: 'new' until a person keeps it (Still true / Keep); synthesis writes 'new', a person's own
-- memory (POST /v1/memories, a correction) starts 'kept'. Items from before the review loop count as
-- kept, so the review queue starts with what is learned from now on. Forgetting stays retirement.
ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS review text NOT NULL DEFAULT 'kept' CHECK (review IN ('new','kept'));
ALTER TABLE memory_items ALTER COLUMN review SET DEFAULT 'new';
-- origin: who made the item (synthesis, a person, the sample). NULL only on rows from before the column.
ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS origin text CHECK (origin IN ('synthesis','person','sample'));
UPDATE memory_items m SET origin = CASE
    WHEN EXISTS (SELECT 1 FROM evidence v JOIN episodes e ON e.project_id = v.project_id AND e.id = v.episode_id
                 WHERE v.memory_id = m.id AND e.metadata->>'source' = 'sample') THEN 'sample'
    WHEN EXISTS (SELECT 1 FROM evidence v JOIN episodes e ON e.project_id = v.project_id AND e.id = v.episode_id
                 WHERE v.memory_id = m.id AND e.label LIKE 'remembered via %') THEN 'person'
    ELSE 'synthesis' END
WHERE origin IS NULL;
-- When a person forgot it (PATCH status=retired, MCP memory_forget). Synthesis never re-creates a
-- memory that matches a forgotten one; retirement by anything else (aging) leaves this NULL.
ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS forgotten_at timestamptz;
CREATE INDEX IF NOT EXISTS memory_items_review ON memory_items (project_id) WHERE review = 'new' AND status IN ('active','stale');
CREATE INDEX IF NOT EXISTS memory_items_forgotten ON memory_items (project_id) WHERE forgotten_at IS NOT NULL;
-- Private feedback: a synthesis-made memory a person forgot ('forgotten') or corrected ('wrong'),
-- with its evidence quotes and an excerpt of the source around the first quote (at most ~1500
-- characters). Exported per project for the local extraction gate (benchmark/knowledge/cases.py).
-- Deleting the excerpt's source episode deletes the case; retention blanks the excerpt.
CREATE TABLE IF NOT EXISTS feedback_cases (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind        text NOT NULL CHECK (kind IN ('forgotten','wrong')),
    memory_id   uuid REFERENCES memory_items(id) ON DELETE SET NULL,
    statement   text NOT NULL,
    category    text,
    correction  text,
    quotes      jsonb NOT NULL DEFAULT '[]',
    episode_id  text,
    excerpt     text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (project_id, episode_id) REFERENCES episodes(project_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS feedback_cases_project ON feedback_cases (project_id, created_at DESC);
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'memvine_app') THEN
        RETURN;
    END IF;
    GRANT ALL ON feedback_cases TO memvine_app;
    ALTER TABLE feedback_cases ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS project_scope ON feedback_cases;
    CREATE POLICY project_scope ON feedback_cases
        USING (coalesce(current_setting('memvine.project_id', true), '') = ''
               OR project_id = nullif(current_setting('memvine.project_id', true), '')::uuid);
END $$;
-- end review loop --------------------------------------------------------------------------------
