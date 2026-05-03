import pytest

from pishkar.runtime import _default_model_for_env, _litellm_name

_PROVIDER_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "MOONSHOT_API_KEY",
    "DASHSCOPE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "PISHKAR_MODEL",
)


@pytest.fixture(autouse=True)
def clean_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in _PROVIDER_KEYS:
        monkeypatch.delenv(k, raising=False)


@pytest.mark.parametrize(
    "env_key,expected_model",
    [
        ("ANTHROPIC_API_KEY", "claude-opus-4-7"),
        ("OPENAI_API_KEY", "gpt-4o-mini"),
        ("OPENROUTER_API_KEY", "openrouter/anthropic/claude-3.5-sonnet"),
        ("GROQ_API_KEY", "groq/llama-3.3-70b-versatile"),
        ("MOONSHOT_API_KEY", "moonshot/moonshot-v1-8k"),
        ("DASHSCOPE_API_KEY", "dashscope/qwen-turbo"),
        ("GEMINI_API_KEY", "gemini-3-flash-preview"),
        ("GOOGLE_API_KEY", "gemini-3-flash-preview"),
    ],
)
def test_default_model_picks_per_env_key(
    monkeypatch: pytest.MonkeyPatch, env_key: str, expected_model: str
) -> None:
    monkeypatch.setenv(env_key, "x")
    assert _default_model_for_env() == expected_model


def test_default_model_priority_anthropic_beats_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert _default_model_for_env() == "claude-opus-4-7"


def test_default_model_falls_back_to_anthropic_when_no_key() -> None:
    assert _default_model_for_env() == "claude-opus-4-7"


@pytest.mark.parametrize(
    "model,expected",
    [
        # Bare names → prefixed.
        ("claude-opus-4-7", "anthropic/claude-opus-4-7"),
        ("gpt-4o-mini", "openai/gpt-4o-mini"),
        ("o3-mini", "openai/o3-mini"),
        ("gemini-3-flash-preview", "gemini/gemini-3-flash-preview"),
        ("llama-3.3-70b-versatile", "groq/llama-3.3-70b-versatile"),
        ("moonshot-v1-8k", "moonshot/moonshot-v1-8k"),
        ("kimi-k2", "moonshot/kimi-k2"),
        ("qwen-turbo", "dashscope/qwen-turbo"),
        # Already prefixed → untouched.
        ("anthropic/claude-opus-4-7", "anthropic/claude-opus-4-7"),
        ("openrouter/qwen/qwen-2.5-72b-instruct", "openrouter/qwen/qwen-2.5-72b-instruct"),
        ("groq/llama-3.3-70b-versatile", "groq/llama-3.3-70b-versatile"),
        ("moonshot/moonshot-v1-8k", "moonshot/moonshot-v1-8k"),
        ("dashscope/qwen-turbo", "dashscope/qwen-turbo"),
        ("gemini/gemini-3-flash-preview", "gemini/gemini-3-flash-preview"),
    ],
)
def test_litellm_name_normalization(model: str, expected: str) -> None:
    assert _litellm_name(model) == expected


# --- ModelSelector + discover_available_models ----------------------------


from pishkar.runtime import (  # noqa: E402
    KNOWN_MODELS_BY_PROVIDER,
    ModelSelector,
    discover_available_models,
    provider_for_model,
)


def test_discover_picks_only_providers_with_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for k, _, _ in [
        ("ANTHROPIC_API_KEY", "", ""),
        ("OPENAI_API_KEY", "", ""),
        ("GROQ_API_KEY", "", ""),
        ("GEMINI_API_KEY", "", ""),
        ("GOOGLE_API_KEY", "", ""),
        ("OPENROUTER_API_KEY", "", ""),
        ("MOONSHOT_API_KEY", "", ""),
        ("DASHSCOPE_API_KEY", "", ""),
    ]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "x")
    available = discover_available_models()
    assert "groq" in available
    assert "anthropic" not in available
    assert available["groq"] == list(KNOWN_MODELS_BY_PROVIDER["groq"])


def test_provider_for_model_reverse_lookup() -> None:
    assert provider_for_model("claude-opus-4-7") == "anthropic"
    assert provider_for_model("groq/llama-3.3-70b-versatile") == "groq"
    assert provider_for_model("not-a-real-model") is None


def test_selector_set_model_validates_against_catalog() -> None:
    sel = ModelSelector(
        default="claude-opus-4-7",
        available={"anthropic": ["claude-opus-4-7", "claude-haiku-4-5"]},
    )
    assert sel.current() == "claude-opus-4-7"
    assert sel.set_model("claude-haiku-4-5") is True
    assert sel.current() == "claude-haiku-4-5"
    assert sel.set_model("groq/llama-3.3-70b-versatile") is False
    assert sel.current() == "claude-haiku-4-5"  # unchanged


def test_selector_reset_returns_to_default() -> None:
    sel = ModelSelector(
        default="claude-opus-4-7",
        available={"anthropic": ["claude-opus-4-7", "claude-haiku-4-5"]},
    )
    sel.set_model("claude-haiku-4-5")
    sel.reset()
    assert sel.current() == "claude-opus-4-7"
