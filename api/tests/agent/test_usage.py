from app.agent.messages import Message, sum_usage


def test_sum_usage_across_multiple_assistant_messages():
    messages = [
        Message(role="user", content="hi"),
        Message(role="assistant", content=None, input_tokens=100, output_tokens=20),
        Message(role="tool", content="result"),
        Message(role="assistant", content="done", input_tokens=150, output_tokens=30),
    ]
    assert sum_usage(messages) == (250, 50)


def test_sum_usage_ignores_none_values():
    messages = [Message(role="assistant", content="hi", input_tokens=None, output_tokens=None)]
    assert sum_usage(messages) == (0, 0)


def test_sum_usage_empty_list():
    assert sum_usage([]) == (0, 0)
