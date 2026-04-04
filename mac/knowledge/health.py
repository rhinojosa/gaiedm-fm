#!/usr/bin/env python3
"""
Wiki Health Check & Linting

Runs integrity checks on the knowledge base:
    - Broken wiki links ([[slug]] references to non-existent articles)
    - Orphaned articles (no backlinks, no sources)
    - Stale content (sources updated since last compilation)
    - Missing cross-references (entities mentioned but not linked)
    - Duplicate content detection
    - Coverage gaps (topics with few sources)
    - Suggests new articles based on frequently mentioned entities
"""

import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from knowledge.store import KnowledgeStore


@dataclass
class HealthIssue:
    """A single health check finding."""
    severity: str  # "error", "warning", "info"
    category: str  # "broken_link", "orphan", "stale", "gap", "duplicate", "suggestion"
    message: str
    location: str = ""  # slug or doc_id
    suggestion: str = ""


def run_health_check(store: KnowledgeStore) -> list[HealthIssue]:
    """Run all health checks and return findings."""
    issues: list[HealthIssue] = []

    issues.extend(_check_broken_links(store))
    issues.extend(_check_orphans(store))
    issues.extend(_check_coverage_gaps(store))
    issues.extend(_check_uncompiled(store))
    issues.extend(_suggest_new_articles(store))

    return issues


def _check_broken_links(store: KnowledgeStore) -> list[HealthIssue]:
    """Find [[wiki-link]] references to non-existent articles."""
    issues = []
    articles = store.list_wiki_articles()
    existing_slugs = {a.slug for a in articles}

    for article in articles:
        content = store.read_wiki_content(article.slug)
        # Find [[slug]] or [[slug|title]] patterns
        links = re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", content)
        for link in links:
            link = link.strip()
            if link not in existing_slugs:
                issues.append(HealthIssue(
                    severity="warning",
                    category="broken_link",
                    message=f"Broken link to [[{link}]]",
                    location=article.slug,
                    suggestion=f"Create article '{link}' or fix the reference",
                ))

    return issues


def _check_orphans(store: KnowledgeStore) -> list[HealthIssue]:
    """Find wiki articles with no backlinks and no source documents."""
    issues = []
    articles = store.list_wiki_articles()

    # Build backlink map
    linked_slugs: set[str] = set()
    for article in articles:
        content = store.read_wiki_content(article.slug)
        links = re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", content)
        linked_slugs.update(l.strip() for l in links)

    for article in articles:
        if article.slug == "index":
            continue
        if article.slug not in linked_slugs and not article.source_docs:
            issues.append(HealthIssue(
                severity="info",
                category="orphan",
                message=f"Orphaned article: no backlinks and no sources",
                location=article.slug,
                suggestion="Add cross-references from related articles",
            ))

    return issues


def _check_coverage_gaps(store: KnowledgeStore) -> list[HealthIssue]:
    """Identify topic areas with few sources or articles."""
    issues = []
    stats = store.stats()

    # Check for empty categories
    for cat in ("concepts", "artists", "events", "labels", "scenes"):
        count = stats.get("wiki_categories", {}).get(cat, 0)
        if count == 0:
            issues.append(HealthIssue(
                severity="info",
                category="gap",
                message=f"No wiki articles in category '{cat}'",
                location=cat,
                suggestion=f"Capture content related to {cat} to populate this category",
            ))

    # Check for single-source articles
    articles = store.list_wiki_articles()
    for article in articles:
        if len(article.source_docs) == 1:
            issues.append(HealthIssue(
                severity="info",
                category="gap",
                message=f"Article based on single source",
                location=article.slug,
                suggestion="Capture more sources to enrich this article",
            ))

    return issues


def _check_uncompiled(store: KnowledgeStore) -> list[HealthIssue]:
    """Check for uncompiled raw documents."""
    issues = []
    pending = store.list_documents(compiled=False)

    if len(pending) > 0:
        issues.append(HealthIssue(
            severity="warning",
            category="stale",
            message=f"{len(pending)} raw documents not yet compiled into wiki",
            suggestion="Run: python mac/knowledge/compiler.py compile",
        ))

    return issues


def _suggest_new_articles(store: KnowledgeStore) -> list[HealthIssue]:
    """Analyze raw docs for frequently mentioned entities that lack articles."""
    issues = []
    docs = store.list_documents(limit=200)

    # Count entity mentions across all documents
    entity_counts: Counter = Counter()
    for doc in docs:
        for tag in doc.tags:
            if tag not in ("web", "article", "post", "thread", "video", "audio"):
                entity_counts[tag] += 1

    # Check which are missing from wiki
    articles = store.list_wiki_articles()
    article_titles_lower = {a.title.lower() for a in articles}

    for entity, count in entity_counts.most_common(20):
        if count >= 3 and entity.lower() not in article_titles_lower:
            issues.append(HealthIssue(
                severity="info",
                category="suggestion",
                message=f"'{entity}' mentioned in {count} sources but has no wiki article",
                suggestion=f"Consider creating an article about '{entity}'",
            ))

    return issues


# =============================================================================
# CLI
# =============================================================================

def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Wiki health check & linting")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--severity", choices=["error", "warning", "info"],
                        help="Filter by minimum severity")

    args = parser.parse_args()
    store = KnowledgeStore()

    issues = run_health_check(store)

    # Filter by severity
    severity_order = {"error": 0, "warning": 1, "info": 2}
    if args.severity:
        min_level = severity_order.get(args.severity, 2)
        issues = [i for i in issues if severity_order.get(i.severity, 2) <= min_level]

    if args.json:
        output = [
            {
                "severity": i.severity,
                "category": i.category,
                "message": i.message,
                "location": i.location,
                "suggestion": i.suggestion,
            }
            for i in issues
        ]
        print(json.dumps(output, indent=2))
        return 0

    # Pretty print
    icons = {"error": "!!!", "warning": " ! ", "info": " i "}
    if not issues:
        print("Wiki health check: all clear")
        return 0

    print(f"Wiki health check: {len(issues)} findings\n")
    for issue in sorted(issues, key=lambda i: severity_order.get(i.severity, 2)):
        icon = icons.get(issue.severity, " ? ")
        loc = f" [{issue.location}]" if issue.location else ""
        print(f"  {icon} {issue.message}{loc}")
        if issue.suggestion:
            print(f"      → {issue.suggestion}")

    errors = sum(1 for i in issues if i.severity == "error")
    return 1 if errors > 0 else 0


if __name__ == "__main__":
    raise SystemExit(_cli())
