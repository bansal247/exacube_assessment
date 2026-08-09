from app.agent.messages import AssistantTurn, Message
from app.agent.provider import LLMProvider, ToolSchema


class ScriptedProvider(LLMProvider):
    """Returns a pre-scripted sequence of AssistantTurns, one per call."""

    def __init__(self, turns: list[AssistantTurn]):
        self._turns = list(turns)
        self.calls: list[tuple[list[Message], list[ToolSchema]]] = []

    async def generate(self, system, messages, tools):
        self.calls.append((list(messages), list(tools)))
        return self._turns.pop(0)
