from app.agent.plugins.base import SOURCE_CALL_ID_ARG, OnProgress, Plugin, PluginContext, PluginError, PluginResult
from app.agent.plugins.registry import register_plugin

# Field requirements per chart_type -- kept as plain Python rather than a
# JSON Schema oneOf/if-then (which can express "y_field required when
# chart_type=bar" but gets hard to read and doesn't produce a clearer error
# than just checking it directly).
_REQUIRED_FIELDS = {
    "line": ("x_field", "y_field"),
    "bar": ("x_field", "y_field"),
    "histogram": ("value_field",),
}


@register_plugin
class ChartPlugin(Plugin):
    name = "chart"
    description = (
        "Turn the rows from a prior `query` call into a chart spec the frontend renders. "
        "Use 'line' for a time series, 'bar' for a top-N/categorical comparison, "
        "'histogram' for a distribution of one numeric field. "
        f"Requires '{SOURCE_CALL_ID_ARG}' set to the tool_call_id of the `query` call whose rows to chart."
    )
    consumes = "query"
    display_kind = "chart"
    # No to_file() override -- deliberately. Live/pinned display stays
    # spec-based (the frontend renders it); if a user wants an image file,
    # that's the frontend's own "save as image" action against its own
    # rendered canvas/SVG, not something this backend generates. See
    # README "Pinning" for why: avoids needing a server-side chart
    # renderer at all, for every plugin in this system.
    input_schema = {
        "type": "object",
        "properties": {
            SOURCE_CALL_ID_ARG: {
                "type": "string",
                "description": "tool_call_id of the prior `query` call to chart.",
            },
            "chart_type": {"type": "string", "enum": ["line", "bar", "histogram"]},
            "title": {"type": "string"},
            "x_field": {"type": "string", "description": "Column for the x-axis/category. Required for line/bar."},
            "y_field": {"type": "string", "description": "Column for the y-axis/value. Required for line/bar."},
            "value_field": {
                "type": "string",
                "description": "Column of numeric values to bin. Required for histogram.",
            },
        },
        "required": [SOURCE_CALL_ID_ARG, "chart_type", "title"],
    }

    async def execute(
        self, arguments: dict, context: PluginContext, on_progress: OnProgress | None = None
    ) -> PluginResult:
        chart_type = arguments.get("chart_type")
        title = arguments.get("title")
        if chart_type not in _REQUIRED_FIELDS:
            raise PluginError(f"'chart_type' must be one of {list(_REQUIRED_FIELDS)}", retryable=True)
        if not isinstance(title, str) or not title.strip():
            raise PluginError("'title' is required", retryable=True)

        missing = [f for f in _REQUIRED_FIELDS[chart_type] if not arguments.get(f)]
        if missing:
            raise PluginError(
                f"chart_type '{chart_type}' requires {_REQUIRED_FIELDS[chart_type]}; missing {missing}",
                retryable=True,
            )

        if on_progress:
            await on_progress(f"Building {chart_type} chart...")

        # The loop already validated source_call_id refers to a completed
        # 'query' call (Plugin.consumes) before calling execute() at all.
        source_data = context.prior_results[arguments[SOURCE_CALL_ID_ARG]]
        rows = source_data.get("rows", [])

        field_names = set(rows[0].keys()) if rows else set()
        for f in _REQUIRED_FIELDS[chart_type]:
            column = arguments[f]
            if rows and column not in field_names:
                raise PluginError(
                    f"'{f}' references column '{column}', which isn't in the query result. "
                    f"Available columns: {sorted(field_names)}",
                    retryable=True,
                )

        spec = {
            "chart_type": chart_type,
            "title": title,
            "x_field": arguments.get("x_field"),
            "y_field": arguments.get("y_field"),
            "value_field": arguments.get("value_field"),
            "data": rows,
            # Carried forward from the query plugin's own output -- what
            # makes a pinned chart re-runnable rather than a frozen
            # snapshot (see Pinning in the README).
            "sql": source_data.get("sql"),
        }
        return PluginResult(
            data=spec,
            llm_summary=f"Created a {chart_type} chart '{title}' with {len(rows)} data point(s).",
        )
