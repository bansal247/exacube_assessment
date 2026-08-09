"""Real SQL validation for LLM-generated queries -- parses with sqlglot and
inspects the AST, not string-matching. The brief is explicit that
string-matching for DROP "is not validation and we will get past it"; this
is the actual defense: a single SELECT statement, no write operations
anywhere in the tree (including inside a CTE -- Postgres genuinely allows
`WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x`, which looks like a
read from the outside), no `SELECT ... INTO` (creates a table in Postgres),
and a row cap enforced by rewriting the query's own LIMIT clause rather
than truncating results after the fact -- Postgres itself never
materializes more than the cap.

Used by both the query plugin (live chat) and pin refresh (re-running
previously-LLM-authored SQL later) -- one function, one place this logic
can go stale, rather than two copies drifting apart.
"""

import sqlglot
from sqlglot import exp

_WRITE_NODE_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Merge,
)


class SqlValidationError(Exception):
    pass


def make_read_only_capped(sql_text: str, max_rows: int, dialect: str = "postgres") -> str:
    """Validates sql_text is a single, read-only SELECT and returns an
    equivalent query with LIMIT clamped to max_rows. Raises
    SqlValidationError (never executes anything) if it isn't.
    """
    try:
        statements = [s for s in sqlglot.parse(sql_text, dialect=dialect) if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise SqlValidationError(f"Could not parse SQL: {exc}") from exc

    if len(statements) == 0:
        raise SqlValidationError("No SQL statement found")
    if len(statements) > 1:
        raise SqlValidationError(f"Expected exactly one SQL statement, found {len(statements)}")

    stmt = statements[0]

    if not isinstance(stmt, (exp.Select, exp.Union)):
        raise SqlValidationError(f"Only SELECT statements are allowed, got: {type(stmt).__name__}")

    disallowed = list(stmt.find_all(*_WRITE_NODE_TYPES))
    if disallowed:
        kinds = sorted({type(n).__name__ for n in disallowed})
        raise SqlValidationError(f"Disallowed statement type(s) found in query: {', '.join(kinds)}")

    for select_node in stmt.find_all(exp.Select):
        if select_node.args.get("into"):
            raise SqlValidationError("'SELECT ... INTO' is not allowed (creates a table)")

    return _cap_limit(stmt, max_rows).sql(dialect=dialect)


def _cap_limit(stmt: exp.Select | exp.Union, max_rows: int) -> exp.Select | exp.Union:
    # Narrower than exp.Expression -- matches the isinstance check the one
    # caller already did (only SELECT/UNION ever reach here) and, unlike
    # the base class, both actually declare .limit() as a builder method.
    existing = stmt.args.get("limit")
    if existing is not None:
        existing_value = _limit_value(existing)
        if existing_value is not None and existing_value <= max_rows:
            return stmt  # already within the cap, leave it alone
    return stmt.limit(max_rows)


def _limit_value(limit_node: exp.Limit) -> int | None:
    try:
        return int(limit_node.expression.this)
    except (AttributeError, ValueError, TypeError):
        return None
