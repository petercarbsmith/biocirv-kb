"""Guards for prose that reports a shipped default or a support claim.

Both guards exist because a defect of this kind is silent, and because it has
now happened twice. Flipping `compression.enabled` in `domain/domain.yaml` is a
one-line edit no test notices, so the pages describing what the demo ships keep
asserting the old state: `docs/architecture.md` reported a paired judged-answer
gate as passed after the v0.3 benchmark re-ran it and it did not hold, and
`docs/evaluation.md` kept the superseded run's table under the sentence "the
demo adopts `compression.enabled: true`". Both are claims about evidence,
contradicted by the evidence. The first was found and corrected by hand; the
second survived that correction, which is the argument for a test.

The support range fails the same way. ADR 0008 sets it, several surfaces repeat
it, and the README kept saying `PostgreSQL 15+` for a floor nothing tested.

So each guard reads the source of truth (the domain profile, `CompressionTuning`,
the ADR) and holds the prose to it, rather than pinning the prose to a literal.
Changing a default is still one line; leaving the pages behind now fails.
"""

import re
from pathlib import Path

from sci_rag.domain import CompressionTuning, load_domain

ROOT = Path(__file__).resolve().parents[2]
DOMAIN_DIR = ROOT / "domain"
METHODOLOGY = ROOT / "docs" / "methodology.md"
ARCHITECTURE = ROOT / "docs" / "architecture.md"
EVALUATION = ROOT / "docs" / "evaluation.md"
ROADMAP = ROOT / "docs" / "ROADMAP.md"
BENCHMARKS = ROOT / "docs" / "benchmarks.md"
CONFIGURATION = ROOT / "docs" / "configuration.md"
ADR_POSTGRES = ROOT / "docs" / "adr" / "0008-supported-postgresql-versions.md"

# Pages that state what the demo ships, and therefore have to be re-read when it
# changes. Each also carries the reasoning for why the default is corpus-specific,
# which is worth keeping whichever way the gate lands.
DEMO_CLAIM_PAGES = (METHODOLOGY, ARCHITECTURE, EVALUATION)

# The subset that also states what a reader gets before writing any domain
# profile at all. `docs/architecture.md` describes the demo; `docs/evaluation.md`
# describes running the gate. Only the specification states both defaults.
MODEL_DEFAULT_PAGES = (METHODOLOGY,)

# Phrasings that assert the demo compresses, and phrasings that assert it does
# not. Neither list can be exhaustive, and neither needs to be: the guard
# requires the page to make the claim matching the config *and* to be free of
# the claim contradicting it, so flipping the config without touching the prose
# fails on the stale half even if the new phrasing is one nobody predicted.
DEMO_COMPRESSES = (
    "on for the shipped demo",
    "demo enables compression",
    "enabled for the shipped demo",
    "shipped demo turns compression on",
    "demo adopts `compression.enabled: true`",
)
DEMO_DOES_NOT_COMPRESS = (
    "off for the shipped demo",
    "demo leaves compression off",
    "disabled for the shipped demo",
    "shipped demo ships with compression off",
    "demo keeps compression off",
    "demo keeps `compression.enabled: false`",
)

# The same shape for the other default a reader could act on: what they get
# before writing any domain profile at all.
MODEL_DEFAULT_COMPRESSES = ("on in the model default", "enabled in the model default")
MODEL_DEFAULT_DOES_NOT = ("off in the model default", "disabled in the model default")

# Summary pages must state the current shipped verdict, not leave it as a
# possibility or preserve the rejected floor's verdict as the current one.
SUMMARY_COMPRESSES = ("compression defaults on",)
SUMMARY_DOES_NOT_COMPRESS = ("compression defaults off",)
SUMMARY_HEDGES = ("compression may default on",)

# Surfaces a reader meets the support range through. `docs/planning/` is a
# historical record and excluded from the site, so it is excluded here too.
POSTGRES_CLAIM_PAGES = (
    ROOT / "README.md",
    ROOT / "docs" / "quickstart.md",
    ROOT / "docs" / "faq.md",
)

# Retired floors. The ADR discusses 15 as history and as what will probably keep
# working, so it is not held to this; support is a claim about what is tested.
RETIRED_POSTGRES_CLAIMS = ("postgresql 15+", "postgresql 15 or newer")


def _text(page: Path) -> str:
    """The page as one lowercase line, so a phrase split by wrapping still matches."""
    return re.sub(r"\s+", " ", page.read_text(encoding="utf-8")).casefold()


# "16 through 18" and "16 to 18" are the same claim; the numbers are what a
# page can get wrong, so the pattern pins those and lets the preposition vary.
SUPPORTED_RANGE = re.compile(r"postgresql (\d+) (?:through|to) (\d+)")


