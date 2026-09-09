"""Does the model we ship as the default actually answer?

This is the check that was missing. `gemini-2.5-flash` shipped as the
default in v0.4.0 and is refused for any newly issued AI Studio key with
`404 This model is no longer available to new users`. Nothing offline can
see that: the id is well formed, the credential is valid, and the failure
only exists at the endpoint.

Run it with::

    SCI_RAG_RUN_CLOUD_TESTS=1 \
    SCI_RAG_GOOGLE_API_KEY=<key> \
      uv run pytest tests/cloud/test_default_model_live.py

or against Vertex by exporting ``SCI_RAG_GCP_PROJECT`` and
``SCI_RAG_GCP_LOCATION=global`` instead.

**Prefer a freshly issued key.** The retirement is scoped to new users, so
an older key answers for models a new reader cannot reach, and that is the
whole reason this went unnoticed. A pass here on a long-lived key says less
than it appears to.
"""

from __future__ import annotations

import json
import os

import pytest

from sci_rag.config import DEFAULT_LLM_MODEL, Settings
from sci_rag.llm import get_llm

pytestmark = [
    pytest.mark.cloud,
    pytest.mark.skipif(
        os.environ.get("SCI_RAG_RUN_CLOUD_TESTS") != "1",
        reason="set SCI_RAG_RUN_CLOUD_TESTS=1 for live API checks",
    ),
]


def _settings() -> Settings:
    settings = Settings(llm_model=DEFAULT_LLM_MODEL)
    if settings.credentials_mode() == "none":
        pytest.skip("no Google credentials configured")
    return settings


async def test_the_shipped_default_model_answers() -> None:
    """At the budget the kit actually uses, not a token-starved one.

    `gemini-3.x` spends output tokens on reasoning before it emits anything,
    so a budget under roughly 64 returns an empty string rather than an
    error. Every call site in the kit passes 512 or more, so that floor is
    not reachable in practice, and testing below it would assert a condition
    no caller creates.
    """
    reply = await get_llm(_settings()).generate("Reply with exactly: ready", max_tokens=512)

    assert reply.strip()


async def test_the_shipped_default_model_returns_strict_json() -> None:
    """Structured output, not just prose.

    Graph extraction, HyDE, community summaries, and the judge all ask this
    model for JSON. A default that answers in prose but cannot hold a schema
    would pass the test above and still take out most of the pipeline.
    """
    reply = await get_llm(_settings()).generate(
        'Return only this JSON object and nothing else: {"status": "ok"}',
        json_mode=True,
        max_tokens=512,
    )

    assert json.loads(reply)["status"] == "ok"
