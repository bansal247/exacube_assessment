import json

from openai import AsyncOpenAI

from app.agent.messages import AssistantTurn, Message, ToolCall
from app.agent.provider import LLMProvider, ToolSchema


class OpenAIProvider(LLMProvider):
    """Chat Completions API, not Assistants/Responses -- the stable,
    directly-comparable-to-Anthropic-Messages surface.
    """

    def __init__(self, api_key: str, model: str, max_tokens: int = 4096):
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    async def generate(self, system: str, messages: list[Message], tools: list[ToolSchema]) -> AssistantTurn:
        response = await self._client.chat.completions.create(**self._request_kwargs(system, messages, tools))
        choice = response.choices[0].message
        return AssistantTurn(
            text=choice.content,
            tool_calls=self._parse_tool_calls(choice.tool_calls),
            input_tokens=getattr(response.usage, "prompt_tokens", None),
            output_tokens=getattr(response.usage, "completion_tokens", None),
        )

    def _request_kwargs(self, system: str, messages: list[Message], tools: list[ToolSchema]) -> dict:
        kwargs: dict = dict(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "system", "content": system}, *self._to_openai_messages(messages)],
        )
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.input_schema},
                }
                for t in tools
            ]
        return kwargs

    @staticmethod
    def _parse_tool_calls(raw_tool_calls) -> list[ToolCall]:
        if not raw_tool_calls:
            return []
        return [
            ToolCall(id=tc.id, name=tc.function.name, arguments=json.loads(tc.function.arguments or "{}"))
            for tc in raw_tool_calls
        ]

    @staticmethod
    def _to_openai_messages(messages: list[Message]) -> list[dict]:
        # Unlike Anthropic, OpenAI has a native "tool" role -- no
        # tool-result-as-user-message translation needed here.
        result: list[dict] = []
        for m in messages:
            if m.role == "user":
                result.append({"role": "user", "content": m.content or ""})
            elif m.role == "assistant":
                msg: dict = {"role": "assistant", "content": m.content}
                if m.tool_calls:
                    msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                        }
                        for tc in m.tool_calls
                    ]
                result.append(msg)
            elif m.role == "tool":
                result.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or ""})
        return result
