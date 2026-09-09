"""What the doctor says about credentials a project never asked for.

A project can run entirely on the deterministic local embedder, and the
wizard writes exactly that for ``credentials: offline``. Reporting FAIL for
the state a user deliberately selected trains them to ignore the diagnosis,
so these tests pin both directions: an offline project warns, and a project
that reached for a model and missed a credential still fails.

Every case builds ``Settings`` with the dotenv file switched off and the
credential and generation variables cleared, because a developer's exported
key or a local ``.env`` would otherwise decide the answer instead of the
argument under test.
"""

from __future__ import annotations

from typing import Any

import pytest

from sci_rag.cli.doctor import Check, _google_credential_check, _llm_credential_checks
from sci_rag.config import Settings

_CREDENTIAL_VARS = (
    "SCI_RAG_GOOGLE_API_KEY",
    "SCI_RAG_GCP_PROJECT",
    "SCI_RAG_ANTHROPIC_API_KEY",
    "SCI_RAG_OPENAI_API_KEY",
    "SCI_RAG_OPENAI_BASE_URL",
)
_GENERATION_VARS = (
    "SCI_RAG_LLM_PROVIDER",
    "SCI_RAG_LLM_MODEL",
    "SCI_RAG_EXTRACTION_MODEL",
    "SCI_RAG_JUDGE_MODEL",
)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _CREDENTIAL_VARS + _GENERATION_VARS:
        monkeypatch.delenv(name, raising=False)


def _offline(**overrides: Any) -> Settings:
    """A project with the local embedder and nothing else configured."""
    values: dict[str, Any] = {"embedding_provider": "local-hash", **overrides}
    return Settings(_env_file=None, **values)


def _by_name(checks: list[Check]) -> dict[str, Check]:
    return {check.name: check for check in checks}


# --- the predicate ----------------------------------------------------------


def test_local_hash_without_any_credential_is_offline() -> None:
    assert _offline().is_offline() is True


def test_a_google_embedder_is_not_offline() -> None:
    """Wanting semantic embeddings is cloud intent, credentials or not."""
    assert _offline(embedding_provider="google").is_offline() is False


@pytest.mark.parametrize(
    "credential",
    [
        {"google_api_key": "k"},
        {"gcp_project": "p"},
        {"anthropic_api_key": "k"},
        {"openai_api_key": "k"},
        {"openai_base_url": "http://localhost:8080/v1"},
    ],
)
def test_any_configured_credential_means_the_project_is_not_offline(
    credential: dict[str, str],
) -> None:
    """One credential says a model is in play, so a missing one is a real gap."""
    assert _offline(**credential).is_offline() is False


@pytest.mark.parametrize(
    "generation",
    [
        {"llm_model": "anthropic:claude-opus-5"},
        {"llm_model": "gemini-2.5-pro"},
        {"llm_provider": "anthropic"},
        {"extraction_model": "gemini-2.5-flash-lite"},
        {"judge_model": "openai-compatible:xai/grok-4.1-fast-reasoning"},
    ],
)
def test_naming_a_generation_model_means_the_project_is_not_offline(
    generation: dict[str, str],
) -> None:
    """Writing a model id is how a user says they intend to generate."""
    assert _offline(**generation).is_offline() is False


# --- the llm credential rows ------------------------------------------------


def test_offline_project_warns_instead_of_failing() -> None:
    checks = _llm_credential_checks(_offline())

    assert [check.status for check in checks] == ["warn"]
    row = checks[0]
    assert row.name == "llm credentials (google)"
    assert "answer" in row.detail
    assert "extraction" in row.detail
    assert "judge" in row.detail
    assert "SCI_RAG_GOOGLE_API_KEY" in row.fix


@pytest.mark.parametrize(
    ("overrides", "expected_label"),
    [
        ({"embedding_provider": "google"}, "llm credentials (google)"),
        (
            {"embedding_provider": "google", "llm_model": "anthropic:claude-opus-5"},
            "llm credentials (anthropic)",
        ),
        (
            {
                "embedding_provider": "google",
                "llm_model": "openai-compatible:xai/grok-4.1-fast-reasoning",
            },
            "llm credentials (openai-compatible)",
        ),
        # Local embeddings with a deliberately named cloud model: the embedder
        # is offline but generation is not, so this project really is broken.
        ({"llm_model": "anthropic:claude-opus-5"}, "llm credentials (anthropic)"),
        ({"llm_model": "gemini-2.5-pro"}, "llm credentials (google)"),
        (
            {"judge_model": "openai-compatible:xai/grok-4.1-fast-reasoning"},
            "llm credentials (openai-compatible)",
        ),
    ],
)
def test_cloud_intent_without_credentials_still_fails(
    overrides: dict[str, str], expected_label: str
) -> None:
    checks = _by_name(_llm_credential_checks(_offline(**overrides)))

    assert checks[expected_label].status == "fail"


def test_an_openai_base_url_without_a_key_still_fails() -> None:
    """The one cloud misconfiguration that is not a missing credential."""
    settings = _offline(
        llm_model="openai-compatible:local/mixtral",
        openai_base_url="http://localhost:8080/v1",
    )

    row = _by_name(_llm_credential_checks(settings))["llm credentials (openai-compatible)"]

    assert row.status == "fail"
    assert "SCI_RAG_OPENAI_BASE_URL" in row.detail


def test_a_credentialed_project_is_unaffected() -> None:
    checks = _by_name(_llm_credential_checks(_offline(google_api_key="k")))

    assert checks["llm credentials (google)"].status == "ok"


# --- the google credential row ----------------------------------------------


def test_offline_credentials_row_does_not_claim_embedding_is_broken() -> None:
    """local-hash ingests new documents fine, so saying otherwise is wrong."""
    row = _google_credential_check(_offline())

    assert row.status == "warn"
    assert "local-hash" in row.detail
    assert "embedding new documents will not" not in row.detail


def test_a_google_embedder_without_credentials_still_fails() -> None:
    row = _google_credential_check(_offline(embedding_provider="google"))

    assert row.status == "fail"
    assert "local-hash" in row.fix


def test_configured_credentials_report_their_mode() -> None:
    assert _google_credential_check(_offline(google_api_key="k")).status == "ok"
    assert _google_credential_check(_offline(gcp_project="p")).detail == "mode=vertex"
