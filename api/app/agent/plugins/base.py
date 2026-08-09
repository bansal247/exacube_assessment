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
- Chaining -- consuming another plugin's output: `consumes` declares which
  plugin (by name) this one expects to read from, and every consuming
  plugin's input_schema must accept the result via the fixed argument name
  SOURCE_CALL_ID_ARG. The loop (not each plugin) validates that the
  referenced call actually happened and actually was of the declared kind
  before calling execute() -- one enforcement point, not one per plugin.
- Streaming progress: `execute()` takes an optional `on_progress` callback
  -- `await on_progress("message")` any number of times before returning.
  Non-streaming callers (AgentLoop.run()) simply don't pass one (defaults
  to None; plugins must no-op if it's absent, never assume it's there).
  The streaming loop path passes a real one that forwards each call as a
  ToolProgress stage event.
- Artifacts -- pinning and downloading: `display_kind` declares how a
  result should render on a dashboard ("table" | "chart" | "file"; "file"
  means download-only, not inline-previewable -- see Pinning in the
  README). `to_file()` is how a plugin *optionally* offers a real
  downloadable export of its own result -- the default implementation
  returns None (not downloadable via the backend at all), and each plugin
  that wants to be downloadable overrides it and owns its own export
  format. There is deliberately no central "if display_kind == 'table':
  render_csv() elif ...:" dispatch anywhere in the system -- that's exactly
  the hardcoded-per-type chain the brief warns scores badly. The artifact
  endpoint just calls whatever the producing plugin implements.
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal

if TYPE_CHECKING:
    import asyncpg

OnProgress = Callable[[str], Awaitable[None]]

# Fixed argument name a consuming plugin's input_schema must use for the
# upstream call it reads from -- one convention, not a per-plugin field
# name, so the loop can validate it generically.
SOURCE_CALL_ID_ARG = "source_call_id"

DisplayKind = Literal["table", "chart", "file"]


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
    # None if this plugin doesn't consume another's output. Otherwise the
    # name of the plugin it expects SOURCE_CALL_ID_ARG to reference -- e.g.
    # chart.consumes = "query".
    consumes: ClassVar[str | None] = None
    # How this plugin's result should render on a dashboard/pin tile.
    # "table" and "chart" preview inline; "file" is download-only.
    display_kind: ClassVar[DisplayKind]

    @abstractmethod
    async def execute(
        self, arguments: dict, context: PluginContext, on_progress: OnProgress | None = None
    ) -> PluginResult: ...

    async def to_file(self, data: Any) -> ArtifactFile | None:
        """Optional: render this plugin's result data as a downloadable
        file. Default is "not downloadable" -- a plugin overrides this only
        if it has something meaningful to export (query -> CSV, a future
        excel/powerpoint plugin -> its own primary output). Not abstract,
        because most plugins won't need it and the contract shouldn't force
        every plugin author to write a no-op.
        """
        return None
