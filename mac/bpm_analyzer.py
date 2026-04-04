#!/usr/bin/env python3
"""
Deep House Radio — BPM & Key Analysis

Analyzes audio tracks for BPM, musical key, energy, and optimal mix points
using librosa. Results are stored in the music catalog SQLite database.

Usage:
    uv run python mac/bpm_analyzer.py analyze              # Analyze all unanalyzed
    uv run python mac/bpm_analyzer.py analyze --force       # Re-analyze all
    uv run python mac/bpm_analyzer.py analyze --path file   # Analyze one file
    uv run python mac/bpm_analyzer.py status                # Show analysis stats
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

# Lazy-load librosa (heavy import)
_librosa = None


def _get_librosa():
    global _librosa
    if _librosa is None:
        import librosa
        _librosa = librosa
    return _librosa


# Import catalog DB from music_scanner
sys.path.insert(0, str(Path(__file__).parent))
from music_scanner import get_db, DB_PATH, log


# =============================================================================
# CAMELOT WHEEL (for harmonic mixing)
# =============================================================================

# Maps (key, mode) to Camelot notation
# mode: "major" or "minor"
CAMELOT_WHEEL = {
    ("C", "major"): "8B", ("A", "minor"): "8A",
    ("G", "major"): "9B", ("E", "minor"): "9A",
    ("D", "major"): "10B", ("B", "minor"): "10A",
    ("A", "major"): "11B", ("F#", "minor"): "11A",
    ("E", "major"): "12B", ("C#", "minor"): "12A",
    ("B", "major"): "1B", ("G#", "minor"): "1A",
    ("F#", "major"): "2B", ("D#", "minor"): "2A",
    ("Db", "major"): "3B", ("Bb", "minor"): "3A",
    ("Ab", "major"): "4B", ("F", "minor"): "4A",
    ("Eb", "major"): "5B", ("C", "minor"): "5A",
    ("Bb", "major"): "6B", ("G", "minor"): "6A",
    ("F", "major"): "7B", ("D", "minor"): "7A",
}

# Pitch class index to key name
PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "Bb", "B"]


def camelot_compatible(key_a: str, key_b: str) -> bool:
    """Check if two Camelot keys are compatible for harmonic mixing.

    Compatible means: same key, +/-1 on the wheel, or same number A<->B.
    """
    if not key_a or not key_b:
        return True  # Unknown keys — allow mixing

    try:
        num_a, letter_a = int(key_a[:-1]), key_a[-1]
        num_b, letter_b = int(key_b[:-1]), key_b[-1]
    except (ValueError, IndexError):
        return True

    # Same key
    if key_a == key_b:
        return True
    # Same number, different letter (major/minor swap)
    if num_a == num_b:
        return True
    # Adjacent on wheel (same letter)
    if letter_a == letter_b and abs(num_a - num_b) in (1, 11):
        return True
    return False


# =============================================================================
# AUDIO ANALYSIS
# =============================================================================

def analyze_track(filepath: Path) -> dict:
    """Analyze a single track for BPM, key, energy, and mix points.

    Returns dict with: bpm, key, energy, mix_in_pt, mix_out_pt
    """
    librosa = _get_librosa()

    result = {
        "bpm": None,
        "key": None,
        "energy": None,
        "mix_in_pt": None,
        "mix_out_pt": None,
    }

    try:
        # Load audio (mono, 22050 Hz for analysis — fast)
        y, sr = librosa.load(str(filepath), sr=22050, mono=True, duration=300)
        if len(y) == 0:
            return result

        duration = len(y) / sr

        # --- BPM Detection ---
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        # tempo can be array in newer librosa
        bpm = float(np.atleast_1d(tempo)[0])

        # House music sanity check: if detected BPM is half/double, correct it
        if bpm < 100 and bpm > 50:
            bpm *= 2
        elif bpm > 160:
            bpm /= 2
        result["bpm"] = round(bpm, 1)

        # --- Key Detection ---
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        chroma_avg = chroma.mean(axis=1)
        pitch_class = int(np.argmax(chroma_avg))
        key_name = PITCH_CLASSES[pitch_class]

        # Determine major/minor using Krumhansl-Schmuckler
        major_profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
        minor_profile = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

        major_corr = np.corrcoef(chroma_avg, np.roll(major_profile, pitch_class))[0, 1]
        minor_corr = np.corrcoef(chroma_avg, np.roll(minor_profile, pitch_class))[0, 1]

        mode = "major" if major_corr > minor_corr else "minor"
        camelot = CAMELOT_WHEEL.get((key_name, mode), f"{key_name}{mode[0]}")
        result["key"] = camelot

        # --- Energy (RMS-based, 0-1 scale) ---
        rms = librosa.feature.rms(y=y)[0]
        result["energy"] = round(float(np.mean(rms) / (np.max(rms) + 1e-6)), 3)

        # --- Mix Points (intro/outro detection) ---
        # Find where energy first exceeds 50% of max (mix-in point)
        # and where it last exceeds 50% (mix-out point)
        rms_smooth = np.convolve(rms, np.ones(50) / 50, mode="same")
        threshold = np.max(rms_smooth) * 0.4

        above = np.where(rms_smooth > threshold)[0]
        if len(above) > 2:
            # Convert frame indices to seconds
            frames_per_sec = sr / 512  # default hop_length
            mix_in = float(above[0]) / frames_per_sec
            mix_out = float(above[-1]) / frames_per_sec
            # Clamp to reasonable values
            result["mix_in_pt"] = round(min(mix_in, 30.0), 1)
            result["mix_out_pt"] = round(min(mix_out, duration), 1)
        else:
            result["mix_in_pt"] = 0.0
            result["mix_out_pt"] = round(duration, 1)

    except Exception as e:
        log(f"  Analysis failed for {filepath.name}: {e}")

    return result


def analyze_catalog(conn: sqlite3.Connection, force: bool = False) -> int:
    """Analyze all tracks in catalog that haven't been analyzed yet."""
    if force:
        rows = conn.execute("SELECT path FROM tracks").fetchall()
    else:
        rows = conn.execute("SELECT path FROM tracks WHERE bpm IS NULL").fetchall()

    analyzed = 0
    total = len(rows)
    log(f"Analyzing {total} tracks...")

    for i, row in enumerate(rows):
        filepath = Path(row["path"])
        if not filepath.exists():
            log(f"  [{i+1}/{total}] MISSING: {filepath.name}")
            continue

        log(f"  [{i+1}/{total}] {filepath.name}")
        result = analyze_track(filepath)

        if result["bpm"] is not None:
            conn.execute("""
                UPDATE tracks SET
                    bpm = ?, key = ?, energy = ?,
                    mix_in_pt = ?, mix_out_pt = ?
                WHERE path = ?
            """, (
                result["bpm"], result["key"], result["energy"],
                result["mix_in_pt"], result["mix_out_pt"],
                str(filepath),
            ))
            analyzed += 1

        # Commit every 10 tracks
        if (i + 1) % 10 == 0:
            conn.commit()

    conn.commit()
    log(f"Done. Analyzed {analyzed}/{total} tracks.")
    return analyzed


