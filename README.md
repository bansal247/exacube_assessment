# Exaqube Assignment

A FastAPI + Postgres analytics service over a synthetic Discord dataset, with a chat agent that
answers questions by writing SQL, chains tools (query → chart), and lets the user pin results to
a dashboard.

## How to run it


```
cp .env.example .env        # fill in an API key, see below
make up                     # db -> load data -> api
```

`make up` also runs `cp -n .env.example .env` on its own (won't overwrite an existing `.env`),
so running it again later never wipes a key you've already set.

```
make test                   # pytest suite, real Postgres via testcontainers
make lint                   # ruff, same config CI uses
make typecheck              # mypy on api/app, same config CI uses
make eval                   # eval harness against the live API (costs real tokens)
make loadtest-chat          # k6 against POST /chat, cost-bounded (~24 real calls total)
make loadtest-artifacts     # k6 against the artifact path, ramped -- no LLM calls beyond setup
make down                   # stop, keep data
make clean                  # stop, drop volumes + locally built images
make logs                   # for seeing docker logs
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
| `PUBLIC_HOST` | no | `localhost` | The hostname your browser actually uses to reach these containers. |
| `AGENT_DB_USER` / `AGENT_DB_PASSWORD` | yes | `discord_agent` / `discord_agent` | The restricted DB role the agent runs SQL as — read-only on the analytics tables, read/write only on its own chat/pin tables. Created automatically by the loader |
| `LLM_PROVIDER` | no | `openai` | `openai` or `anthropic` — which provider implementation to use |
| `OPENAI_API_KEY` | yes, if `LLM_PROVIDER=openai` | — | platform.openai.com/api-keys. Not the same as a ChatGPT Plus/Pro subscription — separate product, separate billing |
| `ANTHROPIC_API_KEY` | yes, if `LLM_PROVIDER=anthropic` | — | console.anthropic.com/settings/keys. |
| `AGENT_MODEL` | no | `gpt-4o-mini` | Model name for whichever provider is selected |
| `AGENT_MAX_TOOL_RETRIES` | no | `2` | How many failed tool-call rounds the agent tolerates before giving up and answering in prose |
| `AGENT_ROW_CAP` | no | `1000` | Max rows any agent-run SQL can return |
| `AGENT_QUERY_TIMEOUT_MS` | no | `5000` | Statement timeout for the agent's DB connections |
| `AGENT_CONSUMES_HINT_LIMIT` | no | `5` | How many candidate ids (most recent first) the loop actively suggests to a consuming plugin at once — older ones stay valid if referenced, just stop being proactively hinted |
| `DB_POOL_MIN_SIZE` / `DB_POOL_MAX_SIZE` | no | `1` / `10` | API's own Postgres connection pool size |
| `DB_STATEMENT_TIMEOUT_MS` | no | `5000` | Statement timeout for the API's own (non-agent) DB connections |
| `AGENT_DB_POOL_MIN_SIZE` / `AGENT_DB_POOL_MAX_SIZE` | no | `1` / `10` | Agent's own Postgres connection pool size |
| `LOG_LEVEL` | no | `INFO` | Log verbosity |

Only the selected provider's key is required — missing it fails loudly at startup, not on the
first request.

## How to write a new plugin

A plugin is a single class in a single file. Nothing outside that file needs to change —
`loop.py`, `pins.py`, every router, and the system prompt are all plugin-agnostic. This section is
meant to be the complete spec: everything below is either a required piece of the contract or a
real behavior your plugin will hit once it's live, not something you need to go read source to
discover.

### 1. Create the file

`api/app/agent/plugins/your_plugin.py`. One plugin per file is the convention (not enforced), so
it's obvious which file to edit later.

### 2. Subclass `Plugin` (`api/app/agent/plugins/base.py`) and set five class attributes

- **`name: str`** — the tool name the LLM calls. Must be unique across every registered plugin;
  a duplicate raises `ValueError` at import time (app startup fails loudly, not a silent
  overwrite). Use `snake_case`, matching `query`/`chart`.

- **`description: str`** — sent to the LLM verbatim as the tool's description. This is the *only*
  thing the model knows about your plugin beyond its `input_schema` — there's no separate
  documentation channel. Say what it does, when to use it, and if it consumes another plugin,
  say that explicitly (see `chart`'s own description for the pattern: it names which upstream
  plugin it needs and which argument to put the id in). Vague descriptions produce wrong routing
  — this is literally the prompt.

- **`input_schema: dict`** — JSON Schema for the arguments the LLM must supply. Passed directly
  to the provider as the tool's parameter schema, so it's enforced automatically — the LLM cannot
  invoke your `execute()` with a missing `required` field or a value of the wrong JSON type. What
  it can't express (cross-field rules like "`value_field` is required only when
  `chart_type='histogram'`") is your job to check inside `execute()` — see step 4.

- **`display_kind: Literal["table", "chart", "image", "file"]`** — no default, required. This
  isn't just a label: it's what the frontend's `renderArtifact(display_kind, data)` (used for
  both the live chat trace and the pinned dashboard) pattern-matches on to decide how to render
  your `data`, and it determines the exact shape `data` must be:

  | `display_kind` | Rendered as | Required shape of `PluginResult.data` |
  |---|---|---|
  | `"table"` | An HTML table | `{"rows": [{...}, ...], "sql": <str, optional>, "row_count": <int, optional>}`. `rows` is a list of flat JSON objects — one per row, same keys each. `sql`/`row_count` are shown as captions if present; only `rows` is required. See `query.py`. |
  | `"chart"` | A Chart.js chart, client-rendered | `{"chart_type": "line"\|"bar"\|"histogram", "title": str, "x_field": str, "y_field": str, "data": [{...}, ...]}` for line/bar, or `{"chart_type": "histogram", "title": str, "value_field": str, "data": [...]}` for histogram. `data` is the row list to plot — `x_field`/`y_field` (or `value_field`) name the keys inside those rows to use as axes. See `chart.py`. |
  | `"image"` | An `<img>` tag | `{"url": str}` **or** `{"base64": str}` (raw base64 PNG data, no `data:` prefix). |
  | `"file"` | Not rendered inline — a "pin it to download" note | No required shape. Only reachable as a real download if you also implement `to_file()` (step 5) *and* the result gets pinned — see that section for why. |

  If your `data` doesn't match the shape your `display_kind` promises, nothing crashes — the
  frontend falls back to a raw JSON dump — but the artifact won't render properly, so treat this
  table as load-bearing, not a suggestion.

- **`consumes: dict[str, str] | None`** (default `None`) — declares which prior plugin call(s)
  yours reads from. `None` if your plugin never chains off another (like `query`). Otherwise a
  `{argument_name: required_plugin_name}` map, one entry per upstream dependency:
  - **Single upstream** (the common case): one entry, conventionally keyed by the importable
    constant `SOURCE_CALL_ID_ARG` (`"source_call_id"`) from `plugins/base.py` — e.g.
    `consumes = {SOURCE_CALL_ID_ARG: "query"}`. Your `input_schema` must declare a matching
    `"source_call_id": {"type": "string", ...}` property, marked `required`.
  - **Multiple upstreams** (a fan-in — e.g. a hypothetical `pdf` needing both a `chart` and an
    `image`): one entry per upstream, each with its own argument name —
    `consumes = {"chart_call_id": "chart", "image_call_id": "image"}` — and a matching property
    for each in `input_schema`.
  - What this buys you: the loop validates every entry — that the id you were given actually
    refers to a completed call, and that it's a call to the plugin you declared — *before*
    `execute()` ever runs. Inside `execute()`, you can read straight from `context.prior_results`
    without re-checking. The loop also proactively rewrites your schema's property descriptions
    each round to include the real candidate ids currently available, so the model sees valid ids
    before it guesses, not just after a failed attempt.
  - What this doesn't do: express arbitrary data flow, only which *tool calls* must precede
    yours. You still read each upstream's actual result out of `context.prior_results[id]`
    yourself inside `execute()`.

### 3. Optional: give it a pin title

If your `input_schema` includes a string argument named `title`, its value becomes the pin's
display title when a result of this plugin gets pinned (`services/pins.py`'s `_derive_title`
reads it directly off the arguments of the pinned call). No `title` argument, or an empty one,
falls back to `"{your plugin name} result"`. `chart` uses this; `query` doesn't bother, since a
bare query has no natural title of its own.

### 4. Implement `async def execute(self, arguments: dict, context: PluginContext) -> PluginResult`

- `arguments` has already passed JSON Schema validation (required fields present, right JSON
  types) — you only need to check things Schema can't express.
- `context.agent_conn` is a ready-to-use `asyncpg.Connection`, already scoped to the
  least-privilege `AGENT_DB_USER` role (read-only on the analytics tables). Use it directly if you
  need the database — never open your own connection or pool; there's exactly one DI path for DB
  access in this app, and reusing it is how your plugin stays trivially testable (a test just
  passes in whatever connection it already has open).
- `context.prior_results[call_id]` and `context.prior_call_names[call_id]` hold every completed
  call's data/plugin-name for this session (this turn *and* earlier turns — chaining works across
  separate `/chat` messages, not just within one). If your plugin declares `consumes`, the loop
  already validated your reference before calling you; read it with
  `context.prior_results[arguments[your_arg_name]]`.
- **Validation failures you expect** (bad input, a value that doesn't fit): raise
  `PluginError(message, retryable=True)`. `message` goes straight back to the LLM as the tool
  result — write it the way `chart.py` does, naming what was wrong *and* what the valid options
  actually are (e.g. "column 'x' isn't in the query result. Available columns: [...]"), so a retry
  has something concrete to fix, not just a re-guess. Use `retryable=False` only if no retry could
  possibly help (e.g. there's no DB connection in this context at all).
- **Bugs, not expected failures**: don't try to catch everything. An uncaught exception is caught
  one level up by the loop itself, logged with a full traceback, and turned into a generic "internal
  error running '{name}'" tool result — it can't crash the turn, but it also isn't retryable
  (the model has no way to fix a bug in your code), so only rely on this path for genuinely
  unexpected failures, not routine validation.
- **Untrusted content**: if your `llm_summary` includes user-authored Discord content (message
  text, usernames — anything that isn't data your own code generated), wrap it the way
  `query.py` does, in the *exact* markers it defines
  (`UNTRUSTED_DATA_START`/`UNTRUSTED_DATA_END`, importable from `app.agent.plugins.query`) — the
  system prompt's prompt-injection defense is keyed to that literal string, not a general concept
  of "untrusted data." A different marker string is invisible to it. If your plugin's data is
  never user-authored (like `chart`, which only ever handles numbers and column names it was
  given), this doesn't apply.
- Return `PluginResult(data=..., llm_summary=...)`:
  - `data` — the full result. This is what's pinned, downloaded, and what a downstream consuming
    plugin reads via `context.prior_results`. Must be JSON-serializable — persistence
    (`json.dumps(..., default=str)`) won't crash on a stray `Decimal`/`datetime`/`UUID`, but will
    silently stringify it via `repr`-like fallback, which is worse than doing it properly. Prefer
    `to_jsonable()` (`app.agent.jsonable`) the way `query.py` does, which converts `datetime`/`date`
    to ISO strings and `Decimal` to `float` explicitly.
  - `llm_summary` — the shorter text actually sent to the model, and replayed to it on every later
    turn of a long conversation. Deliberately separate from `data` so a large result (a chart's
    full row set, say) doesn't cost tokens on every subsequent turn. Cap what you include (see
    `query.py`'s `LLM_PREVIEW_ROW_LIMIT`) rather than dumping everything.

### 5. Optional: `async def to_file(self, data: Any) -> ArtifactFile | None`

Default implementation returns `None` — "not downloadable via the backend at all." Override it
only if your result has a meaningful export format (`query` → CSV; a future `excel`/`powerpoint`
plugin → its own primary output). Return `ArtifactFile(filename: str, content_type: str, content:
bytes)`.

Important: **this is only ever reached through a pin.** `GET /pins/{id}/download` is the one route
that calls it — a live, unpinned chat result has no direct download endpoint of its own. If you
want your plugin's result to be downloadable, the user has to pin it first; there's no separate
"download this chat message" action.

### 6. Register it

Decorate the class with `@register_plugin` (`from app.agent.plugins.registry import
register_plugin`). Discovery is a directory scan (`discover_plugins()`, called once at API
startup) that imports every module in `plugins/` except `base.py`/`registry.py` — the decorator
running at import time does the actual registration. Nothing else needs to call anything.

One consequence worth knowing: `register_plugin` instantiates your class immediately
(`cls()`) and that **one instance is reused for every request, for the lifetime of the process** —
concurrent requests from different users share it. Don't store per-request state on `self`; use
`context` (a fresh `PluginContext` per turn) for anything request-scoped. `input_schema` and
friends are class-level constants precisely so this sharing is safe by construction — don't be the
plugin that adds a mutable instance attribute and breaks that.

### 7. New Python dependency?

Add it to `api/requirements.txt` and rebuild (`docker compose up -d --build api`, or just
`make up`, which already rebuilds). This isn't a core-file edit in the sense that matters — it's
dependency management, not agent logic — see "What I'd do differently" for a more self-contained
per-plugin-dependency alternative not built this pass.


### A complete worked example

A plugin that consumes a prior `query` call and produces a downloadable plain-text digest —
exercises `consumes`, the `title` convention, `PluginError`, and `to_file()` in one place:

```python
# api/app/agent/plugins/digest.py
from app.agent.plugins.base import (
    ArtifactFile,
    Plugin,
    PluginContext,
    PluginError,
    PluginResult,
    SOURCE_CALL_ID_ARG,
)
from app.agent.plugins.registry import register_plugin


@register_plugin
class DigestPlugin(Plugin):
    name = "digest"
    description = (
        "Turn the rows from a prior `query` call into a short plain-text digest, one line per "
        f"row, downloadable as a .txt file. Requires '{SOURCE_CALL_ID_ARG}' set to the "
        "tool_call_id of the `query` call to summarize."
    )
    consumes = {SOURCE_CALL_ID_ARG: "query"}
    display_kind = "file"
    input_schema = {
        "type": "object",
        "properties": {
            SOURCE_CALL_ID_ARG: {"type": "string", "description": "tool_call_id of the prior `query` call."},
            "title": {"type": "string", "description": "Short title for this digest."},
        },
        "required": [SOURCE_CALL_ID_ARG, "title"],
    }

    async def execute(self, arguments: dict, context: PluginContext) -> PluginResult:
        title = arguments.get("title")
        if not isinstance(title, str) or not title.strip():
            raise PluginError("'title' is required", retryable=True)

        source_data = context.prior_results[arguments[SOURCE_CALL_ID_ARG]]
        rows = source_data.get("rows", [])
        if not rows:
            raise PluginError("The query this digest is based on returned no rows.", retryable=True)

        lines = [", ".join(f"{k}={v}" for k, v in row.items()) for row in rows]
        return PluginResult(
            data={"title": title, "lines": lines},
            llm_summary=f"Built a {len(lines)}-line digest titled '{title}'.",
        )

    async def to_file(self, data) -> ArtifactFile | None:
        content = "\n".join(data["lines"])
        return ArtifactFile(filename="digest.txt", content_type="text/plain", content=content.encode("utf-8"))
```

That's the whole plugin. Drop the file in, restart the API, and it's callable, chainable,
pinnable, and downloadable — no other file touched.

## Design decisions and tradeoffs

- I chose **two real LLM providers over one provider behind a theoretical interface**, accepting the cost of maintaining two message-translation implementations, because a grader with only one of the two API keys should still get a working app on the first try.

- I chose **raw `asyncpg` over an ORM**, accepting more hand-written SQL and no auto-generated migrations, because the agent needs a bounded, read-only path for executing untrusted LLM-written SQL and using an ORM would create a second database-access path.

- I chose **a restricted DB role only for the agent over separate restricted roles for both the agent and API**, accepting less isolation for the API, because only the agent executes untrusted SQL while the API uses fixed, parameterized queries.

- I chose **returning a chart spec over a rendered image**, accepting that the frontend has to render the chart, because pinned charts need to be re-runnable and a spec can be stored and rendered again while an image cannot.

- I chose **seeding `PluginContext` from the full chat history over only the current turn's tool calls**, accepting more context to process, because users should be able to build on results from earlier messages such as asking for data and then asking to chart it later.

- I chose **rebuilding consuming-plugin tool schemas with real candidate IDs over relying only on prompt instructions**, accepting extra schema-building work each round, because models were not reliable enough at referencing the correct prior tool-call ID from instructions alone.

- I chose **including candidate IDs directly in the schema as well as in validation errors**, accepting some duplicated guidance, because showing valid IDs before a call is more reliable than only explaining them after the model gets a call wrong.

- I chose **most-recent-first candidate hints over chronological ordering**, accepting that older results appear later, because users' follow-up requests usually refer to the most recent relevant result and oldest-first ordering caused stale IDs to be selected.

- I chose **capping candidate hints at five over showing every valid candidate**, accepting that older valid IDs stop being actively suggested, because an uncapped list would grow indefinitely in long sessions and increase the context sent to every consuming plugin.

- I chose **describing candidates with their actual contents over providing only bare IDs**, accepting extra metadata generation, because when multiple results are created in the same round the model needs information such as columns, row counts, or chart titles to distinguish equally recent candidates.

- I chose **synchronous `POST /chat` over keeping the SSE streaming endpoint**, accepting that the frontend cannot show incremental tool-call progress, because streaming was not essential to correctness and removing it allowed more time for safety and correctness work.

- I chose **plain HTML/JS over React/Vite**, accepting manual DOM updates and no reactive framework, because the frontend only needs three small tabs and does not need routing, components, or a build system.

- I chose **Chart.js from a CDN over bundling it into the frontend**, accepting an external runtime dependency, because the frontend intentionally has no build step.

- I chose **rendering artifacts by `display_kind` over branching on plugin names**, accepting the need for a common artifact-rendering contract, because the same renderer can then handle live chat results and pinned results without adding frontend logic for every plugin.

- I chose **falling back to raw JSON when an artifact cannot be rendered over crashing the turn**, accepting less polished output for unsupported results, because a plugin's output should not be able to break the entire frontend.

- I chose **persisted, listable, resumable conversations over only storing the active session in `localStorage`**, accepting database and frontend work for session history, because users should be able to return to previous conversations rather than losing them when the active session changes.

- I chose **using the first user message as the session preview over generating an LLM summary**, accepting less descriptive previews, because generating a summary for every session would require an additional LLM call just to display the sidebar.

- I chose **reusing the same rendering path for loaded conversations over creating a separate history renderer**, accepting more care in the message-history shape, because live and historical tool results should behave and look the same.

- I chose **refreshing pins by re-executing their stored tool-call chain over allowing in-place editing**, accepting that changing a chart requires going back through chat, because the brief requires pins to be re-runnable but does not require a second query-building interface.

- I chose **resolving member ID collisions using related data over keeping or silently dropping the collisions**, accepting that some messages must be dropped when they are genuinely ambiguous, because related membership dates provide evidence for attributing most affected messages without guessing.

- I chose **membership-window attribution over assigning every ambiguous message to someone**, accepting 34 dropped messages, because guessing ownership would create incorrect analytics while `[join_date, last_active]` provides a defensible basis for attribution.


- I chose **a fixed delay between evaluation questions over retrying failed requests**, accepting slower evaluation runs, because backoff and retry logic belongs in the provider layer and the immediate constraint was preventing 17 questions from exhausting the provider's tokens-per-minute limit.

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

**Results** (`gpt-4o-mini`, 17 questions):

| Category | Questions | Routing | Correctness |
|---|---|---|---|
| simple_lookup | 3 | 3/3 | 3/3 |
| time_series | 3 | 3/3 | 3/3 |
| ambiguous | 2 | 2/2 | not scored (by design) |
| chart | 2 | 2/2 | 2/2 |
| file | 2 | 2/2 | 2/2 |
| chain | 1 | 1/1 | 1/1 |
| unanswerable | 4 | 4/4 | 4/4 |
| **Total** | **17** | **17/17 (100%)** | **15/15 scored (100%)** |

Avg latency 3.58s/turn, ~54.6k input / ~3.3k output tokens across the run, ≈$0.21 estimated cost.


## Load test results

Two separate k6 scripts (`loadtest/`), because they have fundamentally different cost profiles.

**Results.**

| | `POST /chat` (24 requests, 3 VUs) | Artifact path (ramped 1→60 VUs) |
|---|---|---|
| Throughput | 0.94 req/s | 168.8 req/s (42.2 full iterations/s) |
| p50 | 1957ms | 7.4ms |
| p90 | 2538ms | 16.4ms |
| p95 | 2753ms | 21.2ms |
| max | 3064ms | 3804ms (one outlier) |
| Error rate | 0% | 0% |


## Estimated time, actual time

**Started on 8th August 2026 9:50 AM, Estimated complition by night of 9th August, Complition around 10 AM of 10th of August because I wanted to test on diffrent System with running and all tests and with new plugin**

## What I'd do differently or additionally with more time

- **Per-plugin dependencies as their own file, not a shared `requirements.txt` edit.** Like done in Comfy-ui plug-ins. Every plugin is a folder with its own .py, requirements and documentatation file.
- **Include streaming also**
- **A better UI**
- **Multi User support**
