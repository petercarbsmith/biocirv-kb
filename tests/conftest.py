"""Test configuration.

The environment is pinned BEFORE any sci_rag import: the embedding column
dimension is baked into the models at import time, and tests use a small
dimension (64) with the deterministic local-hash embedder so the whole
suite runs offline.

Integration tests need Postgres. Point SCI_RAG_TEST_DATABASE_URL at any
Postgres with the pgvector extension available (the repo's
Docker, local, and cloud development backends all work); if it is unreachable,
integration tests skip with a clear message instead of failing.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("SCI_RAG_EMBEDDING_PROVIDER", "local-hash")
os.environ.setdefault("SCI_RAG_EMBEDDING_DIM", "64")
os.environ["SCI_RAG_DATABASE_URL"] = os.environ.get(
    "SCI_RAG_TEST_DATABASE_URL",
    "postgresql+asyncpg://sci_rag:sci_rag@localhost:5433/sci_rag_test",
)
# Never let a developer's real .env leak into tests.
os.environ.setdefault("SCI_RAG_GOOGLE_API_KEY", "")
os.environ.setdefault("SCI_RAG_GCP_PROJECT", "")

# The generation model is part of that leak, and it was missed. `.env.example`
# ships `SCI_RAG_LLM_MODEL`, so most working copies have it set, and
# `Settings.is_offline()` asks whether every generation setting still equals
# its shipped default. A developer whose `.env` named the previous default
# therefore passed the offline diagnostics tests for the wrong reason, and one
# whose `.env` was merely stale failed them on a diff that never touched the
# feature. Pinning these here is what makes those tests about the code.
from sci_rag.config import DEFAULT_LLM_MODEL

os.environ.setdefault("SCI_RAG_LLM_PROVIDER", "google")
os.environ.setdefault("SCI_RAG_LLM_MODEL", DEFAULT_LLM_MODEL)

import pytest
import pytest_asyncio
from sqlalchemy import text

from sci_rag.config import get_settings, reset_settings_cache

reset_settings_cache()


@pytest.fixture(scope="session")
def settings():  # type: ignore[no-untyped-def]
    return get_settings()


@pytest_asyncio.fixture(scope="session")
async def database():  # type: ignore[no-untyped-def]
    """A clean schema in the test database, or a skip if Postgres is down."""
    from sci_rag.db import Base, dispose_engine, get_engine

    engine = get_engine()
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        await dispose_engine()
        pytest.skip(
            f"Postgres unavailable at {get_settings().database_url!r} ({type(exc).__name__}). "
            "Start it with `SCI_RAG_DB_BACKEND=docker make db-up`, "
            "`SCI_RAG_DB_BACKEND=local make db-up`, or "
            "`SCI_RAG_DB_BACKEND=cloud make db-up`; then set "
            "SCI_RAG_TEST_DATABASE_URL to that backend's disposable test database."
        )
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await dispose_engine()


@pytest_asyncio.fixture()
async def clean_tables(database):  # type: ignore[no-untyped-def]
    """Truncate all tables so each test starts from an empty knowledge base."""
    async with database.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE documents, chunks, document_citations, kg_entities, "
                "kg_relationships, kg_communities, entity_resolution_audit CASCADE"
            )
        )
    yield


@pytest.fixture()
def local_embedder(settings):  # type: ignore[no-untyped-def]
    from sci_rag.embed import LocalHashEmbedder

    return LocalHashEmbedder(settings.embedding_dim)


@pytest.fixture()
def demo_domain():  # type: ignore[no-untyped-def]
    """Fixture providing the shipped demo DomainProfile independent of the active project domain."""
    from sci_rag.domain import (
        DomainConfig,
        DomainProfile,
        EntityTypeSpec,
        QueryClassSpec,
        RelationTypeSpec,
    )

    config = DomainConfig(
        name="My Scientific KB",
        description="A short description of your domain.",
        entity_types=[
            EntityTypeSpec(
                name="Feedstock",
                description="A biomass or residue stream (almond prunings, rice straw, walnut shells)",
            ),
            EntityTypeSpec(
                name="Crop",
                description="The crop a residue comes from (almonds, rice, grapes)",
            ),
            EntityTypeSpec(
                name="Region",
                description="A geographic area (a county, a valley, a state)",
            ),
            EntityTypeSpec(
                name="ConversionProcess",
                description=(
                    "A process turning feedstock into products "
                    "(gasification, anaerobic digestion, pyrolysis)"
                ),
            ),
            EntityTypeSpec(
                name="Product",
                description="An output of conversion (renewable diesel, biochar, biogas)",
            ),
            EntityTypeSpec(
                name="Property",
                description="A measured characteristic (moisture content, ash content, yield per acre)",
            ),
            EntityTypeSpec(
                name="Equipment",
                description="Machinery used in collection or conversion (chippers, balers, digesters)",
            ),
            EntityTypeSpec(
                name="Policy",
                description="A regulation, incentive, or standard affecting residue use",
            ),
            EntityTypeSpec(
                name="Organization",
                description="An agency, lab, company, or program",
            ),
        ],
        relation_types=[
            RelationTypeSpec(
                name="PRODUCES",
                description="Source yields target (a crop produces a residue; a process produces a product)",
            ),
            RelationTypeSpec(
                name="CONVERTED_BY",
                description="Feedstock is converted by a process",
            ),
            RelationTypeSpec(
                name="LOCATED_IN",
                description="Something is grown, collected, or sited in a region",
            ),
            RelationTypeSpec(
                name="HAS_PROPERTY",
                description="Feedstock or product has a measured property",
            ),
            RelationTypeSpec(
                name="REQUIRES",
                description="A process or activity needs an input, condition, or equipment",
            ),
            RelationTypeSpec(
                name="AFFECTS",
                description="A policy, practice, or property influences something",
            ),
            RelationTypeSpec(
                name="COMPETES_WITH",
                description="Two uses or pathways compete for the same resource",
            ),
        ],
        query_classes=[
            QueryClassSpec(
                name="availability",
                keywords=[
                    "availability",
                    "supply",
                    "tons",
                    "acreage",
                    "yield",
                    "county",
                    "region",
                    "much",
                ],
                hyde_instruction=(
                    "Write as a regional biomass resource assessment would: name the region, "
                    "the crop and residue, and quantitative availability (tons per year, acres, yield per acre)."
                ),
            ),
            QueryClassSpec(
                name="properties",
                keywords=[
                    "moisture",
                    "ash",
                    "composition",
                    "content",
                    "heating",
                    "density",
                    "lignin",
                    "cellulose",
                ],
                hyde_instruction=(
                    "Write as a feedstock characterization section would: report measured "
                    "properties with units and typical ranges."
                ),
            ),
            QueryClassSpec(
                name="conversion",
                keywords=[
                    "conversion",
                    "process",
                    "digestion",
                    "pyrolysis",
                    "gasification",
                    "fermentation",
                    "produce",
                    "biofuel",
                ],
                hyde_instruction=(
                    "Write as a process description would: name the conversion technology, "
                    "operating conditions, inputs, and product yields."
                ),
            ),
            QueryClassSpec(
                name="logistics",
                keywords=[
                    "collection",
                    "transport",
                    "storage",
                    "cost",
                    "baling",
                    "chipping",
                    "logistics",
                ],
                hyde_instruction=(
                    "Write as a supply chain analysis would: describe collection methods, "
                    "equipment, storage, and costs per ton."
                ),
            ),
        ],
    )
    return DomainProfile(config=config, directory=Path(__file__).parents[1] / "domain")
