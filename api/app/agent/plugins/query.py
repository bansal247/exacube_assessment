import csv
import io
import json
import logging

import asyncpg

from app.agent.jsonable import to_jsonable
from app.agent.plugins.base import ArtifactFile, Plugin, PluginContext, PluginError, PluginResult
from app.agent.plugins.registry import register_plugin
from app.agent.schema_context import SCHEMA_DESCRIPTION
from app.agent.sql_safety import SqlValidationError, make_read_only_capped
from app.config import settings

logger = logging.getLogger(__name__)

# Baseline safety already in place regardless of the validation below: this
# plugin only ever runs on context.agent_conn, which is always acquired
# from the AGENT_DB_USER role's pool (SELECT-only on domain tables, see
# db/load.py provision_agent_role), that pool sets statement_timeout, and
# asyncpg's extended query protocol (conn.fetch, even with zero bind
# parameters) rejects multi-statement input at the wire level. The
# make_read_only_capped() call below is the *real* validation the brief
# asks for (parsed AST, not string-matching) -- these other properties are
# defense in depth, not a substitute for it.

# How many rows go into the LLM-facing summary. The model needs to actually
# see values to "explain the result" (the brief's own phrasing) -- a bare
# row count isn't enough -- but inlining an unbounded result set would blow
# up token cost on every later turn once history is replayed. `data` (the
# full result) is separate and complete up to agent_row_cap (Safety's row
# cap, enforced before execution -- see sql_safety.py), not this limit.
LLM_PREVIEW_ROW_LIMIT = 50

# Wraps the row preview so the system prompt can tell the model, in one
# place, that anything between these markers is DATA -- possibly
# adversarial, since it's user-authored Discord content -- never
# instructions to follow. See README "Part 3 Safety" for what this does and
# doesn't defend against.
UNTRUSTED_DATA_START = "<untrusted_query_result>"
UNTRUSTED_DATA_END = "</untrusted_query_result>"


@register_plugin
class QueryPlugin(Plugin):
    name = "query"
    description = (
        "Execute a single read-only SQL SELECT statement against the Discord "
        "analytics database and return the matching rows as structured data. "
        "Use this for any question requiring data lookup or aggregation.\n\n" + SCHEMA_DESCRIPTION
    )
    display_kind = "table"
    input_schema = {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "A single SELECT statement. No INSERT/UPDATE/DELETE/DDL.",
            }
        },
        "required": ["sql"],
    }

    async def execute(self, arguments: dict, context: PluginContext) -> PluginResult:
        sql_text = arguments.get("sql")
        if not isinstance(sql_text, str) or not sql_text.strip():
            raise PluginError("'sql' argument is required and must be a non-empty string", retryable=True)
        if context.agent_conn is None:
            raise PluginError("query plugin has no database connection in this context", retryable=False)

        try:
            safe_sql = make_read_only_capped(sql_text, max_rows=settings.agent_row_cap)
        except SqlValidationError as exc:
            logger.warning("sql rejected by validation", extra={"sql": sql_text, "reason": str(exc)})
            # retryable=True: this is exactly the case the loop's bounded
            # retry exists for -- the LLM sees why its SQL was rejected and
            # can produce a valid single-SELECT in response.
            raise PluginError(f"Query rejected: {exc}", retryable=True) from exc

        logger.info("executing sql", extra={"sql": safe_sql})
        try:
            rows = await context.agent_conn.fetch(safe_sql)
        except asyncpg.PostgresError as exc:
            logger.warning("sql execution failed", extra={"sql": safe_sql, "error": str(exc)})
            raise PluginError(f"SQL execution failed: {exc}", retryable=True) from exc

        data_rows = to_jsonable([dict(r) for r in rows])
        logger.info("sql completed", extra={"row_count": len(data_rows)})
        return PluginResult(
            # `sql` is the validated, cap-applied query that actually ran --
            # not necessarily byte-identical to what the LLM wrote (e.g. a
            # missing LIMIT was added) -- so a pinned chart's "underlying
            # query" (Pinning section) re-runs exactly what executed before,
            # not a version that might now behave differently.
            data={"sql": safe_sql, "row_count": len(data_rows), "rows": data_rows},
            llm_summary=self._summarize(data_rows),
        )

    async def to_file(self, data) -> ArtifactFile | None:
        rows = data.get("rows", [])
        buffer = io.StringIO()
        if rows:
            writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return ArtifactFile(
            filename="query_result.csv",
            content_type="text/csv",
            content=buffer.getvalue().encode("utf-8"),
        )

    @staticmethod
    def _summarize(rows: list[dict]) -> str:
        row_count = len(rows)
        if row_count == 0:
            return "Query returned 0 rows."
        preview = rows[:LLM_PREVIEW_ROW_LIMIT]
        text = (
            f"Query returned {row_count} row(s). Rows:\n"
            f"{UNTRUSTED_DATA_START}\n{json.dumps(preview, default=str)}\n{UNTRUSTED_DATA_END}"
        )
        if row_count > LLM_PREVIEW_ROW_LIMIT:
            text += f"\n(showing first {LLM_PREVIEW_ROW_LIMIT} of {row_count} rows)"
        return text
