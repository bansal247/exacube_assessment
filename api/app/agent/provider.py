"""LLM provider abstraction. The agent loop only ever talks to this
interface -- swapping providers means writing a new class here, nothing
else in the agent changes. AnthropicProvider and OpenAIProvider are both
real implementations, selected at startup via LLM_PROVIDER -- proof this
interface actually swaps, not just an interface that could in theory.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.agent.messages import AssistantTurn, Message


@dataclass
class ToolSchema:
    name: str
    description: str
    input_schema: dict


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
