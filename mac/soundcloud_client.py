#!/usr/bin/env python3
"""
Deep House Radio — SoundCloud Integration

Searches SoundCloud for deep house and progressive house tracks.
Supplements the local music library as a secondary source.

Requires: pip install soundcloud-v2

Usage:
    uv run python mac/soundcloud_client.py search "deep house"
    uv run python mac/soundcloud_client.py search "Lane 8" --limit 10
    uv run python mac/soundcloud_client.py download <track_url> <output_path>
    uv run python mac/soundcloud_client.py status
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

CACHE_DIR = Path(os.environ.get("WRIT_DATA_DIR", "~/.writ")).expanduser()
CACHE_PATH = CACHE_DIR / "soundcloud_cache.json"
TOKEN_PATH = CACHE_DIR / "soundcloud_token"
CACHE_TTL = 3600  # 1 hour

# Download directory for SoundCloud tracks
DOWNLOAD_DIR = Path(os.environ.get(
    "SOUNDCLOUD_DOWNLOAD_DIR",
    str(Path(__file__).resolve().parents[1] / "output" / "soundcloud"),
))


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[soundcloud {ts}] {msg}", flush=True)


# =============================================================================
# CACHE
# =============================================================================

def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2))


def _get_cached(key: str) -> list | None:
    cache = _load_cache()
    entry = cache.get(key)
    if entry and time.time() - entry.get("timestamp", 0) < CACHE_TTL:
        return entry.get("results", [])
    return None


def _set_cached(key: str, results: list) -> None:
    cache = _load_cache()
    cache[key] = {"timestamp": time.time(), "results": results}
    _save_cache(cache)


# =============================================================================
# SOUNDCLOUD API (via soundcloud-v2)
# =============================================================================

_client = None


def _get_client():
    """Lazy-initialize the SoundCloud client."""
    global _client
    if _client is not None:
        return _client

    try:
        from soundcloud import SoundCloud
        _client = SoundCloud()

        # Try to load OAuth token for authenticated access
        if TOKEN_PATH.exists():
            token = TOKEN_PATH.read_text().strip()
            if token:
                _client = SoundCloud(auth_token=token)
                log("Authenticated with OAuth token")

        return _client
    except ImportError:
        log("soundcloud-v2 not installed. Run: pip install soundcloud-v2")
        return None
    except Exception as e:
        log(f"SoundCloud client init failed: {e}")
        return None


def search_tracks(
    query: str,
    limit: int = 20,
    genre: str | None = None,
) -> list[dict]:
    """Search SoundCloud for tracks.

    Returns list of track dicts with: id, title, artist, duration, bpm, url, stream_url
    """
    cache_key = f"search:{query}:{genre}:{limit}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    client = _get_client()
    if client is None:
        return []

    try:
        results = []
        for track in client.search_tracks(query):
            if len(results) >= limit:
                break

            # Filter by genre if specified
            if genre and track.genre:
                if genre.lower() not in track.genre.lower():
                    continue

            # Skip very short tracks (<60s) or very long (>600s)
            duration_secs = (track.duration or 0) / 1000
            if duration_secs < 60 or duration_secs > 600:
                continue

            result = {
                "id": track.id,
                "title": track.title or "",
                "artist": track.user.username if track.user else "",
                "duration": duration_secs,
                "bpm": getattr(track, "bpm", None),
                "genre": track.genre or "",
                "url": track.permalink_url or "",
                "streamable": getattr(track, "streamable", False),
                "downloadable": getattr(track, "downloadable", False),
            }
            results.append(result)
            time.sleep(0.5)  # Rate limiting

        _set_cached(cache_key, results)
        return results

    except Exception as e:
        log(f"Search failed: {e}")
        return []


def download_track(url: str, output_path: Path) -> bool:
    """Download a SoundCloud track using yt-dlp (if available).

    This respects the artist's download settings.
    """
    try:
        cmd = [
            "yt-dlp",
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "0",  # Best quality
            "-o", str(output_path.with_suffix(".%(ext)s")),
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            log(f"Downloaded: {output_path.name}")
            return True
        else:
            log(f"Download failed: {result.stderr[:200]}")
            return False
    except FileNotFoundError:
        log("yt-dlp not installed. Run: pip install yt-dlp")
        return False
    except Exception as e:
        log(f"Download error: {e}")
        return False


def is_available() -> bool:
    """Check if SoundCloud client is working."""
    client = _get_client()
    return client is not None


# =============================================================================
# CLI
# =============================================================================

def _cli() -> int:
    parser = argparse.ArgumentParser(description="Deep House Radio SoundCloud client")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_search = sub.add_parser("search", help="Search for tracks")
    p_search.add_argument("query", type=str, help="Search query")
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument("--genre", type=str, help="Filter by genre")

    p_dl = sub.add_parser("download", help="Download a track")
    p_dl.add_argument("url", type=str, help="SoundCloud track URL")
    p_dl.add_argument("output", type=str, nargs="?", help="Output path")

    sub.add_parser("status", help="Check SoundCloud availability")

    args = parser.parse_args()

    if args.cmd == "status":
        if is_available():
            print("SoundCloud client: available")
            cache = _load_cache()
            print(f"Cache entries: {len(cache)}")
            print(f"Token file: {'present' if TOKEN_PATH.exists() else 'not found'}")
        else:
            print("SoundCloud client: unavailable")
            print("Install: pip install soundcloud-v2")
        return 0

    if args.cmd == "search":
        tracks = search_tracks(args.query, limit=args.limit, genre=args.genre)
        for t in tracks:
            bpm_str = f"{t['bpm']:.0f}" if t.get("bpm") else "---"
            dur_str = f"{t['duration']:.0f}s"
            print(f"  {bpm_str:>3s} BPM | {dur_str:>4s} | {t['artist']:25s} - {t['title']}")
        print(f"\n  Found {len(tracks)} tracks")
        return 0

    if args.cmd == "download":
        output = Path(args.output) if args.output else DOWNLOAD_DIR / "track"
        output.parent.mkdir(parents=True, exist_ok=True)
        ok = download_track(args.url, output)
        return 0 if ok else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
