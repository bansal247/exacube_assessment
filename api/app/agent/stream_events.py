"""The stages the Streaming section asks to surface: reasoning, then which
tool was picked and with what arguments, then that tool's progress, then
its result, then the final prose. One dataclass per stage rather than a
single generic "event" blob, so a client (or a test) can pattern-match on
type instead of parsing a string tag.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass
class SessionStarted:
    # Always the first event -- lets a client watching a brand-new chat
    # (no session_id sent in the request) learn the server-assigned id
    # immediately, rather than only once the whole turn finishes.
    session_id: UUID


@dataclass
class Reasoning:
    text: str


@dataclass
class ToolSelected:
    tool_call_id: str
    name: str
    arguments: dict


@dataclass
class ToolProgress:
    tool_call_id: str
    message: str


@dataclass
class ToolResult:
    tool_call_id: str
    name: str
    is_error: bool
    result: Any


@dataclass
class FinalAnswer:
    session_id: UUID
    text: str


@dataclass
class StreamError:
    message: str


LoopStreamEvent = SessionStarted | Reasoning | ToolSelected | ToolProgress | ToolResult | FinalAnswer | StreamError
