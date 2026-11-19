-- 0015 password reset links: a one-time link that lets a person set a new password.
--
-- Made by an owner or admin for a member (POST /v1/org/members/{id}/reset; the console shows the
-- link to pass on) or by the server's operator for anyone (`memvine-server reset-link <email>`,
-- run where the server runs, so it needs neither a password nor a key). Single use, expires after
-- a day, and making a new one for the same person cancels the earlier ones. Using it ends every
-- sign-in the person had. Only a hash of the link's token is stored, as for invites.
CREATE TABLE password_resets (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  text UNIQUE NOT NULL,
    created_by  text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    used_at     timestamptz,
    revoked_at  timestamptz
);
CREATE INDEX password_resets_user ON password_resets (user_id);
