-- 0009 profiles: an always-current short summary per person and per project, served at session start
-- (profiles.py). Derived from memory, never a source of truth: every line cites the memories it
-- restates (lines: [{"text", "memory_ids": [...]}]), and a rebuild replaces it whole. A profile turns
-- dirty when a memory it cites stops being current, or when a memory relevant to it changes
-- (memory_events after built_from_event_id); the worker rebuilds dirty ones at most every 10 minutes.
-- subject: the person's identity for 'person', '' for 'project'. Deleting the project deletes them.
CREATE TABLE profiles (
    project_id           uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    subject_kind         text NOT NULL CHECK (subject_kind IN ('person', 'project')),
    subject              text NOT NULL DEFAULT '',
    lines                jsonb NOT NULL DEFAULT '[]',
    token_estimate       int NOT NULL DEFAULT 0,
    dirty                boolean NOT NULL DEFAULT true,
    built_at             timestamptz,
    built_from_event_id  bigint NOT NULL DEFAULT 0,
    created_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, subject_kind, subject),
    CHECK ((subject_kind = 'project') = (subject = ''))
);
CREATE INDEX profiles_dirty ON profiles (built_at) WHERE dirty;
-- Row-level security: rls.sql.
