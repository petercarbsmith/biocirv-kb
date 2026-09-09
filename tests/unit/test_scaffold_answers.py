"""ProjectAnswers validates the whole answer set, not one field at a time.

Several answers only make sense together: offline credentials cannot use a
Google embedder, and an LLM-drafted ontology needs a draft to have actually
come back.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sci_rag.domain import DomainConfig, EntityTypeSpec
from sci_rag.scaffold.answers import ProjectAnswers
from sci_rag.scaffold.questions import default_answers


def _answers(**overrides: object) -> ProjectAnswers:
    raw = dict(default_answers())
    raw.update({k: str(v) for k, v in overrides.items()})
    return ProjectAnswers.from_raw(raw)


def test_defaults_validate() -> None:
    answers = _answers()
    assert answers.project_name == "My Scientific KB"
    assert answers.repo_name == "my-scientific-kb"
    assert answers.include_cloud_database is False


def test_cloud_database_answer_is_parsed_independently_of_the_manager() -> None:
    for manager in ("uv", "pixi", "conda", "venv+pip"):
        assert _answers(
            environment_manager=manager, include_cloud_database="Yes"
        ).include_cloud_database


def test_offline_credentials_force_the_local_embedder() -> None:
    answers = _answers(credentials="offline", embedding_provider="google")
    assert answers.embedding_provider == "local-hash"


def test_offline_credentials_downgrade_an_llm_ontology() -> None:
    """Drafting needs a model. Offline projects keep the worked example."""
    answers = _answers(credentials="offline", ontology="draft_with_llm")
    assert answers.ontology == "keep_demo_example"


def test_draft_without_a_draft_falls_back_visibly() -> None:
    answers = _answers(credentials="google_ai_studio", ontology="draft_with_llm")
    assert answers.ontology == "keep_demo_example"
    assert answers.drafted_ontology is None


def test_draft_is_kept_when_one_was_accepted() -> None:
    drafted = DomainConfig(name="Membrane KB", entity_types=[EntityTypeSpec(name="Membrane")])
    raw = dict(default_answers())
    raw["ontology"] = "draft_with_llm"
    answers = ProjectAnswers.from_raw(raw, drafted_ontology=drafted)
    assert answers.ontology == "draft_with_llm"
    assert answers.drafted_ontology is not None
    assert answers.drafted_ontology.entity_types[0].name == "Membrane"


def test_demo_only_corpus_keeps_the_demo_corpus() -> None:
    answers = _answers(corpus_source="demo_only", include_demo_corpus="No")
    assert answers.include_demo_corpus is True


def test_extras_follow_the_parser_and_reranker_answers() -> None:
    assert _answers(pdf_parser="pypdf", reranker="none").extras == []
    assert "docling" in _answers(pdf_parser="docling").extras
    assert "rerank" in _answers(reranker="local_cross_encoder").extras
    # The LLM reranker needs no extra at all.
    assert _answers(reranker="llm").extras == []


def test_reranker_answer_maps_onto_the_domain_tuning_model() -> None:
    assert _answers(reranker="none").reranker_tuning().enabled is False
    llm = _answers(reranker="llm").reranker_tuning()
    assert (llm.enabled, llm.adapter) == (True, "llm")
    local = _answers(reranker="local_cross_encoder").reranker_tuning()
    assert (local.enabled, local.adapter) == (True, "local")


def test_unknown_environment_manager_is_rejected() -> None:
    with pytest.raises(ValidationError, match="poetry"):
        _answers(environment_manager="poetry")


def test_python_version_outside_the_supported_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _answers(python_version="3.9")


def test_contact_email_must_look_like_an_address() -> None:
    with pytest.raises(ValidationError, match="contact_email"):
        _answers(contact_email="not-an-email")


def test_blank_contact_email_is_allowed() -> None:
    """The mailto is a courtesy header, not a requirement."""
    assert _answers(contact_email="").contact_email == ""


def test_runner_resolves_to_a_profile() -> None:
    assert _answers().runner.key == "uv"


def test_an_offline_project_is_never_offered_the_drafters() -> None:
    """Offering a model-backed step to a project with no model is a dead end."""
    from sci_rag.scaffold.answers import ProjectAnswers

    answers = ProjectAnswers(
        project_name="Offline KB",
        repo_name="offline-kb",
        credentials="offline",
        draft_domain_files=True,
    )

    assert answers.draft_domain_files is False
    assert any("drafting not offered" in note for note in answers.coercions)


def test_a_credentialed_project_keeps_the_drafters_offered() -> None:
    from sci_rag.scaffold.answers import ProjectAnswers

    answers = ProjectAnswers(
        project_name="Cloud KB",
        repo_name="cloud-kb",
        credentials="google_ai_studio",
        draft_domain_files=True,
    )

    assert answers.draft_domain_files is True


def test_api_key_is_never_represented_or_dumped() -> None:
    key = "secret-key-that-must-not-escape"
    answers = _answers(credentials="google_ai_studio", google_api_key=key)

    assert answers.google_api_key == key
    assert key not in repr(answers)
    assert "google_api_key" not in answers.model_dump()
