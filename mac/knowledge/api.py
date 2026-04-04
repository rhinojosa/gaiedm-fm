#!/usr/bin/env python3
"""
Knowledge Base API Server

HTTP API for the knowledge base. Provides endpoints for:
    - Capturing URLs (POST /kb/capture)
    - Listing/reading raw documents (GET /kb/documents)
    - Wiki articles (GET /kb/wiki)
    - Triggering compilation (POST /kb/compile)
    - Health checks (GET /kb/health)
    - Q&A queries (POST /kb/ask)
    - Statistics (GET /kb/stats)

Can run standalone or be integrated into the main API server.
"""

import http.server
import json
import os
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).parents[1]))

from knowledge.store import KnowledgeStore
from knowledge.capture import capture_url, detect_platform, detect_content_type
from knowledge.compiler import compile_new_documents, compile_index, recompile_article
from knowledge.health import run_health_check


KB_PORT = int(os.environ.get("KB_API_PORT", "8002"))

# Shared store instance
_store: KnowledgeStore | None = None


def get_store() -> KnowledgeStore:
    global _store
    if _store is None:
        _store = KnowledgeStore()
    return _store


class KBRequestHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for knowledge base API."""

    def log_message(self, format, *args):
        pass  # Suppress default logging

    def _send_json(self, data: dict | list, status: int = 200):
        body = json.dumps(data, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str):
        self._send_json({"error": message}, status=status)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        body = self.rfile.read(length)
        return json.loads(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # -------------------------------------------------------------------------
    # GET ROUTES
    # -------------------------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)
        store = get_store()

        try:
            if path == "/kb/stats":
                self._send_json(store.stats())

            elif path == "/kb/documents":
                platform = params.get("platform", [None])[0]
                content_type = params.get("type", [None])[0]
                tag = params.get("tag", [None])[0]
                compiled = params.get("compiled", [None])[0]
                limit = int(params.get("limit", [50])[0])

                compiled_bool = None
                if compiled == "true":
                    compiled_bool = True
                elif compiled == "false":
                    compiled_bool = False

                docs = store.list_documents(
                    platform=platform, content_type=content_type,
                    compiled=compiled_bool, tag=tag, limit=limit,
                )
                self._send_json({
                    "count": len(docs),
                    "documents": [
                        {
                            "doc_id": d.doc_id,
                            "url": d.url,
                            "title": d.title,
                            "platform": d.platform,
                            "content_type": d.content_type,
                            "author": d.author,
                            "tags": d.tags,
                            "word_count": d.word_count,
                            "compiled": d.compiled,
                            "captured_at": d.captured_at,
                        }
                        for d in docs
                    ],
                })

            elif path.startswith("/kb/documents/"):
                doc_id = int(path.split("/")[-1])
                doc = store.get_document(doc_id)
                if not doc:
                    self._send_error(404, f"Document #{doc_id} not found")
                    return
                content = store.read_document_content(doc)
                self._send_json({
                    "doc_id": doc.doc_id,
                    "url": doc.url,
                    "title": doc.title,
                    "platform": doc.platform,
                    "content_type": doc.content_type,
                    "author": doc.author,
                    "published_date": doc.published_date,
                    "tags": doc.tags,
                    "images": doc.images,
                    "metadata": doc.metadata,
                    "word_count": doc.word_count,
                    "compiled": doc.compiled,
                    "captured_at": doc.captured_at,
                    "content": content,
                })

            elif path == "/kb/wiki":
                category = params.get("category", [None])[0]
                articles = store.list_wiki_articles(category=category)
                self._send_json({
                    "count": len(articles),
                    "articles": [
                        {
                            "article_id": a.article_id,
                            "slug": a.slug,
                            "title": a.title,
                            "category": a.category,
                            "summary": a.summary,
                            "word_count": a.word_count,
                            "source_count": len(a.source_docs),
                            "updated_at": a.updated_at,
                        }
                        for a in articles
                    ],
                })

            elif path.startswith("/kb/wiki/"):
                slug = path[len("/kb/wiki/"):]
                article = store.get_wiki_article(slug)
                if not article:
                    self._send_error(404, f"Article not found: {slug}")
                    return
                content = store.read_wiki_content(slug)
                self._send_json({
                    "article_id": article.article_id,
                    "slug": article.slug,
                    "title": article.title,
                    "category": article.category,
                    "summary": article.summary,
                    "source_docs": article.source_docs,
                    "word_count": article.word_count,
                    "created_at": article.created_at,
                    "updated_at": article.updated_at,
                    "content": content,
                })

            elif path == "/kb/health":
                issues = run_health_check(store)
                self._send_json({
                    "total_issues": len(issues),
                    "errors": sum(1 for i in issues if i.severity == "error"),
                    "warnings": sum(1 for i in issues if i.severity == "warning"),
                    "info": sum(1 for i in issues if i.severity == "info"),
                    "issues": [
                        {
                            "severity": i.severity,
                            "category": i.category,
                            "message": i.message,
                            "location": i.location,
                            "suggestion": i.suggestion,
                        }
                        for i in issues
                    ],
                })

            elif path == "/kb/detect":
                url = params.get("url", [None])[0]
                if not url:
                    self._send_error(400, "Missing 'url' parameter")
                    return
                self._send_json({
                    "url": url,
                    "platform": detect_platform(url),
                    "content_type": detect_content_type(url),
                })

            else:
                self._send_error(404, f"Unknown endpoint: {path}")

        except Exception as e:
            self._send_error(500, f"Internal error: {str(e)}")

    # -------------------------------------------------------------------------
    # POST ROUTES
    # -------------------------------------------------------------------------

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        store = get_store()

        try:
            body = self._read_body()

            if path == "/kb/capture":
                url = body.get("url")
                if not url:
                    self._send_error(400, "Missing 'url' field")
                    return

                tags = body.get("tags", [])
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",")]

                # Check for duplicate
                existing = store.get_document_by_url(url)
                if existing and not body.get("force", False):
                    self._send_json({
                        "status": "duplicate",
                        "message": f"URL already captured as #{existing.doc_id}",
                        "doc_id": existing.doc_id,
                        "title": existing.title,
                    })
                    return

                doc = capture_url(url, store, tags)
                if doc:
                    self._send_json({
                        "status": "captured",
                        "doc_id": doc.doc_id,
                        "title": doc.title,
                        "platform": doc.platform,
                        "content_type": doc.content_type,
                        "word_count": doc.word_count,
                        "tags": doc.tags,
                    }, status=201)
                else:
                    self._send_error(422, "Failed to extract content from URL")

            elif path == "/kb/capture/batch":
                urls = body.get("urls", [])
                tags = body.get("tags", [])
                if not urls:
                    self._send_error(400, "Missing 'urls' field")
                    return

                results = []
                for url in urls:
                    existing = store.get_document_by_url(url)
                    if existing:
                        results.append({
                            "url": url, "status": "duplicate",
                            "doc_id": existing.doc_id,
                        })
                        continue
                    doc = capture_url(url, store, tags)
                    if doc:
                        results.append({
                            "url": url, "status": "captured",
                            "doc_id": doc.doc_id, "title": doc.title,
                        })
                    else:
                        results.append({"url": url, "status": "failed"})

                captured = sum(1 for r in results if r["status"] == "captured")
                self._send_json({
                    "total": len(urls),
                    "captured": captured,
                    "duplicates": sum(1 for r in results if r["status"] == "duplicate"),
                    "failed": sum(1 for r in results if r["status"] == "failed"),
                    "results": results,
                })

            elif path == "/kb/compile":
                max_docs = body.get("max", 10)
                # Run compilation in background thread
                def _compile():
                    compile_new_documents(store, max_docs=max_docs)

                thread = threading.Thread(target=_compile, daemon=True)
                thread.start()
                self._send_json({
                    "status": "started",
                    "message": f"Compiling up to {max_docs} documents in background",
                })

            elif path == "/kb/compile/article":
                slug = body.get("slug")
                if not slug:
                    self._send_error(400, "Missing 'slug' field")
                    return
                ok = recompile_article(store, slug)
                self._send_json({
                    "status": "success" if ok else "failed",
                    "slug": slug,
                })

            elif path == "/kb/ask":
                question = body.get("question")
                if not question:
                    self._send_error(400, "Missing 'question' field")
                    return

                answer = _answer_question(store, question)
                self._send_json(answer)

            else:
                self._send_error(404, f"Unknown endpoint: {path}")

        except json.JSONDecodeError:
            self._send_error(400, "Invalid JSON body")
        except Exception as e:
            self._send_error(500, f"Internal error: {str(e)}")

    # -------------------------------------------------------------------------
    # DELETE ROUTES
    # -------------------------------------------------------------------------

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        store = get_store()

        try:
            if path.startswith("/kb/documents/"):
                doc_id = int(path.split("/")[-1])
                ok = store.delete_document(doc_id)
                if ok:
                    self._send_json({"status": "deleted", "doc_id": doc_id})
                else:
                    self._send_error(404, f"Document #{doc_id} not found")
            else:
                self._send_error(404, f"Unknown endpoint: {path}")
        except Exception as e:
            self._send_error(500, f"Internal error: {str(e)}")


# =============================================================================
# Q&A ENGINE
# =============================================================================

def _answer_question(store: KnowledgeStore, question: str) -> dict:
    """Answer a question using the wiki as context.

    Reads the wiki index to find relevant articles, then feeds them
    to Claude along with the question.
    """
    # Read index for article summaries
    index_content = store.read_wiki_content("index")
    articles = store.list_wiki_articles()

    # Find relevant articles by keyword matching
    question_lower = question.lower()
    relevant_articles = []

    for article in articles:
        score = 0
        title_lower = article.title.lower()
        summary_lower = article.summary.lower()

        # Check title/summary overlap with question
        for word in question_lower.split():
            if len(word) > 3:
                if word in title_lower:
                    score += 3
                if word in summary_lower:
                    score += 1

        if score > 0:
            relevant_articles.append((score, article))

    # Sort by relevance, take top 5
    relevant_articles.sort(key=lambda x: x[0], reverse=True)
    top_articles = [a for _, a in relevant_articles[:5]]

    # Build context from relevant articles
    context_parts = []
    total_words = 0
    max_context_words = 6000

    for article in top_articles:
        content = store.read_wiki_content(article.slug)
        words = len(content.split())
        if total_words + words > max_context_words:
            # Truncate
            remaining = max_context_words - total_words
            content = " ".join(content.split()[:remaining])
        context_parts.append(f"## {article.title}\n\n{content}")
        total_words += words
        if total_words >= max_context_words:
            break

    # If no relevant articles found, use recent raw docs
    if not context_parts:
        recent = store.list_documents(limit=5)
        for doc in recent:
            content = store.read_document_content(doc)
            context_parts.append(f"## {doc.title}\n\n{content[:2000]}")

    context = "\n\n---\n\n".join(context_parts)

    prompt = f"""You are the knowledge base assistant for Deep House Radio, a deep house
and progressive house music station. Answer questions using the wiki context below.

Be specific — reference artists, tracks, labels, venues, dates when relevant.
If the answer isn't in the context, say so honestly.

WIKI CONTEXT:
{context}

QUESTION: {question}

Answer concisely and factually based on the context above."""

    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0 and result.stdout.strip():
            return {
                "question": question,
                "answer": result.stdout.strip(),
                "sources": [a.slug for a in top_articles],
                "source_count": len(top_articles),
            }
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return {
        "question": question,
        "answer": "Unable to generate answer (Claude CLI unavailable).",
        "sources": [],
        "source_count": 0,
    }


# =============================================================================
# SERVER STARTUP
# =============================================================================

def start_kb_server(port: int = KB_PORT, daemon: bool = True):
    """Start the KB API server."""
    server = http.server.HTTPServer(("0.0.0.0", port), KBRequestHandler)
    if daemon:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"KB API server running on port {port}")
        return server
    else:
        print(f"KB API server running on port {port}")
        server.serve_forever()


def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Knowledge Base API server")
    parser.add_argument("--port", type=int, default=KB_PORT, help=f"Port (default: {KB_PORT})")

    args = parser.parse_args()
    start_kb_server(port=args.port, daemon=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
