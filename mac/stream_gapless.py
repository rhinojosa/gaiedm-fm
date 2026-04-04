#!/usr/bin/env python3
"""
Deep House Radio — Music-First Streamer with DJ Crossfading

Streams music tracks to Icecast with beat-matched crossfade transitions,
DJ interjections between sets, and anthem rotation.

Flow: track → crossfade → track → ... → DJ interjection → track → ...
"""

import subprocess
import random
import signal
import sys
import os
import re
import json
import time
import urllib.request
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field

# Import subsystems
try:
    from play_history import get_history
    HISTORY_ENABLED = True
except ImportError:
    HISTORY_ENABLED = False

try:
    from schedule import load_schedule, StationSchedule
    SCHEDULE_ENABLED = True
except ImportError:
    SCHEDULE_ENABLED = False

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Directories
DJ_SEGMENTS_DIR = PROJECT_ROOT / "output" / "talk_segments"
AI_BUMPERS_DIR = PROJECT_ROOT / "output" / "music_bumpers"

# Weekly schedule
DEFAULT_SCHEDULE_PATH = PROJECT_ROOT / "config" / "schedule.yaml"
SCHEDULE_PATH = Path(os.environ.get("WRIT_SCHEDULE_PATH", str(DEFAULT_SCHEDULE_PATH))).expanduser()

# Icecast config
ICECAST_HOST = os.environ.get("ICECAST_HOST", "localhost")
ICECAST_PORT = int(os.environ.get("ICECAST_PORT", "8000"))
ICECAST_MOUNT = os.environ.get("ICECAST_MOUNT", "/stream")
ICECAST_USER = os.environ.get("ICECAST_USER", "source")
ICECAST_PASS = os.environ.get("ICECAST_PASS", "writ_source_2024")
ICECAST_URL = f"icecast://{ICECAST_USER}:{ICECAST_PASS}@{ICECAST_HOST}:{ICECAST_PORT}{ICECAST_MOUNT}"
ICECAST_STATUS_URL = os.environ.get(
    "ICECAST_STATUS_URL",
    f"http://{ICECAST_HOST}:{ICECAST_PORT}/status-json.xsl",
)

# =============================================================================
# RUNTIME STATE
# =============================================================================

running = True
encoder_proc = None
skip_current = False
current_track_info: dict = {
    "track": None,
    "artist": None,
    "type": None,
    "bpm": None,
    "key": None,
    "show_id": None,
    "show": None,
    "listeners": 0,
}

# Command file
COMMAND_FILE = Path(
    os.environ.get("WRIT_COMMAND_FILE", str(PROJECT_ROOT / "command.txt"))
).expanduser()

# Now playing JSON
DEFAULT_NOW_PLAYING = PROJECT_ROOT / "output" / "now_playing.json"
NOW_PLAYING_PATHS = [DEFAULT_NOW_PLAYING]

env_now_playing = os.environ.get("WRIT_NOW_PLAYING_PATHS")
if env_now_playing:
    NOW_PLAYING_PATHS = [
        Path(p).expanduser() for p in env_now_playing.split(os.pathsep) if p
    ]
else:
    public_repo_path = (
        Path.home() / "GitHub" / "keltokhy.github.io" / "public" / "now_playing.json"
    )
    if public_repo_path.parent.exists():
        NOW_PLAYING_PATHS.append(public_repo_path)

NOW_PLAYING_PATHS = list(dict.fromkeys(NOW_PLAYING_PATHS))


# =============================================================================
# SHOW CONTEXT
# =============================================================================

@dataclass
class ShowContext:
    show_id: str
    show_name: str
    show_description: str
    host: str
    topic_focus: str
    segment_types: list[str]
    bumper_style: str
    voices: dict[str, str] = field(default_factory=dict)
    bpm_range: tuple[int, int] = (118, 128)
    music_genres: list[str] = field(default_factory=lambda: ["deep house"])
    crossfade_beats: int = 32
    dj_frequency: int = 4
    anthem_enabled: bool = True
    anthem_frequency: int = 4


