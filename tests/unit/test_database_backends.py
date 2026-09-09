"""Contracts shared by the Docker, local, and cloud development backends."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]


def _make(target: str, backend: str, *, dry_run: bool = True) -> subprocess.CompletedProcess[str]:
    command = ["make", "--no-print-directory"]
    if dry_run:
        command.append("-n")
    command.extend([target, f"SCI_RAG_DB_BACKEND={backend}"])
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=os.environ,
        timeout=30,
    )


@pytest.mark.parametrize(
    ("backend", "up_command", "down_command"),
    [
        ("docker", "docker compose up -d --wait", "docker compose down"),
        (
            "local",
            "uv run python scripts/local_postgres.py start",
            "uv run python scripts/local_postgres.py stop",
        ),
        (
            "cloud",
            "uv run python scripts/cloud_postgres.py start",
            "uv run python scripts/cloud_postgres.py stop",
        ),
    ],
)
def test_make_dispatches_each_database_backend(
    backend: str, up_command: str, down_command: str
) -> None:
    up = _make("db-up", backend)
    down = _make("db-down", backend)

    assert up.returncode == 0, up.stdout + up.stderr
    assert down.returncode == 0, down.stdout + down.stderr
    assert up_command in up.stdout
    assert down_command in down.stdout


def test_make_rejects_an_unknown_database_backend_without_running_a_tool() -> None:
    result = _make("db-up", "mystery", dry_run=False)

    assert result.returncode != 0
    message = result.stdout + result.stderr
    assert "docker" in message and "local" in message and "cloud" in message


@pytest.mark.parametrize(
    ("backend", "expected"),
    [
        ("docker", "docker compose up -d --wait"),
        ("local", "scripts/local_postgres.py start"),
        ("cloud", "scripts/cloud_postgres.py start"),
    ],
)
def test_doctor_names_the_selected_database_backend(
    monkeypatch: pytest.MonkeyPatch, backend: str, expected: str
) -> None:
    from sci_rag.cli.doctor import _database_start_hint

    monkeypatch.setenv("SCI_RAG_DB_BACKEND", backend)

    assert expected in _database_start_hint()


def test_integration_skip_guidance_names_every_supported_backend() -> None:
    conftest = (REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")

    assert "SCI_RAG_DB_BACKEND=docker" in conftest
    assert "SCI_RAG_DB_BACKEND=local" in conftest
    assert "SCI_RAG_DB_BACKEND=cloud" in conftest


def test_cloud_state_directory_is_ignored() -> None:
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert ".cloudsql/" in ignored
