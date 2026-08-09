"""LLM provider abstraction. The agent loop only ever talks to this
interface -- swapping providers means writing a new class here, nothing
else in the agent changes. Anthropic is the only implementation for now;
the interface exists to keep that a choice, not a hard dependency.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.agent.messages import AssistantTurn, Message


@dataclass
class ToolSchema:
    name: str
    description: str
    input_schema: dict


@dataclass
class TextDelta:
    """An incremental chunk of prose as the model generates it -- the
    "reasoning" stage the Streaming section asks to surface live, not just
    the final answer typed out after the fact.
    """

    text: str


@dataclass
class TurnComplete:
    """Terminal event for one generate_stream() call: the same AssistantTurn
    the non-streaming generate() would have returned, once the provider has
    finished this turn (Anthropic's own get_final_message(), not something
    reconstructed by hand from deltas -- avoids drift between the streamed
    text and the authoritative final content).
    """

    turn: AssistantTurn


ProviderStreamEvent = TextDelta | TurnComplete


class LLMProvider(ABC):
    @abstractmethod
    async def generate(
        self, system: str, messages: list[Message], tools: list[ToolSchema]
    ) -> AssistantTurn:
        """One provider call. `tools=[]` means "answer in prose only, no
        tool use" -- how the loop enforces graceful surrender once the retry
        budget is exhausted.
        """
        ...

    @abstractmethod
    def generate_stream(
        self, system: str, messages: list[Message], tools: list[ToolSchema]
    ) -> AsyncIterator[ProviderStreamEvent]:
        """Same call, incremental: TextDelta events as prose is generated,
        followed by exactly one terminal TurnComplete. A separate method
        from generate() (not generate() always streaming) so the existing
        non-streaming loop path is untouched by this addition.
        """
        ...
