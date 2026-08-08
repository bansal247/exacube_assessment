-- Discord analytics schema.
-- Idempotent: safe to run against a fresh or already-initialized database.

CREATE TABLE IF NOT EXISTS servers (
    server_id                      TEXT PRIMARY KEY,
    server_name                    TEXT NOT NULL,
    owner_id                       TEXT NOT NULL,
    creation_date                  TIMESTAMPTZ NOT NULL,
    region                         TEXT NOT NULL,
    verification_level             SMALLINT NOT NULL CHECK (verification_level BETWEEN 0 AND 3),
    default_message_notifications  SMALLINT NOT NULL CHECK (default_message_notifications BETWEEN 0 AND 1),
    explicit_content_filter        SMALLINT NOT NULL CHECK (explicit_content_filter BETWEEN 0 AND 2),
    system_channel_id              TEXT,
    afk_channel_id                 TEXT,
    afk_timeout                    INTEGER,
    widget_enabled                 BOOLEAN NOT NULL,
    premium_tier                   SMALLINT NOT NULL CHECK (premium_tier BETWEEN 0 AND 3),
    premium_subscription_count     INTEGER NOT NULL CHECK (premium_subscription_count >= 0),
    approximate_member_count       INTEGER NOT NULL CHECK (approximate_member_count >= 0),
    approximate_presence_count     INTEGER NOT NULL CHECK (approximate_presence_count >= 0)
);

CREATE TABLE IF NOT EXISTS channels (
    channel_id              TEXT PRIMARY KEY,
    server_id                TEXT NOT NULL REFERENCES servers(server_id),
    channel_name             TEXT NOT NULL,
    channel_type             TEXT NOT NULL CHECK (channel_type IN ('text', 'voice')),
    topic                     TEXT,
    nsfw                      BOOLEAN NOT NULL,
    rate_limit_per_user       INTEGER NOT NULL CHECK (rate_limit_per_user >= 0),
    "position"                INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_channels_server_id ON channels(server_id);

CREATE TABLE IF NOT EXISTS members (
    user_id           TEXT NOT NULL,
    server_id         TEXT NOT NULL REFERENCES servers(server_id),
    username          TEXT NOT NULL,
    display_name      TEXT NOT NULL,
    discriminator     TEXT NOT NULL,
    avatar_hash       TEXT,
    is_bot            BOOLEAN NOT NULL,
    join_date         TIMESTAMPTZ NOT NULL,
    last_active       TIMESTAMPTZ NOT NULL,
    roles             TEXT[] NOT NULL DEFAULT '{}',
    messages_sent     INTEGER NOT NULL CHECK (messages_sent >= 0),
    voice_minutes     INTEGER NOT NULL CHECK (voice_minutes >= 0),
    is_owner          BOOLEAN NOT NULL,
    PRIMARY KEY (user_id, server_id)
);

CREATE INDEX IF NOT EXISTS idx_members_server_id ON members(server_id);
CREATE INDEX IF NOT EXISTS idx_members_last_active ON members(last_active);


CREATE TABLE IF NOT EXISTS messages (
    message_id        TEXT PRIMARY KEY,
    server_id         TEXT NOT NULL REFERENCES servers(server_id),
    channel_id        TEXT NOT NULL REFERENCES channels(channel_id),
    user_id           TEXT NOT NULL,
    "timestamp"       TIMESTAMPTZ NOT NULL,
    content            TEXT NOT NULL,
    has_attachment     BOOLEAN NOT NULL,
    has_embed          BOOLEAN NOT NULL,
    reaction_count      INTEGER NOT NULL CHECK (reaction_count >= 0),
    is_pinned           BOOLEAN NOT NULL,
    length              INTEGER NOT NULL CHECK (length >= 0),
    FOREIGN KEY (user_id, server_id) REFERENCES members(user_id, server_id)
);

-- Time-bucketed queries (activity per day/hour, per channel, per server)
-- dominate this workload, so timestamp is indexed both alone and composed
-- with the columns those queries group/filter by.
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages("timestamp");
CREATE INDEX IF NOT EXISTS idx_messages_server_timestamp ON messages(server_id, "timestamp");
CREATE INDEX IF NOT EXISTS idx_messages_channel_timestamp ON messages(channel_id, "timestamp");
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, server_id);

-- Grain: one row per (server_id, date) -- pre-aggregated daily server stats.
CREATE TABLE IF NOT EXISTS daily_stats (
    server_id        TEXT NOT NULL REFERENCES servers(server_id),
    date              DATE NOT NULL,
    total_messages    INTEGER NOT NULL CHECK (total_messages >= 0),
    new_members       INTEGER NOT NULL CHECK (new_members >= 0),
    active_members     INTEGER NOT NULL CHECK (active_members >= 0),
    total_members      INTEGER NOT NULL CHECK (total_members >= 0),
    day_of_week        SMALLINT NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    is_weekend          BOOLEAN NOT NULL,
    PRIMARY KEY (server_id, date)
);

CREATE INDEX IF NOT EXISTS idx_daily_stats_date ON daily_stats(date);

-- Grain: one row per (channel_id, date) -- pre-aggregated daily channel stats.
CREATE TABLE IF NOT EXISTS channel_daily_stats (
    channel_id        TEXT NOT NULL REFERENCES channels(channel_id),
    server_id         TEXT NOT NULL REFERENCES servers(server_id),
    date               DATE NOT NULL,
    message_count       INTEGER NOT NULL CHECK (message_count >= 0),
    active_users         INTEGER NOT NULL CHECK (active_users >= 0),
    PRIMARY KEY (channel_id, date)
);

CREATE INDEX IF NOT EXISTS idx_channel_daily_stats_date ON channel_daily_stats(date);
CREATE INDEX IF NOT EXISTS idx_channel_daily_stats_server ON channel_daily_stats(server_id);
