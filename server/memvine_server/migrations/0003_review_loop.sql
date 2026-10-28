-- 0003 review loop
-- Converted from the "review loop" block of the hash-gated schema.sql. Statements keep their IF NOT EXISTS
-- forms: a database that ran that schema.sql before migrations already has them, and is baselined
-- at 0001 and then runs this file again harmlessly.
-- The review loop (review.py).
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
-- Row-level security for feedback_cases: rls.sql (it runs after every batch of migrations).
