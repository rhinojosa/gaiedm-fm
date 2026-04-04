#!/usr/bin/env python3
"""
URL Capture & Content Extraction

Accepts URLs from various platforms (Instagram, LinkedIn, X, Reddit, YouTube,
SoundCloud, articles, etc.), extracts content, and converts to markdown for
storage in the knowledge base.

Supports:
    - Instagram posts/reels (captions, image descriptions)
    - X/Twitter posts and threads
    - Reddit posts and top comments
    - LinkedIn posts and articles
    - YouTube videos (metadata, transcripts)
    - SoundCloud tracks (metadata)
    - General web articles (Readability extraction)
    - PDF documents
    - RSS feeds
"""

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Ensure parent is on path for sibling imports
sys.path.insert(0, str(Path(__file__).parents[1]))

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from knowledge.store import RawDocument, KnowledgeStore


# =============================================================================
# PLATFORM DETECTION
# =============================================================================

PLATFORM_PATTERNS = {
    "instagram": [
        r"instagram\.com/(p|reel|stories)/",
        r"instagr\.am/",
    ],
    "x": [
        r"(twitter\.com|x\.com)/\w+/status/",
    ],
    "reddit": [
        r"reddit\.com/r/\w+/(comments|s)/",
        r"redd\.it/",
    ],
    "linkedin": [
        r"linkedin\.com/(posts|pulse|feed|in/[\w-]+/)",
    ],
    "youtube": [
        r"youtube\.com/watch",
        r"youtu\.be/",
        r"youtube\.com/shorts/",
    ],
    "soundcloud": [
        r"soundcloud\.com/",
    ],
    "spotify": [
        r"open\.spotify\.com/(track|album|playlist|episode)/",
    ],
    "bandcamp": [
        r"\.bandcamp\.com/",
    ],
    "mixcloud": [
        r"mixcloud\.com/",
    ],
    "resident_advisor": [
        r"ra\.co/",
        r"residentadvisor\.net/",
    ],
}


def detect_platform(url: str) -> str:
    """Detect the platform from a URL. Returns platform name or 'web'."""
    for platform, patterns in PLATFORM_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, url, re.IGNORECASE):
                return platform
    return "web"


def detect_content_type(url: str) -> str:
    """Detect content type from URL. Returns: article, post, video, audio, image, thread."""
    platform = detect_platform(url)

    if platform == "youtube":
        return "video"
    if platform in ("soundcloud", "spotify", "bandcamp", "mixcloud"):
        return "audio"
    if platform == "instagram":
        if "/reel/" in url:
            return "video"
        return "post"
    if platform == "x":
        return "post"
    if platform == "reddit":
        return "thread"
    if platform == "linkedin":
        if "/pulse/" in url:
            return "article"
        return "post"

    # Check file extension
    path = urlparse(url).path.lower()
    if path.endswith(".pdf"):
        return "document"
    if any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp")):
        return "image"
    if any(path.endswith(ext) for ext in (".mp3", ".wav", ".flac", ".ogg")):
        return "audio"
    if any(path.endswith(ext) for ext in (".mp4", ".webm", ".mov")):
        return "video"

    return "article"


# =============================================================================
# CONTENT EXTRACTORS
# =============================================================================

@dataclass
class ExtractedContent:
    """Result of content extraction from a URL."""
    title: str = ""
    author: str = ""
    body: str = ""
    published_date: str = ""
    platform: str = "web"
    content_type: str = "article"
    tags: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    raw_html: str = ""


def _fetch_url(url: str, timeout: int = 30) -> str:
    """Fetch URL content. Returns HTML string."""
    if HAS_HTTPX:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; DeepHouseRadio-KB/1.0)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text
    else:
        import urllib.request
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; DeepHouseRadio-KB/1.0)",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")