# =============================================================================
# CLI
# =============================================================================

def _cli() -> int:
    parser = argparse.ArgumentParser(description="BPM & key analysis for music catalog")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_analyze = sub.add_parser("analyze", help="Analyze tracks in catalog")
    p_analyze.add_argument("--force", action="store_true", help="Re-analyze all tracks")
    p_analyze.add_argument("--path", type=str, help="Analyze a single file")

    sub.add_parser("status", help="Show analysis statistics")

    args = parser.parse_args()
    conn = get_db()

    if args.cmd == "analyze":
        if args.path:
            filepath = Path(args.path).expanduser().resolve()
            log(f"Analyzing: {filepath}")
            result = analyze_track(filepath)
            print(f"  BPM:      {result['bpm']}")
            print(f"  Key:      {result['key']}")
            print(f"  Energy:   {result['energy']}")
            print(f"  Mix-in:   {result['mix_in_pt']}s")
            print(f"  Mix-out:  {result['mix_out_pt']}s")
            return 0

        analyze_catalog(conn, force=args.force)
        return 0

    if args.cmd == "status":
        total = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        analyzed = conn.execute("SELECT COUNT(*) FROM tracks WHERE bpm IS NOT NULL").fetchone()[0]
        pending = total - analyzed

        print(f"BPM Analysis Status:")
        print(f"  Total tracks:  {total}")
        print(f"  Analyzed:      {analyzed}")
        print(f"  Pending:       {pending}")

        if analyzed > 0:
            stats = conn.execute("""
                SELECT
                    MIN(bpm) as min_bpm, MAX(bpm) as max_bpm, AVG(bpm) as avg_bpm,
                    AVG(energy) as avg_energy
                FROM tracks WHERE bpm IS NOT NULL
            """).fetchone()
            print(f"  BPM range:     {stats['min_bpm']:.0f} - {stats['max_bpm']:.0f}")
            print(f"  Avg BPM:       {stats['avg_bpm']:.1f}")
            print(f"  Avg energy:    {stats['avg_energy']:.3f}")

            # Key distribution
            print(f"  Top keys:")
            for row in conn.execute("""
                SELECT key, COUNT(*) as cnt FROM tracks
                WHERE key IS NOT NULL
                GROUP BY key ORDER BY cnt DESC LIMIT 5
            """):
                print(f"    {row['key']:5s} {row['cnt']}")

        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
