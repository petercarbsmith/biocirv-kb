from sci_rag.config import Settings
from sci_rag.embed.cache import QueryEmbeddingCache
from sci_rag.embed.local_hash import LocalHashEmbedder
from sci_rag.embed.provider import (
    EmbeddingDimensionError,
    EmbeddingProvider,
    EmbeddingTask,
    l2_normalize,
)


def get_embedder(settings: Settings) -> EmbeddingProvider:
    """The embedder selected by ``SCI_RAG_EMBEDDING_PROVIDER``."""
    if settings.embedding_provider == "local-hash":
        return LocalHashEmbedder(settings.embedding_dim)
    # Imported lazily so offline use never touches the Google SDK.
    from sci_rag.embed.google import GoogleEmbedder

    return GoogleEmbedder(settings)


__all__ = [
    "EmbeddingDimensionError",
    "EmbeddingProvider",
    "EmbeddingTask",
    "LocalHashEmbedder",
    "QueryEmbeddingCache",
    "get_embedder",
    "l2_normalize",
]
