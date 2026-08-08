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

## Part 2 — API

`make up` now also builds and starts the API after loading data — it's live at
`http://localhost:8000` (interactive docs at `/docs`) once it finishes.

FastAPI service in [`api/`](api/). Layered `routers` (HTTP/Pydantic) → `services` (business
logic, no HTTP dependency) → `repositories` (raw SQL over an `asyncpg` pool, no ORM).

### Endpoints

- `GET /health` — checks DB connectivity, `200` or `503`.
- `GET /servers`, `GET /servers/{id}` — paginated list / detail.
- `GET /servers/{id}/channels` — paginated, 404 if server doesn't exist.
- `GET /servers/{id}/members` — paginated, sortable (`sort_by`, `order`), 404 if server doesn't exist.
- `GET /servers/{id}/activity` — time-series aggregate, `granularity=day|hour`, optional
  `channel_id`/`from`/`to`. **Computed in Postgres**, not Python:
  - `day` + no `channel_id` → `daily_stats` (pre-aggregated, per server).
  - `day` + `channel_id` → `channel_daily_stats` (pre-aggregated, per channel).
  - `hour` → `GROUP BY date_trunc('hour', timestamp)` over `messages` directly, since there's
    no hourly pre-aggregation in the source data. Caveat documented in code and here:
    `messages_sample.csv` is a ~5000-row *sample*, not the full log, so hourly counts from it
    approximate true hourly volume rather than represent it exactly, unlike the day-grain
    tables which are genuine totals.

### Key decisions

- **Raw `asyncpg`, no ORM**, in the API and in the (future) agent's `query` plugin alike. The
  agent has to parse and execute untrusted LLM-generated SQL as a bounded, read-only,
  single-statement operation regardless of what the API uses — introducing an ORM for the
  API's own fixed queries would mean two different DB-access mechanisms doing the same job.
  One raw-SQL mechanism, shared, was cheaper than two idiomatic ones.
- **Separate least-privilege DB role for the API** (`API_DB_USER`/`API_DB_PASSWORD`,
  provisioned idempotently by `db/load.py`, SELECT-only, no DDL/DML) — distinct from the
  loader's role, which owns the tables. The agent's query plugin will get its own further-
  restricted role on top of this in Part 3 (statement timeout, row cap); the API's queries are
  fixed and reviewed, so plain least-privilege SELECT is enough here.
- **Consistent error envelope** — `{"error": {"code", "message", "details"}}` — for every 4xx
  (validation, not-found, bad-input) and for unexpected 500s, so nothing leaks a raw stack
  trace or FastAPI's default error shape inconsistently.
- **`sort_by` is never string-interpolated directly.** It's a `Literal`-typed query param
  (rejected as `422` if it's anything else), mapped through a fixed whitelist dict to the
  actual column name before being placed in SQL — values/params still go through bound
  placeholders (`$1`, `$2`, ...), only the whitelisted column/direction go into the SQL text.
- **No `GET /messages` endpoint, deliberately cut.** Message-level access (browse, filter,
  "what did Carol post last week") is exactly what Part 3's agent `query` plugin is for —
  generate SQL, execute it, return structured results. A hardcoded `/messages?filter=...`
  endpoint would just be a fixed, less flexible version of that, for a use case the frontend
  spec (data table + chart + chat + pinned dashboard) doesn't obviously need on its own. Risk
  accepted: if the agent/plugin work slips, Part 4 has no pre-built message-level endpoint to
  fall back on — cheap to add later if that happens, not worth building speculatively now.

### Tests

`api/tests/`, run against a real Postgres via `testcontainers-python` (not SQLite, not mocks) —
schema applied once per session from the same `db/schema.sql` Part 1 uses, data reset and
re-seeded per test. Covers the happy paths and the unhappy ones the brief calls out: 404s for
unknown servers/channels, 422s for invalid pagination/sort/granularity, 400 for `from > to`,
and the three different SQL paths behind the single `/activity` endpoint.

**The app under test is wired to the same least-privilege `API_DB_USER` role it runs as in
production**, not the Postgres superuser — a session fixture provisions a `test_api_role` with
the identical `SELECT`-only grants `db/load.py` gives the real role (duplicated rather than
imported, since `load.py` depends on `psycopg` which the API test image doesn't install; kept
in sync by review). Only fixture setup/teardown (seeding, `TRUNCATE`) uses an admin connection.
This means every endpoint test is implicitly also proof the role's grants are sufficient for
the app to work — and [`test_db_role.py`](api/tests/test_db_role.py) is the explicit negative
case: connects directly as that role and asserts `INSERT`/`UPDATE`/`DELETE`/`CREATE TABLE`/
`DROP TABLE` all fail with `InsufficientPrivilegeError`. The brief requires this be "enforced
at the database, not in the prompt" — this is what actually checks that, instead of trusting
the app layer to never issue a write.

Run with:
```
make test
```
This builds a throwaway container ([`api/Dockerfile.test`](api/Dockerfile.test)) and runs
`pytest` inside it, with the host's Docker socket mounted so `testcontainers` can start its own
Postgres as a sibling container (independent of the `db` service in `docker-compose.yml`).
`network_mode: host` on that container is what makes the sibling Postgres's mapped port
reachable — Linux-only, acceptable since the brief targets a Linux/Docker host. If you'd rather
run it directly: `cd api && pip install -r dev-requirements.txt && pytest` (needs local Docker
access either way, for testcontainers itself).

**Not yet verified end-to-end** — Docker wasn't available (without sudo) in the environment
these were written in, so this run hasn't happened yet. Syntax-checked (`py_compile`) but not
executed.

**Note on Part 6 (load testing):** this `pytest`/`testcontainers` suite is a separate mechanism
from the load testing Part 6 asks for. This suite proves correctness (right status codes, right
data, right error shapes) against a real but disposable database. Part 6's load test (k6/Locust)
will instead hit the live `api`/agent endpoints under concurrency to measure throughput and
p50/p95/p99 — different tool, different question, added once there's an agent/artifact endpoint
worth stress-testing.

### Other Make targets

- `make down` — stops and removes containers/networks (`docker compose down`); keeps the
  `pgdata` volume, so data survives.
- `make clean` — `docker compose down -v --rmi local --remove-orphans`: also drops the
  `pgdata` volume and any images this project built locally. Full teardown.

