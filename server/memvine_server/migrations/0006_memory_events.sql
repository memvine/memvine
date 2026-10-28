-- 0006 memory events: the history of every memory and the project's activity feed (events.py).
-- One row per change: created, restated, superseded, forgotten, stale, ... (events.KINDS).
-- Rows hold ids, kinds, who and when, never customer text: quotes and statements are joined at
-- read time, so deleting a source still deletes its text. Deleting a memory (its last source went)
-- deletes its history; deleting the project deletes all of it. Retention leaves events alone.
-- actor: 'synthesis' | 'person:<email or identity>' | 'key:<name>' | 'system' | 'consolidation'.
CREATE TABLE memory_events (
    id          bigserial PRIMARY KEY,
    project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    memory_id   uuid REFERENCES memory_items(id) ON DELETE CASCADE,
    kind        text NOT NULL,
    actor       text NOT NULL DEFAULT 'system',
    detail      jsonb NOT NULL DEFAULT '{}',
    episode_id  text,
    at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX memory_events_project_at ON memory_events (project_id, at DESC, id DESC);
CREATE INDEX memory_events_memory_at ON memory_events (memory_id, at, id) WHERE memory_id IS NOT NULL;

-- Backfill: memories from before this migration get a history from what their rows already say.
-- Every row carries detail.backfill, and each step skips memories that already have one, so
-- running these statements again adds nothing.
INSERT INTO memory_events (project_id, memory_id, kind, actor, detail, episode_id, at)
SELECT m.project_id, m.id, 'created',
       CASE WHEN coalesce(m.origin, 'synthesis') = 'synthesis' THEN 'synthesis' ELSE 'system' END,
       jsonb_build_object('backfill', true, 'origin', coalesce(m.origin, 'synthesis')),
       (SELECT min(v.episode_id) FROM evidence v WHERE v.memory_id = m.id),
       m.recorded_at
FROM memory_items m
WHERE NOT EXISTS (SELECT 1 FROM memory_events e WHERE e.memory_id = m.id AND e.kind = 'created');

INSERT INTO memory_events (project_id, memory_id, kind, actor, detail, at)
SELECT m.project_id, m.id, 'supersedes', 'system', jsonb_build_object('backfill', true, 'of', m.supersedes), m.recorded_at
FROM memory_items m
WHERE m.supersedes IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM memory_events e WHERE e.memory_id = m.id AND e.kind = 'supersedes');

INSERT INTO memory_events (project_id, memory_id, kind, actor, detail, at)
SELECT m.project_id, m.id, 'superseded', 'system', jsonb_build_object('backfill', true, 'by', m.superseded_by),
       coalesce(m.valid_to, n.recorded_at, m.recorded_at)
FROM memory_items m LEFT JOIN memory_items n ON n.id = m.superseded_by
WHERE m.superseded_by IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM memory_events e WHERE e.memory_id = m.id AND e.kind = 'superseded');

INSERT INTO memory_events (project_id, memory_id, kind, actor, detail, at)
SELECT m.project_id, m.id, 'contradicted', 'system', jsonb_build_object('backfill', true, 'with', c.other), m.recorded_at
FROM memory_items m CROSS JOIN LATERAL unnest(m.conflicts_with) AS c(other)
WHERE NOT EXISTS (SELECT 1 FROM memory_events e WHERE e.memory_id = m.id AND e.kind = 'contradicted');

INSERT INTO memory_events (project_id, memory_id, kind, actor, detail, at)
SELECT m.project_id, m.id, 'forgotten', 'system', jsonb_build_object('backfill', true), m.forgotten_at
FROM memory_items m
WHERE m.forgotten_at IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM memory_events e WHERE e.memory_id = m.id AND e.kind = 'forgotten');
-- Row-level security for memory_events: rls.sql.
