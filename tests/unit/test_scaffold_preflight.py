"""Credential preflight is bounded, actionable, and safe to run offline in tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from google.auth.exceptions import DefaultCredentialsError

from sci_rag.config import DEFAULT_LLM_MODEL, Settings
from sci_rag.scaffold.preflight import (
    CredentialProbe,
    explicit_google_settings,
    probe_google_credentials,
)


class _Client:
    def __init__(self, *, reply: str = "ready", failure: Exception | None = None) -> None:
        self.reply = reply
        self.failure = failure
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self.generate_content))

    async def generate_content(self, **_kwargs):  # type: ignore[no-untyped-def]
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(text=self.reply)


def _install_client(monkeypatch, *, failure: Exception | None = None) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
    calls: list[dict[str, object]] = []

    def factory(**kwargs):  # type: ignore[no-untyped-def]
        calls.append(kwargs)
        return _Client(failure=failure)

    monkeypatch.setattr("google.genai.Client", factory)
    return calls


def test_ai_studio_key_can_complete_a_tiny_generation_probe(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls = _install_client(monkeypatch)

    result = probe_google_credentials(api_key="test-secret-key", timeout_s=1)

    assert result.ok is True
    assert DEFAULT_LLM_MODEL in result.detail
    assert calls == [{"api_key": "test-secret-key"}]
    assert "test-secret-key" not in repr(result)


def test_explicit_settings_ignore_a_parent_project_dotenv(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / ".env").write_text(
        "SCI_RAG_GOOGLE_API_KEY=ambient-parent-key\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    settings = explicit_google_settings(gcp_project="selected-project")

    assert settings.google_api_key is None
    assert settings.gcp_project == "selected-project"


def test_vertex_checks_adc_before_calling_the_model(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls = _install_client(monkeypatch)
    adc_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "google.auth.default",
        lambda **kwargs: adc_calls.append(kwargs) or (SimpleNamespace(), "detected-project"),
    )

    result = probe_google_credentials(gcp_project="science-project", timeout_s=1)

    assert result.ok is True
    assert adc_calls
    assert calls == [{"vertexai": True, "project": "science-project", "location": "us-central1"}]


def test_missing_adc_names_the_login_command(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _install_client(monkeypatch)

    def missing(**_kwargs):  # type: ignore[no-untyped-def]
        raise DefaultCredentialsError("credentials not found")

    monkeypatch.setattr("google.auth.default", missing)

    result = probe_google_credentials(gcp_project="science-project", timeout_s=1)

    assert result == CredentialProbe(
        ok=False,
        detail="Application Default Credentials were not found.",
        fix="Run `gcloud auth application-default login`, then try again.",
    )


@pytest.mark.parametrize(
    ("failure", "detail", "fix_fragment"),
    [
        (
            RuntimeError("400 API key not valid. Please pass a valid API key."),
            "Google rejected the API key.",
            "aistudio.google.com/apikey",
        ),
        (
            RuntimeError("403 SERVICE_DISABLED: Generative Language API is disabled"),
            "The Google model API is not enabled for this project.",
            "Enable",
        ),
        (
            ConnectionError("DNS lookup failed"),
            "Could not reach the Google model service.",
            "network",
        ),
    ],
)
def test_common_failures_map_to_concrete_fixes(
    monkeypatch, failure: Exception, detail: str, fix_fragment: str
) -> None:  # type: ignore[no-untyped-def]
    _install_client(monkeypatch, failure=failure)

    result = probe_google_credentials(api_key="never-print-this", timeout_s=1)

    assert result.ok is False
    assert result.detail == detail
    assert fix_fragment in result.fix
    assert "never-print-this" not in repr(result)


def test_probe_timeout_is_reported_without_leaking_an_exception(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class SlowClient(_Client):
        async def generate_content(self, **_kwargs):  # type: ignore[no-untyped-def]
            await asyncio.Event().wait()

    monkeypatch.setattr("google.genai.Client", lambda **_kwargs: SlowClient())

    result = probe_google_credentials(api_key="slow-key", timeout_s=0.01)

    assert result.ok is False
    assert "timed out" in result.detail
    assert "network" in result.fix


async def test_doctor_generation_probe_uses_the_shared_failure_mapping(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from sci_rag.cli.doctor import _live_probe

    async def failed(_settings, **_kwargs):  # type: ignore[no-untyped-def]
        return CredentialProbe(False, "Mapped provider failure.", "Use the mapped fix.")

    monkeypatch.setattr("sci_rag.scaffold.preflight.probe_model_credentials", failed)

    checks = await _live_probe(Settings(_env_file=None, embedding_provider="local-hash"))
    generation = next(check for check in checks if check.name == "generation probe")

    assert generation.status == "fail"
    assert generation.detail == "Mapped provider failure."
    assert generation.fix == "Use the mapped fix."


# --- wrong location for a partner model -------------------------------------


@pytest.fixture()
def stub_adc(monkeypatch):  # type: ignore[no-untyped-def]
    """Vertex mode resolves ADC before it calls the model; keep that offline."""
    monkeypatch.setattr(
        "google.auth.default",
        lambda **_kwargs: (SimpleNamespace(), "detected-project"),
    )


#
# F-029: `docs/extend.md` tells a reader that a partner model served only from
# `global` fails with "a clear `400 ... is not servable in region` or `404 ...
# not found`, so `sci-rag doctor --probe` will catch it before a pipeline run
# does". The probe caught the failure and then classified it as a generic
# credential problem, so the guide's own repair, `SCI_RAG_GCP_LOCATION=global`,
# appeared nowhere in the diagnosis.


@pytest.mark.parametrize(
    "failure",
    [
        # Vertex, Anthropic publisher model, wrong region.
        RuntimeError(
            "400 Publisher Model `projects/p/locations/us-central1/publishers/anthropic/"
            "models/claude-haiku-4-5` is not servable in region us-central1."
        ),
        # Vertex, OpenAI-compatible partner endpoint, same cause, 404 shape.
        RuntimeError(
            "404 Publisher Model `publishers/xai/models/grok-4-fast` not found in "
            "location us-central1."
        ),
        # The message a live call actually returns, captured on 2026-08-30
        # with SCI_RAG_GCP_LOCATION=us-central1 against a partner model that
        # answers from global. #181 took its two phrasings from the guide
        # rather than from a response, and this third one matched neither, so
        # the case it was written for fell back to the generic diagnosis.
        RuntimeError(
            "Error code: 400 - [{'error': {'code': 400, 'message': \"Publisher model "
            "'xai/grok-4.1-fast-non-reasoning' in region 'us-central1' is only "
            "available via global endpoint.\", 'status': 'FAILED_PRECONDITION'}}]"
        ),
    ],
    ids=["not-servable-in-region", "not-found-in-location", "only-via-global-endpoint"],
)
def test_a_wrong_location_names_the_location_setting(
    monkeypatch, stub_adc, failure: Exception
) -> None:  # type: ignore[no-untyped-def]
    _install_client(monkeypatch, failure=failure)

    result = probe_google_credentials(gcp_project="a-project", location="us-central1", timeout_s=1)

    assert result.ok is False
    # The effective location, so a reader can see what was actually asked.
    assert "us-central1" in result.detail
    assert "SCI_RAG_GCP_LOCATION=global" in result.fix
    # Not the generic branch, which names five unrelated things.
    assert "Check the project ID, ADC login" not in result.fix


def test_a_location_404_also_offers_the_model_garden_reading(monkeypatch, stub_adc) -> None:  # type: ignore[no-untyped-def]
    """A 404 has a second cause a 400 does not, and it has to say so.

    `not servable in region` means the model exists and is served elsewhere.
    `not found in location` also covers a model id that is wrong, or one whose
    Model Garden offering was never enabled for this project. Collapsing them
    would send a reader to change a location that was never the problem.
    """
    _install_client(
        monkeypatch,
        failure=RuntimeError(
            "404 Publisher Model `publishers/xai/models/typo` not found in location us-central1."
        ),
    )

    result = probe_google_credentials(gcp_project="a-project", location="us-central1", timeout_s=1)

    assert "SCI_RAG_GCP_LOCATION=global" in result.fix
    assert "Model Garden" in result.fix


def test_a_location_diagnostic_does_not_reprint_the_provider_payload(monkeypatch, stub_adc) -> None:  # type: ignore[no-untyped-def]
    """The raw message names an internal project path; the diagnosis must not."""
    _install_client(
        monkeypatch,
        failure=RuntimeError(
            "400 Publisher Model `projects/secret-project-1234/locations/us-central1/"
            "publishers/anthropic/models/claude-haiku-4-5` is not servable in region us-central1."
        ),
    )

    result = probe_google_credentials(gcp_project="a-project", location="us-central1", timeout_s=1)

    assert "secret-project-1234" not in repr(result)
    assert "Publisher Model" not in repr(result)


@pytest.mark.parametrize(
    ("failure", "detail"),
    [
        (
            RuntimeError("403 SERVICE_DISABLED: Vertex AI API is disabled"),
            "The Google model API is not enabled for this project.",
        ),
        (
            RuntimeError("400 `not-a-model` is not a valid publisher model name"),
            "Credential check failed (RuntimeError).",
        ),
        (
            RuntimeError("Could not automatically determine credentials"),
            "Application Default Credentials were not found.",
        ),
    ],
    ids=["api-disabled", "malformed-model", "authentication"],
)
def test_failures_that_are_not_about_location_keep_their_own_diagnosis(  # type: ignore[no-untyped-def]
    monkeypatch, stub_adc, failure: Exception, detail: str
) -> None:
    """A new branch must not swallow the ones already answering correctly.

    Quota is deliberately absent. A 429 or `RESOURCE_EXHAUSTED` is retryable
    by design, so it reaches this mapper only after the shared retry budget
    and cannot be exercised against a one-second probe deadline without
    testing the backoff instead of the classification.
    """
    _install_client(monkeypatch, failure=failure)

    result = probe_google_credentials(gcp_project="a-project", location="us-central1", timeout_s=1)

    # `startswith`, not equality: the generic branch now appends the
    # provider's own redacted text, which is the point of that branch. What
    # this test guards is the classification in front of it.
    assert result.detail.startswith(detail)
    assert "SCI_RAG_GCP_LOCATION" not in result.fix


# Captured from the Generative Language API on 2026-08-31, calling a model a
# freshly issued key may no longer use. Quoted rather than paraphrased: #181
# shipped two phrasings taken from a guide, Vertex sent a third, and #232 had
# to fix it. This is the fourth, from a different surface again.
RETIREMENT_MESSAGE = (
    "404 NOT_FOUND. This model models/gemini-2.5-flash is no longer available "
    "to new users. Please update your code to use models/gemini-3.6-flash for "
    "the latest features and improvements."
)


def test_a_retired_model_is_diagnosed_as_a_model_problem(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The old answer sent a reader to check three things that were all fine.

    Before this, the retirement phrasing matched no marker and fell through
    to "Credential check failed (ClientError)" with "Check the AI Studio key,
    model, and quota". The key was valid, the quota was fine, and the model
    name was spelled correctly.
    """
    _install_client(monkeypatch, failure=RuntimeError(RETIREMENT_MESSAGE))

    result = probe_google_credentials(api_key="never-print-this", timeout_s=1)

    assert result.ok is False
    assert "no longer available" in result.detail.lower()
    assert "never-print-this" not in repr(result)


