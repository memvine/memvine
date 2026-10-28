-- 0005 key connection: what the console's "Connect your agent" page needs to know about a key.
-- created_by: the signed-in person who created it (NULL for keys made by an organization key, the
-- operator, or before this migration), so the page can tell whether you already have a key.
-- last_user_agent: the User-Agent of the key's last call (the plugins send memvine-claude-code/<v>,
-- memvine-codex/<v>; the SDKs memvine-sdk-ts/<v>, memvine-sdk-python/<v>), to show the version.
ALTER TABLE api_keys ADD COLUMN created_by uuid REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE api_keys ADD COLUMN last_user_agent text;
CREATE INDEX request_log_key ON request_log (key_id, at) WHERE key_id IS NOT NULL;
