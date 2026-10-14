"""Modular PDF and Markdown figure extraction, caption matching, and chunk formatting.

Figure parsing is opt-in per document via the corpus manifest (`extract_figures: true`),
the `--extract-figures` CLI flag, or `SCI_RAG_EXTRACT_FIGURES=true`. Keeping figure
extraction in its own module isolates image decoding, caption regexes, and mention
scanning from standard text parsing when figure extraction is not enabled.
"""

from __future__ import annotations

import hashlib
import io
import re
import struct
import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from sci_rag.ingest.parsers import Block

log = structlog.get_logger(__name__)

IMAGE_PLACEHOLDER = "<!-- image -->"

# Minimum pixel dimension on BOTH axes to keep an extracted image (filters logos/icons/rules).
MIN_FIGURE_DIMENSION = 100

_FIGURE_CAPTION_RE = re.compile(
    r"^\s*(?:\*\*|__)?(?P<kind>(?i:Figure|Fig\.?|Scheme))\s*"
    r"(?P<num>(?:[Ss]?\d+[A-Za-z]?|[IVX]+))"
    r"(?:\*\*|__)?(?:\s*[\.\:\-\u2013\u2014]|\s+[A-Z])",
)
_FIGURE_LABEL_SEARCH_RE = re.compile(
    r"\b(?P<kind>Figure|Fig\.?|Scheme)\s*(?P<num>(?:S?\d+[A-Za-z]?|[IVX]+))\b",
    re.IGNORECASE,
)


@dataclass
class ExtractedFigure:
    """Metadata and rendered PNG bytes for an extracted document figure."""

    figure_index: int
    figure_label: str | None = None
    caption: str = ""
    page_number: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    width: int = 0
    height: int = 0
    png_bytes: bytes | None = None
    image_sha256: str | None = None
    mention_snippets: list[str] = field(default_factory=list)


def looks_like_figure_caption(line: str) -> bool:
    """Return True if a line begins with a scientific figure or scheme caption header."""
    stripped = line.strip()
    if len(stripped) < 10:
        return False
    return _FIGURE_CAPTION_RE.match(stripped) is not None


def extract_figure_label(text: str) -> str | None:
    """Return a canonical figure label such as ``'Figure 1'`` or ``'Scheme 3'``."""
    m = _FIGURE_CAPTION_RE.match(text.strip())
    if m is None:
        m = _FIGURE_LABEL_SEARCH_RE.search(text)
    if m is None:
        return None
    kind_raw = m.group("kind").lower()
    prefix = "Scheme" if kind_raw.startswith("scheme") else "Figure"
    return f"{prefix} {m.group('num')}"


def find_figure_mentions(
    label: str | None,
    prose_blocks: Sequence[str],
    *,
    caption: str = "",
    max_mentions: int = 3,
) -> list[str]:
    """Find up to ``max_mentions`` body paragraphs that reference ``label`` (e.g., ``Figure 1``)."""
    if not label:
        return []
    m = _FIGURE_LABEL_SEARCH_RE.search(label)
    if m is None:
        return []
    raw_num = re.escape(m.group("num"))
    pattern = re.compile(
        rf"\b(?:Figures?|Figs?\.?|Schemes?)\s*{raw_num}(?![0-9])\b",
        re.IGNORECASE,
    )
    mentions: list[str] = []
    norm_caption = " ".join(caption.split()).lower()
    for para in prose_blocks:
        clean = " ".join(para.split())
        if not clean:
            continue
        if norm_caption and clean.lower() == norm_caption:
            continue
        if looks_like_figure_caption(clean):
            continue
        if pattern.search(clean):
            if len(clean) > 600:
                clean = clean[:597].rstrip() + "..."
            if clean not in mentions:
                mentions.append(clean)
            if len(mentions) >= max_mentions:
                break
    return mentions


