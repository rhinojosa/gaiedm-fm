#!/usr/bin/env python3
"""
Knowledge Base Storage

Manages raw document storage (files + SQLite catalog) for the knowledge base.
All captured content goes through here before wiki compilation.

Storage layout:
    ~/.deep-house-radio/kb/
        raw/                    # Raw captured content as .md files
            2026-04-04_instagram_post_abc123.md
            2026-04-04_article_some-title.md
        wiki/                   # Compiled wiki (LLM-generated)
            index.md            # Master index
            concepts/           # Concept articles
            artists/            # Artist profiles
            events/             # Festival/event coverage
            labels/             # Label profiles
            scenes/             # Scene/city articles
        catalog.db              # SQLite catalog of all documents
"""

import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


# =============================================================================
# DATA MODEL
# =============================================================================

@dataclass
class RawDocument:
    """A captured raw document in the knowledge base."""
    doc_id: int = 0
    url: str = ""
    title: str = ""
    platform: str = "web"
    content_type: str = "article"
    author: str = ""
    published_date: str = ""
    captured_at: str = ""
    tags: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    raw_path: str = ""     # Relative path to .md file in raw/
    word_count: int = 0
    compiled: bool = False  # Has this been compiled into the wiki?


@dataclass
class WikiArticle:
    """A compiled wiki article."""
    article_id: int = 0
    slug: str = ""          # e.g. "concepts/melodic-house"
    title: str = ""
    category: str = ""      # concepts, artists, events, labels, scenes
    summary: str = ""       # Brief summary for index
    source_docs: list[int] = field(default_factory=list)  # doc_ids
    backlinks: list[str] = field(default_factory=list)     # slugs that link here
    word_count: int = 0
    created_at: str = ""
    updated_at: str = ""


# =============================================================================
# KNOWLEDGE STORE
# =============================================================================

KB_ROOT = Path.home() / ".deep-house-radio" / "kb"

