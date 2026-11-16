-- 0014 team spaces: a person's sessions stay in a project of their own, and a team's project
-- holds only the memories people chose to share.
--
-- Until now a team used one project: every member's sessions went to it, and anyone in the
-- organization could read them in the console. People will not connect an agent on those terms.
--
--   projects.owner_user_id a personal project: only this person opens it in the console, whatever
--                          role anyone else in the organization holds. An organization key does
--                          not reach it either. NULL: the organization's, as before. Deleted with
--                          its owner's account, never handed to the organization.
--   audit_events.owner_user_id
--                          set on what happens in a personal project: its owner reads those audit
--                          lines, the organization does not (also after the project is deleted).
--   projects.team_space    true: the project takes shared memories (POST /v1/memories) and refuses
--                          conversation (POST /v1/ingest, session events). It never holds a session.
--   projects.share_policy  what a team space asks of the plugins connected to it: 'ask' (a person
--                          says which of a session's memories to share) or 'auto' (the ones
--                          suggested are shared, and the person can undo).
--   memory_items.shared_at, shared_ref
--                          on a memory in a person's own project: when it was shared with a team
--                          space and the memory it became there, so it is not suggested again.
ALTER TABLE projects ADD COLUMN owner_user_id uuid REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE audit_events ADD COLUMN owner_user_id uuid;
ALTER TABLE projects ADD COLUMN team_space boolean NOT NULL DEFAULT false;
ALTER TABLE projects ADD COLUMN share_policy text NOT NULL DEFAULT 'ask' CHECK (share_policy IN ('ask', 'auto'));
ALTER TABLE memory_items ADD COLUMN shared_at timestamptz;
ALTER TABLE memory_items ADD COLUMN shared_ref text;
