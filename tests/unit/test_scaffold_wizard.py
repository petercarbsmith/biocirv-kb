"""run_wizard: three ways in, one ProjectAnswers out.

--defaults and --answers-file exist so CI and the docs can generate a project
reproducibly; the interactive path is what a user actually sees.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
import yaml

from sci_rag.scaffold.questions import QUESTIONS, Question, default_for, is_asked
from sci_rag.scaffold.wizard import AnswerFileError, collect_answers, run_wizard


def _advanced_input(
    overrides: dict[str, str] | None = None,
    *,
    retries: dict[str, list[str]] | None = None,
) -> io.StringIO:
    """Build positional input by question name, including the setup fork."""
    overrides = overrides or {}
    retries = retries or {}
    replies = ["2"]
    gathered: dict[str, str] = {}
    for question in QUESTIONS:
        if not is_asked(question, gathered):
            continue
        default = default_for(question, gathered)
        attempts = retries.get(question.name, [overrides.get(question.name, "")])
        replies.extend(attempts)
        final = attempts[-1]
        if not final:
            gathered[question.name] = default
        elif question.choices and final.isdigit():
            gathered[question.name] = question.choices[int(final) - 1]
        else:
            gathered[question.name] = final
    return io.StringIO("\n".join(replies) + "\n")


# One answer needs a person: drafting an ontology means reading what the model
# proposed and accepting or redrafting it. Interactively that is the right
# first suggestion, so pressing Enter still selects it. `--defaults` runs
# unattended and takes `keep_demo_example` instead, which is why these
# comparisons hold everything else equal rather than asserting plain equality.
# Before #164 the two converged only because an unaccepted draft was coerced
# back to the demo after the fact.
INTERACTIVE_ONLY_ANSWERS = ("ontology",)


def _besides_interactive_only(answers: object) -> dict[str, object]:
    return {
        field: value
        for field, value in vars(answers).items()
        if field not in INTERACTIVE_ONLY_ANSWERS and field != "coercions"
    }


def _matches_defaults(answers: object) -> bool:
    return _besides_interactive_only(answers) == _besides_interactive_only(
        run_wizard(defaults=True)
    )


def test_defaults_need_no_input() -> None:
    answers = run_wizard(defaults=True)
    assert answers.project_name == "My Scientific KB"
    assert answers.repo_name == "my-scientific-kb"
    assert answers.environment_manager == "uv"


def test_an_answers_file_overrides_the_defaults(tmp_path: Path) -> None:
    path = tmp_path / "answers.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "project_name": "Membrane Materials KB",
                "description": "Membrane chemistry",
                "corpus_source": "openalex_topic",
                "openalex_topic": "polyamide membrane fouling",
                "max_results": 250,
            }
        ),
        encoding="utf-8",
    )
    answers = run_wizard(answers_file=path)
    assert answers.project_name == "Membrane Materials KB"
    # repo_name still derives from the answered project name.
    assert answers.repo_name == "membrane-materials-kb"
    assert answers.openalex_topic == "polyamide membrane fouling"
    assert answers.max_results == 250


def test_an_answers_file_is_reproducible(tmp_path: Path) -> None:
    path = tmp_path / "answers.yaml"
    path.write_text(yaml.safe_dump({"project_name": "Battery KB"}), encoding="utf-8")
    assert run_wizard(answers_file=path) == run_wizard(answers_file=path)


def test_an_unknown_key_in_an_answers_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "answers.yaml"
    path.write_text(yaml.safe_dump({"projct_name": "typo"}), encoding="utf-8")
    with pytest.raises(AnswerFileError, match="projct_name"):
        run_wizard(answers_file=path)


def test_pressing_enter_through_everything_yields_the_defaults() -> None:
    answers = run_wizard(input_stream=io.StringIO("\n" * 40))

    assert _matches_defaults(answers)


def test_only_an_unattended_run_settles_the_ontology_in_advance() -> None:
    """The one answer the two routes choose differently, stated out loud.

    Asserted on the raw answers rather than on ProjectAnswers, because an
    interactive selection of `draft_with_llm` that never gets a draft accepted
    is coerced back to the demo afterwards. That coercion is a runtime
    fallback for a human whose credential check failed; `--defaults` should
    not be walking into it at all.
    """
    interactive = collect_answers(input_stream=io.StringIO("\n" * 40), output_stream=io.StringIO())
    unattended = collect_answers(defaults=True)

    assert interactive["ontology"] == "draft_with_llm"
    assert unattended["ontology"] == "keep_demo_example"


def test_quick_mode_asks_six_questions_and_keeps_every_other_default() -> None:
    output = io.StringIO()

    answers = run_wizard(
        quick=True,
        input_stream=io.StringIO("\n" * 6),
        output_stream=output,
    )

    assert _matches_defaults(answers)
    transcript = output.getvalue()
    for name in (
        "project_name",
        "description",
        "contact_email",
        "environment_manager",
        "credentials",
        "corpus_source",
    ):
        assert name in transcript
    assert "repo_name" not in transcript
    assert "embedding_dim" not in transcript
    assert "include_cloud_database" not in transcript


@pytest.mark.parametrize("env_name", ["SCI_RAG_GOOGLE_API_KEY", "GOOGLE_API_KEY"])
def test_existing_google_key_is_offered_for_reuse_without_being_displayed(
    monkeypatch, env_name: str
) -> None:  # type: ignore[no-untyped-def]
    key = "existing-secret-key"
    monkeypatch.setenv(env_name, key)
    output = io.StringIO()

    answers = run_wizard(
        quick=True,
        input_stream=io.StringIO("\n" * 7),
        output_stream=output,
    )

    assert answers.google_api_key == key
    transcript = output.getvalue()
    assert "Use the Google API key already set in your environment?" in transcript
    assert key not in transcript


def test_plain_sessions_show_the_setup_fork_and_default_to_quick() -> None:
    output = io.StringIO()

    answers = run_wizard(
        input_stream=io.StringIO("\n" * 7),
        output_stream=output,
    )

    assert _matches_defaults(answers)
    transcript = output.getvalue()
    assert "Select Setup" in transcript
    assert "1 - Quick" in transcript
    assert "2 - Advanced" in transcript
    assert "repo_name" not in transcript


def test_tty_detection_preselects_the_first_installed_environment_manager(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from sci_rag.scaffold.prompt import QuestionaryPrompter

    class StubPrompter(QuestionaryPrompter):
        def __init__(self) -> None:
            self.notes: list[str] = []

        def banner(self) -> None:
            pass

        def text(self, question, default: str) -> str:  # type: ignore[no-untyped-def]
            return default

        def choice(self, question, default: str) -> str:  # type: ignore[no-untyped-def]
            return default

        def secret(self, question, default: str) -> str:  # type: ignore[no-untyped-def]
            return default

        def note(self, message: str) -> None:
            self.notes.append(message)

    prompter = StubPrompter()
    monkeypatch.setattr("sci_rag.scaffold.prompt.make_prompter", lambda **_kwargs: prompter)
    monkeypatch.setattr("sci_rag.scaffold.runners.detect_environment_manager", lambda: "pixi")

    answers = run_wizard(quick=False)

    assert answers.environment_manager == "pixi"
    assert any("pixi" in note and "PATH" in note for note in prompter.notes)


def test_defaults_do_not_inspect_the_environment_manager_on_a_tty(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from sci_rag.scaffold.prompt import QuestionaryPrompter

    prompter = QuestionaryPrompter.__new__(QuestionaryPrompter)
    monkeypatch.setattr("sci_rag.scaffold.prompt.make_prompter", lambda **_kwargs: prompter)

    def unexpected_detection() -> str:
        raise AssertionError("--defaults must not inspect PATH")

    monkeypatch.setattr("sci_rag.scaffold.runners.detect_environment_manager", unexpected_detection)

    assert run_wizard(defaults=True).environment_manager == "uv"


def test_secret_questions_use_the_masked_prompt_method(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: list[str] = []

    class StubPrompter:
        def banner(self) -> None:
            pass

        def secret(self, question: Question, default: str) -> str:
            seen.append(question.name)
            return "hidden"

    monkeypatch.setattr(
        "sci_rag.scaffold.wizard.QUESTIONS",
        (Question("api_key", "api_key", "", quick=True, secret=True),),
    )
    monkeypatch.setattr("sci_rag.scaffold.prompt.make_prompter", lambda **_kwargs: StubPrompter())

    assert collect_answers(quick=True) == {"api_key": "hidden"}
    assert seen == ["api_key"]


def test_the_interactive_session_asks_in_the_documented_order() -> None:
    output = io.StringIO()
    run_wizard(input_stream=_advanced_input(), output_stream=output)
    transcript = output.getvalue()
    assert transcript.index("project_name") < transcript.index("credentials")
    assert transcript.index("credentials") < transcript.index("corpus_source")
    assert transcript.index("corpus_source") < transcript.index("initialize_git")


def test_choice_questions_are_numbered_like_the_documented_transcript() -> None:
    output = io.StringIO()
    run_wizard(input_stream=io.StringIO("\n" * 40), output_stream=output)
    transcript = output.getvalue()
    assert "Select credentials" in transcript
    assert "1 - google_ai_studio" in transcript
    assert "Choose from [1/2/3]" in transcript


def test_answers_are_read_where_the_gates_open_them() -> None:
    """Choosing OpenAlex opens two follow-ups the default path never asks."""
    answers = run_wizard(
        input_stream=_advanced_input(
            {
                "corpus_source": "2",
                "openalex_topic": "polyamide membrane fouling",
                "max_results": "250",
                "ontology": "2",
            }
        )
    )
    assert answers.corpus_source == "openalex_topic"
    assert answers.openalex_topic == "polyamide membrane fouling"
    assert answers.max_results == 250


def test_an_invalid_choice_is_re_asked() -> None:
    output = io.StringIO()
    # "9" is not a listed credential; the wizard must ask again rather than
    # accept it or crash.
    replies = ["1", "", "", "", "", "9", "3", ""]
    answers = run_wizard(input_stream=io.StringIO("\n".join(replies) + "\n"), output_stream=output)
    assert answers.credentials == "offline"


def test_an_invalid_text_answer_is_re_asked() -> None:
    answers = run_wizard(input_stream=_advanced_input(retries={"embedding_dim": ["many", "1024"]}))
    assert answers.embedding_dim == 1024
