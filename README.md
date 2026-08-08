# Exaqube Assignment

## Part 1 — Data foundation

### How to run

```
make up or sudo make up
```

Runs `cp .env.example .env`, starts Postgres, then runs the loader (see [`Makefile`](Makefile)).
`load.py` applies `schema.sql` itself (all `CREATE TABLE/INDEX IF NOT EXISTS`) before loading.
Re-running `make up` is safe — every insert is an `ON CONFLICT` upsert keyed on the table's
primary key, so nothing duplicates.

### Schema

`servers` → `channels` → `members` → `messages`, plus two pre-aggregated fact tables:
`daily_stats` (one row per server per day) and `channel_daily_stats` (one row per channel per
day). Full DDL in [`db/schema.sql`](db/schema.sql).

Key decisions:
- **Timestamps as UTC.** Source CSVs are naive (no offset); loaded as `TIMESTAMPTZ`, treating
  every naive value as UTC.
- **`members` PK is `(user_id, server_id)`**, not `user_id` alone. In this specific CSV
  snapshot `user_id` never actually repeats across servers, but `data_dictionary.txt` only
  documents it as unique *per server* — that's a documented contract, not a coincidence to
  build on. Composite PK costs nothing (`server_id` is needed for the FK to `servers` anyway).
- **Indexes target time-bucketed queries** (the brief's stated dominant pattern), not blanket
  coverage: `messages(timestamp)`, `messages(server_id, timestamp)`,
  `messages(channel_id, timestamp)`, plus the two stats tables' date columns.

### Data cleaning

Two related issues found by inspecting the CSVs directly:

**1. 52 duplicate `(user_id, server_id)` keys in `members.csv`** — each is two *different*
people (different usernames/join dates/message counts) sharing a generated id, not the same
person twice. 104 of 2827 rows affected (3.68%); every collision group has exactly 2 members.

**2. 185 of 5000 messages (3.70%) reference a collided key**, so attribution is ambiguous.

Resolution, in [`db/load.py`](db/load.py):
- Per collision, the earlier joiner (by `join_date`) keeps the original `user_id`; the later
  joiner becomes `<user_id>_2`.
- For the 185 messages, attribute by each member's `[join_date, last_active]` window vs. the
  message timestamp: **102** land in exactly one window → attributed; **49** land in neither →
  attributed to the nearer window boundary; **34** land in both → dropped as genuinely
  ambiguous rather than guessed. Final loaded count: 5000 − 34 = 4966.
- Chose the window heuristic over an arbitrary tiebreak (accepting more loader complexity)
  because a plausible-but-wrong attribution is worse than a documented gap in a dataset built
  for per-user analytics. Checked the signal isn't overclaimed: even unambiguous messages fall
  outside their own member's window 35.72% of the time, so `last_active` is a moderate signal
  at best — which is also why the double-window matches are dropped rather than tie-broken
  further.

Everything else that looked unusual turned out fine on inspection, no cleaning needed:
`roles` multi-value fields are correctly CSV-quoted (an earlier `awk -F,` pass mis-split them,
not a data bug); nulls in `afk_channel_id`/`topic`/`avatar_hash`/`roles` are legitimate
"not set" states; no orphaned FKs, no duplicate `message_id`s, no `last_active` before
`join_date`.
