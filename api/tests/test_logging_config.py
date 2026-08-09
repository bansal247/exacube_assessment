import json
import logging

from app.logging_config import JsonFormatter, trace_id_var


def _format(record: logging.LogRecord) -> dict:
    return json.loads(JsonFormatter().format(record))


def _make_record(msg="hello", extra=None, level=logging.INFO) -> logging.LogRecord:
    record = logging.LogRecord(
        name="app.test", level=level, pathname=__file__, lineno=1, msg=msg, args=(), exc_info=None
    )
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


def test_basic_fields_present():
    payload = _format(_make_record("hello"))
    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert "timestamp" in payload


def test_extra_fields_are_surfaced():
    payload = _format(_make_record("tool selected", extra={"tool_call_id": "call_1", "tool_name": "query"}))
    assert payload["tool_call_id"] == "call_1"
    assert payload["tool_name"] == "query"


def test_trace_id_included_when_set():
    token = trace_id_var.set("trace-abc")
    try:
        payload = _format(_make_record("hi"))
    finally:
        trace_id_var.reset(token)
    assert payload["trace_id"] == "trace-abc"


def test_trace_id_absent_when_not_set():
    payload = _format(_make_record("hi"))
    assert "trace_id" not in payload


def test_output_is_one_json_object_per_line_no_newlines_inside():
    payload_str = JsonFormatter().format(_make_record("hi"))
    assert "\n" not in payload_str
    json.loads(payload_str)  # doesn't raise