def format_figure_body(block: Block) -> str:
    """Assemble the enriched text body of a figure block for hybrid retrieval."""
    fig = block.figure
    caption = (fig.caption if fig and fig.caption else block.text).strip()
    if caption == IMAGE_PLACEHOLDER:
        caption = ""
    label = (fig.figure_label if fig and fig.figure_label else None) or extract_figure_label(
        caption
    )
    header = f"[{label}]" if label else "[Figure]"
    first_line = f"{header} {caption}".strip() if caption else header
    parts = [first_line]
    if fig and fig.mention_snippets:
        parts.append("Context from text: " + " | ".join(fig.mention_snippets))
    return "\n\n".join(parts)


def image_dimensions(data: bytes) -> tuple[int, int] | None:
    """Inspect PNG or JPEG header bytes to extract ``(width, height)`` without Pillow."""
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    if len(data) >= 4 and data[:2] == b"\xff\xd8":
        idx = 2
        n = len(data)
        while idx + 9 < n:
            if data[idx] != 0xFF:
                idx += 1
                continue
            marker = data[idx + 1]
            if marker in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }:
                h, w = struct.unpack(">HH", data[idx + 5 : idx + 9])
                return int(w), int(h)
            if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                idx += 2
                continue
            if idx + 4 > n:
                break
            seg_len = struct.unpack(">H", data[idx + 2 : idx + 4])[0]
            if seg_len < 2:
                break
            idx += 2 + seg_len
    return None


def encode_rgb_png(width: int, height: int, raw: bytes, channels: int = 3) -> bytes:
    """Encode raw 8-bit grayscale (1ch), RGB (3ch), or RGBA (4ch) scanlines into a valid PNG."""
    color_type = {1: 0, 3: 2, 4: 6}.get(channels)
    if color_type is None or len(raw) != width * height * channels:
        return b""

    def _chunk(tag: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    stride = width * channels
    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)
        row_start = y * stride
        scanlines.extend(raw[row_start : row_start + stride])

    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    compressed = zlib.compress(bytes(scanlines), level=6)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", compressed)
        + _chunk(b"IEND", b"")
    )


def populate_figure_blocks(blocks: list[Block]) -> list[Block]:
    """Filter empty figure placeholders, normalize labels, and pair prose mentions."""
    prose_texts = [b.text for b in blocks if b.kind == "text"]
    cleaned: list[Block] = []
    fig_idx = 0

    for block in blocks:
        if block.kind != "figure":
            cleaned.append(block)
            continue

        fig = block.figure
        caption = (fig.caption if fig and fig.caption else block.text).strip()
        if caption == IMAGE_PLACEHOLDER:
            caption = ""
        has_image = bool(fig and fig.png_bytes)
        if not caption and not has_image:
            # Drop uncaptioned placeholder with no image bytes
            continue

        label = (fig.figure_label if fig and fig.figure_label else None) or extract_figure_label(
            caption
        )
        mentions = find_figure_mentions(label, prose_texts, caption=caption)
        if fig is None:
            fig = ExtractedFigure(
                figure_index=fig_idx,
                figure_label=label,
                caption=caption,
                mention_snippets=mentions,
            )
        else:
            fig.figure_index = fig_idx
            fig.figure_label = label
            fig.caption = caption
            fig.mention_snippets = mentions

        block.text = caption or (label or "Figure")
        block.figure = fig
        cleaned.append(block)
        fig_idx += 1

    return cleaned


def build_docling_converter(*, extract_figures: bool = False) -> Any:
    """Create a Docling DocumentConverter, enabling picture crops only when requested."""
    from docling.document_converter import DocumentConverter

    if not extract_figures:
        return DocumentConverter()

    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import PdfFormatOption

        pipeline_options = PdfPipelineOptions(
            generate_picture_images=True,
            images_scale=2.0,
        )
        return DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            }
        )
    except Exception:
        return DocumentConverter()


