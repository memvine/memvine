-- Synthesis health (health.py). Each worker process records when its loops last polled the queue;
-- the newest beat tells whether any worker is alive. Rows of processes gone for a day are pruned.
CREATE TABLE worker_heartbeats (
    worker_id     text PRIMARY KEY,          -- host:pid:random, one per process
    host          text NOT NULL,
    pid           int NOT NULL,
    started_at    timestamptz NOT NULL DEFAULT now(),
    last_poll_at  timestamptz NOT NULL DEFAULT now()
);

-- When the alert webhook last fired for each condition, so a lasting problem is posted at most
-- once per alert_repeat_minutes, whichever process notices it.
CREATE TABLE alert_state (
    condition     text PRIMARY KEY,
    last_sent_at  timestamptz NOT NULL,
    message       text NOT NULL
);
