from anthropic import AsyncAnthropic

from app.agent.messages import AssistantTurn, Message, ToolCall
from app.agent.provider import LLMProvider, ToolSchema


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str, max_tokens: int = 4096):
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    async def generate(self, system: str, messages: list[Message], tools: list[ToolSchema]) -> AssistantTurn:
        response = await self._client.messages.create(**self._request_kwargs(system, messages, tools))
        return self._to_assistant_turn(response.content, response.usage)

    def _request_kwargs(self, system: str, messages: list[Message], tools: list[ToolSchema]) -> dict:
        kwargs: dict = dict(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=self._to_anthropic_messages(messages),
        )
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools
            ]
        return kwargs

    @staticmethod
    def _to_assistant_turn(content_blocks, usage=None) -> AssistantTurn:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in content_blocks:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))
        return AssistantTurn(
            text="\n".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )

    @staticmethod
    def _to_anthropic_messages(messages: list[Message]) -> list[dict]:
        # Anthropic has no "tool" role -- a tool result is a user-role
        # message containing a tool_result content block. This translation
        # is the only place that fact needs to be known.
        result: list[dict] = []
        for m in messages:
            if m.role == "user":
                result.append({"role": "user", "content": m.content or ""})
            elif m.role == "assistant":
                content: list[dict] = []
                if m.content:
                    content.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    content.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
                result.append({"role": "assistant", "content": content})
            elif m.role == "tool":
                result.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id,
                                "content": m.content or "",
                                "is_error": m.is_error,
                            }
                        ],
                    }
                )
        return result
