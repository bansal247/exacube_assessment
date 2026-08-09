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
    anthropic_api_key: str
    agent_model: str = "claude-3-5-sonnet-20241022"
    agent_max_tool_retries: int = 2


settings = Settings()
