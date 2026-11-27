import pytest
from app.agent import llm
from app.config import Settings


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_defaults_point_to_deepseek() -> None:
    settings = Settings(_env_file=None)

    assert settings.llm_model == "deepseek-chat"
    assert settings.llm_base_url == "https://api.deepseek.com/v1"
    assert settings.llm_api_key == ""


def test_generator_reads_llm_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch,
        LLM_API_KEY="test-key",
        LLM_MODEL="deepseek-reasoner",
        LLM_BASE_URL="https://llm.example.com/v1",
    )

    generator = llm.get_generator(settings)

    assert isinstance(generator, llm.DeepSeekRationaleGenerator)
    assert generator.api_key == "test-key"
    assert generator.model == "deepseek-reasoner"
    assert generator.base_url == "https://llm.example.com/v1"


def test_generator_falls_back_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    settings = Settings(_env_file=None)

    assert isinstance(llm.get_generator(settings), llm.TemplateGenerator)


def test_generator_treats_placeholder_key_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch, LLM_API_KEY="sk-your-api-key-here")

    assert isinstance(llm.get_generator(settings), llm.TemplateGenerator)


def test_generator_reads_process_env_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "env-only-key")

    generator = llm.get_generator()

    assert isinstance(generator, llm.DeepSeekRationaleGenerator)
    assert generator.api_key == "env-only-key"
