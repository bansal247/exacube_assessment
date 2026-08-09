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

-- Agent chat history.
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- tool_calls/tool_call_id/tool_name carry the LLM provider's native
-- tool-calling protocol (Anthropic tool_use/tool_result blocks today) so a
-- session's history can be replayed back to the provider verbatim on the
-- next turn, and so a pinned chart (Part 3 "Pinning") can trace back to the
-- exact tool call and arguments that produced it.
CREATE TABLE IF NOT EXISTS chat_messages (
    message_id     BIGSERIAL PRIMARY KEY,
    session_id     UUID NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    role           TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
    content        TEXT,
    tool_calls     JSONB,
    tool_call_id   TEXT,
    tool_name      TEXT,
    is_error       BOOLEAN NOT NULL DEFAULT false,
    -- Full structured plugin output (e.g. a chart's spec+data), distinct
    -- from `content` -- which for role='tool' rows holds the short
    -- LLM-facing summary that gets replayed to the provider on later
    -- turns. `data` is what the API response and a future "pin this
    -- chart" action read; kept separate so replaying history to the LLM
    -- doesn't re-spend tokens on a large payload every turn.
    data           JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, message_id);

-- Generic across any plugin's output, not chart-specific: a pin is "the
-- chain of tool calls that produced this, plus a cached snapshot of what
-- it produced." call_chain is an ordered JSON array of
-- {tool_call_id, plugin_name, arguments} (see app/agent/replay.py) --
-- refreshing re-executes each step in order through the plugin registry,
-- not a single stored SQL string, so this works whether the chain is
-- [query], [query, chart], or a future plugin/chain this table was never
-- specifically designed around.
CREATE TABLE IF NOT EXISTS pinned_artifacts (
    pin_id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id           UUID NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    source_tool_call_id  TEXT NOT NULL,
    plugin_name          TEXT NOT NULL,
    display_kind         TEXT NOT NULL CHECK (display_kind IN ('table', 'chart', 'image', 'file')),
    title                TEXT NOT NULL,
    call_chain           JSONB NOT NULL,
    cached_data          JSONB NOT NULL,
    cached_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    "position"           INTEGER NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pinned_artifacts_position_key UNIQUE ("position") DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS idx_pinned_artifacts_session ON pinned_artifacts(session_id);
