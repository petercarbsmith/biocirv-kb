from sci_rag.ingest.chunker import chunk_document
from sci_rag.ingest.parsers import ParsedDocument, parse_markdown

MARKDOWN = """# Biomass Conversion Handbook

## 1 Introduction

Agricultural residues are an abundant feedstock for biofuel production.
Their availability varies strongly by county and by crop calendar.

## 2 Methods

### 2.1 Feedstock handling

Almond prunings are chipped in the field before transport. Moisture content
at collection typically ranges from 30 to 45 percent.

| Feedstock | Yield (t/ac) | Moisture (%) |
|-----------|--------------|--------------|
| Almond prunings | 1.2 | 38 |
| Rice straw | 2.9 | 22 |

Chipped material is screened to remove oversize particles before storage.
"""


def _chunks(markdown: str = MARKDOWN, **kwargs):  # type: ignore[no-untyped-def]
    doc = parse_markdown(markdown, fallback_title="fallback")
    return chunk_document(doc, **kwargs)


def test_title_comes_from_h1() -> None:
    doc = parse_markdown(MARKDOWN, fallback_title="fallback")
    assert doc.title == "Biomass Conversion Handbook"


def test_every_chunk_carries_a_breadcrumb() -> None:
    for chunk in _chunks():
        first_line = chunk.content.splitlines()[0]
        assert first_line.startswith("Biomass Conversion Handbook")


def test_section_paths_track_heading_hierarchy() -> None:
    chunks = _chunks()
    paths = [c.section_path for c in chunks]
    assert "1 Introduction" in paths
    assert any(p == "2 Methods > 2.1 Feedstock handling" for p in paths)


def test_tables_are_isolated_and_intact() -> None:
    tables = [c for c in _chunks() if c.is_table]
    assert len(tables) == 1
    assert "Almond prunings" in tables[0].body
    assert "Rice straw" in tables[0].body
    # The table header never bleeds into a prose chunk. (Up to two trailing
    # data rows may carry over as bounded overlap; that is by design.)
    for chunk in _chunks():
        if not chunk.is_table:
            assert "| Feedstock |" not in chunk.content
            assert "|---" not in chunk.content


def test_chunks_do_not_span_sections() -> None:
    for chunk in _chunks():
        assert "Introduction" not in chunk.body or "chipped" not in chunk.body.lower()


def test_overlap_carries_tail_into_next_chunk() -> None:
    # Force tiny chunks so the same section splits into several.
    long_section = "# T\n\n## S\n\n" + "\n\n".join(
        f"Sentence number {i} talks about biomass logistics in detail." for i in range(12)
    )
    doc = parse_markdown(long_section, fallback_title="T")
    chunks = chunk_document(doc, target_tokens=40, overlap_tokens=15)
    assert len(chunks) >= 2
    # The start of chunk N+1 repeats the tail of chunk N.
    tail_sentence = chunks[0].body.split("Sentence")[-1]
    marker = f"Sentence{tail_sentence.split('.')[0]}."
    assert marker in chunks[1].body


def test_overlap_does_not_cross_headings() -> None:
    md = (
        "# T\n\n## A\n\nAlpha content sentence one. Alpha content sentence two.\n\n"
        "## B\n\nBeta content only here."
    )
    doc = parse_markdown(md, fallback_title="T")
    chunks = chunk_document(doc, target_tokens=30, overlap_tokens=20)
    beta = [c for c in chunks if c.section_path == "B"]
    assert beta and all("Alpha" not in c.body for c in beta)


def test_oversized_paragraph_splits_on_sentences() -> None:
    huge = " ".join(f"This is sentence {i} about pretreatment chemistry." for i in range(80))
    doc = ParsedDocument(title="T", raw_text=huge)
    chunks = chunk_document(doc, target_tokens=100, overlap_tokens=0)
    assert len(chunks) > 1
    assert all(c.token_count <= 160 for c in chunks)  # header adds a little


