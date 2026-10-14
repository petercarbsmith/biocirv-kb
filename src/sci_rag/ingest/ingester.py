"""Ingestion: files in, embedded chunks out.

For each corpus entry: parse, chunk, deduplicate, embed, store, all inside
one transaction per document so a failure never leaves half a document
behind. Re-running ingestion over the same files is a no-op; content
identity is a SHA-256 over the chunked text, enforced by a unique
constraint in the database as the concurrency backstop.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sci_rag.config import get_settings
from sci_rag.db import Chunk, Document, DocumentFigure, new_id, session_scope
from sci_rag.embed.provider import EmbeddingProvider
from sci_rag.ingest.chunker import ChunkDraft, chunk_document
from sci_rag.ingest.image_store import ImageStore, get_image_store
from sci_rag.ingest.manifest import CorpusEntry
from sci_rag.ingest.parsers import parse_file

log = structlog.get_logger(__name__)


@dataclass
class IngestOutcome:
    path: str
    status: str  # "ingested" | "skipped_duplicate" | "failed"
    detail: str = ""
    document_id: str | None = None
    chunk_count: int = 0


@dataclass
class IngestReport:
    outcomes: list[IngestOutcome] = field(default_factory=list)

    @property
    def ingested(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "ingested")

    @property
    def skipped(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "skipped_duplicate")

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "failed")


def content_hash_for(drafts: list[ChunkDraft]) -> str:
    joined = "\n\n".join(d.body for d in drafts)
    normalized = " ".join(joined.split()).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_citation(entry: CorpusEntry, title: str) -> str:
    """A human-readable citation from whatever metadata the manifest offered."""
    pieces: list[str] = []
    if entry.authors:
        pieces.append(", ".join(entry.authors))
    if entry.year:
        pieces.append(f"({entry.year})")
    pieces.append(title if title.endswith(".") else f"{title}.")
    if entry.doi:
        pieces.append(f"https://doi.org/{entry.doi.removeprefix('https://doi.org/')}")
    elif entry.url:
        pieces.append(entry.url)
    return " ".join(pieces)


async def ingest_entries(
    entries: list[CorpusEntry],
    *,
    embedder: EmbeddingProvider,
    target_tokens: int = 800,
    overlap_tokens: int = 150,
    prefer_docling: bool = True,
    extract_figures: bool | None = None,
    image_store: ImageStore | None = None,
) -> IngestReport:
    report = IngestReport()
    for entry in entries:
        outcome = await _ingest_one(
            entry,
            embedder=embedder,
            target_tokens=target_tokens,
            overlap_tokens=overlap_tokens,
            prefer_docling=prefer_docling,
            extract_figures=extract_figures,
            image_store=image_store,
        )
        report.outcomes.append(outcome)
        log.info(
            "ingest_outcome",
            path=outcome.path,
            status=outcome.status,
            chunks=outcome.chunk_count,
            detail=outcome.detail or None,
        )
    return report


async def _ingest_one(
    entry: CorpusEntry,
    *,
    embedder: EmbeddingProvider,
    target_tokens: int,
    overlap_tokens: int,
    prefer_docling: bool,
    extract_figures: bool | None = None,
    image_store: ImageStore | None = None,
) -> IngestOutcome:
    path_str = str(entry.path)
    settings = get_settings()
    should_extract_figures = (
        entry.extract_figures
        if entry.extract_figures is not None
        else (extract_figures if extract_figures is not None else settings.extract_figures)
    )
    try:
        parsed = parse_file(
            entry.path,
            prefer_docling=prefer_docling,
            extract_figures=should_extract_figures,
        )
    except FileNotFoundError:
        return IngestOutcome(path=path_str, status="failed", detail="file not found")
    except Exception as exc:
        return IngestOutcome(path=path_str, status="failed", detail=f"parse error: {exc}")

    if not should_extract_figures and parsed.blocks is not None:
        parsed.blocks = [b for b in parsed.blocks if b.kind != "figure"]

    title = entry.title or parsed.title
    drafts = chunk_document(
        parsed,
        target_tokens=target_tokens,
        overlap_tokens=overlap_tokens,
        extract_figures=should_extract_figures,
    )
    if not should_extract_figures:
        drafts = [d for d in drafts if not d.is_figure]
    if not drafts:
        return IngestOutcome(path=path_str, status="failed", detail="no extractable text")

    digest = content_hash_for(drafts)
    source_ref = entry.url or path_str
    store = image_store or get_image_store(settings)
    try:
        async with session_scope() as session:
            existing_doc = await session.scalar(
                select(Document).where(
                    (Document.content_hash == digest) | (Document.source_ref == source_ref)
                )
            )
            if existing_doc is not None:
                figure_drafts = [d for d in drafts if d.is_figure and d.figure is not None]
                if should_extract_figures and figure_drafts:
                    existing_figs = int(
                        await session.scalar(
                            select(func.count(DocumentFigure.id)).where(
                                DocumentFigure.document_id == existing_doc.id
                            )
                        )
                        or 0
                    )
                    if existing_figs == 0:
                        fig_vectors = await embedder.embed(
                            [d.content for d in figure_drafts], task="document"
                        )
                        max_idx = await session.scalar(
                            select(func.max(Chunk.chunk_index)).where(
                                Chunk.document_id == existing_doc.id
                            )
                        )
                        start_idx = (int(max_idx) + 1) if max_idx is not None else 0
                        backfill_records: list[tuple[str, ChunkDraft]] = []
                        for offset, (draft, vector) in enumerate(
                            zip(figure_drafts, fig_vectors, strict=True)
                        ):
                            chunk_id = new_id()
                            session.add(
                                Chunk(
                                    id=chunk_id,
                                    document_id=existing_doc.id,
                                    chunk_index=start_idx + offset,
                                    content=draft.content,
                                    token_count=draft.token_count,
                                    section_path=draft.section_path,
                                    is_table=False,
                                    is_figure=True,
                                    embedding=vector,
                                    embedding_version=embedder.version,
                                )
                            )
                            backfill_records.append((chunk_id, draft))
                        await _persist_figure_records(
                            session, existing_doc.id, backfill_records, store
                        )
                        existing_doc.chunk_count = int(existing_doc.chunk_count or 0) + len(
                            figure_drafts
                        )
                        return IngestOutcome(
                            path=path_str,
                            status="ingested",
                            detail=f"added {len(figure_drafts)} figure(s) to document {existing_doc.id}",
                            document_id=existing_doc.id,
                            chunk_count=len(figure_drafts),
                        )
                return IngestOutcome(
                    path=path_str,
                    status="skipped_duplicate",
                    detail=f"same content as document {existing_doc.id}",
                    document_id=existing_doc.id,
                )

            vectors = await embedder.embed([d.content for d in drafts], task="document")

            document = Document(
                title=title,
                source=entry.source,
                source_ref=entry.url or path_str,
                authors=entry.authors,
                publication_year=entry.year,
                doi=entry.doi,
                journal=entry.journal,
                formatted_citation=build_citation(entry, title),
                license_class=entry.license_class,
                license_source=entry.license_source
                or ("manifest" if entry.license_class != "unknown" else None),
                content_hash=digest,
                page_count=parsed.page_count,
                chunk_count=len(drafts),
                extra={
                    "parser": parsed.metadata.get("parser", "text"),
                    "ingested_at_utc": datetime.now(UTC).isoformat(),
                },
            )
            session.add(document)
            await session.flush()

            figure_records: list[tuple[str, ChunkDraft]] = []
            for index, (draft, vector) in enumerate(zip(drafts, vectors, strict=True)):
                chunk_id = new_id()
                session.add(
                    Chunk(
                        id=chunk_id,
                        document_id=document.id,
                        chunk_index=index,
                        content=draft.content,
                        token_count=draft.token_count,
                        section_path=draft.section_path,
                        is_table=draft.is_table,
                        is_figure=draft.is_figure,
                        embedding=vector,
                        embedding_version=embedder.version,
                    )
                )
                if draft.is_figure and draft.figure is not None:
                    figure_records.append((chunk_id, draft))

            if figure_records:
                await _persist_figure_records(session, document.id, figure_records, store)
            document_id = document.id
    except IntegrityError:
        # Another process ingested the same content between our check and our
        # commit; the unique constraint on content_hash is the backstop.
        return IngestOutcome(
            path=path_str, status="skipped_duplicate", detail="concurrent duplicate"
        )
    except Exception as exc:
        return IngestOutcome(path=path_str, status="failed", detail=f"{type(exc).__name__}: {exc}")

    return IngestOutcome(
        path=path_str,
        status="ingested",
        document_id=document_id,
        chunk_count=len(drafts),
    )


async def _persist_figure_records(
    session: AsyncSession,
    document_id: str,
    records: list[tuple[str, ChunkDraft]],
    store: ImageStore,
) -> None:
    await session.flush()
    for fig_idx, (chunk_id, draft) in enumerate(records):
        fig = draft.figure
        if fig is None:
            continue
        image_uri: str | None = None
        if fig.png_bytes:
            image_uri = await store.put_figure(document_id, fig_idx, fig.png_bytes)
        bbox_dict: dict[str, float] | None = None
        if fig.bbox is not None and len(fig.bbox) == 4:
            bbox_dict = {
                "l": float(fig.bbox[0]),
                "t": float(fig.bbox[1]),
                "r": float(fig.bbox[2]),
                "b": float(fig.bbox[3]),
            }
        session.add(
            DocumentFigure(
                document_id=document_id,
                chunk_id=chunk_id,
                figure_index=fig_idx,
                figure_label=fig.figure_label,
                caption=fig.caption,
                page_number=fig.page_number,
                bbox=bbox_dict,
                image_uri=image_uri,
                image_sha256=fig.image_sha256 or None,
                width=fig.width or None,
                height=fig.height or None,
            )
        )