def extract_docling_figures(document: Any) -> list[ExtractedFigure]:
    """Extract ``PictureItem`` objects and captions from a Docling document."""
    figures: list[ExtractedFigure] = []
    pictures = getattr(document, "pictures", None) or []
    for idx, pic in enumerate(pictures):
        caption = ""
        caption_fn = getattr(pic, "caption_text", None)
        if callable(caption_fn):
            try:
                caption = str(caption_fn(document) or "").strip()
            except Exception:
                caption = ""

        page_no: int | None = None
        bbox_tuple: tuple[float, float, float, float] | None = None
        prov = getattr(pic, "prov", None)
        if prov and len(prov) > 0:
            first_prov = prov[0]
            pno = getattr(first_prov, "page_no", None)
            if isinstance(pno, int):
                page_no = pno
            bbox = getattr(first_prov, "bbox", None)
            if bbox is not None:
                l_val = getattr(bbox, "l", None)
                t_val = getattr(bbox, "t", None)
                r_val = getattr(bbox, "r", None)
                b_val = getattr(bbox, "b", None)
                if (
                    isinstance(l_val, (int, float))
                    and isinstance(t_val, (int, float))
                    and isinstance(r_val, (int, float))
                    and isinstance(b_val, (int, float))
                ):
                    bbox_tuple = (
                        float(l_val),
                        float(t_val),
                        float(r_val),
                        float(b_val),
                    )

        png_bytes: bytes | None = None
        width = 0
        height = 0
        image_sha256: str | None = None
        pil_img = None
        get_image_fn = getattr(pic, "get_image", None)
        if callable(get_image_fn):
            try:
                pil_img = get_image_fn(document)
            except Exception:
                pil_img = None
        if pil_img is None:
            image_ref = getattr(pic, "image", None)
            pil_img = getattr(image_ref, "pil_image", None) if image_ref is not None else None

        if pil_img is not None:
            try:
                size = getattr(pil_img, "size", None)
                if isinstance(size, tuple) and len(size) == 2:
                    width, height = int(size[0]), int(size[1])
                if width >= MIN_FIGURE_DIMENSION and height >= MIN_FIGURE_DIMENSION:
                    buf = io.BytesIO()
                    pil_img.save(buf, format="PNG")
                    png_bytes = buf.getvalue()
                    image_sha256 = hashlib.sha256(png_bytes).hexdigest()
                else:
                    # Skip tiny icons/logos unless they carry a real figure caption
                    width, height = 0, 0
            except Exception as exc:
                log.debug("docling_picture_encode_failed", error=str(exc))

        if png_bytes is None and not caption:
            continue

        figures.append(
            ExtractedFigure(
                figure_index=idx,
                figure_label=extract_figure_label(caption) if caption else None,
                caption=caption,
                page_number=page_no,
                bbox=bbox_tuple,
                width=width,
                height=height,
                png_bytes=png_bytes,
                image_sha256=image_sha256,
            )
        )
    return figures


def extract_pypdf_figures(reader: Any) -> list[ExtractedFigure]:
    """Extract embedded images >= 100x100 px per page from a ``pypdf.PdfReader``."""
    figures: list[ExtractedFigure] = []
    for page_idx, page in enumerate(reader.pages, start=1):
        try:
            images = getattr(page, "images", None) or []
        except Exception:
            images = []
        for img_file in images:
            try:
                data = getattr(img_file, "data", None)
                if not isinstance(data, (bytes, bytearray)) or not data:
                    continue
                raw_bytes = bytes(data)
                dims = image_dimensions(raw_bytes)
                png_bytes: bytes | None = None
                w, h = 0, 0
                if dims is not None and raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                    w, h = dims
                    png_bytes = raw_bytes
                else:
                    pil_img = getattr(img_file, "image", None)
                    if pil_img is not None:
                        size = getattr(pil_img, "size", None)
                        if isinstance(size, tuple) and len(size) == 2:
                            w, h = int(size[0]), int(size[1])
                            buf = io.BytesIO()
                            pil_img.save(buf, format="PNG")
                            png_bytes = buf.getvalue()
                if png_bytes is None or w < MIN_FIGURE_DIMENSION or h < MIN_FIGURE_DIMENSION:
                    continue
                figures.append(
                    ExtractedFigure(
                        figure_index=len(figures),
                        page_number=page_idx,
                        width=w,
                        height=h,
                        png_bytes=png_bytes,
                        image_sha256=hashlib.sha256(png_bytes).hexdigest(),
                    )
                )
            except Exception as exc:
                log.debug("pypdf_image_extract_failed", page=page_idx, error=str(exc))
    return figures
