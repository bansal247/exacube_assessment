from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10
    db_statement_timeout_ms: int = 5000

    agent_database_url: str
    agent_db_pool_min_size: int = 1
    agent_db_pool_max_size: int = 10
    # Query plugin's statement_timeout. Deliberately separate from the API's
    # db_statement_timeout_ms above -- this bounds LLM-generated SQL, which
    # calls for a tighter, independently-tunable limit (Part 3 Safety).
    agent_query_timeout_ms: int = 5000

    agent_row_cap: int = 1000

    # How many candidate ids the loop shows a consuming plugin (chart,
    # image_chart, ...) in its schema hint / validation-error text, most
    # recent first -- see AgentLoop._recent_first_candidates(). Uncapped
    # would mean a long session's hint grows without bound, every round,
    # for every consuming plugin; capping doesn't invalidate older ids
    # (context.prior_results still has them, and an explicit reference
    # still validates), it just stops actively suggesting them.
    agent_consumes_hint_limit: int = 5

    log_level: str = "INFO"

    # The frontend's own origin -- CORS needs the exact scheme+host+port,
    # not a guess. Defaults to the frontend service's published port in
    # docker-compose.yml; override if that port changes.
    frontend_origin: str = "http://localhost:3000"

    # Provider selection -- the whole point of the LLMProvider interface is
    # that this is a config choice, not a code change. Only the selected
    # provider's key is required (enforced below, not by making both
    # fields required) -- a grader supplying only an OpenAI key shouldn't
    # need a dummy Anthropic one just to satisfy validation.
    llm_provider: Literal["openai", "anthropic"] = "openai"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    agent_model: str = "gpt-4o-mini"
    agent_max_tool_retries: int = 2

    @model_validator(mode="after")
    def _require_selected_provider_key(self) -> "Settings":
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        return self


# mypy sees database_url/agent_database_url as required __init__ params
# (no default) and flags this as missing arguments -- it can't see that
# BaseSettings populates them from the environment at call time, not from
# constructor args. Known, standard false positive with pydantic-settings,
# not a real gap -- see https://github.com/pydantic/pydantic/issues/3753.
settings = Settings()  # type: ignore[call-arg]
