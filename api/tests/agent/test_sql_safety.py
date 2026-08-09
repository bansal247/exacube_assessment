"""sql_safety.make_read_only_capped -- the brief's own bar is explicit:
"string-matching for DROP is not validation and we will get past it," so
these tests specifically include the sneaky cases a keyword/prefix check
would miss (a write hidden inside a CTE, SELECT ... INTO), not just the
obvious ones.
"""

import pytest

from app.agent.sql_safety import SqlValidationError, make_read_only_capped


def test_adds_limit_when_missing():
    result = make_read_only_capped("SELECT * FROM servers", max_rows=100)
    assert "LIMIT 100" in result.upper()


def test_leaves_limit_below_cap_unchanged():
    result = make_read_only_capped("SELECT * FROM servers LIMIT 10", max_rows=100)
    assert "LIMIT 10" in result.upper()
    assert "LIMIT 100" not in result.upper()


def test_clamps_limit_above_cap():
    result = make_read_only_capped("SELECT * FROM servers LIMIT 999999", max_rows=100)
    assert "LIMIT 100" in result.upper()
    assert "999999" not in result


def test_limit_exactly_at_cap_is_unchanged():
    result = make_read_only_capped("SELECT * FROM servers LIMIT 100", max_rows=100)
    assert "LIMIT 100" in result.upper()


def test_multiple_statements_rejected():
    with pytest.raises(SqlValidationError, match="exactly one"):
        make_read_only_capped("SELECT 1; SELECT 2", max_rows=100)


def test_multiple_statements_with_trailing_write_rejected():
    with pytest.raises(SqlValidationError):
        make_read_only_capped("SELECT 1; DROP TABLE servers", max_rows=100)


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE servers",
        "DELETE FROM servers",
        "UPDATE servers SET server_name = 'x'",
        "INSERT INTO servers (server_id) VALUES ('x')",
        "TRUNCATE servers",
        "ALTER TABLE servers ADD COLUMN x TEXT",
        "CREATE TABLE evil (id INT)",
    ],
)
def test_top_level_write_statements_rejected(sql):
    with pytest.raises(SqlValidationError):
        make_read_only_capped(sql, max_rows=100)


def test_write_hidden_inside_a_cte_is_rejected():
    # Postgres genuinely allows data-modifying CTEs -- this looks like a
    # SELECT from the outside (top-level node is a SELECT), which is
    # exactly the case a leading-keyword check would miss.
    sql = "WITH deleted AS (DELETE FROM servers RETURNING *) SELECT * FROM deleted"
    with pytest.raises(SqlValidationError):
        make_read_only_capped(sql, max_rows=100)


def test_select_into_is_rejected():
    with pytest.raises(SqlValidationError, match="INTO"):
        make_read_only_capped("SELECT * INTO new_table FROM servers", max_rows=100)


def test_union_of_selects_is_allowed_and_capped():
    sql = "SELECT server_id FROM servers UNION SELECT channel_id FROM channels"
    result = make_read_only_capped(sql, max_rows=50)
    assert "LIMIT 50" in result.upper()


def test_unparseable_sql_is_rejected():
    with pytest.raises(SqlValidationError):
        make_read_only_capped("SELECT this is not ; ; valid !! sql (((", max_rows=100)


def test_empty_sql_is_rejected():
    with pytest.raises(SqlValidationError):
        make_read_only_capped("", max_rows=100)
