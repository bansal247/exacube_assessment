"""Eval harness: sends every question in questions.py to a live, running
/chat endpoint (real LLM calls -- this costs real tokens), scores routing
(did it call the right tools, in order) and correctness, and reports
latency/token cost per turn.

Correctness scoring compares the agent's own `query` result against a
hand-written reference query -- not string matching on the prose reply.
For scorable questions, the harness appends a plain-language instruction
asking the agent to alias its SQL result columns to match the reference
query's own aliases (see questions.py's expected_columns), then compares
only those columns' values -- this is what makes scoring resilient to
`COUNT(*) AS n` vs `AS count` vs `AS total_servers` all being equally
correct SQL, without also silently accepting an agent that ignores the
instruction and returns the wrong numbers.

Run via `make eval` (assumes `make up` is already running with a real
ANTHROPIC_API_KEY set). Writes a timestamped JSON file to eval/results/ and
prints a summary to stdout.
"""

import asyncio
import json
import os
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as time_, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from questions import QUESTIONS, EvalQuestion  # noqa: E402

API_BASE_URL = os.environ.get("EVAL_API_BASE_URL", "http://api:8000")
# Seconds to sleep between questions -- 17 questions fired back-to-back can
# trip a provider's tokens-per-minute limit well before the run finishes
# (seen directly: an openai.RateLimitError surfacing as a bare 500 mid-run).
# A fixed delay, not a real backoff/retry -- this is eval-only pacing, not
# a substitute for the retry handling the app itself still doesn't have.
EVAL_QUESTION_DELAY_SECONDS = float(os.environ.get("EVAL_QUESTION_DELAY_SECONDS", "3"))
DATABASE_URL = os.environ.get(
    "EVAL_DATABASE_URL",
    f"postgresql://{os.environ.get('POSTGRES_USER', 'discord')}:"
    f"{os.environ.get('POSTGRES_PASSWORD', 'discord')}@db:5432/"
    f"{os.environ.get('POSTGRES_DB', 'discord_analytics')}",
)

# Published per-token pricing for the default model (AGENT_MODEL), as of
# this session -- NOT fetched live, NOT necessarily current by the time
# this runs. Update to match whatever model is actually configured. Cost
# figures in this report are an estimate against this rate, not a billing
# record.
PRICE_PER_MILLION_INPUT_TOKENS = 3.00
PRICE_PER_MILLION_OUTPUT_TOKENS = 15.00


@dataclass
class QuestionResult:
    id: str
    category: str
    question: str
    expected_tools: list[str]
    actual_tools: list[str]
    routing_pass: bool
    correctness: str  # "pass" | "fail" | "not_scored"
    reply: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    error: str | None = None
    # Only populated when reference_sql is set -- lets a human see *why*
    # correctness failed (genuinely wrong data vs. e.g. a column-alias
    # mismatch against the reference query) instead of just a verdict.
    actual_sql: str | None = None
    expected_rows: list[dict] | None = None
    actual_rows: list[dict] | None = None


def _routing_pass(expected: list[str], actual: list[str]) -> bool:
    if not expected:
        return actual == []
    it = iter(actual)
    return all(name in it for name in expected)


def _normalize_value(v: Any) -> Any:
    """Makes a value comparable across two very different sources: raw
    asyncpg types (Decimal, date/datetime) for expected_rows vs.
    already-JSON-decoded types (str, float, int) for actual_rows -- the
    agent's own query result, run through the API's to_jsonable. Column
    *names* are handled separately (see _row_value_set); this only
    normalizes the values themselves so e.g. Decimal('216.6000...') and
    float(216.6), or datetime(...) and "2026-03-08T00:00:00+00:00", compare
    equal instead of failing on representation alone.
    """
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, Decimal):
        return round(float(v), 2)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        # A bare date (not datetime) -- promoted to midnight before
        # isoformat() so it lands on the exact same "...T00:00:00" string
        # datetime.fromisoformat() below produces when the *other* side of
        # the comparison is a JSON-decoded "2026-03-08" string. Without
        # this, date(2026,3,8).isoformat() == "2026-03-08" (no time
        # component at all) never matches its own string counterpart.
        return datetime.combine(v, datetime.min.time()).isoformat()
    if isinstance(v, time_):
        return v.isoformat()
    if isinstance(v, float):
        return round(v, 2)
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip()
        try:
            # Accepts both "...T..." and "... ..." separators, "Z" or
            # "+00:00" offsets -- the two formats seen from asyncpg's own
            # str() vs the API's isoformat() serialization.
            return datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()
        except ValueError:
            pass
        try:
            return round(float(s), 2)
        except ValueError:
            pass
        return s
    if isinstance(v, (list, tuple)):
        return tuple(sorted(_normalize_value(x) for x in v))
    if isinstance(v, dict):
        return tuple(sorted((k, _normalize_value(x)) for k, x in v.items()))
    return v


