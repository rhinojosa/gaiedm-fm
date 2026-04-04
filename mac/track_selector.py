#!/usr/bin/env python3
"""
Deep House Radio — Intelligent Track Selector

Selects the next track to play based on:
- Show context (BPM range, genres, vibe)
- BPM continuity (keep adjacent tracks within ~3 BPM)
- Harmonic compatibility (Camelot wheel)
- Anthem rotation (every N tracks)
- Deduplication (avoid recently played)
- Energy curve management

Combines local music catalog with optional SoundCloud results.
"""

from __future__ import annotations

import random
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from music_scanner import get_db
from bpm_analyzer import camelot_compatible
from crossfade_mixer import TrackInfo


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[selector {ts}] {msg}", flush=True)


# =============================================================================
# TRACK SELECTION
# =============================================================================

@dataclass
class ShowContext:
    """Current show parameters for track selection."""
    show_id: str
    bpm_range: tuple[int, int] = (118, 128)
    music_genres: list[str] | None = None
    anthem_enabled: bool = True
    anthem_frequency: int = 4
    crossfade_beats: int = 32


def select_next_track(
    conn: sqlite3.Connection,
    show: ShowContext,
    previous_track: TrackInfo | None = None,
    track_count: int = 0,
    recent_paths: set[str] | None = None,
) -> TrackInfo | None:
    """Select the next track to play.

    Args:
        conn: Database connection to music catalog
        show: Current show context
        previous_track: The track currently playing (for BPM/key continuity)
        track_count: How many tracks have played this set (for anthem rotation)
        recent_paths: Set of recently played file paths to exclude

    Returns:
        TrackInfo for the selected track, or None if no tracks available.
    """
    if recent_paths is None:
        recent_paths = set()

    # Determine if this should be an anthem
    is_anthem_slot = (
        show.anthem_enabled
        and show.anthem_frequency > 0
        and track_count > 0
        and track_count % show.anthem_frequency == 0
    )

    if is_anthem_slot:
        track = _select_anthem(conn, show, previous_track, recent_paths)
        if track:
            log(f"  ANTHEM: {track.path.name}")
            return track
        # Fall through to regular selection if no anthems available

    return _select_regular(conn, show, previous_track, recent_paths)


def _select_anthem(
    conn: sqlite3.Connection,
    show: ShowContext,
    previous: TrackInfo | None,
    recent_paths: set[str],
) -> TrackInfo | None:
    """Select an anthem track."""
    bpm_lo, bpm_hi = show.bpm_range
    # Widen BPM range slightly for anthems (they're worth the stretch)
    bpm_lo -= 4
    bpm_hi += 4

    query = """
        SELECT * FROM tracks
        WHERE is_anthem = 1
        AND (show_id = ? OR show_id IS NULL)
        AND duration > 60
    """
    params: list[Any] = [show.show_id]

    if previous and previous.bpm:
        # Prefer anthems close to current BPM
        query += " AND bpm IS NOT NULL AND bpm >= ? AND bpm <= ?"
        params.extend([bpm_lo, bpm_hi])

    query += " ORDER BY last_played ASC NULLS FIRST, RANDOM() LIMIT 20"

    rows = conn.execute(query, params).fetchall()
    return _pick_best(rows, previous, recent_paths)


def _select_regular(
    conn: sqlite3.Connection,
    show: ShowContext,
    previous: TrackInfo | None,
    recent_paths: set[str],
) -> TrackInfo | None:
    """Select a regular (non-anthem) track."""
    bpm_lo, bpm_hi = show.bpm_range

    # If we have a previous track, tighten BPM range for smooth transitions
    if previous and previous.bpm:
        target_bpm = previous.bpm
        bpm_lo = max(bpm_lo, target_bpm - 3)
        bpm_hi = min(bpm_hi, target_bpm + 3)

    query = """
        SELECT * FROM tracks
        WHERE (show_id = ? OR show_id IS NULL)
        AND duration > 60
    """
    params: list[Any] = [show.show_id]

    # Prefer tracks with BPM data in range, but don't exclude unanalyzed
    query += " AND (bpm IS NULL OR (bpm >= ? AND bpm <= ?))"
    params.extend([bpm_lo, bpm_hi])

    query += " ORDER BY last_played ASC NULLS FIRST, RANDOM() LIMIT 30"

    rows = conn.execute(query, params).fetchall()

    track = _pick_best(rows, previous, recent_paths)
    if track:
        return track

    # Fallback: widen to full show BPM range
    bpm_lo, bpm_hi = show.bpm_range
    query = """
        SELECT * FROM tracks
        WHERE (show_id = ? OR show_id IS NULL)
        AND duration > 60
        AND (bpm IS NULL OR (bpm >= ? AND bpm <= ?))
        ORDER BY RANDOM() LIMIT 20
    """
    rows = conn.execute(query, [show.show_id, bpm_lo, bpm_hi]).fetchall()
    return _pick_best(rows, previous, recent_paths)


def _pick_best(
    rows: list[sqlite3.Row],
    previous: TrackInfo | None,
    recent_paths: set[str],
) -> TrackInfo | None:
    """From candidate rows, pick the best match considering key compatibility."""
    candidates = [r for r in rows if r["path"] not in recent_paths]
    if not candidates:
        # Allow recent if nothing else available
        candidates = list(rows)
    if not candidates:
        return None

    # Score candidates
    scored = []
    for row in candidates:
        score = 0.0

        # Key compatibility bonus
        if previous and previous.key and row["key"]:
            if camelot_compatible(previous.key, row["key"]):
                score += 10.0

        # BPM proximity bonus (if previous track known)
        if previous and previous.bpm and row["bpm"]:
            bpm_diff = abs(previous.bpm - row["bpm"])
            score += max(0, 5.0 - bpm_diff)  # 0-5 points for BPM closeness

        # Freshness bonus (less recently played = higher score)
        if row["last_played"] is None:
            score += 3.0  # Never played = good
        else:
            hours_ago = (time.time() - row["last_played"]) / 3600
            score += min(3.0, hours_ago / 8)  # Up to 3 points for freshness

        # Energy variety (slight randomization)
        score += random.uniform(0, 2.0)

        scored.append((score, row))

    # Sort by score descending, pick from top 5
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:min(5, len(scored))]
    _, best = random.choice(top)

    return _row_to_track_info(best)


def _row_to_track_info(row: sqlite3.Row) -> TrackInfo:
    """Convert a database row to a TrackInfo object."""
    return TrackInfo(
        path=Path(row["path"]),
        bpm=row["bpm"] or 120.0,
        mix_in_pt=row["mix_in_pt"] or 0.0,
        mix_out_pt=row["mix_out_pt"] or row["duration"] or 300.0,
        duration=row["duration"] or 300.0,
        key=row["key"] or "",
    )


def mark_played(conn: sqlite3.Connection, track: TrackInfo) -> None:
    """Record that a track was played."""
    conn.execute("""
        UPDATE tracks SET
            last_played = ?,
            play_count = COALESCE(play_count, 0) + 1
        WHERE path = ?
    """, (time.time(), str(track.path)))
    conn.commit()


def get_catalog_track_count(conn: sqlite3.Connection, show_id: str) -> int:
    """Get number of tracks available for a show."""
    row = conn.execute(
        "SELECT COUNT(*) FROM tracks WHERE (show_id = ? OR show_id IS NULL) AND duration > 60",
        (show_id,),
    ).fetchone()
    return row[0] if row else 0
