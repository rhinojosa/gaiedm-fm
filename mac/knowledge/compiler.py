#!/usr/bin/env python3
"""
LLM Wiki Compiler

Takes raw captured documents and compiles them into a structured wiki using
an LLM (Claude CLI). The compiler:
    1. Reads uncompiled raw documents
    2. Identifies concepts, entities, and themes
    3. Creates or updates wiki articles with summaries and cross-references
    4. Maintains an index file with backlinks
    5. Categorizes content into: concepts, artists, events, labels, scenes

The wiki is incremental — new documents get folded into the existing wiki
without rewriting everything.
"""

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from knowledge.store import KnowledgeStore, RawDocument, WikiArticle


# =============================================================================
# CATEGORY DEFINITIONS
# =============================================================================

WIKI_CATEGORIES = {
    "concepts": "Musical concepts, genres, production techniques, and cultural ideas",
    "artists": "DJ and producer profiles — career, style, notable tracks, labels",
    "events": "Festivals, club nights, showcases, tours, and one-off events",
    "labels": "Record label profiles — sound, roster, history, key releases",
    "scenes": "City and regional electronic music scenes — venues, culture, history",
}

CATEGORY_HINTS = {
    "artists": ["dj", "producer", "artist", "musician", "vocalist"],
    "events": ["festival", "event", "tour", "rave", "party", "showcase", "ade", "tomorrowland",
               "ultra", "creamfields", "edc", "burning man", "sonar", "awakenings"],
    "labels": ["records", "label", "imprint", "recordings", "music group"],
    "scenes": ["scene", "city", "club", "venue", "ibiza", "berlin", "amsterdam", "london",
               "miami", "tulum", "barcelona", "detroit", "chicago"],
}


# =============================================================================
# LLM INTERFACE
# =============================================================================

def _run_claude(prompt: str, timeout: int = 120) -> str | None:
    """Run Claude CLI with a prompt. Returns response text or None."""
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"Claude CLI error: {e}")
    return None


def _run_claude_with_context(system: str, user: str, timeout: int = 120) -> str | None:
    """Run Claude with system + user prompt via CLI."""
    full_prompt = f"{system}\n\n---\n\n{user}"
    return _run_claude(full_prompt, timeout=timeout)


# =============================================================================
# COMPILATION STEPS
# =============================================================================

def analyze_document(doc: RawDocument, content: str) -> dict:
    """Use LLM to analyze a raw document and extract structured data.

    Returns dict with: summary, concepts, entities (artists/labels/events/scenes),
    suggested_category, suggested_articles.
    """
    prompt = f"""You are a knowledge base curator for an electronic music radio station
(deep house, progressive house, melodic techno).

Analyze this captured document and extract structured information.

DOCUMENT:
Title: {doc.title}
Platform: {doc.platform}
Author: {doc.author}
Tags: {', '.join(doc.tags)}

CONTENT:
{content[:8000]}

Respond in VALID JSON only (no markdown fences). Structure:
{{
    "summary": "2-3 sentence summary of the document",
    "concepts": ["list", "of", "musical concepts", "mentioned"],
    "artists": ["artist names mentioned"],
    "labels": ["label names mentioned"],
    "events": ["event/festival names mentioned"],
    "scenes": ["city/venue/scene references"],
    "suggested_category": "one of: concepts, artists, events, labels, scenes",
    "suggested_articles": [
        {{"slug": "category/article-slug", "title": "Article Title", "reason": "why this article should exist"}}
    ],
    "key_facts": ["important specific facts worth preserving"]
}}"""

    response = _run_claude(prompt, timeout=90)
    if not response:
        return _fallback_analysis(doc, content)

    # Parse JSON (handle markdown fences)
    response = response.strip()
    if response.startswith("```"):
        response = re.sub(r"^```\w*\n?", "", response)
        response = re.sub(r"\n?```$", "", response)

    try:
        return json.loads(response)
    except json.JSONDecodeError:
        return _fallback_analysis(doc, content)