def _supported_range() -> tuple[str, str]:
    """The range as ADR 0008 states it, which is what every page has to repeat."""
    found = SUPPORTED_RANGE.search(_text(ADR_POSTGRES))
    assert found, f"ADR 0008 should state a supported range matching {SUPPORTED_RANGE.pattern!r}"
    return found.group(1), found.group(2)


def _assert_prose_matches(
    pages: tuple[Path, ...],
    enabled: bool,
    on_phrases: tuple[str, ...],
    off_phrases: tuple[str, ...],
    subject: str,
    source: str,
) -> None:
    """Hold every page to the claim `enabled` supports, and to no other."""
    wanted, unwanted = (on_phrases, off_phrases) if enabled else (off_phrases, on_phrases)
    state = "on" if enabled else "off"
    verb = "enables" if enabled else "disables"

    for page in pages:
        text = _text(page)
        where = page.relative_to(ROOT)

        assert any(phrase in text for phrase in wanted), (
            f"{where} should say compression is {state} {subject}, because {source} "
            f"{verb} it. Expected one of {list(wanted)}"
        )
        stale = [phrase for phrase in unwanted if phrase in text]
        assert not stale, (
            f"{where} still claims compression is not {state} {subject} ({stale}), "
            f"but {source} says otherwise"
        )


def test_compression_prose_matches_the_shipped_demo_default() -> None:
    _assert_prose_matches(
        pages=DEMO_CLAIM_PAGES,
        enabled=load_domain(DOMAIN_DIR).config.compression.enabled,
        on_phrases=DEMO_COMPRESSES,
        off_phrases=DEMO_DOES_NOT_COMPRESS,
        subject="for the shipped demo",
        source="domain/domain.yaml",
    )


def test_compression_prose_matches_the_model_default() -> None:
    _assert_prose_matches(
        pages=MODEL_DEFAULT_PAGES,
        enabled=CompressionTuning().enabled,
        on_phrases=MODEL_DEFAULT_COMPRESSES,
        off_phrases=MODEL_DEFAULT_DOES_NOT,
        subject="in the model default",
        source="CompressionTuning",
    )


def test_compression_summary_pages_match_the_shipped_demo_default() -> None:
    pages = (ROADMAP, BENCHMARKS)
    _assert_prose_matches(
        pages=pages,
        enabled=load_domain(DOMAIN_DIR).config.compression.enabled,
        on_phrases=SUMMARY_COMPRESSES,
        off_phrases=SUMMARY_DOES_NOT_COMPRESS,
        subject="for the shipped demo",
        source="domain/domain.yaml",
    )
    for page in pages:
        hedges = [phrase for phrase in SUMMARY_HEDGES if phrase in _text(page)]
        assert not hedges, f"{page.relative_to(ROOT)} still hedges the shipped default: {hedges}"


def test_configuration_reference_shows_the_shipped_compression_default() -> None:
    row = re.search(
        r"\| `compression\.enabled` \| bool \| (?P<default>true|false) \|",
        CONFIGURATION.read_text(encoding="utf-8"),
    )
    assert row is not None, "configuration.md should publish the compression.enabled row"

    enabled = load_domain(DOMAIN_DIR).config.compression.enabled
    assert row.group("default") == str(enabled).lower(), (
        "configuration.md should take the shipped compression default from domain/domain.yaml"
    )


def test_the_pages_keep_the_reasoning_for_a_per_corpus_default() -> None:
    """The verdict changes per corpus; the argument for gating it does not."""
    for page in DEMO_CLAIM_PAGES:
        text = _text(page)
        assert "gate" in text, (
            f"{page.relative_to(ROOT)} should keep the judged-answer gate in the picture"
        )
        assert "corpus" in text, (
            f"{page.relative_to(ROOT)} should keep why the default is corpus-specific"
        )
        assert "benchmarks.md" in text, (
            f"{page.relative_to(ROOT)} should point at docs/benchmarks.md for the run"
        )


def test_the_postgresql_support_range_is_stated_the_same_everywhere() -> None:
    floor, ceiling = _supported_range()

    for page in POSTGRES_CLAIM_PAGES:
        text = _text(page)
        where = page.relative_to(ROOT)

        stated = SUPPORTED_RANGE.search(text)
        assert stated, f"{where} should state a PostgreSQL range, as ADR 0008 does"
        assert stated.groups() == (floor, ceiling), (
            f"{where} says PostgreSQL {stated.group(1)} to {stated.group(2)}, but "
            f"ADR 0008 supports {floor} through {ceiling}"
        )

        stale = [claim for claim in RETIRED_POSTGRES_CLAIMS if claim in text]
        assert not stale, f"{where} still claims a retired PostgreSQL floor: {stale}"
