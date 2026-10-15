"""Pluggable storage for extracted PDF figure bitmaps.

Local development writes figure PNGs under ``SCI_RAG_IMAGE_DIR``
(``data/processed/images/{document_id}/fig_{index:03d}.png``). Cloud Run
deployments set ``SCI_RAG_IMAGE_BUCKET`` and store the same object layout
under ``gs://{bucket}/images/{document_id}/fig_{index:03d}.png`` using
application-default credentials over the Google Cloud Storage JSON API.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx

from sci_rag.config import Settings, get_settings

_STORAGE_SCOPE = "https://www.googleapis.com/auth/devstorage.read_write"


class ImageStore(Protocol):
    """Persistence contract for extracted figure PNGs."""

    async def put_figure(self, document_id: str, figure_index: int, png_bytes: bytes) -> str:
        """Persist ``png_bytes`` and return the canonical ``image_uri``."""

    async def get_figure_bytes(self, image_uri: str) -> bytes:
        """Read the PNG bytes previously stored at ``image_uri``."""


def _write_local_file(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _read_local_file(target: Path) -> bytes:
    if not target.is_file():
        raise FileNotFoundError(f"Figure image not found: {target}")
    return target.read_bytes()


class LocalImageStore:
    """Filesystem-backed figure store for local development and tests."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)

    async def put_figure(self, document_id: str, figure_index: int, png_bytes: bytes) -> str:
        safe_doc = Path(document_id).name
        target = self.base_dir / safe_doc / f"fig_{figure_index:03d}.png"
        await asyncio.to_thread(_write_local_file, target, png_bytes)
        return str(target)

    async def get_figure_bytes(self, image_uri: str) -> bytes:
        if image_uri.startswith("gs://"):
            bucket, _ = _parse_gs_uri(image_uri)
            gcs = GcsImageStore(bucket=bucket)
            return await gcs.get_figure_bytes(image_uri)
        target = Path(image_uri)
        return await asyncio.to_thread(_read_local_file, target)


def _parse_gs_uri(image_uri: str) -> tuple[str, str]:
    if not image_uri.startswith("gs://"):
        raise ValueError(f"Expected gs:// URI, got {image_uri!r}")
    rest = image_uri[len("gs://") :]
    bucket, sep, object_name = rest.partition("/")
    if not bucket or not sep or not object_name:
        raise ValueError(f"Malformed gs:// URI: {image_uri!r}")
    return bucket, object_name


class _GcsTokenSource:
    """Application-default OAuth token refreshed on demand for GCS calls."""

    def __init__(self) -> None:
        import google.auth

        self._credentials, _ = google.auth.default(scopes=[_STORAGE_SCOPE])

    def _refresh(self) -> None:
        from google.auth.transport.requests import Request

        self._credentials.refresh(Request())

    async def token(self) -> str:
        if not self._credentials.valid:
            await asyncio.to_thread(self._refresh)
        return str(self._credentials.token)


class GcsImageStore:
    """Google Cloud Storage figure store using ADC and httpx."""

    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "images",
        http_client: httpx.AsyncClient | None = None,
        token_provider: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        self.bucket = bucket.strip()
        self.prefix = prefix.strip("/")
        self._http_client = http_client
        self._token_provider = token_provider
        self._token_source: _GcsTokenSource | None = None

    async def _auth_token(self) -> str:
        if self._token_provider is not None:
            return await self._token_provider()
        if self._token_source is None:
            self._token_source = _GcsTokenSource()
        return await self._token_source.token()

    async def put_figure(self, document_id: str, figure_index: int, png_bytes: bytes) -> str:
        safe_doc = Path(document_id).name
        rel_name = f"fig_{figure_index:03d}.png"
        object_name = (
            f"{self.prefix}/{safe_doc}/{rel_name}" if self.prefix else f"{safe_doc}/{rel_name}"
        )
        encoded_name = quote(object_name, safe="")
        url = (
            f"https://storage.googleapis.com/upload/storage/v1/b/{self.bucket}/o"
            f"?uploadType=media&name={encoded_name}"
        )
        token = await self._auth_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "image/png",
        }
        if self._http_client is not None:
            resp = await self._http_client.post(url, content=png_bytes, headers=headers)
            resp.raise_for_status()
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, content=png_bytes, headers=headers)
                resp.raise_for_status()
        return f"gs://{self.bucket}/{object_name}"

    async def get_figure_bytes(self, image_uri: str) -> bytes:
        if not image_uri.startswith("gs://"):
            return await asyncio.to_thread(_read_local_file, Path(image_uri))
        bucket, object_name = _parse_gs_uri(image_uri)
        encoded_name = quote(object_name, safe="")
        url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{encoded_name}?alt=media"
        token = await self._auth_token()
        headers = {"Authorization": f"Bearer {token}"}
        if self._http_client is not None:
            resp = await self._http_client.get(url, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=headers)
        if resp.status_code == 404:
            raise FileNotFoundError(f"Figure image not found in GCS: {image_uri}")
        resp.raise_for_status()
        return resp.content


def get_image_store(settings: Settings | None = None) -> ImageStore:
    """Select the configured figure image store."""
    cfg = settings or get_settings()
    if cfg.image_bucket:
        return GcsImageStore(bucket=cfg.image_bucket)
    return LocalImageStore(base_dir=cfg.image_dir)