def _fallback_analysis(doc: RawDocument, content: str) -> dict:
    """Simple keyword-based analysis when LLM is unavailable."""
    words = content.lower()
    artists = []
    for name in ["lane 8", "ben böhmer", "yotto", "tinlicker", "digweed", "sasha",
                  "prydz", "deadmau5", "above & beyond", "camelphat", "artbat",
                  "tale of us", "anyma", "nora en pure", "rufus du sol"]:
        if name in words:
            artists.append(name.title())

    category = "concepts"
    for cat, hints in CATEGORY_HINTS.items():
        if any(h in words for h in hints):
            category = cat
            break

    return {
        "summary": f"Captured from {doc.platform}: {doc.title}",
        "concepts": doc.tags[:5],
        "artists": artists,
        "labels": [],
        "events": [],
        "scenes": [],
        "suggested_category": category,
        "suggested_articles": [],
        "key_facts": [],
    }


def compile_article(
    store: KnowledgeStore,
    slug: str,
    title: str,
    category: str,
    source_docs: list[RawDocument],
    existing_content: str = "",
) -> str | None:
    """Use LLM to compile/update a wiki article from source documents.

    If existing_content is provided, the LLM updates rather than rewrites.
    """
    # Build source material
    source_material = []
    for doc in source_docs[:10]:  # Limit to 10 sources
        content = store.read_document_content(doc)
        source_material.append(
            f"### Source: {doc.title} ({doc.platform})\n"
            f"Author: {doc.author} | Date: {doc.published_date}\n"
            f"Tags: {', '.join(doc.tags)}\n\n"
            f"{content[:3000]}\n"
        )

    sources_text = "\n---\n".join(source_material)

    if existing_content:
        prompt = f"""You are maintaining a knowledge base wiki for an electronic music
radio station (deep house, progressive house, melodic techno).

UPDATE this existing wiki article with information from the new sources.
Preserve existing content and structure. Add new facts, update outdated info,
and add cross-references to other articles using [[wiki-link]] syntax.

EXISTING ARTICLE:
{existing_content[:4000]}

NEW SOURCES:
{sources_text[:6000]}

Write the COMPLETE updated article in markdown. Include:
- A clear title as # heading
- Organized sections
- Cross-references as [[category/slug]] links
- A "Sources" section at the bottom listing source URLs
- Keep it factual, specific, and useful for music fans

Output ONLY the markdown article, no preamble."""
    else:
        prompt = f"""You are building a knowledge base wiki for an electronic music
radio station (deep house, progressive house, melodic techno).

Write a wiki article for: **{title}** (Category: {category})

SOURCE MATERIAL:
{sources_text[:8000]}

Write a comprehensive markdown article. Include:
- A clear title as # heading
- Organized sections appropriate to the category
- Cross-references to related topics using [[category/slug]] links
- Specific facts: dates, track names, label names, venues, BPM ranges
- A "Sources" section at the bottom
- For artists: discography highlights, style description, label affiliations, notable sets
- For events: dates, location, lineups, significance
- For labels: roster, sound, key releases, history
- For concepts: explanation, key artists, recommended tracks
- For scenes: venues, culture, history, key figures

Output ONLY the markdown article, no preamble."""

    return _run_claude(prompt, timeout=180)


def compile_index(store: KnowledgeStore) -> str:
    """Generate/update the master wiki index."""
    articles = store.list_wiki_articles()
    stats = store.stats()

    sections = {}
    for article in articles:
        cat = article.category
        if cat not in sections:
            sections[cat] = []
        sections[cat].append(article)

    parts = [
        f"# Deep House Radio Knowledge Base\n",
        f"*{stats['wiki_articles']} articles | {stats['wiki_total_words']:,} words | "
        f"{stats['raw_documents']} sources*\n",
        f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n",
    ]

    for cat_name, cat_desc in WIKI_CATEGORIES.items():
        cat_articles = sections.get(cat_name, [])
        parts.append(f"\n## {cat_name.title()}\n")
        parts.append(f"*{cat_desc}*\n")
        if cat_articles:
            for a in sorted(cat_articles, key=lambda x: x.title):
                summary = f" — {a.summary}" if a.summary else ""
                parts.append(f"- [[{a.slug}|{a.title}]]{summary}")
        else:
            parts.append("*(no articles yet)*")

    # Uncompiled sources
    pending = stats["raw_uncompiled"]
    if pending > 0:
        parts.append(f"\n---\n\n*{pending} source documents pending compilation.*")

    return "\n".join(parts)


