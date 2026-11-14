-- 0013 profile slots: in conversation mode memory is a small profile of the person, beside the
-- conversation itself, which is stored and searched word for word (passages).
--
-- Keeping every concrete detail (0011) gave about 800 memories per 100,000-token conversation. Half
-- restated the assistant, nine in ten had a near-twin, and a changed value had no place to land:
-- among hundreds of similar lines the model retired the wrong one, or kept "2 remote days" beside
-- "three days" with no link (docs/plans/conversation-memory-plan.md).
--
-- A slot names the one thing a profile memory holds the value of: "constraint/book-budget",
-- "plan/essay-deadline", "person/kelly". A new value for a slot replaces the current one, which stays
-- as history in the order said (supersedes); a statement that cannot be true together with the
-- current one, with no change announced, is kept beside it (conflicts_with). NULL everywhere else:
-- coding-mode memory, and details kept before this migration, which recall no longer serves.
ALTER TABLE memory_items ADD COLUMN slot text;
CREATE INDEX memory_items_slot ON memory_items (project_id, slot) WHERE slot IS NOT NULL;
