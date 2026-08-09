from app.agent.messages import AssistantTurn, Message, ToolCall
from app.agent.provider import LLMProvider, TextDelta, ToolSchema, TurnComplete


class ScriptedProvider(LLMProvider):
    """Returns a pre-scripted sequence of AssistantTurns, one per call.

    generate_stream() wraps the same script: by default it yields the turn's
    text (if any) as a single TextDelta followed by TurnComplete -- enough
    for streaming-path tests that don't care about the exact chunking. Pass
    text_deltas_by_index to script specific multi-chunk deltas for a given
    call when a test needs to assert on incremental behavior itself.
    """

    def __init__(self, turns: list[AssistantTurn], text_deltas_by_index: dict[int, list[str]] | None = None):
        self._turns = list(turns)
        self.calls: list[tuple[list[Message], list[ToolSchema]]] = []
        self._text_deltas_by_index = text_deltas_by_index or {}
        self._call_index = 0

    async def generate(self, system, messages, tools):
        self.calls.append((list(messages), list(tools)))
        return self._turns.pop(0)

    async def generate_stream(self, system, messages, tools):
        self.calls.append((list(messages), list(tools)))
        index = self._call_index
        self._call_index += 1
        turn = self._turns.pop(0)

        deltas = self._text_deltas_by_index.get(index)
        if deltas is not None:
            for chunk in deltas:
                yield TextDelta(text=chunk)
        elif turn.text:
            yield TextDelta(text=turn.text)

        yield TurnComplete(turn=turn)
