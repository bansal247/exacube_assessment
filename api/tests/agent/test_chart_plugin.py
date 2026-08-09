import pytest

from app.agent.plugins.base import PluginContext, PluginError
from app.agent.plugins.chart import ChartPlugin


def _context_with_query_result(rows, sql="SELECT 1"):
    return PluginContext(
        prior_results={"q1": {"sql": sql, "row_count": len(rows), "rows": rows}},
        prior_call_names={"q1": "query"},
    )


def test_chart_declares_it_consumes_query():
    assert ChartPlugin.consumes == "query"


def test_chart_declares_chart_display_kind():
    assert ChartPlugin.display_kind == "chart"


@pytest.mark.asyncio
async def test_chart_to_file_is_not_downloadable():
    # Deliberate: no server-side image rendering anywhere in this system --
    # image export is a frontend concern against its own rendered chart.
    plugin = ChartPlugin()
    assert await plugin.to_file({"chart_type": "bar", "data": []}) is None


@pytest.mark.asyncio
async def test_line_chart_happy_path():
    rows = [{"date": "2026-01-01", "count": 10}, {"date": "2026-01-02", "count": 20}]
    context = _context_with_query_result(rows, sql="SELECT date, count FROM daily_stats")
    plugin = ChartPlugin()

    result = await plugin.execute(
        {"source_call_id": "q1", "chart_type": "line", "title": "Daily volume", "x_field": "date", "y_field": "count"},
        context,
    )

    assert result.data["chart_type"] == "line"
    assert result.data["data"] == rows
    assert result.data["sql"] == "SELECT date, count FROM daily_stats"
    assert "2 data point" in result.llm_summary


@pytest.mark.asyncio
async def test_bar_chart_top_n():
    rows = [{"user": "alice", "messages": 500}, {"user": "carol", "messages": 900}]
    context = _context_with_query_result(rows)
    plugin = ChartPlugin()

    result = await plugin.execute(
        {"source_call_id": "q1", "chart_type": "bar", "title": "Top posters", "x_field": "user", "y_field": "messages"},
        context,
    )
    assert result.data["chart_type"] == "bar"


@pytest.mark.asyncio
async def test_histogram_requires_value_field_not_x_y():
    rows = [{"length": 12}, {"length": 45}]
    context = _context_with_query_result(rows)
    plugin = ChartPlugin()

    result = await plugin.execute(
        {"source_call_id": "q1", "chart_type": "histogram", "title": "Message length", "value_field": "length"},
        context,
    )
    assert result.data["chart_type"] == "histogram"
    assert result.data["value_field"] == "length"


@pytest.mark.asyncio
async def test_invalid_chart_type_is_plugin_error():
    context = _context_with_query_result([{"a": 1}])
    plugin = ChartPlugin()
    with pytest.raises(PluginError):
        await plugin.execute({"source_call_id": "q1", "chart_type": "pie", "title": "x"}, context)


@pytest.mark.asyncio
async def test_line_chart_missing_y_field_is_plugin_error():
    context = _context_with_query_result([{"date": "2026-01-01", "count": 1}])
    plugin = ChartPlugin()
    with pytest.raises(PluginError):
        await plugin.execute({"source_call_id": "q1", "chart_type": "line", "title": "x", "x_field": "date"}, context)


@pytest.mark.asyncio
async def test_referencing_nonexistent_column_is_plugin_error():
    rows = [{"date": "2026-01-01", "count": 1}]
    context = _context_with_query_result(rows)
    plugin = ChartPlugin()
    with pytest.raises(PluginError, match="not in the query result"):
        await plugin.execute(
            {"source_call_id": "q1", "chart_type": "line", "title": "x", "x_field": "date", "y_field": "nope"},
            context,
        )


@pytest.mark.asyncio
async def test_empty_result_set_does_not_crash():
    context = _context_with_query_result([])
    plugin = ChartPlugin()
    result = await plugin.execute(
        {"source_call_id": "q1", "chart_type": "bar", "title": "Nothing here", "x_field": "a", "y_field": "b"},
        context,
    )
    assert result.data["data"] == []
    assert "0 data point" in result.llm_summary
