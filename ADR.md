# Architecture Decision Records

Short-form ADRs for this project's three biggest calls, as the brief asks for. Each is the
decision, the alternative it beat, and the cost accepted — not a full template, just enough to
stand alone. The README's "Design decisions and tradeoffs" section covers everything else.

## ADR 1: The plugin contract is declarative, with `consumes` as a multi-parent dependency map

**Decision.** Every agent capability beyond the core loop is a `Plugin` subclass declaring `name`,
`description`, `input_schema` (JSON Schema), `display_kind` (how the frontend renders its
result), and an optional `consumes: dict[str, str] | None` mapping argument names to the plugin
name each argument's value must reference. Discovery is a directory scan (`plugins/*.py`) plus a
`@register_plugin` decorator — dropping a file in is sufficient; no router, prompt, or core-loop
edit is required. `consumes` started as a single `str | None` (one upstream dependency, matching
what `chart` needed) and was generalized to a map once a fan-in case came up — a hypothetical
`pdf` needing both a `chart` and an `image` at once — expressing a real dependency DAG instead of
just a line, with the loop validating every entry independently in one enforcement point instead
of duplicated per plugin.

**Alternatives considered.** Entry-point-based discovery (packaging ceremony a single-package
project doesn't need) and a manually maintained plugin import list (exactly the "edit a file to
add a plugin" pattern the brief scores against). For `consumes` specifically: keeping it a single
string and picking one upstream arbitrarily for a fan-in plugin, or bolting on a second,
differently-named mechanism alongside it.

**Consequences.** `_validate_consumes`, the schema-hint injection, and `replay.py`'s chain-building
all operate on a dict/DAG now instead of one fixed field — more code than the single-parent case
strictly needed. In exchange, no plugin (including a future fan-in one) needs a second, parallel
mechanism. No plugin actually needs more than one upstream today; this is contract surface built
ahead of the plugin that will use it, the same way `display_kind: "image"` exists before any
plugin returns one.

## ADR 2: Pinning stores a replayable tool-call chain, not a chart-specific record

**Decision.** `pinned_artifacts` stores an ordered chain of `{tool_call_id, plugin_name,
arguments}` steps (`agent/replay.py`), re-executed through the plugin registry on refresh, rather
than a chart-specific table with SQL/chart-type/field columns.

**Alternatives considered.** A `pinned_charts` table (built first, then reworked) with columns
specific to chart's own shape — SQL text, chart type, x/y field names. It works for exactly one
plugin's output and breaks the moment a plugin with a different, or no, SQL concept — a future
`images` plugin, or a bare `query` result — needs pinning too.

**Consequences.** More code than "store one SQL string, re-run it" — a real chain-walking/replay
mechanism instead of a single stored value. In exchange, any plugin's result, or any chain of
plugin results, is pinnable without special-casing, and a pinned chart is genuinely re-runnable —
the brief's own bar, "not a dead PNG" — because refreshing means re-executing the same real chain
through the same real plugins, not reformatting a cached image.

## ADR 3: SQL safety is enforced via AST parsing (`sqlglot`), not string matching

**Decision.** `sql_safety.py` parses every LLM-generated query with `sqlglot` and rejects the
request unless the parsed tree is a single read-only `SELECT`/`UNION` with no write node anywhere
in it — including inside a data-modifying CTE (`WITH x AS (DELETE FROM t RETURNING *) SELECT *
FROM x`, which has a `SELECT` as its top-level node and would fool a keyword check). The row cap
is enforced by rewriting the query's own `LIMIT` clause on the parsed tree, not by fetching
everything and truncating in application code.

**Alternatives considered.** Keyword/regex string matching for disallowed statements (`DROP`,
`DELETE`, ...). The brief is explicit that this "is not validation and we will get past it," and
the CTE case above is a concrete, working example of exactly how it gets bypassed.

**Consequences.** A real parsing dependency (`sqlglot`) and a validation path that has to be kept
in sync with SQL dialects and edge cases, in exchange for a defense that holds against the
specific bypass the brief names, not just the common case. This is the primary safety mechanism,
not defense-in-depth on top of something else — the least-privilege DB role and statement timeout
are additional layers, not substitutes for it.
