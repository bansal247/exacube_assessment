# Generate matplotlib base64 png from a prior `query` call's rows. Requires '{SOURCE_CALL_ID_ARG}' set to the tool_call_id of the `query` call to visualize.
import base64

from app.agent.plugins.base import (
    ArtifactFile,
    Plugin,
    PluginContext,
    PluginError,
    PluginResult,
    SOURCE_CALL_ID_ARG,
)
from app.agent.plugins.registry import register_plugin

import io

import matplotlib.pyplot as plt


@register_plugin
class ScatterPlotPlugin(Plugin):
    name = "scatter_plot"
    description = (
        "Generate a scatter plot image from the rows of a prior `query` call. Requires '{SOURCE_CALL_ID_ARG}' set to the "
        "tool_call_id of the `query` call to visualize."
    )
    consumes = {SOURCE_CALL_ID_ARG: "query"}
    display_kind = "image"
    input_schema = {
        "type": "object",
        "properties": {
            SOURCE_CALL_ID_ARG: {"type": "string", "description": "tool_call_id of the prior `query` call."},
            "title": {"type": "string", "description": "Short title for this scatter plot."},
            "x_field": {"type": "string", "description": "Column for the x-axis."},
            "y_field": {"type": "string", "description": "Column for the y-axis."},
        },
        "required": [SOURCE_CALL_ID_ARG, "title", "x_field", "y_field"],
    }

    async def execute(self, arguments: dict, context: PluginContext) -> PluginResult:
        title = arguments.get("title")
        if not isinstance(title, str) or not title.strip():
            raise PluginError("'title' is required", retryable=True)

        x_field = arguments.get("x_field")
        if not isinstance(x_field, str) or not x_field.strip():
            raise PluginError("'x_field' is required", retryable=True)

        y_field = arguments.get("y_field")
        if not isinstance(y_field, str) or not y_field.strip():
            raise PluginError("'y_field' is required", retryable=True)

        source_data = context.prior_results[arguments[SOURCE_CALL_ID_ARG]]

        if not source_data:
            raise PluginError("Source query result was not found.", retryable=True)
        rows = source_data.get("rows", [])
        if not rows:
            raise PluginError("The query this scatter plot is based on returned no rows.", retryable=True)
        # Extract x/y values from the query result.
        x_values = []
        y_values = []

        for row in rows:
            if x_field not in row or y_field not in row:
                continue

            x = row[x_field]
            y = row[y_field]

            if x is None or y is None:
                continue

            try:
                x_values.append(float(x))
                y_values.append(float(y))
            except (TypeError, ValueError):
                continue

        if not x_values:
            raise PluginError(
                f"No valid numeric values found for '{x_field}' and '{y_field}'.",
                retryable=True,
            )

        # Render the plot in memory.
        fig, ax = plt.subplots(figsize=(8, 5))

        ax.scatter(x_values, y_values)
        ax.set_title(title)
        ax.set_xlabel(x_field)
        ax.set_ylabel(y_field)
        ax.grid(True, alpha=0.3)

        fig.tight_layout()

        # PNG -> bytes -> Base64.
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", dpi=150, bbox_inches="tight")
        plt.close(fig)

        b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        return PluginResult(
            data={"base64": b64},
            llm_summary=(
                f"Rendered a scatter plot titled '{title}' with x-axis '{x_field}' and y-axis '{y_field}' "
                f"({len(rows)} row(s) from the source query) as a PNG image."
            ),
        )
 
    async def to_file(self, data) -> ArtifactFile | None:
        png_bytes = base64.b64decode(data["base64"])
        return ArtifactFile(filename="scatter_plot.png", content_type="image/png", content=png_bytes)