def get_show_context(station_schedule) -> ShowContext:
    """Resolve the current show from the schedule."""
    resolved = station_schedule.resolve()
    return ShowContext(
        show_id=resolved.show_id,
        show_name=resolved.name,
        show_description=resolved.description,
        host=resolved.host,
        topic_focus=resolved.topic_focus,
        segment_types=resolved.segment_types,
        bumper_style=resolved.bumper_style,
        voices=dict(resolved.voices),
        bpm_range=resolved.bpm_range,
        music_genres=list(resolved.music_genres),
        crossfade_beats=resolved.crossfade_beats,
        dj_frequency=resolved.dj_frequency,
        anthem_enabled=resolved.anthem_enabled,
        anthem_frequency=resolved.anthem_frequency,
    )


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def signal_handler(signum, frame):
    global running, encoder_proc
    log("Shutting down...")
    running = False
    if encoder_proc:
        encoder_proc.terminate()
    sys.exit(0)


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


LISTENER_CACHE_SECONDS = 15
_last_listener_count = 0
_last_listener_check = 0.0


def get_listener_count() -> int:
    """Fetch listener count with a short cache."""
    global _last_listener_count, _last_listener_check
    now = time.time()
    if now - _last_listener_check < LISTENER_CACHE_SECONDS:
        return _last_listener_count

    _last_listener_check = now
    try:
        with urllib.request.urlopen(ICECAST_STATUS_URL, timeout=1.5) as resp:
            data = json.load(resp)
        source = data.get("icestats", {}).get("source", {})
        _last_listener_count = int(source.get("listeners", 0) or 0)
    except Exception:
        pass

    return _last_listener_count