def _format_instruction(expected_columns: list[str]) -> str:
    """Appended to the question actually sent to the agent -- asks it to
    alias its own SQL result columns to match the reference query's, so
    scoring isn't at the mercy of whichever alias the model happens to pick
    this run. Deliberately plain language, not a schema/tool-level
    constraint -- the agent still writes its own SQL freely, this only
    pins down what it calls the columns in the result.
    """
    columns = ", ".join(expected_columns)
    return f"\n\n(When you run SQL to answer this, alias the result columns exactly as: {columns}.)"


def _project(row: dict, expected_columns: list[str]) -> tuple:
    """Projects a row onto exactly expected_columns, normalizing each
    value. A column the agent didn't select/alias as instructed reads as
    None here -- a real miss (it didn't follow the format instruction),
    not a scoring artifact.
    """
    return tuple(_normalize_value(row.get(c)) for c in expected_columns)


def _rows_equivalent(expected: list[dict], actual: list[dict], expected_columns: list[str]) -> bool:
    """Compares only expected_columns' values, ignoring any extra columns
    either side has -- the agent is free to select additional columns (e.g.
    server_name alongside server_id for a chart label) as long as the
    instructed columns carry the right values. Row order doesn't matter
    (multiset equality via Counter), but which row a value belongs to
    does -- two rows can't "average out" to look right.
    """
    if len(expected) != len(actual):
        return False
    expected_rows = Counter(_project(r, expected_columns) for r in expected)
    actual_rows = Counter(_project(r, expected_columns) for r in actual)
    if expected_rows != actual_rows:
        print(f"Expected rows (projected to {expected_columns}): {expected_rows}")
        print(f"Actual rows (projected to {expected_columns}): {actual_rows}")
    return expected_rows == actual_rows


async def _run_reference_query(pool: asyncpg.Pool, sql: str) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql)
    return [dict(r) for r in rows]


def _last_query_result(tool_calls: list[dict]) -> tuple[str | None, list[dict] | None]:
    for tc in reversed(tool_calls):
        if tc["name"] == "query" and not tc["is_error"] and isinstance(tc["result"], dict):
            rows = tc["result"].get("rows")
            if rows is not None:
                return tc["result"].get("sql"), rows
    return None, None


async def _ask(client: httpx.AsyncClient, question: str) -> dict[str, Any]:
    resp = await client.post("/chat", json={"message": question}, timeout=120)
    resp.raise_for_status()
    return resp.json()


async def _evaluate_one(client: httpx.AsyncClient, pool: asyncpg.Pool, q: EvalQuestion) -> QuestionResult:
    question_text = q.question + (_format_instruction(q.expected_columns) if q.expected_columns else "")
    try:
        body = await _ask(client, question_text)
    except Exception as exc:  # noqa: BLE001 -- a failed HTTP call is still a result to report
        return QuestionResult(
            id=q.id,
            category=q.category,
            question=q.question,
            expected_tools=q.expected_tools,
            actual_tools=[],
            routing_pass=False,
            correctness="fail",
            reply="",
            latency_ms=0,
            input_tokens=0,
            output_tokens=0,
            error=str(exc),
        )

    actual_tools = [tc["name"] for tc in body["tool_calls"]]
    routing_pass = _routing_pass(q.expected_tools, actual_tools)

    correctness = "not_scored"
    actual_sql: str | None = None
    expected_rows: list[dict] | None = None
    actual_rows: list[dict] | None = None
    if q.reference_sql is not None:
        expected_rows = await _run_reference_query(pool, q.reference_sql)
        actual_sql, actual_rows = _last_query_result(body["tool_calls"])
        correctness = (
            "pass" if actual_rows is not None and _rows_equivalent(expected_rows, actual_rows, q.expected_columns) else "fail"
        )
    elif not q.expected_tools:
        # Decline questions: correctness = did it actually decline (no
        # data-fetching tool call) and say something, not just go silent.
        correctness = "pass" if actual_tools == [] and len(body["reply"].strip()) > 0 else "fail"

    # Debug fields (the reference query's rows, the agent's own rows, the
    # agent's actual SQL) are only worth the JSON weight when something
    # needs explaining -- a clean pass on both axes has nothing to debug.
    if routing_pass and correctness == "pass":
        actual_sql = expected_rows = actual_rows = None

    return QuestionResult(
        id=q.id,
        category=q.category,
        question=q.question,
        expected_tools=q.expected_tools,
        actual_tools=actual_tools,
        routing_pass=routing_pass,
        correctness=correctness,
        reply=body["reply"],
        latency_ms=body["latency_ms"],
        input_tokens=body["input_tokens"],
        output_tokens=body["output_tokens"],
        actual_sql=actual_sql,
        expected_rows=expected_rows,
        actual_rows=actual_rows,
    )


