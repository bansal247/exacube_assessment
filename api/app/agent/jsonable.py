from datetime import date, datetime
from decimal import Decimal
from typing import Any


def to_jsonable(value: Any) -> Any:
    """asyncpg returns native Python types (datetime, Decimal, etc) that
    json.dumps can't serialize directly. Recursively converts a Record/dict/
    list tree into something json.dumps-safe, for tool results sent to the
    LLM and persisted to chat_messages.content.
    """
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value
