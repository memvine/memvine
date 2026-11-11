-- 0011 memory mode: what a project's memory is for, which decides how much is kept and how much
-- recall returns.
--   coding        agent sessions on a codebase: thread pages for the work, and only durable
--                 knowledge that passes the three tests (too much memory is worse than too little).
--   conversation  a person talking with an assistant over time: every concrete detail is kept
--                 (facts, dates, numbers, events, plans, preferences, instructions), no thread
--                 pages, and recall returns the matching details with their source excerpts.
ALTER TABLE projects ADD COLUMN memory_mode text NOT NULL DEFAULT 'coding'
    CHECK (memory_mode IN ('coding', 'conversation'));