async def main() -> None:
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    results: list[QuestionResult] = []
    started_at = time.monotonic()

    async with httpx.AsyncClient(base_url=API_BASE_URL) as client:
        for i, q in enumerate(QUESTIONS):
            if i > 0 and EVAL_QUESTION_DELAY_SECONDS > 0:
                await asyncio.sleep(EVAL_QUESTION_DELAY_SECONDS)
            print(f"[{q.id}] {q.question}")
            result = await _evaluate_one(client, pool, q)
            results.append(result)
            print(
                f"  routing={'PASS' if result.routing_pass else 'FAIL'} "
                f"correctness={result.correctness} latency={result.latency_ms:.0f}ms "
                f"tokens={result.input_tokens}in/{result.output_tokens}out"
            )
            if result.error:
                print(f"  ERROR: {result.error}")

    await pool.close()
    total_wall_time = time.monotonic() - started_at

    _report(results, total_wall_time)


def _report(results: list[QuestionResult], total_wall_time: float) -> None:
    n = len(results)
    routing_passed = sum(1 for r in results if r.routing_pass)
    scored = [r for r in results if r.correctness != "not_scored"]
    correctness_passed = sum(1 for r in scored if r.correctness == "pass")

    total_input = sum(r.input_tokens for r in results)
    total_output = sum(r.output_tokens for r in results)
    avg_latency = sum(r.latency_ms for r in results) / n if n else 0
    estimated_cost = (
        total_input / 1_000_000 * PRICE_PER_MILLION_INPUT_TOKENS
        + total_output / 1_000_000 * PRICE_PER_MILLION_OUTPUT_TOKENS
    )

    print("\n" + "=" * 60)
    print(f"Routing:     {routing_passed}/{n} ({routing_passed / n:.0%})" if n else "Routing: n/a")
    print(
        f"Correctness: {correctness_passed}/{len(scored)} scored "
        f"({correctness_passed / len(scored):.0%}), {n - len(scored)} not automatically scored"
        if scored
        else "Correctness: no scorable questions"
    )
    print(f"Avg latency: {avg_latency:.0f}ms  |  Total wall time: {total_wall_time:.1f}s")
    print(f"Tokens: {total_input} in / {total_output} out  |  Est. cost: ${estimated_cost:.4f}")

    by_category: dict[str, list[QuestionResult]] = {}
    for r in results:
        by_category.setdefault(r.category, []).append(r)
    print("\nBy category:")
    for category, cat_results in sorted(by_category.items()):
        cat_routing = sum(1 for r in cat_results if r.routing_pass)
        print(f"  {category}: routing {cat_routing}/{len(cat_results)}")
        for r in cat_results:
            if not r.routing_pass or r.correctness == "fail":
                print(f"    FAILED [{r.id}]: routing={r.routing_pass} correctness={r.correctness}")

    results_dir = Path(__file__).resolve().parent / "results"
    results_dir.mkdir(exist_ok=True)
    out_path = results_dir / f"results_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out_path.write_text(
        json.dumps(
            {
                "summary": {
                    "routing_score": routing_passed / n if n else None,
                    "correctness_score": correctness_passed / len(scored) if scored else None,
                    "questions_total": n,
                    "questions_correctness_scored": len(scored),
                    "avg_latency_ms": avg_latency,
                    "total_input_tokens": total_input,
                    "total_output_tokens": total_output,
                    "estimated_cost_usd": estimated_cost,
                },
                "results": [asdict(r) for r in results],
            },
            indent=2,
            # expected_rows comes straight from asyncpg (real date/datetime
            # objects); actual_rows already went through the API's own
            # to_jsonable. default=str covers the former the same way the
            # API's own JSON encoding does.
            default=str,
        )
    )
    print(f"\nFull results written to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