def test_raw_text_heading_and_table_heuristics() -> None:
    raw = (
        "2.1 Feedstock Supply\n\n"
        "Rice straw is collected after harvest and baled for storage. The bales are wrapped.\n\n"
        "Feedstock\tYield\tMoisture\nRice straw\t2.9\t22\nAlmond\t1.2\t38\n\n"
        "MATERIALS AND METHODS\n\n"
        "Samples were dried at 105 degrees Celsius until mass stabilized overnight."
    )
    doc = ParsedDocument(title="Report", raw_text=raw)
    chunks = chunk_document(doc)
    assert any(c.is_table for c in chunks)
    paths = {c.section_path for c in chunks}
    assert "2.1 Feedstock Supply" in paths
    assert "MATERIALS AND METHODS" in paths


def test_chunking_is_deterministic() -> None:
    a = [c.content for c in _chunks()]
    b = [c.content for c in _chunks()]
    assert a == b


def test_dehyphenation_and_page_numbers() -> None:
    raw = "Fermentation of ligno-\ncellulosic feedstocks is challenging for many reasons.\n\n42\n\nThe process continues normally afterward with steady conversion rates."
    doc = ParsedDocument(title="T", raw_text=raw)
    chunks = chunk_document(doc)
    joined = " ".join(c.body for c in chunks)
    assert "lignocellulosic" in joined
    assert "\n42\n" not in joined


def test_figures_are_isolated_and_carry_mention_context() -> None:
    md = (
        "# Biomass Atlas\n\n"
        "## 3 Results\n\n"
        "Figure 1 shows the spatial residue yield across the Central Valley.\n\n"
        "<!-- image -->\n\n"
        "Figure 1: Spatial residue yield map for almond and rice feedstocks.\n\n"
        "Downstream collection depots are sited near high-density counties."
    )
    # By default (extract_figures=False), <!-- image --> is stripped and captions stay prose.
    default_doc = parse_markdown(md, fallback_title="fallback")
    default_chunks = chunk_document(default_doc)
    assert not any(c.is_figure for c in default_chunks)
    assert all("<!-- image -->" not in c.content for c in default_chunks)

    # When opted in (extract_figures=True), figures become dedicated chunks with mention context.
    opt_in_doc = parse_markdown(md, fallback_title="fallback", extract_figures=True)
    chunks = chunk_document(opt_in_doc, extract_figures=True)
    figures = [c for c in chunks if c.is_figure]
    assert len(figures) == 1
    fig_chunk = figures[0]
    assert fig_chunk.section_path == "3 Results"
    assert "[Figure 1] Figure 1: Spatial residue yield map" in fig_chunk.body
    assert "Context from text: Figure 1 shows the spatial residue yield" in fig_chunk.body
    assert fig_chunk.figure is not None
    assert fig_chunk.figure.figure_label == "Figure 1"

    # Figure caption text must not be duplicated into adjacent prose chunks or overlap.
    prose_chunks = [c for c in chunks if not c.is_figure]
    assert prose_chunks
    for prose in prose_chunks:
        assert "[Figure 1]" not in prose.content
        assert "Spatial residue yield map for almond" not in prose.content


def test_compound_figure_captions_and_no_exponential_backtracking() -> None:
    from sci_rag.ingest.chunker import _CAPTION_FIRST_SENTENCE, _INLINE_CAPTION_SPLIT
    from sci_rag.ingest.figures import looks_like_figure_caption

    assert looks_like_figure_caption("Figure 1, 2, and 3: Biomass yield by county.")
    assert looks_like_figure_caption("Figures 2-1 and Fig. 2-2. Pretreatment mass balance.")

    for adversarial in (
        ".Figure0" + ", 0" * 50 + "!",
        ".Figure0" + ",figs0" * 50 + "!",
        "Figure0" + ",figures0" * 50 + "!",
        "Figure0" + ",schemes0" * 50 + "!",
    ):
        assert _INLINE_CAPTION_SPLIT.search(adversarial) is None
        assert _CAPTION_FIRST_SENTENCE.match(adversarial) is None
        assert not looks_like_figure_caption(adversarial)
