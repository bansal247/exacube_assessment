"""15+ eval questions spanning the categories the brief asks for. Each
question's expected_tools is checked as an ordered subsequence of the
agent's actual tool calls (extra calls in between are tolerated; missing or
out-of-order ones fail) -- routing scoring. reference_sql, where present,
is run directly against the DB and compared to the agent's own `query` tool
call result -- correctness scoring. reference_sql=None means "not
automatically scored" (ambiguous-phrasing questions, where the brief itself
says the agent must pick and state its own definition -- there's no single
correct reference query for "which channels are dying").

expected_columns, where present, is the reference_sql's own list of column
aliases -- the single source of truth for what a "correct" result looks
like shape-wise. It's used two ways: (1) the eval harness appends a plain-
language instruction asking the agent to alias its own SQL result columns
the same way, so scoring isn't at the mercy of whichever alias the model
happens to pick this run (`n` vs `count` vs `total_servers` for the same
COUNT(*)); (2) scoring then projects both the reference result and the
agent's result onto exactly these columns and compares values directly,
instead of comparing whole row shapes. An agent that ignores the
instruction and aliases differently anyway will now correctly fail --
that's a real routing/instruction-following miss, not a scoring artifact.
"""

from dataclasses import dataclass, field


@dataclass
class EvalQuestion:
    id: str
    category: str
    question: str
    expected_tools: list[str]
    reference_sql: str | None = None
    expected_columns: list[str] = field(default_factory=list)
    notes: str = ""


QUESTIONS: list[EvalQuestion] = [
    # -- simple lookups --
    EvalQuestion(
        id="simple_1",
        category="simple_lookup",
        question="How many servers are in this dataset in total?",
        expected_tools=["query"],
        reference_sql="SELECT COUNT(*) AS n FROM servers",
        expected_columns=["n"],
    ),
    EvalQuestion(
        id="simple_2",
        category="simple_lookup",
        question="How many channels are there across all servers combined?",
        expected_tools=["query"],
        reference_sql="SELECT COUNT(*) AS n FROM channels",
        expected_columns=["n"],
    ),
    EvalQuestion(
        id="simple_3",
        category="simple_lookup",
        question="What region is the server with id 'server_001' in?",
        expected_tools=["query"],
        reference_sql="SELECT region FROM servers WHERE server_id = 'server_001'",
        expected_columns=["region"],
    ),
    # -- time-series aggregates --
    EvalQuestion(
        id="timeseries_1",
        category="time_series",
        question="Across all servers, which single day had the most total messages sent?",
        expected_tools=["query"],
        reference_sql=(
            "SELECT date, SUM(total_messages) AS messages FROM daily_stats "
            "GROUP BY date ORDER BY messages DESC LIMIT 1"
        ),
        expected_columns=["date", "messages"],
    ),
    EvalQuestion(
        id="timeseries_2",
        category="time_series",
        question="What is the average number of new members joining per day, across all servers?",
        expected_tools=["query"],
        reference_sql="SELECT AVG(new_members) AS avg_new_members FROM daily_stats",
        expected_columns=["avg_new_members"],
    ),
    EvalQuestion(
        id="timeseries_3",
        category="time_series",
        question="Compare average daily message volume on weekdays vs weekends.",
        expected_tools=["query"],
        reference_sql=(
            "SELECT is_weekend, AVG(total_messages) AS avg_messages FROM daily_stats GROUP BY is_weekend"
        ),
        expected_columns=["is_weekend", "avg_messages"],
    ),
    # -- ambiguous phrasing (brief: agent must pick a definition and state it) --
    EvalQuestion(
        id="ambiguous_1",
        category="ambiguous",
        question="Which channels are dying?",
        expected_tools=["query"],
        reference_sql=None,
        notes="No single correct reference query -- the agent must state its own definition of 'dying'. Routing-scored only.",
    ),
    EvalQuestion(
        id="ambiguous_2",
        category="ambiguous",
        question="Who are the most engaged users?",
        expected_tools=["query"],
        reference_sql=None,
        notes="Same as ambiguous_1 -- 'engaged' is undefined by the question. Routing-scored only.",
    ),
    # -- requires a chart --
    EvalQuestion(
        id="chart_1",
        category="chart",
        question="Chart the total messages per day for server_001 from daily_stats.",
        expected_tools=["query", "chart"],
        reference_sql="SELECT date, total_messages FROM daily_stats WHERE server_id = 'server_001' ORDER BY date",
        expected_columns=["date", "total_messages"],
    ),
    EvalQuestion(
        id="chart_2",
        category="chart",
        question="Show me a bar chart of the top 5 servers by approximate member count.",
        expected_tools=["query", "chart"],
        reference_sql=(
            "SELECT server_id, approximate_member_count FROM servers "
            "ORDER BY approximate_member_count DESC LIMIT 5"
        ),
        expected_columns=["server_id", "approximate_member_count"],
    ),
    # -- requires a file (no excel/powerpoint this pass -- query's CSV export
    # via to_file() is the only file-producing capability that exists; see
    # README eval section for why this category is thin by construction) --
    EvalQuestion(
        id="file_1",
        category="file",
        question="Give me the full list of channels in server_001 so I can export it.",
        expected_tools=["query"],
        reference_sql="SELECT * FROM channels WHERE server_id = 'server_001'",
        expected_columns=[
            "channel_id", "server_id", "channel_name", "channel_type", "topic", "nsfw", "rate_limit_per_user",
            "position",
        ],
        notes="Scored on query correctness. The actual CSV export is a separate action (pin then GET /pins/{id}/download), not part of the chat turn itself.",
    ),
    EvalQuestion(
        id="file_2",
        category="file",
        question="Export the member list for server_002.",
        expected_tools=["query"],
        reference_sql="SELECT * FROM members WHERE server_id = 'server_002'",
        expected_columns=[
            "user_id", "server_id", "username", "display_name", "discriminator", "avatar_hash", "is_bot",
            "join_date", "last_active", "roles", "messages_sent", "voice_minutes", "is_owner",
        ],
        notes="Same caveat as file_1.",
    ),
    # -- multi-tool chain (explicit two-step instruction in one turn) --
    EvalQuestion(
        id="chain_1",
        category="chain",
        question="Look up total messages per channel in server_001, then chart it as a bar chart.",
        expected_tools=["query", "chart"],
        reference_sql=(
            "SELECT channel_id, SUM(message_count) AS total FROM channel_daily_stats "
            "WHERE server_id = 'server_001' GROUP BY channel_id"
        ),
        expected_columns=["channel_id", "total"],
    ),
    # -- not answerable from this dataset (at least 3 required) --
    EvalQuestion(
        id="unanswerable_1",
        category="unanswerable",
        question="What's the weather like in Tokyo today?",
        expected_tools=[],
    ),
    EvalQuestion(
        id="unanswerable_2",
        category="unanswerable",
        question="Delete all messages posted by the most active user.",
        expected_tools=[],
        notes="Tests both decline AND that the agent doesn't attempt a write it structurally can't perform.",
    ),
    EvalQuestion(
        id="unanswerable_3",
        category="unanswerable",
        question="What's your favorite color?",
        expected_tools=[],
    ),
    EvalQuestion(
        id="unanswerable_4",
        category="unanswerable",
        question="Send an email summary of server activity to the server owner.",
        expected_tools=[],
        notes="No email/notification tool exists -- correct answer is decline, not a query that ignores the email part.",
    ),
]
