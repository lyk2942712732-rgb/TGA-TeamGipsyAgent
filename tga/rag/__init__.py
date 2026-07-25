"""RAG extension contracts; no knowledge backend is enabled yet."""

from .retrieval import NullRAGRetriever, RAGChunk, RAGQuery, RAGRetriever

__all__ = ["NullRAGRetriever", "RAGChunk", "RAGQuery", "RAGRetriever"]
