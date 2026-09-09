"""`sci-rag-new`: start a project from a parent directory.

The other half of the factory. `sci-rag init` configures a checkout you
already have; this one runs where there is nothing yet, fetches the template
at the tag matching its own version, and then applies the same answers through
the same appliers.

Installed as its own entry point so the first thing a new user runs is
``pipx install sci-rag-kit && sci-rag-new``, with no repository to clone and no
files to hand-edit first.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console

from sci_rag.config import DEFAULT_LLM_MODEL

if TYPE_CHECKING:
    from sci_rag.llm import LLMClient
    from sci_rag.scaffold.preflight import CredentialProbe
    from sci_rag.scaffold.prompt import Prompter
    from sci_rag.scaffold.questions import Question

app = typer.Typer(
    name="sci-rag-new",
    help="Create a new scientific RAG project from the sci-rag-kit template.",
    add_completion=False,
    pretty_exceptions_show_locals=False,
)
console = Console()


@app.command()
def new(
    output_dir: Path = typer.Option(
        Path("."),
        "--output-dir",
        "-o",
        help="Where to create the project directory. Defaults to the current directory.",
    ),
    defaults: bool = typer.Option(
        False, "--defaults", help="Take every default without asking. Useful in CI."
    ),
    answers_file: Path | None = typer.Option(
        None,
        "--answers-file",
        help="A YAML file of answers, for reproducible generation. Unanswered questions "
        "take their default. Answers that need a person, such as accepting a drafted "
        "ontology, must be supplied explicitly.",
    ),
    quick: bool | None = typer.Option(
        None,
        "--quick/--advanced",
        help="Ask six setup questions, or expose every option.",
    ),
    no_tty: bool = typer.Option(
        False,
        "--no-tty",
        help="Use plain numbered prompts even in a supported terminal.",
    ),
    no_preflight: bool = typer.Option(
        False,
        "--no-preflight",
        help="Skip the live credential check before downloading the template.",
    ),
    ref: str | None = typer.Option(
        None,
        "--ref",
        help="Fetch the template at this tag or branch, overriding the tag that matches "
        "this generator's version.",
    ),
    template_path: Path | None = typer.Option(
        None,
        "--template-path",
        help=(
            "Generate from a local checkout with no network access. "
            "Copies what the checkout tracks, so local state stays local."
        ),
    ),
) -> None:
    """Create a new Sci RAG Kit project."""
    try:
        _run_new(
            output_dir=output_dir,
            defaults=defaults,
            answers_file=answers_file,
            quick=quick,
            no_tty=no_tty,
            no_preflight=no_preflight,
            ref=ref,
            template_path=template_path,
        )
    except typer.Exit:
        raise
    except KeyboardInterrupt:
        console.print("[yellow]Setup cancelled.[/yellow]")
        raise typer.Exit(1) from None
    except Exception as exc:
        console.print(f"[red]Setup failed unexpectedly ({type(exc).__name__}).[/red]")
        console.print("Try again with --no-preflight or --no-tty; report the error if it repeats.")
        raise typer.Exit(1) from None


def _run_new(
    *,
    output_dir: Path,
    defaults: bool,
    answers_file: Path | None,
    quick: bool | None,
    no_tty: bool,
    no_preflight: bool,
    ref: str | None,
    template_path: Path | None,
) -> None:
    from sci_rag.scaffold.answers import ProjectAnswers
    from sci_rag.scaffold.apply import apply_all
    from sci_rag.scaffold.fetch import TemplateFetchError, fetch_template
    from sci_rag.scaffold.prompt import PromptAborted, make_prompter
    from sci_rag.scaffold.report import print_scaffold_report
    from sci_rag.scaffold.wizard import AnswerFileError, collect_answers, confirm_ontology_draft

    non_interactive = defaults or answers_file is not None
    try:
        raw = collect_answers(
            defaults=defaults,
            answers_file=answers_file,
            quick=quick,
            plain=no_tty,
        )
    except AnswerFileError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except PromptAborted as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(1) from exc

    skip_draft = False
    llm = None
    if not non_interactive and raw.get("credentials") != "offline":
        prompter = make_prompter(plain=no_tty)
        if no_preflight:
            if _credential_value(raw):
                llm = _build_explicit_llm(raw)
            else:
                skip_draft = True
                prompter.note(
                    "No credential was entered, so ontology drafting will wait until later."
                )
        else:
            verified = _preflight_credentials(raw, prompter)
            skip_draft = not verified
            if verified:
                llm = _build_explicit_llm(raw)

    # Built once to resolve the directory name; rebuilt after the fetch, when a
    # drafted ontology may exist to fold in.
    target = output_dir.expanduser().resolve() / ProjectAnswers.from_raw(raw).repo_name

    console.print(f"\nFetching sci-rag-kit for [bold]{target.name}[/bold]...")
    try:
        source = fetch_template(target, ref=ref, template_path=template_path)
    except TemplateFetchError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"  from {source}")

    drafted = None
    if raw.get("ontology") == "draft_with_llm" and raw.get("credentials") != "offline":
        if non_interactive:
            console.print(
                "[yellow]  Skipping the LLM ontology draft: accepting or redrafting one "
                "needs an interactive session. Keeping the worked example.[/yellow]"
            )
        elif not skip_draft:
            drafted = confirm_ontology_draft(
                target / "domain",
                project_name=raw["project_name"],
                description=raw.get("description", ""),
                llm=llm,
            )

    answers = ProjectAnswers.from_raw(raw, drafted_ontology=drafted)  # type: ignore[arg-type]
    changes = apply_all(answers, target)
    print_scaffold_report(
        answers,
        changes,
        console=console,
        created_directory=True,
    )


def _credential_value(raw: dict[str, str]) -> str:
    if raw.get("credentials") == "vertex_ai":
        return raw.get("gcp_project", "")
    return raw.get("google_api_key", "")


def _credential_question(name: str) -> Question:
    from sci_rag.scaffold.questions import QUESTIONS

    return next(question for question in QUESTIONS if question.name == name)


def _ask_replacement_credential(raw: dict[str, str], prompter: Prompter) -> None:
    if raw.get("credentials") == "vertex_ai":
        question = _credential_question("gcp_project")
        raw["gcp_project"] = prompter.text(question, "")
        return
    question = _credential_question("google_api_key")
    raw["google_api_key"] = prompter.secret(question, "")


def _preflight_credentials(
    raw: dict[str, str],
    prompter: Prompter,
    *,
    probe: Callable[..., CredentialProbe] | None = None,
) -> bool:
    probe_fn = probe
    if probe_fn is None:
        from sci_rag.scaffold.preflight import probe_google_credentials

        probe_fn = probe_google_credentials

    while True:
        prompter.note("Checking the credential with one small model request...")
        result = probe_fn(
            api_key=raw.get("google_api_key", ""),
            gcp_project=raw.get("gcp_project", ""),
            model=raw.get("llm_model", DEFAULT_LLM_MODEL),
        )
        if result.ok:
            prompter.note(result.detail)
            return True

        prompter.error(result.detail)
        if result.fix:
            prompter.note(result.fix)
        action = prompter.menu(
            "Credential recovery",
            (
                ("retry", "Try a different credential"),
                ("studio", "Switch to an AI Studio key instead"),
                (
                    "continue",
                    "Continue without a model (keeps the worked example ontology)",
                ),
            ),
            "retry",
        )
        if action == "continue":
            return False
        if action == "studio":
            raw["credentials"] = "google_ai_studio"
            raw["gcp_project"] = ""
        _ask_replacement_credential(raw, prompter)


def _build_explicit_llm(raw: dict[str, str]) -> LLMClient:
    from sci_rag.scaffold.preflight import build_explicit_google_llm

    return build_explicit_google_llm(
        api_key=raw.get("google_api_key", ""),
        gcp_project=raw.get("gcp_project", ""),
        model=raw.get("llm_model", DEFAULT_LLM_MODEL),
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
