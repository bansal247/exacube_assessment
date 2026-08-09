"""Settings' conditional provider-key requirement: only the selected
LLM_PROVIDER's key must be present, not both.
"""

import pytest
from pydantic import ValidationError

from app.config import Settings

_BASE_KWARGS = dict(
    database_url="postgresql://x@localhost/x",
    agent_database_url="postgresql://x@localhost/x",
)


def test_openai_provider_requires_openai_key():
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        Settings(**_BASE_KWARGS, llm_provider="openai", openai_api_key=None)


def test_openai_provider_with_key_is_valid():
    settings = Settings(**_BASE_KWARGS, llm_provider="openai", openai_api_key="sk-test")
    assert settings.llm_provider == "openai"


def test_anthropic_provider_requires_anthropic_key():
    with pytest.raises(ValidationError, match="ANTHROPIC_API_KEY"):
        Settings(**_BASE_KWARGS, llm_provider="anthropic", anthropic_api_key=None)


def test_anthropic_provider_with_key_is_valid():
    settings = Settings(**_BASE_KWARGS, llm_provider="anthropic", anthropic_api_key="sk-ant-test")
    assert settings.llm_provider == "anthropic"


def test_anthropic_key_not_required_when_openai_selected():
    # The other provider's key being absent must not block startup.
    settings = Settings(**_BASE_KWARGS, llm_provider="openai", openai_api_key="sk-test", anthropic_api_key=None)
    assert settings.anthropic_api_key is None
