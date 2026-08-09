"""Hand-written summary of db/schema.sql for the system prompt and the query
plugin's tool description. Only the six analytics tables -- never
chat_sessions/chat_messages/pinned_artifacts, which exist for the agent's
own session bookkeeping, not as part of "the dataset" it answers questions
about. Not introspected from the DB at runtime -- there are only a handful
of tables and they change rarely, so a maintained description is simpler
than a live-introspection path, at the cost of needing to keep this in sync
by hand when schema.sql changes.

That cost is real, not hypothetical: `members` was missing discriminator
and avatar_hash here (both real columns) until a live eval run showed the
agent writing `NULL AS discriminator, NULL AS avatar_hash` instead of
actually selecting them -- it had no way to know those columns existed. If
a question about a column comes back suspiciously null/missing, check here
before assuming the agent reasoned wrong.
"""

SCHEMA_DESCRIPTION = """\
Tables (all timestamps are UTC):

servers(server_id PK, server_name, owner_id, creation_date, region,
  verification_level, default_message_notifications, explicit_content_filter,
  system_channel_id, afk_channel_id, afk_timeout, widget_enabled,
  premium_tier, premium_subscription_count, approximate_member_count,
  approximate_presence_count)

channels(channel_id PK, server_id FK->servers, channel_name, channel_type
  ['text'|'voice'], topic, nsfw, rate_limit_per_user, position)

members(user_id, server_id, PK(user_id, server_id), FK server_id->servers,
  username, display_name, discriminator, avatar_hash, is_bot, join_date,
  last_active, roles (text[]), messages_sent, voice_minutes, is_owner)
  -- messages_sent/voice_minutes here are lifetime totals from the source
  -- data, not derived from the messages table below.

messages(message_id PK, server_id FK->servers, channel_id FK->channels,
  user_id, timestamp, content, has_attachment, has_embed, reaction_count,
  is_pinned, length)
  -- This is a ~5000-row SAMPLE of messages, not the full log. Good for
  -- content lookups and hourly-granularity estimates; NOT a reliable
  -- source for exact total message counts -- use daily_stats /
  -- channel_daily_stats for those instead.

daily_stats(server_id, date, PK(server_id, date), FK server_id->servers,
  total_messages, new_members, active_members, total_members, day_of_week
  [0=Monday..6=Sunday], is_weekend)
  -- Pre-aggregated true daily totals per server. Prefer this over messages
  -- for "how many messages/day" style questions.

channel_daily_stats(channel_id, server_id, date, PK(channel_id, date),
  FK channel_id->channels, FK server_id->servers, message_count,
  active_users)
  -- Pre-aggregated true daily totals per channel. Prefer this over messages
  -- for per-channel daily volume.
"""
