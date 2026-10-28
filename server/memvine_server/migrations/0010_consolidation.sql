-- 0010 background consolidation (consolidation.py): a periodic, conservative tidy-up per project
-- that catches what write-time reconciliation missed. Nothing is deleted; every action is
-- reversible and recorded in memory_events (actor 'consolidation').

-- One row per run: what it did (stats: merged, linked, flagged, judged pairs, checked items, or
-- skipped with a reason) or the error that stopped it.
CREATE TABLE consolidation_runs (
    id           bigserial PRIMARY KEY,
    project_id   uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    stats        jsonb NOT NULL DEFAULT '{}',
    error        text
);
CREATE INDEX consolidation_runs_project ON consolidation_runs (project_id, started_at DESC);

-- One consolidation job per project waits or runs at a time (consolidation.schedule_due, queue).
CREATE UNIQUE INDEX jobs_one_consolidation ON jobs (project_id)
    WHERE kind = 'consolidate' AND status IN ('queued', 'running');

-- When the project's last run finished: a run is due after enough new memories, or a day.
ALTER TABLE projects ADD COLUMN consolidated_at timestamptz;

-- A doubtful case surfaces for a person instead of being acted on: the memory stays as it is.
--   no_longer_passes:   it seems to fail the three tests (flag_reason says which and why)
--   possible_duplicate: it may say the same as flag_related
--   possible_outdated:  flag_related may have replaced it
-- Dismissing (PATCH flag null) or keeping it clears the flag.
ALTER TABLE memory_items ADD COLUMN flag text
    CHECK (flag IN ('no_longer_passes', 'possible_duplicate', 'possible_outdated'));
ALTER TABLE memory_items ADD COLUMN flag_reason text;
ALTER TABLE memory_items ADD COLUMN flag_related uuid REFERENCES memory_items(id) ON DELETE SET NULL;
CREATE INDEX memory_items_flag ON memory_items (project_id) WHERE flag IS NOT NULL;

-- Why a retired memory is retired: 'forgotten' (a person), 'merged' (consolidation, into
-- merged_into). NULL on rows retired before this column. PATCH status active on a merged one un-merges it.
ALTER TABLE memory_items ADD COLUMN retired_reason text CHECK (retired_reason IN ('forgotten', 'merged'));
ALTER TABLE memory_items ADD COLUMN merged_into uuid REFERENCES memory_items(id) ON DELETE SET NULL;
UPDATE memory_items SET retired_reason = 'forgotten' WHERE status = 'retired' AND forgotten_at IS NOT NULL;

-- What consolidation already judged, so a later run does not ask the model again about the same
-- pair (step 'pair': memory_id older, other_id newer) or item (step 'bar': other_id = memory_id).
-- Holds ids and a verdict word only.
CREATE TABLE consolidation_judgements (
    project_id  uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    memory_id   uuid NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
    other_id    uuid NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
    step        text NOT NULL CHECK (step IN ('pair', 'bar')),
    verdict     text NOT NULL,
    at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (memory_id, other_id, step)
);
CREATE INDEX consolidation_judgements_project ON consolidation_judgements (project_id);
-- Row-level security for both tables: rls.sql.
