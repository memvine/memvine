-- 0007 recall recency: a project's own half-life for recency in recall's ranking (recall.py:
-- recency_factor). NULL: the server default (MEMVINE_RECALL_HALF_LIFE_DAYS, 90 days); 0: no recency
-- weighting. Only facts and gotchas decay; decisions, rules and preferences never do.
ALTER TABLE projects ADD COLUMN recall_half_life_days real CHECK (recall_half_life_days >= 0);
