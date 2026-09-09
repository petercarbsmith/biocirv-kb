"""Re-embed planner: find stale embeddings, re-embed exactly those.

Stale means the stored embedding_version differs from the configured
embedder's version (community summaries with no stamp count as stale).
The planner never touches rows that are already current, refuses
cross-dimension work outright (that is a migration, not a reindex), and
the dry run writes nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from sci_rag.db import Chunk, KgCommunity, get_session_factory
from sci_rag.embed.planner import ReindexRefused, apply_reindex, plan_reindex
from sci_rag.ingest import CorpusEntry, ingest_entries

pytestmark = pytest.mark.integration

STALE_VERSION = "old-embedder@64"


@pytest.fixture()
def corpus_entries(tmp_path: Path) -> list[CorpusEntry]:
    docs = []
    for name, text in (
        ("rice", "Rice straw availability is near 310,000 tons per year in the region."),
        ("almond", "Almond prunings are chipped in winter at 1.2 tons per acre."),
    ):
        p = tmp_path / f"{name}.md"
        p.write_text(text)
        docs.append(CorpusEntry(path=p, title=name, license_class="public", source="tests"))
    return docs


async def _age_first_chunk_and_add_community(local_embedder) -> str:  # type: ignore[no-untyped-def]
    """Mark one chunk stale and insert one community with no version stamp."""
    factory = get_session_factory()
    async with factory() as session:
        first_id = (await session.execute(select(Chunk.id).order_by(Chunk.id))).scalars().first()
        await session.execute(
            update(Chunk).where(Chunk.id == first_id).values(embedding_version=STALE_VERSION)
        )
        [vector] = await local_embedder.embed(["a cluster summary"], task="document")
        session.add(
            KgCommunity(
                title="test community",
                summary="A cluster of related concepts.",
                summary_embedding=vector,
                summary_embedding_version=None,
            )
        )
        await session.commit()
        assert first_id is not None
        return first_id


async def test_plan_reports_only_stale_rows(clean_tables, corpus_entries, local_embedder):  # type: ignore[no-untyped-def]
    await ingest_entries(corpus_entries, embedder=local_embedder)
    await _age_first_chunk_and_add_community(local_embedder)
    plan = await plan_reindex(get_session_factory(), local_embedder)
    assert plan.stale_chunks == 1
    assert plan.stale_communities == 1
    assert plan.chunk_versions == {STALE_VERSION: 1}
    assert plan.total_chunks >= 2


async def test_apply_reindexes_only_stale_and_stamps_versions(
    clean_tables, corpus_entries, local_embedder
):  # type: ignore[no-untyped-def]
    await ingest_entries(corpus_entries, embedder=local_embedder)
    stale_id = await _age_first_chunk_and_add_community(local_embedder)
    outcome = await apply_reindex(get_session_factory(), local_embedder, batch_size=1)
    assert outcome.chunks_reembedded == 1
    assert outcome.communities_reembedded == 1

    factory = get_session_factory()
    async with factory() as session:
        versions = set(
            (await session.execute(select(Chunk.embedding_version).distinct())).scalars()
        )
        assert versions == {local_embedder.version}
        stale_vector = await session.scalar(select(Chunk.embedding).where(Chunk.id == stale_id))
        assert stale_vector is not None
        community_version = await session.scalar(select(KgCommunity.summary_embedding_version))
        assert community_version == local_embedder.version


async def test_apply_skips_when_everything_current(clean_tables, corpus_entries, local_embedder):  # type: ignore[no-untyped-def]
    await ingest_entries(corpus_entries, embedder=local_embedder)
    outcome = await apply_reindex(get_session_factory(), local_embedder)
    assert outcome.chunks_reembedded == 0
    assert outcome.communities_reembedded == 0


async def test_dimension_mismatch_refused_without_writes(
    clean_tables, corpus_entries, local_embedder
):  # type: ignore[no-untyped-def]
    from sci_rag.embed.local_hash import LocalHashEmbedder

    await ingest_entries(corpus_entries, embedder=local_embedder)
    await _age_first_chunk_and_add_community(local_embedder)
    wrong_dim = LocalHashEmbedder(dim=32)
    with pytest.raises(ReindexRefused, match="dimension"):
        await plan_reindex(get_session_factory(), wrong_dim)
    with pytest.raises(ReindexRefused, match="dimension"):
        await apply_reindex(get_session_factory(), wrong_dim)
    factory = get_session_factory()
    async with factory() as session:
        stale = await session.scalar(
            select(func.count(Chunk.id)).where(Chunk.embedding_version == STALE_VERSION)
        )
    assert stale == 1  # nothing was touched


def _run_cli(*args: str, **overrides: str) -> subprocess.CompletedProcess[str]:
    """Invoke the real CLI in a subprocess: the command spins up its own
    event loop and engine, which cannot be done inside the test loop.

    ``overrides`` go into the child's environment. That is the only way to
    exercise a setting the parent process already bound at import time, which
    is exactly the drift F-023 is about.
    """
    environment = os.environ.copy()
    environment.update(overrides)
    return subprocess.run(
        [sys.executable, "-m", "sci_rag.cli.main", *args],
        capture_output=True,
        text=True,
        timeout=120,
        env=environment,
        cwd=Path(__file__).parents[2],
    )


async def test_cli_dry_run_reports_and_writes_nothing(clean_tables, corpus_entries, local_embedder):  # type: ignore[no-untyped-def]
    await ingest_entries(corpus_entries, embedder=local_embedder)
    await _age_first_chunk_and_add_community(local_embedder)
    result = _run_cli("embed", "reindex", "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "stale" in result.stdout.lower()
    factory = get_session_factory()
    async with factory() as session:
        stale = await session.scalar(
            select(func.count(Chunk.id)).where(Chunk.embedding_version == STALE_VERSION)
        )
    assert stale == 1


async def test_cli_apply_reindexes(clean_tables, corpus_entries, local_embedder):  # type: ignore[no-untyped-def]
    await ingest_entries(corpus_entries, embedder=local_embedder)
    await _age_first_chunk_and_add_community(local_embedder)
    result = _run_cli("embed", "reindex", "--apply")
    assert result.returncode == 0, result.stdout + result.stderr
    factory = get_session_factory()
    async with factory() as session:
        stale = await session.scalar(
            select(func.count(Chunk.id)).where(Chunk.embedding_version == STALE_VERSION)
        )
    assert stale == 0


# --- environment against live schema -----------------------------------------
#
# F-023 in the 2026-08-29 documentation route audit: on a populated database
# the reindex guard compared the embedder's dimension against
# db.models.EMBEDDING_DIM, and both sides came from the same process setting.
# Changing SCI_RAG_EMBEDDING_DIM therefore moved both sides at once, the guard
# saw no difference, and the command told the reader to re-run with --apply
# against a column that could not hold the vectors it was about to make.
#
# The setting is bound when the models import, so the only honest way to test
# this is a child process that starts with a different one. `doctor` already
# catches this case, which is what made the disagreement visible.

WRONG_DIMENSION = "999"


async def _stale_chunk_count() -> int:
    factory = get_session_factory()
    async with factory() as session:
        return (
            await session.scalar(
                select(func.count(Chunk.id)).where(Chunk.embedding_version == STALE_VERSION)
            )
        ) or 0


async def test_doctor_and_reindex_agree_that_the_live_column_disagrees(
    clean_tables, corpus_entries, local_embedder
):  # type: ignore[no-untyped-def]
    """The audit found doctor exiting 1 and reindex exiting 0 on one database."""
    await ingest_entries(corpus_entries, embedder=local_embedder)
    await _age_first_chunk_and_add_community(local_embedder)

    doctor = _run_cli("doctor", SCI_RAG_EMBEDDING_DIM=WRONG_DIMENSION)
    reindex = _run_cli("embed", "reindex", "--dry-run", SCI_RAG_EMBEDDING_DIM=WRONG_DIMENSION)

    assert doctor.returncode != 0, "doctor should still refuse a dimension mismatch"
    assert reindex.returncode != 0, (
        "reindex accepted a dimension the live column cannot hold:\n"
        + reindex.stdout
        + reindex.stderr
    )


async def test_dry_run_refuses_when_the_setting_left_the_live_column_behind(
    clean_tables, corpus_entries, local_embedder
):  # type: ignore[no-untyped-def]
    await ingest_entries(corpus_entries, embedder=local_embedder)
    await _age_first_chunk_and_add_community(local_embedder)

    result = _run_cli("embed", "reindex", "--dry-run", SCI_RAG_EMBEDDING_DIM=WRONG_DIMENSION)
    output = (result.stdout + result.stderr).lower()

    assert result.returncode != 0, output
    assert "dimension" in output
    assert "--apply" not in result.stdout, "a refusal must not invite the reader to apply it"
    assert await _stale_chunk_count() == 1, "a dry run must not write"


async def test_apply_refuses_before_embedding_or_writing_anything(
    clean_tables, corpus_entries, local_embedder
):  # type: ignore[no-untyped-def]
    await ingest_entries(corpus_entries, embedder=local_embedder)
    await _age_first_chunk_and_add_community(local_embedder)

    result = _run_cli("embed", "reindex", "--apply", SCI_RAG_EMBEDDING_DIM=WRONG_DIMENSION)
    output = result.stdout + result.stderr

    assert result.returncode != 0, output
    # Failing is not enough. Before the fix, apply reached the database and
    # died trying to store a 999-wide vector in a 64-wide column, which is a
    # crash rather than a refusal. The guidance has to be there.
    assert "dimension" in output.lower(), output
    assert "SCI_RAG_EMBEDDING_DIM" in output, output
    assert "Traceback" not in output, "a refusal must not surface as a traceback"
    assert "asyncpg" not in output.lower(), "apply reached the database before refusing:\n" + output
    assert await _stale_chunk_count() == 1, "apply wrote under a refused dimension"

    factory = get_session_factory()
    async with factory() as session:
        current = await session.scalar(
            select(func.count(Chunk.id)).where(Chunk.embedding_version == local_embedder.version)
        )
    assert current == 1, "apply re-stamped rows under a refused dimension"


async def test_a_version_change_at_the_same_dimension_still_plans_normally(
    clean_tables, corpus_entries, local_embedder
):  # type: ignore[no-untyped-def]
    """Fail closed on width must not fail closed on the ordinary upgrade."""
    await ingest_entries(corpus_entries, embedder=local_embedder)
    await _age_first_chunk_and_add_community(local_embedder)

    result = _run_cli("embed", "reindex", "--dry-run")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "stale" in result.stdout.lower()
    assert await _stale_chunk_count() == 1
