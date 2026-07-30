"""Independent Retrieval Domain public surface."""

from tga.domain.retrieval.chunks import ChunkLocator, ChunkLocatorKind, DocumentChunk
from tga.domain.retrieval.context_pack import RetrievedContextItem, RetrievedContextPack
from tga.domain.retrieval.corpus import (
    CorpusSource,
    CorpusSourceKind,
    KnowledgeBase,
    OwnerScope,
    OwnerScopeName,
    RetrievalChannel,
    RetrievalPolicy,
    TrustLevel,
)
from tga.domain.retrieval.documents import CorpusDocument, DocumentRevision
from tga.domain.retrieval.indexes import IndexBinding, IndexSnapshot
from tga.domain.retrieval.runs import (
    RetrievalHit,
    RetrievalMethod,
    RetrievalRequest,
    RetrievalRun,
)

__all__ = [
    "ChunkLocator", "ChunkLocatorKind", "CorpusDocument", "CorpusSource",
    "CorpusSourceKind", "DocumentChunk", "DocumentRevision", "IndexBinding", "IndexSnapshot",
    "KnowledgeBase", "OwnerScope", "OwnerScopeName", "RetrievalChannel",
    "RetrievalHit", "RetrievalMethod", "RetrievalPolicy", "RetrievalRequest",
    "RetrievalRun", "RetrievedContextItem", "RetrievedContextPack", "TrustLevel",
]