# =============================================================================
# MAIN COMPILATION PIPELINE
# =============================================================================

def compile_new_documents(store: KnowledgeStore, max_docs: int = 10) -> int:
    """Compile unprocessed raw documents into the wiki.

    Returns number of documents processed.
    """
    docs = store.list_documents(compiled=False, limit=max_docs)
    if not docs:
        print("No uncompiled documents.")
        return 0

    print(f"Compiling {len(docs)} documents...")
    processed = 0

    for doc in docs:
        print(f"\n  Analyzing: {doc.title}")
        content = store.read_document_content(doc)
        if not content:
            print(f"    Skipping (empty content)")
            store.mark_compiled(doc.doc_id)
            continue

        # Analyze with LLM
        analysis = analyze_document(doc, content)
        print(f"    Category: {analysis.get('suggested_category', '?')}")
        print(f"    Concepts: {', '.join(analysis.get('concepts', [])[:5])}")

        # Create/update articles for suggested entries
        for suggestion in analysis.get("suggested_articles", [])[:3]:
            slug = suggestion.get("slug", "")
            title = suggestion.get("title", "")
            if not slug or not title:
                continue

            category = slug.split("/")[0] if "/" in slug else analysis.get("suggested_category", "concepts")

            # Check if article exists
            existing = store.get_wiki_article(slug)
            existing_content = store.read_wiki_content(slug) if existing else ""

            print(f"    {'Updating' if existing else 'Creating'}: {slug}")
            article_md = compile_article(
                store, slug, title, category, [doc], existing_content
            )

            if article_md:
                summary = analysis.get("summary", "")[:200]
                store.save_wiki_article(
                    slug=slug, title=title, content_md=article_md,
                    category=category, summary=summary, source_docs=[doc.doc_id],
                )

        store.mark_compiled(doc.doc_id)
        processed += 1

    # Rebuild index
    if processed > 0:
        print("\n  Rebuilding index...")
        index_md = compile_index(store)
        index_path = store.wiki_dir / "index.md"
        index_path.write_text(index_md, encoding="utf-8")
        print(f"  Index updated ({len(index_md.split())} words)")

    print(f"\nCompiled {processed} documents.")
    return processed


def recompile_article(store: KnowledgeStore, slug: str) -> bool:
    """Force recompile a specific wiki article from its sources."""
    article = store.get_wiki_article(slug)
    if not article:
        print(f"Article not found: {slug}")
        return False

    source_docs = []
    for doc_id in article.source_docs:
        doc = store.get_document(doc_id)
        if doc:
            source_docs.append(doc)

    if not source_docs:
        print(f"No source documents for {slug}")
        return False

    print(f"Recompiling: {slug} from {len(source_docs)} sources")
    article_md = compile_article(
        store, slug, article.title, article.category, source_docs
    )
    if article_md:
        store.save_wiki_article(
            slug=slug, title=article.title, content_md=article_md,
            category=article.category, summary=article.summary,
            source_docs=article.source_docs,
        )
        return True
    return False


# =============================================================================
# CLI
# =============================================================================

def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="LLM wiki compiler")
    sub = parser.add_subparsers(dest="command")

    compile_cmd = sub.add_parser("compile", help="Compile new documents into wiki")
    compile_cmd.add_argument("--max", type=int, default=10, help="Max documents to process")

    sub.add_parser("index", help="Rebuild wiki index")

    recompile_cmd = sub.add_parser("recompile", help="Recompile a specific article")
    recompile_cmd.add_argument("slug", help="Article slug (e.g. artists/lane-8)")

    sub.add_parser("compile-all", help="Compile all uncompiled documents")

    args = parser.parse_args()
    store = KnowledgeStore()

    if args.command == "compile":
        compile_new_documents(store, max_docs=args.max)
        return 0

    if args.command == "compile-all":
        total = 0
        while True:
            processed = compile_new_documents(store, max_docs=5)
            total += processed
            if processed == 0:
                break
        print(f"Total compiled: {total}")
        return 0

    if args.command == "index":
        index_md = compile_index(store)
        index_path = store.wiki_dir / "index.md"
        index_path.write_text(index_md, encoding="utf-8")
        print(index_md)
        return 0

    if args.command == "recompile":
        ok = recompile_article(store, args.slug)
        return 0 if ok else 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
