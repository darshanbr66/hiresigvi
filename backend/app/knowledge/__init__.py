from app.knowledge.base import KnowledgeEntry, KnowledgeRetriever
from app.knowledge.keyword_retriever import KeywordKnowledgeRetriever
from app.knowledge.zodiac_public import ZODIAC_PUBLIC_KNOWLEDGE

__all__ = [
    "KnowledgeEntry",
    "KnowledgeRetriever",
    "KeywordKnowledgeRetriever",
    "ZODIAC_PUBLIC_KNOWLEDGE",
    "get_knowledge_retriever",
]


def get_knowledge_retriever() -> KnowledgeRetriever:
    """The retriever the public assistant (Zodi) uses. Today: keyword matching
    over the curated public entries. Swap this one function to move to
    embeddings/pgvector."""
    return KeywordKnowledgeRetriever(ZODIAC_PUBLIC_KNOWLEDGE)
