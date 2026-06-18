"""KnowledgeArticle model — FAQ / policy articles for chatbot RAG."""
from __future__ import annotations
from datetime import datetime, timezone
from ..extensions import db


class KnowledgeArticle(db.Model):
    """Store admin-authored FAQ articles that the chatbot retrieves at query time.

    The chatbot does a keyword search over title + keywords + body to find the
    most relevant article for a customer question — grounding Claude's answer
    in your actual store policies rather than hallucinated guesses.
    """
    __tablename__ = "knowledge_articles"
    __table_args__ = (
        db.Index("ix_kb_category", "category"),
        db.Index("ix_kb_is_active", "is_active"),
    )

    id         = db.Column(db.Integer, primary_key=True)
    title      = db.Column(db.String(200), nullable=False)
    category   = db.Column(db.String(80),  nullable=False, default="general")
    # Comma-separated trigger keywords: "return,refund,exchange,policy"
    keywords   = db.Column(db.Text, nullable=True)
    body       = db.Column(db.Text, nullable=False)
    is_active  = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    def matches(self, query: str) -> bool:
        """Return True if this article is relevant to the given query."""
        q = query.lower()
        if self.title and any(w in self.title.lower() for w in q.split()):
            return True
        if self.keywords:
            for kw in self.keywords.lower().split(","):
                if kw.strip() and kw.strip() in q:
                    return True
        return False

    @classmethod
    def search(cls, query: str, limit: int = 3) -> list["KnowledgeArticle"]:
        """Return articles relevant to the query, ordered by keyword match quality."""
        articles = db.session.execute(
            db.select(cls).where(cls.is_active == True)  # noqa: E712
        ).scalars().all()
        scored = []
        q = query.lower()
        for art in articles:
            score = 0
            if art.title and any(w in art.title.lower() for w in q.split() if len(w) > 2):
                score += 3
            if art.keywords:
                for kw in art.keywords.lower().split(","):
                    kw = kw.strip()
                    if kw and kw in q:
                        score += 2
            if art.body and any(w in art.body.lower() for w in q.split() if len(w) > 3):
                score += 1
            if score > 0:
                scored.append((score, art))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [art for _, art in scored[:limit]]

    def __repr__(self) -> str:
        return f"<KnowledgeArticle {self.id}: {self.title}>"
