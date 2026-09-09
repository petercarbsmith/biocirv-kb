"""Model-spec parsing and the per-role resolution built on top of it.

The backward-compatibility cases matter most: a configuration written before
providers existed names a bare model id, and must keep resolving to the
default provider without any edit.
"""

from __future__ import annotations

import pytest

from sci_rag.config import Settings
from sci_rag.llm.spec import ModelSpec, UnknownProviderError, parse_model_spec


def test_bare_model_takes_the_default_provider() -> None:
    spec = parse_model_spec("gemini-2.5-flash", default_provider="google")
    assert spec == ModelSpec("google", "gemini-2.5-flash")
    assert str(spec) == "google:gemini-2.5-flash"


def test_inline_provider_overrides_the_default() -> None:
    spec = parse_model_spec("anthropic:claude-opus-5", default_provider="google")
    assert spec == ModelSpec("anthropic", "claude-opus-5")


@pytest.mark.parametrize(
    "raw",
    [
        # Vertex partner models carry a publisher prefix ...
        "openai-compatible:xai/grok-4.1-fast-reasoning",
        "openai-compatible:meta/llama-3.2-90b-vision-instruct-maas",
    ],
)
def test_publisher_prefixes_survive_parsing(raw: str) -> None:
    spec = parse_model_spec(raw, default_provider="google")
    assert spec.provider == "openai-compatible"
    assert spec.model == raw.split(":", 1)[1]


def test_dated_anthropic_snapshot_keeps_its_at_suffix() -> None:
    # ... and Anthropic snapshots use "@", so neither collides with ":".
    spec = parse_model_spec("anthropic:claude-opus-4-5@20251101", default_provider="google")
    assert spec.model == "claude-opus-4-5@20251101"


def test_unknown_provider_names_the_valid_ones() -> None:
    # Guessing that this is a model id would surface later as a confusing 404.
    with pytest.raises(UnknownProviderError, match="openai-compatible"):
        parse_model_spec("bedrock:claude-opus-5", default_provider="google")


@pytest.mark.parametrize("raw", ["", "   ", "anthropic:"])
def test_malformed_specs_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_model_spec(raw, default_provider="google")


def test_roles_fall_back_to_the_generation_model() -> None:
    settings = Settings(llm_model="gemini-2.5-flash")
    assert str(settings.model_spec_for("answer")) == "google:gemini-2.5-flash"
    assert str(settings.model_spec_for("extraction")) == "google:gemini-2.5-flash"
    assert str(settings.model_spec_for("judge")) == "google:gemini-2.5-flash"


def test_roles_resolve_independently_across_providers() -> None:
    settings = Settings(
        llm_model="anthropic:claude-opus-5",
        extraction_model="gemini-2.5-flash",
        judge_model="openai-compatible:xai/grok-4.1-fast-reasoning",
    )
    assert settings.model_spec_for("answer").provider == "anthropic"
    # Bare id, so it lands on llm_provider rather than the answer provider.
    assert settings.model_spec_for("extraction") == ModelSpec("google", "gemini-2.5-flash")
    assert settings.model_spec_for("judge").model == "xai/grok-4.1-fast-reasoning"


def test_default_provider_applies_to_every_bare_role() -> None:
    settings = Settings(llm_provider="anthropic", llm_model="claude-opus-5")
    assert settings.model_spec_for("answer") == ModelSpec("anthropic", "claude-opus-5")
    assert settings.model_spec_for("judge") == ModelSpec("anthropic", "claude-opus-5")
