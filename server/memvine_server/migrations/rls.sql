-- Row-level security: a repeatable migration. migrate.py runs it after every batch of numbered
-- migrations (so tables they add get the grants) and whenever this file changes. It must stay
-- idempotent. A new table that holds project rows: add it to the list below.
--
-- The application runs as memvine_app (not the owner, not a superuser, so policies apply to it).
-- Each pooled connection carries memvine.project_id: the project a request is confined to (a
-- project key, or the console acting on one project), or '' for work that spans projects (the
-- worker, organization administration). A confined connection sees and writes only its project's
-- rows, whatever a query forgets to filter.
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
                             'threads', 'thread_sources', 'thread_links', 'memory_links', 'feedback_cases'] LOOP
        CONTINUE WHEN to_regclass(format('public.%I', t)) IS NULL;  -- a later migration adds it
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
