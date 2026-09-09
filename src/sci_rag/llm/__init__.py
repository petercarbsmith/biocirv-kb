"""Generation providers and the factory that selects one.

``get_llm`` is the only place a provider is chosen. Keeping the supported set
visible in a single ``if`` chain -- rather than behind a plug-in loader -- is
the same trade-off ``sci_rag.embed`` makes, and it means a reader can see
every backend the kit talks to without leaving this file.
"""

from sci_rag.config import LLMRole, Settings
from sci_rag.llm.client import (
    LLMClient,
    MockLLM,
    parse_json_loosely,
    retry_async,
)
from sci_rag.llm.google import GoogleLLM
from sci_rag.llm.spec import ModelSpec, UnknownProviderError, parse_model_spec


def get_llm(
    settings: Settings,
    *,
    role: LLMRole = "answer",
    model: str | None = None,
    api_key_override: str | None = None,
) -> LLMClient:
    """Build the client for a call site.

    ``role`` picks which configured model spec applies -- answers, high-volume
    extraction, or the evaluation judge. ``model`` overrides it with an
    explicit spec (``"model"`` or ``"provider:model"``), which is what the
    ``--judge-model`` flag passes.
    """
    spec = (
        parse_model_spec(model, default_provider=settings.llm_provider)
        if model
        else settings.model_spec_for(role)
    )

    client: LLMClient
    if spec.provider == "anthropic":
        # Imported lazily: these providers are optional extras, and offline
        # runs must never need their SDKs installed.
        from sci_rag.llm.anthropic import AnthropicLLM

        client = AnthropicLLM(settings, model=spec.model, api_key_override=api_key_override)
    elif spec.provider == "openai-compatible":
        from sci_rag.llm.openai_compat import OpenAICompatLLM

        client = OpenAICompatLLM(settings, model=spec.model, api_key_override=api_key_override)
    else:
        client = GoogleLLM(settings, model=spec.model, api_key_override=api_key_override)

    client.spec = spec
    return client


__all__ = [
    "GoogleLLM",
    "LLMClient",
    "MockLLM",
    "ModelSpec",
    "UnknownProviderError",
    "get_llm",
    "parse_json_loosely",
    "parse_model_spec",
    "retry_async",
]
