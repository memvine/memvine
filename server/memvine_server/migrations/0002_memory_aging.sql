-- 0002 memory aging
-- Converted from the "memory aging" block of the hash-gated schema.sql. Statements keep their IF NOT EXISTS
-- forms: a database that ran that schema.sql before migrations already has them, and is baselined
-- at 0001 and then runs this file again harmlessly.
-- Expired: valid_to on an active (or stale) item is the moment it said it stops being true
-- (extraction's valid_until, "the scratch org expires on 28 Oct"). Past it the item keeps its status;
-- recall stops serving it as current (recall.py NOT_EXPIRED) and the console shows it as Expired.
-- Unused: when a briefing last served an item, and how many times. Items older than
-- memory_unused_days not served in that window are listed for review, never hidden or deleted.
ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS last_recalled_at timestamptz;
ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS recall_count int NOT NULL DEFAULT 0;