class KnowledgeStore:
    """Manages the raw document store and catalog."""

    def __init__(self, root: Path | None = None):
        self.root = root or KB_ROOT
        self.raw_dir = self.root / "raw"
        self.wiki_dir = self.root / "wiki"
        self.db_path = self.root / "catalog.db"

        # Ensure directories exist
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.wiki_dir.mkdir(parents=True, exist_ok=True)
        for subdir in ("concepts", "artists", "events", "labels", "scenes"):
            (self.wiki_dir / subdir).mkdir(exist_ok=True)

        self._init_db()

    def _init_db(self):
        """Initialize SQLite catalog."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS raw_documents (
                doc_id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE,
                title TEXT NOT NULL,
                platform TEXT NOT NULL DEFAULT 'web',
                content_type TEXT NOT NULL DEFAULT 'article',
                author TEXT DEFAULT '',
                published_date TEXT DEFAULT '',
                captured_at TEXT NOT NULL,
                tags TEXT DEFAULT '[]',
                images TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}',
                raw_path TEXT NOT NULL,
                word_count INTEGER DEFAULT 0,
                compiled INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS wiki_articles (
                article_id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                summary TEXT DEFAULT '',
                source_docs TEXT DEFAULT '[]',
                backlinks TEXT DEFAULT '[]',
                word_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_raw_platform ON raw_documents(platform);
            CREATE INDEX IF NOT EXISTS idx_raw_compiled ON raw_documents(compiled);
            CREATE INDEX IF NOT EXISTS idx_raw_tags ON raw_documents(tags);
            CREATE INDEX IF NOT EXISTS idx_wiki_category ON wiki_articles(category);
        """)
        conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # -------------------------------------------------------------------------
    # RAW DOCUMENT OPERATIONS
    # -------------------------------------------------------------------------

    def ingest(
        self,
        url: str,
        title: str,
        content_md: str,
        platform: str = "web",
        content_type: str = "article",
        author: str = "",
        published_date: str = "",
        tags: list[str] | None = None,
        images: list[str] | None = None,
        metadata: dict | None = None,
    ) -> RawDocument:
        """Store a captured document. Returns RawDocument with doc_id set."""
        now = datetime.now().isoformat()
        date_prefix = datetime.now().strftime("%Y-%m-%d")

        # Generate filename
        slug = _slugify(title)[:80] if title else _slugify(url)[:80]
        filename = f"{date_prefix}_{platform}_{slug}.md"
        raw_path = str(Path("raw") / filename)

        # Write markdown file
        full_path = self.root / raw_path
        full_path.write_text(content_md, encoding="utf-8")

        word_count = len(content_md.split())

        conn = self._conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO raw_documents
                   (url, title, platform, content_type, author, published_date,
                    captured_at, tags, images, metadata, raw_path, word_count, compiled)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    url, title, platform, content_type, author, published_date,
                    now, json.dumps(tags or []), json.dumps(images or []),
                    json.dumps(metadata or {}), raw_path, word_count,
                ),
            )
            conn.commit()
            doc_id = conn.execute(
                "SELECT doc_id FROM raw_documents WHERE url = ?", (url,)
            ).fetchone()["doc_id"]
        finally:
            conn.close()

        return RawDocument(
            doc_id=doc_id, url=url, title=title, platform=platform,
            content_type=content_type, author=author, published_date=published_date,
            captured_at=now, tags=tags or [], images=images or [],
            metadata=metadata or {}, raw_path=raw_path, word_count=word_count,
        )

    def get_document(self, doc_id: int) -> RawDocument | None:
        """Get a raw document by ID."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM raw_documents WHERE doc_id = ?", (doc_id,)
            ).fetchone()
            if not row:
                return None
            return _row_to_doc(row)
        finally:
            conn.close()

    def get_document_by_url(self, url: str) -> RawDocument | None:
        """Get a raw document by URL (checks for duplicates)."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM raw_documents WHERE url = ?", (url,)
            ).fetchone()
            if not row:
                return None
            return _row_to_doc(row)
        finally:
            conn.close()

    def list_documents(
        self,
        platform: str | None = None,
        content_type: str | None = None,
        compiled: bool | None = None,
        tag: str | None = None,
        limit: int = 100,
    ) -> list[RawDocument]:
        """List raw documents with optional filters."""
        conn = self._conn()
        try:
            query = "SELECT * FROM raw_documents WHERE 1=1"
            params: list = []

            if platform:
                query += " AND platform = ?"
                params.append(platform)
            if content_type:
                query += " AND content_type = ?"
                params.append(content_type)
            if compiled is not None:
                query += " AND compiled = ?"
                params.append(1 if compiled else 0)
            if tag:
                query += " AND tags LIKE ?"
                params.append(f"%{tag}%")

            query += " ORDER BY captured_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [_row_to_doc(r) for r in rows]
        finally:
            conn.close()

    def read_document_content(self, doc: RawDocument) -> str:
        """Read the markdown content of a raw document."""
        full_path = self.root / doc.raw_path
        if full_path.exists():
            return full_path.read_text(encoding="utf-8")
        return ""

    def mark_compiled(self, doc_id: int):
        """Mark a raw document as compiled into the wiki."""
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE raw_documents SET compiled = 1 WHERE doc_id = ?",
                (doc_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def delete_document(self, doc_id: int) -> bool:
        """Delete a raw document and its file."""
        doc = self.get_document(doc_id)
        if not doc:
            return False

        # Delete file
        full_path = self.root / doc.raw_path
        full_path.unlink(missing_ok=True)

        conn = self._conn()
        try:
            conn.execute("DELETE FROM raw_documents WHERE doc_id = ?", (doc_id,))
            conn.commit()
        finally:
            conn.close()
        return True

    # -------------------------------------------------------------------------
    # WIKI OPERATIONS
    # -------------------------------------------------------------------------

    def save_wiki_article(
        self,
        slug: str,
        title: str,
        content_md: str,
        category: str,
        summary: str = "",
        source_docs: list[int] | None = None,
    ) -> WikiArticle:
        """Save a wiki article (create or update)."""
        now = datetime.now().isoformat()
        wiki_path = self.wiki_dir / f"{slug}.md"
        wiki_path.parent.mkdir(parents=True, exist_ok=True)
        wiki_path.write_text(content_md, encoding="utf-8")

        word_count = len(content_md.split())

        conn = self._conn()
        try:
            existing = conn.execute(
                "SELECT article_id, created_at FROM wiki_articles WHERE slug = ?",
                (slug,),
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE wiki_articles
                       SET title=?, category=?, summary=?, source_docs=?,
                           word_count=?, updated_at=?
                       WHERE slug=?""",
                    (title, category, summary, json.dumps(source_docs or []),
                     word_count, now, slug),
                )
                created_at = existing["created_at"]
                article_id = existing["article_id"]
            else:
                conn.execute(
                    """INSERT INTO wiki_articles
                       (slug, title, category, summary, source_docs, word_count,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (slug, title, category, summary, json.dumps(source_docs or []),
                     word_count, now, now),
                )
                created_at = now
                article_id = conn.execute(
                    "SELECT article_id FROM wiki_articles WHERE slug = ?", (slug,)
                ).fetchone()["article_id"]

            conn.commit()
        finally:
            conn.close()

        return WikiArticle(
            article_id=article_id, slug=slug, title=title, category=category,
            summary=summary, source_docs=source_docs or [], word_count=word_count,
            created_at=created_at, updated_at=now,
        )

    def get_wiki_article(self, slug: str) -> WikiArticle | None:
        """Get a wiki article by slug."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM wiki_articles WHERE slug = ?", (slug,)
            ).fetchone()
            if not row:
                return None
            return _row_to_article(row)
        finally:
            conn.close()

    def list_wiki_articles(self, category: str | None = None) -> list[WikiArticle]:
        """List wiki articles, optionally filtered by category."""
        conn = self._conn()
        try:
            if category:
                rows = conn.execute(
                    "SELECT * FROM wiki_articles WHERE category = ? ORDER BY title",
                    (category,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM wiki_articles ORDER BY category, title"
                ).fetchall()
            return [_row_to_article(r) for r in rows]
        finally:
            conn.close()

    def read_wiki_content(self, slug: str) -> str:
        """Read the markdown content of a wiki article."""
        wiki_path = self.wiki_dir / f"{slug}.md"
        if wiki_path.exists():
            return wiki_path.read_text(encoding="utf-8")
        return ""

    # -------------------------------------------------------------------------
    # STATS
    # -------------------------------------------------------------------------

    def stats(self) -> dict:
        """Get knowledge base statistics."""
        conn = self._conn()
        try:
            raw_count = conn.execute("SELECT COUNT(*) as c FROM raw_documents").fetchone()["c"]
            raw_compiled = conn.execute("SELECT COUNT(*) as c FROM raw_documents WHERE compiled=1").fetchone()["c"]
            raw_words = conn.execute("SELECT COALESCE(SUM(word_count), 0) as w FROM raw_documents").fetchone()["w"]
            wiki_count = conn.execute("SELECT COUNT(*) as c FROM wiki_articles").fetchone()["c"]
            wiki_words = conn.execute("SELECT COALESCE(SUM(word_count), 0) as w FROM wiki_articles").fetchone()["w"]

            platforms = {}
            for row in conn.execute("SELECT platform, COUNT(*) as c FROM raw_documents GROUP BY platform"):
                platforms[row["platform"]] = row["c"]

            categories = {}
            for row in conn.execute("SELECT category, COUNT(*) as c FROM wiki_articles GROUP BY category"):
                categories[row["category"]] = row["c"]

            return {
                "raw_documents": raw_count,
                "raw_compiled": raw_compiled,
                "raw_uncompiled": raw_count - raw_compiled,
                "raw_total_words": raw_words,
                "wiki_articles": wiki_count,
                "wiki_total_words": wiki_words,
                "platforms": platforms,
                "wiki_categories": categories,
            }
        finally:
            conn.close()


# =============================================================================
# HELPERS
# =============================================================================

def _slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text.strip("-")


def _row_to_doc(row: sqlite3.Row) -> RawDocument:
    return RawDocument(
        doc_id=row["doc_id"],
        url=row["url"],
        title=row["title"],
        platform=row["platform"],
        content_type=row["content_type"],
        author=row["author"],
        published_date=row["published_date"],
        captured_at=row["captured_at"],
        tags=json.loads(row["tags"]),
        images=json.loads(row["images"]),
        metadata=json.loads(row["metadata"]),
        raw_path=row["raw_path"],
        word_count=row["word_count"],
        compiled=bool(row["compiled"]),
    )


def _row_to_article(row: sqlite3.Row) -> WikiArticle:
    return WikiArticle(
        article_id=row["article_id"],
        slug=row["slug"],
        title=row["title"],
        category=row["category"],
        summary=row["summary"],
        source_docs=json.loads(row["source_docs"]),
        backlinks=json.loads(row.get("backlinks", "[]") if isinstance(row.get("backlinks"), str) else "[]"),
        word_count=row["word_count"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# =============================================================================
# CLI
# =============================================================================

def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Knowledge base storage")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("stats", help="Show KB statistics")
    sub.add_parser("list", help="List raw documents")
    sub.add_parser("wiki-list", help="List wiki articles")

    get_cmd = sub.add_parser("get", help="Get document content")
    get_cmd.add_argument("doc_id", type=int)

    args = parser.parse_args()
    store = KnowledgeStore()

    if args.command == "stats":
        s = store.stats()
        print(f"Knowledge Base: {store.root}")
        print(f"  Raw documents:  {s['raw_documents']} ({s['raw_total_words']:,} words)")
        print(f"    Compiled:     {s['raw_compiled']}")
        print(f"    Pending:      {s['raw_uncompiled']}")
        print(f"  Wiki articles:  {s['wiki_articles']} ({s['wiki_total_words']:,} words)")
        if s["platforms"]:
            print(f"  Platforms:      {s['platforms']}")
        if s["wiki_categories"]:
            print(f"  Categories:     {s['wiki_categories']}")
        return 0

    if args.command == "list":
        docs = store.list_documents()
        for doc in docs:
            compiled = "✓" if doc.compiled else " "
            print(f"  [{compiled}] #{doc.doc_id:4d}  {doc.platform:12s}  {doc.title[:60]}")
        if not docs:
            print("  (no documents)")
        return 0

    if args.command == "wiki-list":
        articles = store.list_wiki_articles()
        for a in articles:
            print(f"  {a.category:12s}  {a.slug:40s}  {a.title}")
        if not articles:
            print("  (no wiki articles)")
        return 0

    if args.command == "get":
        doc = store.get_document(args.doc_id)
        if doc:
            content = store.read_document_content(doc)
            print(content)
        else:
            print(f"Document #{args.doc_id} not found")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
