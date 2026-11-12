-- 0012 digests: in conversation mode, a short account of each excerpt of the conversation: what was
-- worked on, the problems, what was decided or recommended, with the names that identify them.
-- Details answer a question about one value; a question that asks for a summary of months of talk
-- needs every topic named once, which no ranking of single details gives. Recall serves digests, in
-- time order, to summary questions only (recall.py, digests.py).
--   level 1  one excerpt (episode_id).
--   level 2+ a run of consecutive digests of the level below, rolled up (their parent_id points here),
--            so a conversation too long for its level-1 digests to fit a briefing is still covered whole.
CREATE TABLE digests (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id   uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    level        int NOT NULL DEFAULT 1,
    episode_id   text,
    parent_id    bigint REFERENCES digests(id) ON DELETE SET NULL,
    content      text NOT NULL,
    starts_at    timestamptz NOT NULL,   -- when the first excerpt it covers was said
    occurred_at  timestamptz NOT NULL,   -- when the last one was
    audience     text NOT NULL DEFAULT 'project',
    embedding    vector(384),
    created_at   timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (project_id, episode_id) REFERENCES episodes(project_id, id) ON DELETE CASCADE,
    CHECK ((level = 1) = (episode_id IS NOT NULL))
);
CREATE UNIQUE INDEX digests_episode ON digests (project_id, episode_id) WHERE level = 1;
CREATE INDEX digests_project ON digests (project_id, level, occurred_at);

-- A roll-up repeats what its digests say. When one of them goes (its excerpt was deleted or
-- synthesized again), every roll-up above it goes too; the digests left without a parent are rolled
-- up again later.
CREATE FUNCTION digests_drop_rollups() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.parent_id IS NOT NULL THEN
        DELETE FROM digests WHERE id = OLD.parent_id;
    END IF;
    RETURN OLD;
END $$;
CREATE TRIGGER digests_drop_rollups AFTER DELETE ON digests FOR EACH ROW EXECUTE FUNCTION digests_drop_rollups();
