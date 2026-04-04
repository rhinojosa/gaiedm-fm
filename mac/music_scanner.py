#!/usr/bin/env python3
"""
Deep House Radio — Local Music Library Scanner

Scans configured directories for audio files, extracts metadata (title, artist,
album, genre, duration) via mutagen, and stores everything in a SQLite catalog.
BPM analysis is delegated to bpm_analyzer.py and stored in the same DB.

Usage:
    uv run python mac/music_scanner.py scan              # Full scan
    uv run python mac/music_scanner.py scan --dir ~/Music # Scan specific dir
    uv run python mac/music_scanner.py status             # Show catalog stats
    uv run python mac/music_scanner.py list --show the_deep  # List tracks for show
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import mutagen
from mutagen.easyid3 import EasyID3
from mutagen.mp4 import MP4
from mutagen.flac import FLAC

DB_DIR = Path(os.environ.get("WRIT_DATA_DIR", "~/.writ")).expanduser()
DB_PATH = DB_DIR / "music_catalog.db"

AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".wav", ".ogg", ".opus", ".aac"}


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"[scanner {_ts()}] {msg}", flush=True)


# =============================================================================
# DATABASE
# =============================================================================

def get_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open or create the music catalog database."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tracks (
            path        TEXT PRIMARY KEY,
            title       TEXT,
            artist      TEXT,
            album       TEXT,
            genre       TEXT,
            duration    REAL,
            bpm         REAL,
            key         TEXT,
            energy      REAL,
            is_anthem   INTEGER DEFAULT 0,
            show_id     TEXT,
            mix_in_pt   REAL,
            mix_out_pt  REAL,
            file_mtime  REAL,
            scanned_at  REAL,
            play_count  INTEGER DEFAULT 0,
            last_played REAL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tracks_show ON tracks(show_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tracks_bpm ON tracks(bpm)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tracks_anthem ON tracks(is_anthem)
    """)
    conn.commit()
    return conn


# =============================================================================
# METADATA EXTRACTION
# =============================================================================

def _get_tag(tags: dict, key: str, default: str = "") -> str:
    """Safely extract a tag value, handling lists."""
    val = tags.get(key, default)
    if isinstance(val, list):
        return str(val[0]) if val else default
    return str(val) if val else default


def extract_metadata(filepath: Path) -> dict[str, Any]:
    """Extract metadata from an audio file using mutagen."""
    meta: dict[str, Any] = {
        "path": str(filepath),
        "title": filepath.stem,
        "artist": "",
        "album": "",
        "genre": "",
        "duration": 0.0,
    }

    try:
        audio = mutagen.File(str(filepath))
        if audio is None:
            return meta

        # Duration
        if audio.info:
            meta["duration"] = audio.info.length or 0.0

        # Tags depend on format
        suffix = filepath.suffix.lower()
        if suffix == ".mp3":
            try:
                tags = EasyID3(str(filepath))
                meta["title"] = _get_tag(tags, "title", filepath.stem)
                meta["artist"] = _get_tag(tags, "artist")
                meta["album"] = _get_tag(tags, "album")
                meta["genre"] = _get_tag(tags, "genre")
            except Exception:
                pass

        elif suffix == ".m4a":
            if isinstance(audio, MP4):
                tags = audio.tags or {}
                meta["title"] = _get_tag(tags, "\xa9nam", filepath.stem)
                meta["artist"] = _get_tag(tags, "\xa9ART")
                meta["album"] = _get_tag(tags, "\xa9alb")
                meta["genre"] = _get_tag(tags, "\xa9gen")

        elif suffix == ".flac":
            if isinstance(audio, FLAC):
                meta["title"] = _get_tag(audio, "title", filepath.stem)
                meta["artist"] = _get_tag(audio, "artist")
                meta["album"] = _get_tag(audio, "album")
                meta["genre"] = _get_tag(audio, "genre")

        elif hasattr(audio, "tags") and audio.tags:
            # OGG, Opus, etc.
            tags = audio.tags
            meta["title"] = _get_tag(tags, "title", filepath.stem)
            meta["artist"] = _get_tag(tags, "artist")
            meta["album"] = _get_tag(tags, "album")
            meta["genre"] = _get_tag(tags, "genre")

    except Exception as e:
        log(f"  Warning: metadata extraction failed for {filepath.name}: {e}")

    return meta


