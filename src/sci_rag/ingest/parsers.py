"""Turn source files into a common shape the chunker understands.

Three routes, one output type:

* PDFs go through Docling when it is installed (best table extraction; it
  is the ``docling`` extra because it pulls a large ML stack) and fall back
  to pypdf otherwise, with a clear log line saying which route ran.
* Markdown is parsed directly: headings and pipe tables become structured
  blocks.
* HTML goes through the standard library's parser (no new dependency) and
  produces the same blocks, with page chrome dropped and tables rendered as
  pipe tables so they travel the Markdown route's path. See
  :mod:`sci_rag.ingest.html_parser`.
* Plain text is passed through raw; the chunker applies its own heuristics.

Docling output is exported to Markdown and re-parsed, so both structured
routes share one battle-tested block segmentation instead of tracking two
document models. Figure extraction (`Block(kind="figure")` and PDF image
crops) is modular and opt-in via ``extract_figures=True`` (see
:mod:`sci_rag.ingest.figures`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Literal

import structlog

from sci_rag.ingest.figures import (
    IMAGE_PLACEHOLDER,
    MIN_FIGURE_DIMENSION,
    ExtractedFigure,
    build_docling_converter,
    encode_rgb_png,
    extract_docling_figures,
    extract_figure_label,
    extract_pypdf_figures,
    find_figure_mentions,
    format_figure_body,
    image_dimensions,
    looks_like_figure_caption,
    populate_figure_blocks,
)

log = structlog.get_logger(__name__)

BlockKind = Literal["text", "heading", "table", "figure"]


@dataclass
class Block:
    kind: BlockKind
    text: str
    level: int = 0  # heading depth; 0 for non-headings
    figure: ExtractedFigure | None = None


@dataclass
class ParsedDocument:
    title: str
    # Exactly one of these is set: structured blocks, or raw text for the
    # chunker's heuristic segmentation.
    blocks: list[Block] | None = None
    raw_text: str | None = None
    page_count: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    figures: list[ExtractedFigure] = field(default_factory=list)


SUPPORTED_SUFFIXES = {".pdf", ".md", ".markdown", ".txt", ".html", ".htm"}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def docling_available() -> bool:
    return find_spec("docling") is not None


def parse_file(
    path: Path,
    *,
    prefer_docling: bool = True,
    extract_figures: bool = False,
) -> ParsedDocument:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        if prefer_docling and docling_available():
            return _parse_pdf_docling(path, extract_figures=extract_figures)
        log.info("pdf_parser_fallback", path=str(path), parser="pypdf")
        return _parse_pdf_pypdf(path, extract_figures=extract_figures)
    if suffix in (".md", ".markdown"):
        return parse_markdown(
            path.read_text(encoding="utf-8"),
            fallback_title=path.stem,
            extract_figures=extract_figures,
        )
    if suffix in (".html", ".htm"):
        from sci_rag.ingest.html_parser import parse_html_file

        return parse_html_file(path)
    if suffix == ".txt":
        return ParsedDocument(title=path.stem, raw_text=path.read_text(encoding="utf-8"))
    raise ValueError(
        f"Unsupported file type {suffix!r} for {path.name}. "
        f"Supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}"
    )


def parse_markdown(
    text: str,
    *,
    fallback_title: str,
    extracted_figures: list[ExtractedFigure] | None = None,
    extract_figures: bool = False,
) -> ParsedDocument:
    """Segment markdown into heading, table, text, and (when enabled) figure blocks.

    Figure extraction is active when ``extract_figures=True`` or when
    ``extracted_figures`` are passed explicitly. Bare ``<!-- image -->`` lines
    emitted by Docling are always stripped so they never pollute prose blocks.
    """
    blocks: list[Block] = []
    paragraph: list[str] = []
    table: list[str] = []
    title: str | None = None
    figure_mode = bool(extract_figures or extracted_figures is not None)
    fig_iter = iter(extracted_figures or [])

    def flush_paragraph() -> None:
        if paragraph:
            para_text = "\n".join(paragraph).strip()
            paragraph.clear()
            if figure_mode and looks_like_figure_caption(para_text):
                caption_one_line = " ".join(para_text.split())
                if (
                    blocks
                    and blocks[-1].kind == "figure"
                    and (not blocks[-1].text or blocks[-1].text == IMAGE_PLACEHOLDER)
                ):
                    blocks[-1].text = caption_one_line
                    if blocks[-1].figure is not None and not blocks[-1].figure.caption:
                        blocks[-1].figure.caption = caption_one_line
                else:
                    matched_fig = next(fig_iter, None)
                    if matched_fig is not None and not matched_fig.caption:
                        matched_fig.caption = caption_one_line
                    blocks.append(
                        Block(
                            kind="figure",
                            text=caption_one_line,
                            figure=matched_fig,
                        )
                    )
            else:
                blocks.append(Block(kind="text", text=para_text))

    def flush_table() -> None:
        if table:
            blocks.append(Block(kind="table", text="\n".join(table).strip()))
            table.clear()

    for line in text.splitlines():
        heading = _HEADING_RE.match(line)
        stripped = line.strip()
        if stripped == IMAGE_PLACEHOLDER:
            flush_paragraph()
            flush_table()
            if figure_mode:
                matched_fig = next(fig_iter, None)
                caption_text = matched_fig.caption if matched_fig and matched_fig.caption else ""
                blocks.append(
                    Block(
                        kind="figure",
                        text=caption_text or IMAGE_PLACEHOLDER,
                        figure=matched_fig,
                    )
                )
            continue
        if heading:
            flush_paragraph()
            flush_table()
            level = len(heading.group(1))
            heading_text = heading.group(2).strip()
            if title is None and level == 1:
                # The first H1 is the document title, not a section: it goes
                # into every chunk's breadcrumb already, so emitting it as a
                # heading too would double it in the section path.
                title = heading_text
            else:
                blocks.append(Block(kind="heading", text=heading_text, level=level))
        elif stripped.startswith("|"):
            flush_paragraph()
            table.append(stripped)
        elif not stripped:
            flush_paragraph()
            flush_table()
        else:
            flush_table()
            paragraph.append(stripped)
    flush_paragraph()
    flush_table()

    if figure_mode:
        for leftover_fig in fig_iter:
            if leftover_fig.caption or leftover_fig.png_bytes:
                blocks.append(
                    Block(
                        kind="figure",
                        text=leftover_fig.caption or (leftover_fig.figure_label or "Figure"),
                        figure=leftover_fig,
                    )
                )
        blocks = populate_figure_blocks(blocks)

    blocks = [b for b in blocks if b.text]
    return ParsedDocument(
        title=title or fallback_title,
        blocks=blocks,
        figures=[b.figure for b in blocks if b.kind == "figure" and b.figure is not None],
    )


def _parse_pdf_pypdf(path: Path, *, extract_figures: bool = False) -> ParsedDocument:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [(page.extract_text() or "") for page in reader.pages]
    meta_title = None
    if reader.metadata is not None:
        meta_title = (reader.metadata.title or "").strip() or None
    figures = extract_pypdf_figures(reader) if extract_figures else []
    return ParsedDocument(
        title=meta_title or path.stem,
        raw_text="\f".join(pages),
        page_count=len(pages),
        metadata={"parser": "pypdf"},
        figures=figures,
    )


def _parse_pdf_docling(path: Path, *, extract_figures: bool = False) -> ParsedDocument:
    log.info("pdf_parser", path=str(path), parser="docling", extract_figures=extract_figures)
    converter = build_docling_converter(extract_figures=extract_figures)
    result = converter.convert(str(path))
    document = result.document
    markdown = document.export_to_markdown()
    extracted_figures = extract_docling_figures(document) if extract_figures else None
    parsed = parse_markdown(
        markdown,
        fallback_title=path.stem,
        extracted_figures=extracted_figures,
        extract_figures=extract_figures,
    )
    try:
        parsed.page_count = document.num_pages()
    except Exception:
        parsed.page_count = None
    parsed.metadata["parser"] = "docling"
    return parsed


__all__ = [
    "IMAGE_PLACEHOLDER",
    "MIN_FIGURE_DIMENSION",
    "SUPPORTED_SUFFIXES",
    "Block",
    "BlockKind",
    "ExtractedFigure",
    "ParsedDocument",
    "docling_available",
    "encode_rgb_png",
    "extract_figure_label",
    "find_figure_mentions",
    "format_figure_body",
    "image_dimensions",
    "looks_like_figure_caption",
    "parse_file",
    "parse_markdown",
    "populate_figure_blocks",
]
