"""Unit tests for figure caption parsing, PNG encoding, and image stores."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

import httpx

from sci_rag.ingest.image_store import GcsImageStore, LocalImageStore
from sci_rag.ingest.parsers import (
    Block,
    ExtractedFigure,
    encode_rgb_png,
    extract_figure_label,
    find_figure_mentions,
    image_dimensions,
    looks_like_figure_caption,
    parse_markdown,
    populate_figure_blocks,
)


def test_caption_detection_and_label_extraction() -> None:
    assert looks_like_figure_caption("Figure 1: Feedstock moisture distribution.")
    assert looks_like_figure_caption("Fig. 2a. Pyrolysis yield curve across temperatures.")
    assert looks_like_figure_caption("Scheme 3 - Catalytic upgrading pathway")
    assert not looks_like_figure_caption("Figure 1 shows that moisture drops rapidly in summer.")
    assert not looks_like_figure_caption("Normal prose paragraph without a figure header.")

    assert extract_figure_label("Figure 1: Feedstock moisture distribution.") == "Figure 1"
    assert extract_figure_label("Fig. 2a. Pyrolysis yield curve.") == "Figure 2a"
    assert extract_figure_label("Scheme 3: Upgrading") == "Scheme 3"
    assert extract_figure_label("No label here") is None


def test_find_figure_mentions_excludes_caption_itself() -> None:
    caption = "Figure 2: Reactor temperature profile across beds."
    prose = [
        "As shown in Fig. 2, bed temperature peaks near 500 C. Unrelated sentence here.",
        caption,
        "See also Figure 2 for the steady-state regime after 30 minutes.",
    ]
    mentions = find_figure_mentions("Figure 2", prose, caption=caption)
    assert len(mentions) == 2
    assert "As shown in Fig. 2" in mentions[0]
    assert "See also Figure 2" in mentions[1]


def test_encode_rgb_png_and_dimensions_round_trip() -> None:
    width, height = 120, 105
    raw_rgb = b"\x80\x40\x20" * (width * height)
    png_bytes = encode_rgb_png(width, height, raw_rgb)
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert image_dimensions(png_bytes) == (width, height)


def test_populate_figure_blocks_drops_uncaptioned_placeholders_and_pairs_mentions() -> None:
    large_png = encode_rgb_png(140, 110, b"\x00\xff\x00" * (140 * 110))
    blocks = [
        Block(kind="heading", text="Results", level=1),
        Block(
            kind="figure",
            text="<!-- image -->",
            figure=ExtractedFigure(figure_index=0, figure_label=None, caption=""),
        ),
        Block(
            kind="text",
            text="Figure 1 illustrates the seasonal harvest window across counties.",
        ),
        Block(
            kind="figure",
            text="Figure 1: Seasonal harvest window by county and crop.",
            figure=ExtractedFigure(
                figure_index=1,
                figure_label="Figure 1",
                caption="Figure 1: Seasonal harvest window by county and crop.",
                page_number=2,
                width=140,
                height=110,
                png_bytes=large_png,
            ),
        ),
    ]
    populated = populate_figure_blocks(blocks)
    fig_blocks = [b for b in populated if b.kind == "figure"]
    assert len(fig_blocks) == 1
    fig = fig_blocks[0].figure
    assert fig is not None
    assert fig.figure_index == 0
    assert fig.figure_label == "Figure 1"
    assert fig.page_number == 2
    assert fig.width == 140
    assert fig.height == 110
    assert fig.png_bytes == large_png
    assert any("illustrates the seasonal harvest" in m for m in fig.mention_snippets)


def test_parse_markdown_strips_docling_image_comments_and_extracts_figures() -> None:
    png = encode_rgb_png(120, 100, b"\x11\x22\x33" * (120 * 100))
    extracted = [
        ExtractedFigure(
            figure_index=0,
            figure_label=None,
            caption="",
            page_number=4,
            width=120,
            height=100,
            png_bytes=png,
            image_sha256="ab" * 32,
        )
    ]
    md = (
        "# Paper Title\n\n"
        "<!-- image -->\n\n"
        "Figure 4: Mass balance for the two-stage gasifier.\n\n"
        "As summarized in Figure 4, char recycle improves carbon conversion to 88 percent."
    )
    doc = parse_markdown(md, fallback_title="fallback", extracted_figures=extracted)
    assert doc.blocks is not None
    assert all("<!-- image -->" not in b.text for b in doc.blocks)
    fig_blocks = [b for b in doc.blocks if b.kind == "figure"]
    assert len(fig_blocks) == 1
    assert fig_blocks[0].figure is not None
    assert fig_blocks[0].figure.figure_label == "Figure 4"
    assert fig_blocks[0].figure.page_number == 4
    assert fig_blocks[0].figure.png_bytes == png
    assert any("char recycle improves" in m for m in fig_blocks[0].figure.mention_snippets)


async def test_local_image_store_round_trip(tmp_path: Path) -> None:
    store = LocalImageStore(tmp_path / "images")
    payload = encode_rgb_png(100, 100, b"\x10\x20\x30" * (100 * 100))
    uri = await store.put_figure("doc-123", 0, payload)
    assert uri.endswith("doc-123/fig_000.png")
    loaded = await store.get_figure_bytes(uri)
    assert loaded == payload


async def test_gcs_image_store_round_trip() -> None:
    objects: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") == "Bearer fake-gcs-token"
        if request.method == "POST" and "/upload/storage/v1/b/test-bucket/o" in str(request.url):
            name = request.url.params["name"]
            objects[name] = request.content
            return httpx.Response(200, json={"name": name})
        if request.method == "GET" and "/storage/v1/b/test-bucket/o/" in str(request.url):
            encoded = str(request.url.path).split("/o/", 1)[1]
            name = unquote(encoded)
            if name not in objects:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, content=objects[name])
        return httpx.Response(500)

    async def fake_token() -> str:
        return "fake-gcs-token"

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        store = GcsImageStore(
            "test-bucket",
            prefix="figures",
            http_client=client,
            token_provider=fake_token,
        )
        payload = b"\x89PNG\r\n\x1a\nfake"
        uri = await store.put_figure("doc-abc", 2, payload)
        assert uri == "gs://test-bucket/figures/doc-abc/fig_002.png"
        fetched = await store.get_figure_bytes(uri)
        assert fetched == payload
    finally:
        await client.aclose()


def test_manifest_per_document_extract_figures_flag(tmp_path: Path) -> None:
    from sci_rag.ingest.manifest import load_manifest

    manifest_file = tmp_path / "corpus.jsonl"
    manifest_file.write_text(
        '{"path": "a.pdf", "title": "Doc A", "license_class": "public", "extract_figures": true}\n'
        '{"path": "b.pdf", "title": "Doc B", "license_class": "public", "extract_figures": false}\n'
        '{"path": "c.pdf", "title": "Doc C", "license_class": "public"}\n',
        encoding="utf-8",
    )
    entries = load_manifest(manifest_file)
    assert len(entries) == 3
    assert entries[0].extract_figures is True
    assert entries[1].extract_figures is False
    assert entries[2].extract_figures is None


def test_chapter_numbered_and_multi_figure_captions_and_page_matching() -> None:
    from sci_rag.ingest.chunker import chunk_document
    from sci_rag.ingest.parsers import ParsedDocument

    assert looks_like_figure_caption("Figure 1-1. Almond Harvest by anatomical fraction.")
    assert looks_like_figure_caption(
        "Figure 2-3, Figure 2-4, Figure 2-5. Illustration of material flows."
    )
    assert (
        extract_figure_label("Figure 1-1. Almond Harvest by anatomical fraction.") == "Figure 1-1"
    )
    assert (
        extract_figure_label("Figure 2-3, Figure 2-4, Figure 2-5. Illustration of material flows.")
        == "Figure 2-3"
    )

    cover_png = encode_rgb_png(120, 100, b"\x01\x01\x01" * (120 * 100))
    fig_png = encode_rgb_png(140, 110, b"\x22\x44\x88" * (140 * 110))
    doc = ParsedDocument(
        title="Biomass Report",
        raw_text=(
            "Cover Page\n\n\f\n\n"
            "As shown in Figure 1-1, almond hulls account for half of the harvest.\n\n"
            "Figure 1-1. Almond Harvest by anatomical fraction based on county data."
        ),
        blocks=None,
        page_count=2,
        metadata={"parser": "pypdf"},
        figures=[
            ExtractedFigure(
                figure_index=0,
                page_number=1,
                width=120,
                height=100,
                png_bytes=cover_png,
            ),
            ExtractedFigure(
                figure_index=1,
                page_number=2,
                width=140,
                height=110,
                png_bytes=fig_png,
            ),
        ],
    )
    drafts = chunk_document(doc, extract_figures=True)
    fig_drafts = [d for d in drafts if d.is_figure]
    assert len(fig_drafts) == 1
    assert fig_drafts[0].figure is not None
    assert fig_drafts[0].figure.figure_label == "Figure 1-1"
    # Matches the Page 2 chart image rather than the Page 1 cover image
    assert fig_drafts[0].figure.page_number == 2
    assert fig_drafts[0].figure.png_bytes == fig_png
    assert any("almond hulls account for half" in m for m in fig_drafts[0].figure.mention_snippets)