def test_an_unrecognised_failure_keeps_the_provider_message(monkeypatch) -> None:
    """The generic branch used to throw away the one useful thing it had.

    Every gap in the marker list has cost a round trip, because the text that
    would have diagnosed it was in hand and discarded. A message nobody
    anticipated is exactly when the provider's own words are worth most.
    """
    _install_client(monkeypatch, failure=RuntimeError("418 TEAPOT: the carafe is not attached"))

    result = probe_google_credentials(api_key="never-print-this", timeout_s=1)

    assert result.ok is False
    assert "carafe is not attached" in result.detail
    assert "never-print-this" not in repr(result)


def test_a_kept_message_still_redacts_a_credential(monkeypatch) -> None:
    """Keeping the text must not become a way to print a key.

    A provider that echoes the key back in an error is the case this has to
    survive, because it is the one where forwarding is most tempting and most
    dangerous.
    """
    leaky = RuntimeError("400 BAD: key AIzaSyEXAMPLEEXAMPLEEXAMPLEEXAMPLE123 rejected by policy")
    _install_client(monkeypatch, failure=leaky)

    result = probe_google_credentials(api_key="AIzaSyEXAMPLEEXAMPLEEXAMPLEEXAMPLE123", timeout_s=1)

    assert result.ok is False
    assert "AIzaSyEXAMPLE" not in repr(result)
    assert "rejected by policy" in result.detail