def extract_web_article(url: str) -> ExtractedContent:
    """Extract content from a general web article using readability heuristics."""
    html = _fetch_url(url)
    content = ExtractedContent(platform="web", content_type="article")
    content.raw_html = html

    # Extract title
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if title_match:
        content.title = _clean_html(title_match.group(1)).strip()

    # Extract meta description
    meta_desc = re.search(
        r'<meta\s+(?:name|property)=["\'](?:description|og:description)["\']\s+content=["\']([^"\']*)["\']',
        html, re.IGNORECASE,
    )

    # Extract author
    author_match = re.search(
        r'<meta\s+(?:name|property)=["\'](?:author|article:author)["\']\s+content=["\']([^"\']*)["\']',
        html, re.IGNORECASE,
    )
    if author_match:
        content.author = _clean_html(author_match.group(1))

    # Extract published date
    date_match = re.search(
        r'<meta\s+(?:name|property)=["\'](?:article:published_time|date|pubdate)["\']\s+content=["\']([^"\']*)["\']',
        html, re.IGNORECASE,
    )
    if date_match:
        content.published_date = date_match.group(1)

    # Extract og:image
    og_image = re.search(
        r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']*)["\']',
        html, re.IGNORECASE,
    )
    if og_image:
        content.images.append(og_image.group(1))

    # Extract main content using simple heuristics
    # Remove script, style, nav, header, footer, aside
    cleaned = re.sub(r"<(script|style|nav|header|footer|aside|noscript)[^>]*>.*?</\1>",
                      "", html, flags=re.DOTALL | re.IGNORECASE)

    # Try to find article or main content
    article_match = re.search(
        r"<(article|main)[^>]*>(.*?)</\1>", cleaned, re.DOTALL | re.IGNORECASE
    )
    if article_match:
        body_html = article_match.group(2)
    else:
        # Fall back to largest text block
        body_html = cleaned

    content.body = _html_to_markdown(body_html)

    # Extract tags from meta keywords
    keywords = re.search(
        r'<meta\s+name=["\']keywords["\']\s+content=["\']([^"\']*)["\']',
        html, re.IGNORECASE,
    )
    if keywords:
        content.tags = [t.strip() for t in keywords.group(1).split(",") if t.strip()]

    return content


def extract_youtube(url: str) -> ExtractedContent:
    """Extract YouTube video metadata and transcript."""
    content = ExtractedContent(platform="youtube", content_type="video")

    # Use yt-dlp for metadata
    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-download", url],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            content.title = data.get("title", "")
            content.author = data.get("uploader", data.get("channel", ""))
            content.body = data.get("description", "")
            content.published_date = data.get("upload_date", "")
            content.tags = data.get("tags", []) or []
            content.metadata = {
                "duration": data.get("duration"),
                "view_count": data.get("view_count"),
                "like_count": data.get("like_count"),
                "channel_id": data.get("channel_id"),
                "video_id": data.get("id"),
            }
            thumb = data.get("thumbnail")
            if thumb:
                content.images.append(thumb)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        # Fall back to HTML extraction
        return extract_web_article(url)

    # Try to get transcript/subtitles
    try:
        result = subprocess.run(
            ["yt-dlp", "--write-auto-sub", "--sub-lang", "en",
             "--skip-download", "--sub-format", "vtt",
             "--print", "%(requested_subtitles)j", url],
            capture_output=True, text=True, timeout=30,
        )
        # If subtitles exist, download them
        if result.returncode == 0 and "null" not in result.stdout[:10]:
            sub_result = subprocess.run(
                ["yt-dlp", "--write-auto-sub", "--sub-lang", "en",
                 "--skip-download", "--sub-format", "vtt",
                 "-o", "/tmp/kb_yt_sub", url],
                capture_output=True, text=True, timeout=30,
            )
            sub_path = Path("/tmp/kb_yt_sub.en.vtt")
            if sub_path.exists():
                vtt_text = sub_path.read_text()
                transcript = _parse_vtt(vtt_text)
                if transcript:
                    content.body += f"\n\n## Transcript\n\n{transcript}"
                sub_path.unlink(missing_ok=True)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return content