def detect_anthem(filepath: Path, meta: dict) -> bool:
    """Heuristic: detect if a track is an anthem based on filename or metadata."""
    name_lower = filepath.name.lower()
    # Explicit tag in filename
    if "[anthem]" in name_lower or "(anthem)" in name_lower:
        return True
    # Check genre tags
    genre = meta.get("genre", "").lower()
    if "anthem" in genre:
        return True
    return False


def detect_show_from_path(filepath: Path) -> str | None:
    """Infer show_id from directory structure."""
    parts = [p.lower() for p in filepath.parts]
    show_map = {
        "the_deep": "the_deep",
        "deep": "the_deep",
        "anjunadeep": "the_deep",
        "melodic": "the_deep",
        "frequencies": "frequencies",
        "progressive": "frequencies",
        "underground": "frequencies",
        "bedrock": "frequencies",
        "anthem": "anthem_hour",
        "anthem_hour": "anthem_hour",
        "festival": "anthem_hour",
        "peak": "anthem_hour",
    }
    for part in parts:
        for keyword, show_id in show_map.items():
            if keyword in part:
                return show_id
    return None


# =============================================================================
# SCANNING
# =============================================================================

def scan_directory(directory: Path, conn: sqlite3.Connection) -> tuple[int, int]:
    """Scan a directory recursively for audio files. Returns (new, updated) counts."""
    new_count = 0
    updated_count = 0

    if not directory.exists():
        log(f"  Directory not found: {directory}")
        return 0, 0

    for filepath in directory.rglob("*"):
        if not filepath.is_file():
            continue
        if filepath.suffix.lower() not in AUDIO_EXTENSIONS:
            continue

        file_mtime = filepath.stat().st_mtime
        path_str = str(filepath)

        # Check if already scanned and up-to-date
        row = conn.execute(
            "SELECT file_mtime FROM tracks WHERE path = ?", (path_str,)
        ).fetchone()

        if row and row["file_mtime"] == file_mtime:
            continue  # Already scanned, no changes

        meta = extract_metadata(filepath)
        is_anthem = detect_anthem(filepath, meta)
        show_id = detect_show_from_path(filepath)
        now = time.time()

        if row:
            # Update existing record (preserve play_count, bpm, etc.)
            conn.execute("""
                UPDATE tracks SET
                    title = ?, artist = ?, album = ?, genre = ?,
                    duration = ?, is_anthem = ?, show_id = COALESCE(?, show_id),
                    file_mtime = ?, scanned_at = ?
                WHERE path = ?
            """, (
                meta["title"], meta["artist"], meta["album"], meta["genre"],
                meta["duration"], int(is_anthem), show_id,
                file_mtime, now, path_str,
            ))
            updated_count += 1
        else:
            conn.execute("""
                INSERT INTO tracks (path, title, artist, album, genre, duration,
                    is_anthem, show_id, file_mtime, scanned_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                path_str, meta["title"], meta["artist"], meta["album"],
                meta["genre"], meta["duration"], int(is_anthem), show_id,
                file_mtime, now,
            ))
            new_count += 1

    conn.commit()
    return new_count, updated_count


def get_catalog_stats(conn: sqlite3.Connection) -> dict:
    """Get summary statistics for the music catalog."""
    total = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    with_bpm = conn.execute("SELECT COUNT(*) FROM tracks WHERE bpm IS NOT NULL").fetchone()[0]
    anthems = conn.execute("SELECT COUNT(*) FROM tracks WHERE is_anthem = 1").fetchone()[0]

    by_show = {}
    for row in conn.execute(
        "SELECT COALESCE(show_id, 'unassigned') as sid, COUNT(*) as cnt FROM tracks GROUP BY show_id"
    ):
        by_show[row["sid"]] = row["cnt"]

    avg_bpm = conn.execute("SELECT AVG(bpm) FROM tracks WHERE bpm IS NOT NULL").fetchone()[0]
    total_duration = conn.execute("SELECT SUM(duration) FROM tracks").fetchone()[0] or 0

    return {
        "total_tracks": total,
        "with_bpm": with_bpm,
        "anthems": anthems,
        "by_show": by_show,
        "avg_bpm": round(avg_bpm, 1) if avg_bpm else None,
        "total_hours": round(total_duration / 3600, 1),
    }


def get_tracks_for_show(
    conn: sqlite3.Connection,
    show_id: str,
    bpm_range: tuple[int, int] | None = None,
    anthem_only: bool = False,
) -> list[dict]:
    """Get tracks suitable for a show."""
    query = "SELECT * FROM tracks WHERE 1=1"
    params: list[Any] = []

    if show_id:
        query += " AND (show_id = ? OR show_id IS NULL)"
        params.append(show_id)

    if bpm_range:
        query += " AND bpm IS NOT NULL AND bpm >= ? AND bpm <= ?"
        params.extend(bpm_range)

    if anthem_only:
        query += " AND is_anthem = 1"

    query += " ORDER BY last_played ASC NULLS FIRST, RANDOM()"

    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# =============================================================================
# CLI
# =============================================================================

def _cli() -> int:
    parser = argparse.ArgumentParser(description="Deep House Radio music catalog")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="Scan directories for music")
    p_scan.add_argument("--dir", type=str, help="Specific directory to scan")

    sub.add_parser("status", help="Show catalog statistics")

    p_list = sub.add_parser("list", help="List tracks")
    p_list.add_argument("--show", type=str, help="Filter by show_id")
    p_list.add_argument("--anthems", action="store_true", help="Show anthems only")
    p_list.add_argument("--limit", type=int, default=50)

    args = parser.parse_args()
    conn = get_db()

    if args.cmd == "scan":
        if args.dir:
            dirs = [Path(args.dir).expanduser()]
        else:
            # Default music directories
            dirs = [
                Path("~/Music").expanduser(),
                Path("output/music").resolve() if Path("output/music").exists() else None,
            ]
            dirs = [d for d in dirs if d is not None]

        total_new, total_updated = 0, 0
        for d in dirs:
            log(f"Scanning {d}...")
            new, updated = scan_directory(d, conn)
            total_new += new
            total_updated += updated
            log(f"  {new} new, {updated} updated")

        log(f"Done. Total: {total_new} new, {total_updated} updated tracks.")
        return 0

    if args.cmd == "status":
        stats = get_catalog_stats(conn)
        print(f"Music Catalog:")
        print(f"  Total tracks: {stats['total_tracks']}")
        print(f"  With BPM:     {stats['with_bpm']}")
        print(f"  Anthems:      {stats['anthems']}")
        print(f"  Avg BPM:      {stats['avg_bpm'] or 'N/A'}")
        print(f"  Total hours:  {stats['total_hours']}")
        print(f"  By show:")
        for show, count in sorted(stats["by_show"].items()):
            print(f"    {show:20s} {count}")
        return 0

    if args.cmd == "list":
        tracks = get_tracks_for_show(
            conn, args.show or "", anthem_only=args.anthems,
        )[:args.limit]
        for t in tracks:
            bpm_str = f"{t['bpm']:.0f}" if t.get("bpm") else "---"
            anthem_str = " [ANTHEM]" if t.get("is_anthem") else ""
            show_str = t.get("show_id") or "?"
            print(f"  {bpm_str:>3s} BPM | {show_str:12s} | {t['artist']:30s} - {t['title']}{anthem_str}")
        print(f"\n  Showing {len(tracks)} tracks")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
