"""The plugin contract. Every agent capability beyond deciding what to do
implements this -- query and chart today, excel/powerpoint later.

What the interface owes the system, and how each is handled:

- Argument validation: JSON Schema (input_schema) is the first line -- the
  provider itself enforces required/typed fields before a plugin ever sees
  the call. Cross-field validation JSON Schema can't express cleanly (e.g.
  "value_field is required only when chart_type='histogram'") is left to
  each plugin's execute(), raising PluginError -- deliberately not
  generalized into the contract, since it's usually genuinely
  plugin-specific logic.
- Structured errors: PluginError, with `retryable` so the loop can
  distinguish "the LLM should try a fixed call" from "no retry helps."
- Chaining -- consuming one or more other plugins' output: `consumes` maps
  each argument name this plugin reads an upstream tool_call_id from to
  the plugin name it expects that call to be (e.g. a single-parent plugin
  like `chart` uses `{SOURCE_CALL_ID_ARG: "query"}`; a fan-in plugin like a
  hypothetical `pdf` combining a chart and an image could use
  `{"chart_call_id": "chart", "image_call_id": "image"}`). The loop (not
  each plugin) validates every entry -- that the referenced call happened
  and was of the declared kind -- before calling execute(), one
  enforcement point instead of one per plugin. This only expresses a DAG
  of *tool calls*, not arbitrary data flow -- a consuming plugin still
  reads each upstream result out of `PluginContext.prior_results` by id
  inside its own `execute()`, same as ever.
- Artifacts -- pinning and downloading: `display_kind` declares how a
  result should render on a dashboard ("table" | "chart" | "image" | "file").
  "table"/"chart"/"image" preview inline; "file" is download-only. "chart"
  and "image" are deliberately distinct: "chart" means a spec a charting
  library renders (what `chart.py` returns); "image" means the plugin's
  `data` already *is* displayable image content (e.g. a URL, or base64) --
  a future plugin producing actual pictures (not a data-viz spec) would use
  this, and its `to_file()` would return the raw image bytes for download.
  No such plugin exists yet -- this is contract surface only, added so the
  gap (a real image doesn't fit "chart" or "file") doesn't block a future
  plugin author. `to_file()` is how a plugin *optionally* offers a real
  downloadable export of its own result -- the default implementation
  returns None (not downloadable via the backend at all), and each plugin
  that wants to be downloadable overrides it and owns its own export
  format. There is deliberately no central "if display_kind == 'table':
  render_csv() elif ...:" dispatch anywhere in the system -- that's exactly
  the hardcoded-per-type chain the brief warns scores badly. The artifact
  endpoint just calls whatever the producing plugin implements.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal

if TYPE_CHECKING:
    import asyncpg

# Conventional argument name for a plugin with exactly one upstream
# dependency -- not enforced by the loop (which reads whatever keys
# Plugin.consumes declares), just the expected name to use in that common
# case so single-parent plugins don't each invent their own.
SOURCE_CALL_ID_ARG = "source_call_id"

DisplayKind = Literal["table", "chart", "image", "file"]


class PluginError(Exception):
    """Raised by a plugin's execute() to signal a failure the agent should
    see and can reason about -- distinct from an unhandled exception, which
    is a bug. `retryable` tells the loop whether retrying (e.g. the LLM
    fixing its own SQL) is a sane response, vs. a failure no retry can fix.
    """

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.message = message
        self.retryable = retryable


@dataclass
class PluginContext:
    """Passed to every execute() call.

    prior_results holds this turn's earlier tool outputs keyed by
    tool_call_id (full PluginResult.data, not the LLM summary), so a later
    plugin call can reference an earlier one's structured data -- the
    mechanism behind tool chaining. prior_call_names is the matching
    tool_call_id -> plugin name map, used by the loop to validate a
    `consumes` declaration before execute() runs.

    agent_conn is the same request-scoped connection (AGENT_DB_USER role)
    the router already acquired via dependency injection -- plugins that
    need the DB use this rather than reaching for a global pool themselves,
    so there's exactly one DI path for DB access in the whole app, and
    plugins are trivially testable by passing in whatever connection a test
    already has open.
    """

    prior_results: dict[str, Any] = field(default_factory=dict)
    prior_call_names: dict[str, str] = field(default_factory=dict)
    agent_conn: "asyncpg.Connection | None" = None


@dataclass
class PluginResult:
    # Full structured data: what other plugins see via
    # PluginContext.prior_results, and what the API response / pinning
    # reads. Can be arbitrarily large (e.g. a chart's full row set).
    data: Any
    # Short, human/LLM-readable text -- what's actually sent back to the
    # LLM as the tool result and replayed on later turns. Deliberately
    # separate from `data` so a large result doesn't cost tokens on every
    # subsequent turn of a long conversation.
    llm_summary: str


@dataclass
class ArtifactFile:
    filename: str
    content_type: str
    content: bytes


class Plugin(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    # JSON Schema for the arguments the LLM must supply -- passed directly
    # as the provider's tool `input_schema` (Anthropic's tool format already
    # *is* name/description/input_schema, so no translation needed there).
    input_schema: ClassVar[dict]
    # None if this plugin doesn't consume any other plugin's output.
    # Otherwise a {argument_name: required_plugin_name} map -- one entry
    # per upstream dependency. A single-parent plugin (the common case)
    # has exactly one entry, conventionally keyed by SOURCE_CALL_ID_ARG --
    # e.g. chart.consumes = {SOURCE_CALL_ID_ARG: "query"}.
    consumes: ClassVar[dict[str, str] | None] = None
    # How this plugin's result should render on a dashboard/pin tile.
    # "table"/"chart"/"image" preview inline; "file" is download-only.
    display_kind: ClassVar[DisplayKind]

    @abstractmethod
    async def execute(self, arguments: dict, context: PluginContext) -> PluginResult: ...

    async def to_file(self, data: Any) -> ArtifactFile | None:
        """Optional: render this plugin's result data as a downloadable
        file. Default is "not downloadable" -- a plugin overrides this only
        if it has something meaningful to export (query -> CSV, a future
        excel/powerpoint plugin -> its own primary output). Not abstract,
        because most plugins won't need it and the contract shouldn't force
        every plugin author to write a no-op.
        """
        return None
