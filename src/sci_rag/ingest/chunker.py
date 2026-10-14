"""Structure-aware chunking for technical documents.

A generic recursive splitter throws away the two signals that matter most
in scientific writing: where a statement sits in the section hierarchy, and
whether a numerical table survives in one piece. This chunker keeps both:

1. Normalize the raw text: form feeds, stray page-number lines,
   end-of-line hyphenation, null bytes.
2. Segment into paragraphs; drop tiny fragments.
3. Detect headings (numbered, ALL CAPS, Title Case) and maintain a section
   path, e.g. "2 Methods > 2.1 Feedstock handling".
4. Detect table-like paragraphs (pipes, tab columns, aligned grids) and
   emit each table as its own intact chunk.
5. When ``extract_figures=True`` (or when structured figure blocks are
   present), isolate each figure caption and its prose mentions into a
   dedicated ``is_figure=True`` chunk without carrying overlap into or out
   of the figure.
6. Merge ordinary paragraphs up to a target token budget, splitting
   oversized paragraphs on sentence boundaries, and never merging across a
   section boundary.
7. Carry a tail of trailing overlap into the next prose chunk so no idea is
   stranded on a boundary.
8. Prepend the document title and section path to every chunk, so each one
   makes sense (and embeds well) on its own.

Defaults (800-token target, 150-token overlap) suit dense technical PDFs;
both are parameters.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from sci_rag.ingest.figures import (
    ExtractedFigure,
    format_figure_body,
    looks_like_figure_caption,
    populate_figure_blocks,
)
from sci_rag.ingest.parsers import Block, ParsedDocument
from sci_rag.ingest.tokens import count_tokens


@dataclass
class ChunkDraft:
    content: str  # breadcrumb header + body; what gets embedded and stored
    body: str  # body only; used for content hashing
    token_count: int
    section_path: str | None
    is_table: bool
    is_figure: bool = False
    figure: ExtractedFigure | None = None


def chunk_document(
    doc: ParsedDocument,
    *,
    target_tokens: int = 800,
    overlap_tokens: int = 150,
    extract_figures: bool = False,
) -> list[ChunkDraft]:
    blocks = (
        doc.blocks
        if doc.blocks is not None
        else _segment_raw_text(
            doc.raw_text or "",
            extract_figures=extract_figures or bool(doc.figures),
            extracted_figures=doc.figures,
        )
    )
    return _chunk_blocks(
        blocks, title=doc.title, target_tokens=target_tokens, overlap_tokens=overlap_tokens
    )


# --------------------------------------------------------------------------
# Raw-text segmentation (for PDFs extracted with pypdf, and plain .txt)
# --------------------------------------------------------------------------

_PAGE_NUMBER_LINE = re.compile(r"^\s*\d{1,4}\s*$", flags=re.MULTILINE)
_HYPHEN_BREAK = re.compile(r"([a-z])-\n([a-z])")
_NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+)*)[.)]?\s+\S")
_INLINE_CAPTION_SPLIT = re.compile(
    r"(?<=[.!?\)])\s+(?=(?i:Figure|Fig\.?|Scheme)\s*"
    r"(?:[SsA-Za-z]?\d+(?:[\-\.]\d+)*[A-Za-z]?|[IVX]+)"
    r"(?:\s*(?:,|and)\s*(?i:Figures?|Figs?\.?|Schemes?)?\s*"
    r"(?:[SsA-Za-z]?\d+(?:[\-\.]\d+)*[A-Za-z]?|[IVX]+))*"
    r"\s*[\.\:\-\u2013\u2014]\s+[A-Z])"
)
_CAPTION_FIRST_SENTENCE = re.compile(
    r"^(\s*(?i:Figure|Fig\.?|Scheme)\s*"
    r"(?:[SsA-Za-z]?\d+(?:[\-\.]\d+)*[A-Za-z]?|[IVX]+)"
    r"(?:\s*(?:,|and)\s*(?i:Figures?|Figs?\.?|Schemes?)?\s*"
    r"(?:[SsA-Za-z]?\d+(?:[\-\.]\d+)*[A-Za-z]?|[IVX]+))*"
    r"\s*[\.\:\-\u2013\u2014]\s+[^.!?]+[.!?])\s+([A-Z].*)$"
)


def _normalize(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n")
    if text.count("\n \n") > 20:
        text = re.sub(r"\n \n \n+", "\n\n", text)
        text = re.sub(r"\n \n", " ", text)
    text = text.replace("\f", "\n\n")
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = _PAGE_NUMBER_LINE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _segment_raw_text(
    raw: str,
    *,
    extract_figures: bool = False,
    extracted_figures: Sequence[ExtractedFigure] | None = None,
) -> list[Block]:
    blocks: list[Block] = []
    fig_list = list(extracted_figures or [])
    used_fig_indices: set[int] = set()
    has_page_breaks = "\f" in raw and any(f.page_number is not None for f in fig_list)
    raw_pages = raw.split("\f") if has_page_breaks else [raw]

    def _match_figure_for_page(page_no: int | None) -> ExtractedFigure | None:
        if page_no is not None:
            for idx, fig in enumerate(fig_list):
                if idx not in used_fig_indices and fig.page_number == page_no:
                    used_fig_indices.add(idx)
                    return fig
            for idx, fig in enumerate(fig_list):
                if idx not in used_fig_indices and fig.page_number == page_no - 1:
                    used_fig_indices.add(idx)
                    return fig
            return None
        for idx, fig in enumerate(fig_list):
            if idx not in used_fig_indices:
                used_fig_indices.add(idx)
                return fig
        return None

    for page_idx, page_raw in enumerate(raw_pages, start=1):
        page_no = page_idx if has_page_breaks else None
        for raw_para in _normalize(page_raw).split("\n\n"):
            raw_para = raw_para.strip()
            if not raw_para:
                continue
            if _looks_like_table(raw_para):
                blocks.append(Block(kind="table", text=raw_para))
                continue
            heading_level = _heading_level(raw_para)
            if heading_level:
                blocks.append(Block(kind="heading", text=raw_para.strip(), level=heading_level))
                continue
            flowed_full = re.sub(r"\s*\n\s*", " ", raw_para).strip()
            sub_paras = (
                _INLINE_CAPTION_SPLIT.split(flowed_full) if extract_figures else [flowed_full]
            )
            for flowed in sub_paras:
                flowed = flowed.strip()
                if not flowed:
                    continue
                if extract_figures and looks_like_figure_caption(flowed):
                    trailing_prose = ""
                    if len(flowed) > 220:
                        m_cap = _CAPTION_FIRST_SENTENCE.match(flowed)
                        if m_cap and len(m_cap.group(2)) >= 80:
                            flowed = m_cap.group(1).strip()
                            trailing_prose = m_cap.group(2).strip()
                    matched_fig = _match_figure_for_page(page_no)
                    if matched_fig is not None and not matched_fig.caption:
                        matched_fig.caption = flowed
                    blocks.append(Block(kind="figure", text=flowed, figure=matched_fig))
                    if len(trailing_prose) >= 20:
                        blocks.append(Block(kind="text", text=trailing_prose))
                    continue
                if len(flowed) >= 20:
                    blocks.append(Block(kind="text", text=flowed))

    if extract_figures:
        if not has_page_breaks:
            for idx, leftover in enumerate(fig_list):
                if idx not in used_fig_indices and (leftover.caption or leftover.png_bytes):
                    blocks.append(
                        Block(
                            kind="figure",
                            text=leftover.caption or (leftover.figure_label or "Figure"),
                            figure=leftover,
                        )
                    )
        blocks = populate_figure_blocks(blocks)
    return blocks


def _heading_level(para: str) -> int:
    """0 means "not a heading"."""
    if "\n" in para or len(para) > 90:
        return 0
    line = para.strip()
    numbered = _NUMBERED_HEADING.match(line)
    if numbered and len(line.split()) <= 14:
        return line.count(".", 0, len(numbered.group(1))) + 1
    words = line.split()
    letters = [c for c in line if c.isalpha()]
    if letters and len(words) <= 10 and all(c.isupper() for c in letters):
        return 1
    if (
        2 <= len(words) <= 12
        and not line.endswith((".", ",", ";"))
        and sum(1 for w in words if w[:1].isupper()) / len(words) >= 0.6
        and not line[:1].islower()
    ):
        return 2
    return 0


def _looks_like_table(para: str) -> bool:
    lines = [ln for ln in para.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    piped = sum(1 for ln in lines if ln.count("|") >= 2)
    if piped >= max(2, len(lines) // 2):
        return True
    tabbed = sum(1 for ln in lines if ln.count("\t") >= 2)
    if tabbed >= 2:
        return True
    gridded = sum(1 for ln in lines if len(re.findall(r"\S\s{2,}\S", ln)) >= 2)
    return len(lines) >= 3 and gridded >= 3


# --------------------------------------------------------------------------
# Core: blocks -> chunks
# --------------------------------------------------------------------------


class _SectionTracker:
    """Maintains the current heading breadcrumb as headings stream past."""

    def __init__(self) -> None:
        self._stack: list[tuple[int, str]] = []

    def update(self, level: int, title: str) -> None:
        while self._stack and self._stack[-1][0] >= level:
            self._stack.pop()
        self._stack.append((level, title))

    def path(self) -> str | None:
        if not self._stack:
            return None
        return " > ".join(title for _, title in self._stack)


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(])")


def _split_oversized(paragraph: str, target_tokens: int) -> list[str]:
    """Break a huge paragraph on sentence boundaries; hard-split as a last resort."""
    if count_tokens(paragraph) <= target_tokens:
        return [paragraph]
    pieces: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for sentence in _SENTENCE_SPLIT.split(paragraph):
        sentence_tokens = count_tokens(sentence)
        if sentence_tokens > target_tokens:
            # A single monster sentence: split on word count.
            if current:
                pieces.append(" ".join(current))
                current, current_tokens = [], 0
            words = sentence.split()
            step = max(1, len(words) * target_tokens // max(1, sentence_tokens))
            pieces.extend(" ".join(words[i : i + step]) for i in range(0, len(words), step))
            continue
        if current and current_tokens + sentence_tokens > target_tokens:
            pieces.append(" ".join(current))
            current, current_tokens = [], 0
        current.append(sentence)
        current_tokens += sentence_tokens
    if current:
        pieces.append(" ".join(current))
    return pieces


def _overlap_tail(body: str, overlap_tokens: int) -> str:
    """The trailing sentences of a chunk, up to the overlap budget."""
    if overlap_tokens <= 0:
        return ""
    sentences = _SENTENCE_SPLIT.split(body)
    tail: list[str] = []
    total = 0
    for sentence in reversed(sentences):
        sentence_tokens = count_tokens(sentence)
        if total + sentence_tokens > overlap_tokens and tail:
            break
        tail.insert(0, sentence)
        total += sentence_tokens
        if total >= overlap_tokens:
            break
    return " ".join(tail).strip()


def _overlap_tail_lines(body: str, overlap_tokens: int) -> str:
    """Trailing rows of a table, up to the overlap budget.

    Capped at two rows so a small table is never duplicated wholesale into
    the following prose chunk; the point is continuity ("the material in the
    rows above is then screened..."), not repetition. Unlike prose overlap,
    this can legitimately return nothing: when even one row exceeds the
    budget, carrying it would defeat the cap, so the table hands over no
    overlap at all.
    """
    if overlap_tokens <= 0:
        return ""
    tail: list[str] = []
    total = 0
    for line in reversed(body.splitlines()):
        line_tokens = count_tokens(line)
        if total + line_tokens > overlap_tokens or len(tail) >= 2:
            break
        tail.insert(0, line)
        total += line_tokens
    return "\n".join(tail).strip()


def _chunk_blocks(
    blocks: list[Block], *, title: str, target_tokens: int, overlap_tokens: int
) -> list[ChunkDraft]:
    tracker = _SectionTracker()
    chunks: list[ChunkDraft] = []
    buffer: list[str] = []
    buffer_tokens = 0
    pending_overlap = ""

    def finalize(body: str, *, is_table: bool) -> None:
        nonlocal pending_overlap
        body = body.strip()
        if not body:
            return
        section = tracker.path()
        breadcrumb = title if not section else f"{title} > {section}"
        content = f"{breadcrumb}\n\n{body}"
        chunks.append(
            ChunkDraft(
                content=content,
                body=body,
                token_count=count_tokens(content),
                section_path=section,
                is_table=is_table,
                is_figure=False,
            )
        )
        # A chunk hands its tail to the next chunk so no idea is stranded on
        # a boundary. Tables hand over their last rows.
        if is_table:
            pending_overlap = _overlap_tail_lines(body, overlap_tokens)
        else:
            pending_overlap = _overlap_tail(body, overlap_tokens)

    def finalize_figure(block: Block) -> None:
        nonlocal pending_overlap
        body = format_figure_body(block).strip()
        if not body:
            return
        section = tracker.path()
        breadcrumb = title if not section else f"{title} > {section}"
        content = f"{breadcrumb}\n\n{body}"
        chunks.append(
            ChunkDraft(
                content=content,
                body=body,
                token_count=count_tokens(content),
                section_path=section,
                is_table=False,
                is_figure=True,
                figure=block.figure,
            )
        )
        # Figures are self-contained units and do not hand overlap into prose.
        pending_overlap = ""

    def flush_buffer() -> None:
        nonlocal buffer, buffer_tokens
        if buffer:
            finalize("\n\n".join(buffer), is_table=False)
            buffer, buffer_tokens = [], 0

    def consume_overlap_into_buffer() -> None:
        nonlocal pending_overlap, buffer, buffer_tokens
        if pending_overlap and not buffer:
            buffer = [pending_overlap]
            buffer_tokens = count_tokens(pending_overlap)
        pending_overlap = ""

    for block in blocks:
        if block.kind == "heading":
            flush_buffer()
            # Overlap never crosses a section boundary; the breadcrumb of the
            # next chunk would misattribute the carried text.
            pending_overlap = ""
            tracker.update(block.level or 1, block.text)
            continue
        if block.kind == "table":
            flush_buffer()
            finalize(block.text, is_table=True)
            continue
        if block.kind == "figure":
            flush_buffer()
            finalize_figure(block)
            continue
        for piece in _split_oversized(block.text, target_tokens):
            piece_tokens = count_tokens(piece)
            if buffer and buffer_tokens + piece_tokens > target_tokens:
                flush_buffer()
            consume_overlap_into_buffer()
            buffer.append(piece)
            buffer_tokens += piece_tokens
    flush_buffer()
    return chunks