def write_json_atomic(path: Path, payload: dict) -> None:
    """Write JSON atomically to avoid partial reads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload))
    tmp_path.replace(path)


def update_now_playing(
    track: str,
    track_type: str,
    artist: str = "",
    bpm: float | None = None,
    key: str = "",
    show_id: str | None = None,
    show_name: str | None = None,
    host: str | None = None,
    is_anthem: bool = False,
):
    """Update current track info in-memory and write to disk."""
    new_info = {
        "track": track,
        "artist": artist,
        "type": track_type,
        "bpm": bpm,
        "key": key,
        "show_id": show_id,
        "show": show_name,
        "host": host,
        "is_anthem": is_anthem,
        "timestamp": datetime.now().isoformat(),
        "listeners": get_listener_count(),
    }
    current_track_info.update(new_info)
    for k in list(current_track_info):
        if k not in new_info:
            del current_track_info[k]
    for path in NOW_PLAYING_PATHS:
        try:
            write_json_atomic(path, current_track_info)
        except Exception:
            pass


def record_play(filepath: Path, name: str, vibe: str, show_id: str):
    """Record a track play in the history database."""
    if HISTORY_ENABLED:
        try:
            get_history().record_play(
                filepath=str(filepath),
                track_name=name,
                vibe=vibe,
                time_period=show_id,
                listeners=get_listener_count(),
            )
        except Exception:
            pass


def check_command() -> str | None:
    """Check for pending command."""
    try:
        if COMMAND_FILE.exists() and (cmd := COMMAND_FILE.read_text().strip()):
            COMMAND_FILE.write_text("")
            return cmd
    except Exception:
        pass
    return None


# =============================================================================
# DJ SEGMENT MANAGEMENT
# =============================================================================

def get_dj_segments(show_id: str) -> list[Path]:
    """Load pre-generated DJ dialogue segments for a show."""
    show_dir = DJ_SEGMENTS_DIR / show_id
    if not show_dir.exists():
        return []

    segments = sorted(show_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime)
    return segments


def get_track_display(track) -> tuple[str, str]:
    """Get display name and artist for a track."""
    if hasattr(track, 'path'):
        path = track.path
    else:
        path = Path(track)

    # Try to read from catalog DB
    try:
        from music_scanner import get_db
        conn = get_db()
        row = conn.execute(
            "SELECT title, artist FROM tracks WHERE path = ?", (str(path),)
        ).fetchone()
        if row and row["title"]:
            return row["title"], row["artist"] or ""
    except Exception:
        pass

    # Fallback: parse filename
    name = path.stem
    # Try "Artist - Title" format
    if " - " in name:
        parts = name.split(" - ", 1)
        return parts[1].strip(), parts[0].strip()
    return name, ""


# =============================================================================
# AUDIO PIPELINE
# =============================================================================

def start_encoder() -> subprocess.Popen:
    """Start persistent ffmpeg encoder to Icecast."""
    return subprocess.Popen(
        [
            "ffmpeg", "-v", "warning",
            "-re",
            "-f", "s16le",
            "-ar", "44100",
            "-ac", "2",
            "-i", "-",
            "-acodec", "libmp3lame",
            "-b:a", "192k",
            "-content_type", "audio/mpeg",
            "-ice_name", "Deep House Radio",
            "-ice_description", "Feel the frequency",
            "-ice_genre", "Deep House / Progressive House",
            "-f", "mp3",
            ICECAST_URL
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE
    )


def wait_for_encoder_ready(encoder: subprocess.Popen, timeout: float = 2.0) -> bool:
    """Wait briefly to ensure encoder connected."""
    time.sleep(0.3)
    if encoder.poll() is not None:
        try:
            stderr = encoder.stderr.read().decode() if encoder.stderr else ""
            if stderr:
                log(f"Encoder error: {stderr[:200]}")
        except Exception:
            pass
        return False
    return True


# =============================================================================
# MAIN LOOP - MUSIC FIRST WITH CROSSFADING
# =============================================================================

def run():
    global running, encoder_proc, skip_current

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    log("=== Deep House Radio — Music-First Streamer ===")
    log(f"DJ segments: {DJ_SEGMENTS_DIR}")
    log(f"Streaming to: {ICECAST_URL}")

    # Load schedule
    if not SCHEDULE_ENABLED:
        raise RuntimeError("Schedule module is unavailable")
    if not SCHEDULE_PATH.exists():
        raise FileNotFoundError(f"Schedule file not found: {SCHEDULE_PATH}")

    station_schedule = load_schedule(SCHEDULE_PATH)
    log(f"Loaded schedule with {len(station_schedule.shows)} shows")

    # Start embedded API server
    from api_server import start_api_thread
    start_api_thread(current_track_info, lambda: encoder_proc, get_listener_count)
    log("API server started on port 8001")

    # Initialize music catalog
    try:
        from music_scanner import get_db as get_catalog_db
        from track_selector import (
            select_next_track, mark_played, get_catalog_track_count,
            ShowContext as SelectorShowContext,
        )
        from crossfade_mixer import (
            mix_transition, pipe_track_body, pipe_dj_segment, TrackInfo,
        )
        catalog_conn = get_catalog_db()
        CATALOG_ENABLED = True
        log("Music catalog connected")
    except ImportError as e:
        log(f"Music catalog unavailable: {e}")
        CATALOG_ENABLED = False
        catalog_conn = None

    while running:
        log("Starting encoder...")
        encoder_proc = start_encoder()

        if not wait_for_encoder_ready(encoder_proc):
            log("Encoder failed to connect, retrying in 10s...")
            time.sleep(10)
            continue

        log("Encoder connected to Icecast")

        while running and encoder_proc.poll() is None:
            # Get current show
            ctx = get_show_context(station_schedule)
            log(f"Show: {ctx.show_name} ({ctx.show_id})")
            log(f"  BPM: {ctx.bpm_range[0]}-{ctx.bpm_range[1]} | Crossfade: {ctx.crossfade_beats} beats")
            log(f"  DJ every {ctx.dj_frequency} tracks | Anthems: {'every ' + str(ctx.anthem_frequency) if ctx.anthem_enabled else 'off'}")

            if not CATALOG_ENABLED or catalog_conn is None:
                log("  No music catalog — waiting 30s...")
                time.sleep(30)
                continue

            # Check track availability
            track_count_available = get_catalog_track_count(catalog_conn, ctx.show_id)
            if track_count_available == 0:
                log(f"  No tracks for {ctx.show_id} — waiting 30s")
                log(f"  Run: uv run python mac/music_scanner.py scan")
                time.sleep(30)
                continue

            log(f"  {track_count_available} tracks available")

            # Create selector context
            selector_ctx = SelectorShowContext(
                show_id=ctx.show_id,
                bpm_range=ctx.bpm_range,
                music_genres=ctx.music_genres,
                anthem_enabled=ctx.anthem_enabled,
                anthem_frequency=ctx.anthem_frequency,
                crossfade_beats=ctx.crossfade_beats,
            )

            # Track counters
            track_num = 0
            recent_paths: set[str] = set()
            current_track: TrackInfo | None = None

            # --- Music set loop ---
            while running and encoder_proc.poll() is None:
                # Check if show changed
                new_ctx = get_show_context(station_schedule)
                if new_ctx.show_id != ctx.show_id:
                    log(f"Show changed to {new_ctx.show_name} — switching...")
                    break

                # Check for commands
                cmd = check_command()
                if cmd == "skip":
                    log("  Command: skip")
                    skip_current = True

                # Select next track
                track_num += 1
                next_track = select_next_track(
                    catalog_conn, selector_ctx,
                    previous_track=current_track,
                    track_count=track_num,
                    recent_paths=recent_paths,
                )

                if next_track is None:
                    log("  No tracks available — waiting 15s")
                    time.sleep(15)
                    continue

                # Get display info
                title, artist = get_track_display(next_track)
                is_anthem = (
                    ctx.anthem_enabled
                    and ctx.anthem_frequency > 0
                    and track_num % ctx.anthem_frequency == 0
                )

                anthem_tag = " [ANTHEM]" if is_anthem else ""
                bpm_str = f"{next_track.bpm:.0f}" if next_track.bpm else "?"
                key_str = next_track.key or "?"
                log(f"  TRACK {track_num}: {artist} - {title} ({bpm_str} BPM, {key_str}){anthem_tag}")

                update_now_playing(
                    track=title,
                    artist=artist,
                    track_type="anthem" if is_anthem else "music",
                    bpm=next_track.bpm,
                    key=next_track.key,
                    show_id=ctx.show_id,
                    show_name=ctx.show_name,
                    host=ctx.host,
                    is_anthem=is_anthem,
                )

                # --- Play the track ---
                if current_track is not None:
                    # Crossfade from previous track into this one
                    ok = mix_transition(
                        current_track, next_track,
                        encoder_proc.stdin,
                        crossfade_beats=ctx.crossfade_beats,
                    )
                    if not ok:
                        log("  Crossfade failed, reconnecting...")
                        break

                    # Pipe body of new track (after crossfade head, before crossfade tail)
                    beat_dur = 60.0 / (next_track.bpm or 120.0)
                    overlap_secs = ctx.crossfade_beats * beat_dur
                    ok = pipe_track_body(
                        next_track, encoder_proc.stdin,
                        skip_head=overlap_secs * 0.5,
                        skip_tail=overlap_secs,
                    )
                else:
                    # First track — pipe from start (with fade-in via crossfade mixer)
                    ok = pipe_track_body(
                        next_track, encoder_proc.stdin,
                        skip_head=0.0,
                        skip_tail=0.0,
                    )

                if not ok:
                    log("  Track pipe failed, reconnecting...")
                    break

                # Record play
                record_play(next_track.path, f"{artist} - {title}", ctx.bumper_style, ctx.show_id)
                mark_played(catalog_conn, next_track)
                recent_paths.add(str(next_track.path))
                if len(recent_paths) > 50:
                    recent_paths = set(list(recent_paths)[-30:])

                current_track = next_track

                # --- DJ interjection ---
                if track_num % ctx.dj_frequency == 0:
                    dj_segs = get_dj_segments(ctx.show_id)
                    if dj_segs:
                        dj_seg = dj_segs[0]  # Take oldest (FIFO)
                        dj_name = dj_seg.stem
                        log(f"  DJ: {dj_name}")
                        update_now_playing(
                            track=dj_name,
                            track_type="dj",
                            show_id=ctx.show_id,
                            show_name=ctx.show_name,
                            host=ctx.host,
                        )
                        pipe_dj_segment(dj_seg, encoder_proc.stdin)
                        # Consume after playing
                        try:
                            dj_seg.unlink()
                            log(f"    (consumed)")
                        except Exception:
                            pass

            if running and encoder_proc.poll() is None:
                log("Set complete, refreshing...")

        if running:
            log("Encoder died, restarting...")
            time.sleep(2)

    log("=== Stream stopped ===")


if __name__ == "__main__":
    run()
