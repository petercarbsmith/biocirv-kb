"""Shared fixtures for integration tests that exercise the shipped demo corpus."""

from __future__ import annotations

from pathlib import Path

import pytest

from sci_rag.domain import (
    DomainConfig,
    DomainProfile,
    EntityTypeSpec,
    QueryClassSpec,
    RelationTypeSpec,
)

REPO_ROOT = Path(__file__).parents[2]


def build_demo_domain() -> DomainProfile:
    """Return the demo ontology and query classes independent of the active project domain."""
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
    return DomainProfile(config=config, directory=REPO_ROOT / "domain")


@pytest.fixture()
def demo_domain() -> DomainProfile:
    """Fixture providing the demo DomainProfile for offline integration tests."""
    return build_demo_domain()