def extract_reddit(url: str) -> ExtractedContent:
    """Extract Reddit post and top comments via JSON API."""
    content = ExtractedContent(platform="reddit", content_type="thread")

    # Reddit's JSON API: append .json
    json_url = url.rstrip("/") + ".json"
    try:
        if HAS_HTTPX:
            headers = {"User-Agent": "DeepHouseRadio-KB/1.0"}
            with httpx.Client(follow_redirects=True, timeout=15) as client:
                resp = client.get(json_url, headers=headers)
                data = resp.json()
        else:
            import urllib.request
            req = urllib.request.Request(json_url, headers={
                "User-Agent": "DeepHouseRadio-KB/1.0",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())

        if isinstance(data, list) and len(data) >= 1:
            post_data = data[0]["data"]["children"][0]["data"]
            content.title = post_data.get("title", "")
            content.author = f"u/{post_data.get('author', 'unknown')}"
            content.body = post_data.get("selftext", "")
            content.metadata = {
                "subreddit": post_data.get("subreddit"),
                "score": post_data.get("score"),
                "num_comments": post_data.get("num_comments"),
                "url": post_data.get("url"),
            }
            content.tags = [f"r/{post_data.get('subreddit', '')}"]

            created = post_data.get("created_utc")
            if created:
                content.published_date = datetime.utcfromtimestamp(created).isoformat()

            # Extract top comments
            if len(data) >= 2:
                comments = data[1]["data"]["children"]
                top_comments = []
                for c in comments[:10]:  # Top 10
                    if c["kind"] == "t1":
                        cd = c["data"]
                        author = cd.get("author", "unknown")
                        body = cd.get("body", "")
                        score = cd.get("score", 0)
                        if body and score > 0:
                            top_comments.append(f"**u/{author}** ({score} pts):\n{body}")
                if top_comments:
                    content.body += "\n\n## Top Comments\n\n" + "\n\n---\n\n".join(top_comments)

    except Exception:
        return extract_web_article(url)

    return content


def extract_social_post(url: str, platform: str) -> ExtractedContent:
    """Extract social media post content (X, LinkedIn, Instagram).

    Uses yt-dlp for media metadata, falls back to web extraction.
    """
    content = ExtractedContent(platform=platform, content_type="post")

    # Try yt-dlp first (works for X, Instagram, some LinkedIn)
    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-download", url],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            content.title = data.get("title", data.get("description", ""))[:200]
            content.author = data.get("uploader", data.get("channel", ""))
            content.body = data.get("description", "")
            content.published_date = data.get("upload_date", "")
            content.tags = data.get("tags", []) or []
            content.metadata = {
                "like_count": data.get("like_count"),
                "repost_count": data.get("repost_count"),
                "comment_count": data.get("comment_count"),
                "view_count": data.get("view_count"),
            }
            thumb = data.get("thumbnail")
            if thumb:
                content.images.append(thumb)
            return content
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass

    # Fall back to web extraction
    return extract_web_article(url)


# =============================================================================
# MAIN CAPTURE FUNCTION
# =============================================================================

def capture_url(url: str, store: KnowledgeStore, tags: list[str] | None = None) -> RawDocument | None:
    """Capture content from a URL and store in the knowledge base.

    Args:
        url: The URL to capture
        store: KnowledgeStore instance
        tags: Optional additional tags

    Returns:
        RawDocument if successful, None if failed
    """
    platform = detect_platform(url)
    content_type = detect_content_type(url)

    # Route to appropriate extractor
    extractors = {
        "youtube": extract_youtube,
        "reddit": extract_reddit,
        "web": extract_web_article,
    }

    if platform in ("x", "instagram", "linkedin"):
        extractor = lambda u: extract_social_post(u, platform)
    else:
        extractor = extractors.get(platform, extract_web_article)

    try:
        content = extractor(url)
    except Exception as e:
        print(f"Extraction failed for {url}: {e}")
        return None

    if not content.title and not content.body:
        print(f"No content extracted from {url}")
        return None

    # Build markdown
    markdown = _content_to_markdown(content, url)

    # Merge tags
    all_tags = list(set((tags or []) + content.tags + [platform, content_type]))

    # Store
    doc = store.ingest(
        url=url,
        title=content.title or _generate_title(url),
        content_md=markdown,
        platform=platform,
        content_type=content_type,
        author=content.author,
        published_date=content.published_date,
        tags=all_tags,
        images=content.images,
        metadata=content.metadata,
    )

    return doc


# =============================================================================
# HELPERS
# =============================================================================

def _clean_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#39;", "'", text)
    text = re.sub(r"&nbsp;", " ", text)
    return text


def _html_to_markdown(html: str) -> str:
    """Convert HTML to basic markdown."""
    text = html

    # Headers
    for i in range(6, 0, -1):
        text = re.sub(rf"<h{i}[^>]*>(.*?)</h{i}>", lambda m: f"\n{'#' * i} {_clean_html(m.group(1))}\n", text, flags=re.DOTALL | re.IGNORECASE)

    # Bold/italic
    text = re.sub(r"<(strong|b)[^>]*>(.*?)</\1>", r"**\2**", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<(em|i)[^>]*>(.*?)</\1>", r"*\2*", text, flags=re.DOTALL | re.IGNORECASE)

    # Links
    text = re.sub(r'<a[^>]+href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', r"[\2](\1)", text, flags=re.DOTALL | re.IGNORECASE)

    # Images
    text = re.sub(r'<img[^>]+src=["\']([^"\']*)["\'][^>]*/?>', r"![](\1)", text, flags=re.IGNORECASE)

    # Lists
    text = re.sub(r"<li[^>]*>(.*?)</li>", r"- \1", text, flags=re.DOTALL | re.IGNORECASE)

    # Paragraphs and line breaks
    text = re.sub(r"<p[^>]*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

    # Blockquotes
    text = re.sub(r"<blockquote[^>]*>(.*?)</blockquote>",
                  lambda m: "\n> " + _clean_html(m.group(1)).strip().replace("\n", "\n> ") + "\n",
                  text, flags=re.DOTALL | re.IGNORECASE)

    # Code blocks
    text = re.sub(r"<pre[^>]*><code[^>]*>(.*?)</code></pre>",
                  lambda m: f"\n```\n{_clean_html(m.group(1))}\n```\n",
                  text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", text, flags=re.DOTALL | re.IGNORECASE)

    # Remove remaining tags
    text = _clean_html(text)

    # Clean up whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)

    return text.strip()


def _parse_vtt(vtt_text: str) -> str:
    """Parse VTT subtitle file into clean transcript text."""
    lines = []
    seen = set()
    for line in vtt_text.split("\n"):
        line = line.strip()
        if not line or "-->" in line or line.startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        if re.match(r"^\d+$", line):
            continue
        # Remove VTT tags
        clean = re.sub(r"<[^>]+>", "", line)
        if clean and clean not in seen:
            seen.add(clean)
            lines.append(clean)
    return " ".join(lines)


def _content_to_markdown(content: ExtractedContent, url: str) -> str:
    """Convert ExtractedContent to a structured markdown document."""
    parts = []

    # Title
    if content.title:
        parts.append(f"# {content.title}")

    # Metadata block
    meta_lines = [f"- **Source**: [{content.platform}]({url})"]
    if content.author:
        meta_lines.append(f"- **Author**: {content.author}")
    if content.published_date:
        meta_lines.append(f"- **Date**: {content.published_date}")
    if content.content_type:
        meta_lines.append(f"- **Type**: {content.content_type}")
    if content.tags:
        meta_lines.append(f"- **Tags**: {', '.join(content.tags)}")
    parts.append("\n".join(meta_lines))

    # Images
    if content.images:
        img_lines = []
        for img_url in content.images[:5]:
            img_lines.append(f"![{content.title or 'image'}]({img_url})")
        parts.append("\n".join(img_lines))

    # Body
    if content.body:
        parts.append(content.body)

    # Metadata appendix
    if content.metadata:
        interesting = {k: v for k, v in content.metadata.items() if v is not None}
        if interesting:
            meta_md = "\n".join(f"- {k}: {v}" for k, v in interesting.items())
            parts.append(f"## Metadata\n\n{meta_md}")

    return "\n\n".join(parts)


def _generate_title(url: str) -> str:
    """Generate a title from a URL when none is available."""
    parsed = urlparse(url)
    path = parsed.path.strip("/").split("/")
    if path and path[-1]:
        return path[-1].replace("-", " ").replace("_", " ").title()
    return parsed.netloc


# =============================================================================
# CLI
# =============================================================================

def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Capture URL content into knowledge base")
    parser.add_argument("url", nargs="?", help="URL to capture")
    parser.add_argument("--tags", type=str, help="Comma-separated tags")
    parser.add_argument("--detect", action="store_true", help="Just detect platform, don't capture")
    parser.add_argument("--batch", type=str, help="File with URLs (one per line)")

    args = parser.parse_args()

    if args.detect and args.url:
        platform = detect_platform(args.url)
        content_type = detect_content_type(args.url)
        print(f"Platform: {platform}")
        print(f"Content type: {content_type}")
        return 0

    store = KnowledgeStore()
    tags = [t.strip() for t in args.tags.split(",")] if args.tags else []

    if args.batch:
        urls = Path(args.batch).read_text().strip().splitlines()
        success = 0
        for url in urls:
            url = url.strip()
            if not url or url.startswith("#"):
                continue
            print(f"Capturing: {url}")
            doc = capture_url(url, store, tags)
            if doc:
                print(f"  → {doc.title}")
                success += 1
            else:
                print(f"  ✗ Failed")
        print(f"\nCaptured {success}/{len(urls)} URLs")
        return 0

    if args.url:
        doc = capture_url(args.url, store, tags)
        if doc:
            print(f"Captured: {doc.title}")
            print(f"  Platform: {doc.platform}")
            print(f"  Type: {doc.content_type}")
            print(f"  Tags: {', '.join(doc.tags)}")
            print(f"  Stored: {doc.raw_path}")
            return 0
        print("Capture failed")
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
