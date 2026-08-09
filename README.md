# Exaqube Assignment

A FastAPI + Postgres analytics service over a synthetic Discord dataset, with a chat agent that
answers questions by writing SQL, chains tools (query → chart), and lets the user pin results to
a dashboard. Everything the agent can do is a plugin — adding one doesn't touch any core file
(see "How to write a new plugin" below).

A frontend (chat, a data table + chart, and the pinned dashboard) consumes the API — plain
HTML/JS, no build step, served as its own container.

Scope note: `excel` and `powerpoint` plugins, and response streaming, weren't built this pass.
Everything else in the brief was.

## How to run it

Docker and `make`, nothing else.

```
cp .env.example .env        # fill in an API key, see below
make up                     # db -> load data -> api
```

`make up` also runs `cp -n .env.example .env` on its own (won't overwrite an existing `.env`),
so running it again later never wipes a key you've already set.

```
make test                   # pytest suite, real Postgres via testcontainers
make eval                   # eval harness against the live API (costs real tokens)
make down                   # stop, keep data
make clean                  # stop, drop volumes + locally built images
```

`make up` starts Postgres, loads the dataset (safe to re-run), and starts the API on
`http://localhost:8000` (`/docs` for interactive API docs) and the frontend on
`http://localhost:3000`. `POST /chat` is the agent endpoint.

## Environment variables

All in `.env` (copy from `.env.example`).

| Variable | Required | Default | What it's for |
|---|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | yes | `discord` / `discord` / `discord_analytics` | Postgres, and the role the loader and API connect as |
| `POSTGRES_PORT` | no | `5432` | Host port Postgres is published on |
| `API_PORT` | no | `8000` | Host port the API is published on |
| `FRONTEND_PORT` | no | `3000` | Host port the frontend is published on |
| `PUBLIC_HOST` | no | `localhost` | The hostname your browser actually uses to reach these containers. Only needs changing if Docker runs on a different machine than the browser (e.g. a remote server, accessed by IP or over Tailscale) — used to build both the frontend's API URL and the API's CORS allow-list from one value |
| `AGENT_DB_USER` / `AGENT_DB_PASSWORD` | yes | `discord_agent` / `discord_agent` | The restricted DB role the agent runs SQL as — read-only on the analytics tables, read/write only on its own chat/pin tables. Created automatically by the loader |
| `LLM_PROVIDER` | no | `openai` | `openai` or `anthropic` — which provider implementation to use |
| `OPENAI_API_KEY` | yes, if `LLM_PROVIDER=openai` | — | platform.openai.com/api-keys. Not the same as a ChatGPT Plus/Pro subscription — separate product, separate billing |
| `ANTHROPIC_API_KEY` | yes, if `LLM_PROVIDER=anthropic` | — | console.anthropic.com/settings/keys. Same caveat — a Claude Pro subscription doesn't include this |
| `AGENT_MODEL` | no | `gpt-4o-mini` | Model name for whichever provider is selected |
| `AGENT_MAX_TOOL_RETRIES` | no | `2` | How many failed tool-call rounds the agent tolerates before giving up and answering in prose |
| `AGENT_ROW_CAP` | no | `1000` | Max rows any agent-run SQL can return |
| `AGENT_QUERY_TIMEOUT_MS` | no | `5000` | Statement timeout for the agent's DB connections |
| `LOG_LEVEL` | no | `INFO` | Log verbosity |

Only the selected provider's key is required — missing it fails loudly at startup, not on the
first request.

## How to write a new plugin

1. Create `api/app/agent/plugins/your_plugin.py`.
2. Subclass `Plugin` (`plugins/base.py`) and set:
   - `name`, `description`, `input_schema` — JSON Schema for the arguments the LLM must supply.
   - `display_kind` — `"table"` (rows), `"chart"` (a chart spec), `"image"` (an actual picture),
     or `"file"` (download-only, e.g. a workbook or deck).
   - `consumes` (optional) — the name of the plugin yours reads a prior result from. If set,
     your `input_schema` must accept the upstream call's id via the argument name
     `SOURCE_CALL_ID_ARG` (`"source_call_id"`). The loop validates that reference before your
     `execute()` runs, so you can trust it's correct without re-checking.
3. Implement `async def execute(self, arguments, context) -> PluginResult`:
   - Validate anything JSON Schema can't express; raise `PluginError(message, retryable=...)`.
   - Need the database? Use `context.agent_conn` — never open your own connection or pool.
   - Chaining from an upstream call? Read `context.prior_results[arguments["source_call_id"]]`.
   - Return `PluginResult(data=..., llm_summary=...)`. `data` is the full result — what gets
     pinned, downloaded, or chained from. `llm_summary` is the shorter text actually sent back to
     the model, and replayed to it on later turns.
4. Optionally implement `async def to_file(self, data) -> ArtifactFile | None` if your result
   should be downloadable (`ArtifactFile(filename, content_type, content: bytes)`).
5. Decorate the class with `@register_plugin`. Discovery is a directory scan — nothing else to
   wire up.

Nothing in `loop.py`, `pins.py`, any router, or the system prompt needs touching.

```
db/          schema.sql + load.py (idempotent CSV -> Postgres loader, role setup)
api/app/
  routers/   HTTP + Pydantic only
  services/  business logic, testable without HTTP
  repositories/  raw asyncpg SQL, no ORM
  agent/
    loop.py            the agent: decide -> call tool(s) -> observe -> retry -> respond
    provider.py + anthropic_provider.py + openai_provider.py   LLM behind an interface
    plugins/base.py    the plugin contract
    plugins/registry.py    directory-scan discovery
    plugins/query.py, plugins/chart.py    the two implemented plugins
    sql_safety.py      parses and validates LLM-generated SQL
    replay.py          re-runs the tool-call chain behind a pinned artifact
eval/        eval harness, runs against the live API
frontend/    chat, data table + chart, pinned dashboard -- plain HTML/JS, its own container
```

## Design decisions and tradeoffs

**Two real LLM providers, not one plus an interface that could in theory swap.**
`api/app/agent/provider.py` defines `LLMProvider`; `AnthropicProvider` and `OpenAIProvider` are
both fully working implementations, picked at startup by `LLM_PROVIDER`. Cost: two
message-translation implementations to maintain (Anthropic's tool-result-as-user-message
convention vs. OpenAI's native tool role) instead of one. Chose to support both rather than
commit to a single vendor, since a grader with only one of the two keys should still get a
working app on the first try.

**Raw `asyncpg`, no ORM.** The agent has to parse and run untrusted LLM-written SQL as a bounded,
read-only operation regardless of what the rest of the API uses — an ORM for the API's own fixed
queries would mean two different database-access paths doing the same job. Cost: more
hand-written SQL, no auto-generated migrations. Chose one path over two.

**Only the agent gets a restricted DB role, not the API.** The brief's restricted-role
requirement is about the agent executing untrusted SQL; the API's own queries are fixed and
parameterized. A second role for the API would be complexity defending against a risk that layer
doesn't carry. `AGENT_DB_USER` — read-only on the analytics tables, read/write only on its own
chat/pin tables — is the one the brief actually asks for.

**Plugin discovery: directory scan + decorator, not entry_points or a manual import list.**
Entry points are packaging ceremony a single-package project doesn't need. A manual import list
is exactly the "edit a file to add a plugin" pattern the brief scores against.

**`chart` returns a spec, not a rendered image.** A pinned chart needs to be re-runnable, not a
frozen picture. A spec (chart type, field mapping, data) is naturally re-runnable; an image
isn't. Cost: the frontend renders the spec itself. No plugin in this system does server-side
chart rendering — turning a chart into an image is a frontend action against its own rendered
canvas, not something the backend generates.

**Pinning stores a chain of tool calls, not a chart-specific record.** `pinned_artifacts` holds
an ordered chain of `{tool_call_id, plugin_name, arguments}` steps, replayed through the plugin
registry on refresh (`agent/replay.py`), instead of a chart-specific table with SQL/chart-type/
field columns. Costs more code than "store one SQL string, re-run it." Buys "any plugin, or
chain of plugins, is pinnable" without special-casing chart — a future plugin with no SQL concept
at all still fits.

**SQL safety: real AST parsing (`sqlglot`), not string matching.** The brief is explicit that
string matching for `DROP` "is not validation and we will get past it." `sql_safety.py` parses
the query into a tree and rejects a write node anywhere in it, including inside a
data-modifying CTE (`WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x`), which has a
`SELECT` as its top-level node and would fool a keyword check. The row cap is enforced by
rewriting the query's own `LIMIT`, not by fetching everything and truncating in Python.

**`PluginContext` is seeded from the full chat history, not just the current turn's own tool
calls.** This is what lets "how many users per server," then "now chart that" as a separate
message later in the same session, actually chain — the most natural way a real user does this.
The simpler alternative (scoping context to a single turn) breaks that exact case.

**A consuming plugin's tool schema is rebuilt every round with the real candidate ids spliced
into it, on top of a validation error that lists them if a call still gets it wrong.** Prompt
instructions alone weren't reliable enough for the model to consistently reference the right
prior tool call by id. Showing the real ids directly in the schema, not just after a failure, is
the more complete of the two — kept both rather than relying on either alone.

**Downloads render on demand; nothing is persisted server-side.** `GET /pins/{id}/download` calls
the producing plugin's `to_file()` fresh on every request. No temp files, no storage location, no
cleanup job to write — the lifecycle story is that there isn't one.

**Streaming was built, then cut.** An SSE `POST /chat/stream` endpoint existed alongside `/chat`
and worked for the happy path. It was cut before completion to keep time for correctness and
safety work instead. `POST /chat` (synchronous) is the one supported chat path today. Cost: the
frontend can't render tool calls and progress incrementally as the brief's Part 4 section asks
for — a chat turn shows a single pending state, then the full reply and tool-call trace at once
when the response arrives. "A stream that dies halfway" becomes "a request that fails or times
out" instead, since there's no stream to die partway through.

**Frontend is its own container, not static files mounted on the API.** Matches the brief's own
"docker compose up brings up database, backend, frontend" wording, and keeps the frontend
independently deployable/restartable. Cost: a real CORS policy on the API (`FRONTEND_ORIGIN`,
scoped to the frontend's exact origin, not `*` — this API carries chat/pin state, not just public
reads) instead of same-origin by construction, which a static mount would have gotten for free.

**Frontend: plain HTML/JS, no build step, no framework.** Chosen deliberately over React/Vite —
nothing here needs client-side routing, a component framework, or a bundler: three tabs (chat,
explore, dashboard), each a small module that renders into a container element. Cost: no JSX,
no reactive re-rendering — state changes are handled by re-rendering a container's children by
hand. Chart.js is loaded from a CDN in `index.html` rather than vendored, for the same reason: no
build step to run it through.

**The chat trace and the pinned dashboard both render by `display_kind`, not by plugin name.**
`ChatResponse.ToolCallTrace` gained a `display_kind` field (derived from the plugin registry, the
same way `Pin.display_kind` already was) specifically so the frontend has one rendering function
(`renderArtifact()`) for both live chat results and pinned ones, dispatching on `"table"` /
`"chart"` / `"image"` / `"file"` — not a per-plugin-name branch in the frontend to go with the
one Part 3 already avoids in the backend. A plugin whose result doesn't match what that renderer
expects still falls back to raw JSON instead of crashing the turn.

**Past conversations are listable and resumable, not just the one session held in `localStorage`.**
`GET /chat/sessions` (id, timestamps, a preview — the session's first user message, not an
LLM-generated summary, since generating one would cost a call per session just to list them) and
`GET /chat/sessions/{id}/messages` (the full history, in the same shape a live turn already
renders) back a session list in the chat sidebar. The frontend groups that flat message list back
into turns itself — a loaded conversation renders through the exact same `renderToolCall()` /
`renderArtifact()` a live turn uses, not a separate, simpler path for history.

**Pinning is refresh, not edit.** `Refresh` re-executes a pin's stored tool-call chain and updates
the cached numbers — it doesn't let you change the underlying query, filters, or chart type
in place. That matches what the brief actually asks for ("re-runnable, not a dead PNG"); it
doesn't ask for in-place editing. To change what a chart shows, the path is back through chat —
ask an adjusted question, pin the new result, unpin the old one if it's no longer wanted. Pins
stayed immutable-but-rerunnable rather than gaining their own query editor, which would have been
a second, parallel way to construct a query outside the chat/plugin system entirely.

**Two related data-quality issues in the raw CSVs** (found by inspection, not by a failed load):
52 `(user_id, server_id)` collisions in `members.csv` — two different people sharing a generated
id, 3.68% of member rows — and 185 messages referencing one of those collided keys. Chose to
rename the later joiner's `user_id` and attribute ambiguous messages by each member's
`[join_date, last_active]` window where that resolves it (102/185), a nearest-window fallback
(49/185), or drop as genuinely ambiguous (34/185, 0.68% of all messages) rather than guess.
Dropping silently or keeping the collision were the alternatives; both hide a real data problem
instead of resolving it. Full numbers and reasoning are in `db/load.py`.

**Eval harness sleeps a few seconds between questions rather than retrying on failure.** 17
questions fired back-to-back can burn through a provider's tokens-per-minute limit before the run
finishes. A real retry/backoff belongs in the provider layer itself (see Failure modes) and
wasn't built there; adding a fixed delay between eval questions was the smaller, eval-only fix —
it keeps a full run from tripping the limit without pretending the app has retry handling it
doesn't. `EVAL_QUESTION_DELAY_SECONDS` (default 3s) controls it.

## What the agent is defended against, and what's left open

- **Read-only agent role, enforced at the database, not just in application code.**
  `AGENT_DB_USER` gets `SELECT` on the analytics tables and
  `SELECT`/`INSERT`/`UPDATE`/`DELETE` only on its own session/pin tables — a write to a domain
  table fails at the database regardless of what the application layer does or doesn't check.
- **SQL is parsed before it runs; anything but a single read statement is rejected.** See
  `sql_safety.py` above.
- **Statement timeout and row cap**, applied everywhere agent-run SQL executes — live chat and
  pin refresh both, through the same function rather than two copies that could drift apart.
- **Prompt injection.** The tool surface is narrow and side-effect-free — `query` is read-only,
  capped, and validated; `chart` is a pure data transform — so even a fully successful injection
  has very little to actually do. Query results are wrapped in `<untrusted_query_result>` markers
  with an explicit system-prompt instruction that content between those markers is data, never
  instructions. Left open: no content-based injection scanning or filtering (a keyword/regex
  filter would repeat the same "string matching isn't validation" mistake the brief warns
  against elsewhere), and no output-side scanning of the model's replies. The narrow tool surface
  is doing the real work here; the prompt-level marker is cheap insurance on top, not the primary
  defense.
- **Not defended against, honestly: resource exhaustion from a large generated artifact** (a
  very large workbook or deck, many concurrent renders). This only applies to `excel`/
  `powerpoint`, which weren't built. Whichever plugin renders a file next will need its own row/
  slide cap and a concurrency limit before it ships.
- **Not defended against: a provider-side hang or rate limit.** See Failure modes below.

## Eval harness

`eval/questions.py` — 17 questions across every required category (simple lookups, time-series
aggregates, ambiguous phrasing, chart-requiring, file-requiring, an explicit multi-tool chain,
and unanswerable questions). `eval/run_eval.py` sends each to the live `/chat` endpoint and
scores:

- **Routing** — are the expected tool names an ordered subsequence of what the agent actually
  called (extra calls tolerated; missing or out-of-order ones fail)?
- **Correctness** — for questions with a hand-written reference SQL query, the agent is asked to
  alias its own result columns to match the reference query's aliases, and only those columns'
  values are compared against the reference result — not the whole row shape, and not the prose
  reply. Ambiguous-phrasing questions have no single correct reference query by design (the brief
  itself says the agent must pick and state its own definition), so they're routing-scored only.
- **Latency and token cost**, logged per turn and summarized with a rough dollar estimate against
  a fixed per-token price (an estimate, not a billing record).

**Results: [PLACEHOLDER].** Run `make eval` and paste the summary here — routing score,
correctness score, which categories failed, and your read on why.

## Load test results

**[PLACEHOLDER — not done this pass.]** Load-testing the chat and artifact endpoints (p50/p95/
p99, concurrent requests, concurrent renders) wasn't reached this pass.

## Failure modes

What happens today, reasoned from the code, not measured under load:

- **The LLM provider times out or rate-limits.** Neither provider implementation has an explicit
  timeout/retry wrapper around its SDK call — a provider-side hang or a rate limit hits the ASGI
  server's own timeout (if any), or surfaces as a bare 500. Would build: a bounded timeout with
  backoff at the provider layer, surfaced to the loop the same way a plugin failure is, so it
  degrades to "I'm having trouble reaching the model, try again" instead.
- **A plugin throws an unhandled exception.** Already handled — the loop catches any non-
  `PluginError` exception from `execute()`, logs it, and reports a generic tool failure back to
  the model instead of crashing the turn.
- **Postgres disappears mid-turn.** A query in flight raises a Postgres error, caught in
  `query.py` and turned into a retryable plugin error — the agent sees the failure and can retry
  or give up gracefully. A new connection failing to acquire at all (pool exhausted, DB
  unreachable) is less graceful today — it surfaces as a generic 500. Would build: a specific
  error mapping for that case, the same treatment pin refresh already gets.
- **Many concurrent chats.** No explicit concurrency limit on `/chat` — bounded only by the DB
  pool size and the provider's own rate limits. Reasoning from the code says this would queue on
  connection acquisition rather than fail outright, but that's not a measured number.
- **The model writes an expensive query** (e.g. a large join). Caught by two independent layers —
  the statement timeout bounds how long it can run, and the row cap bounds what it can return —
  but the query still costs real database CPU/IO before either kicks in.
- **A deck or workbook render blows out memory.** Not applicable this pass — neither plugin
  exists.

## Estimated time, actual time

**[PLACEHOLDER — fill in.]**

## What I'd do differently or additionally with more time

**[PLACEHOLDER — fill in.]**
