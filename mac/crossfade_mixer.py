#!/usr/bin/env python3
"""
Deep House Radio — Beat-Matched Crossfade Mixer

Handles DJ-style transitions between tracks with:
- BPM-aware crossfading (time-stretch if needed)
- Equal-power crossfade curves (sounds natural)
- Configurable overlap duration (in beats)
- PCM output suitable for piping to ffmpeg encoder

The mixer works by:
1. Decoding the outgoing track's tail via ffmpeg → numpy
2. Decoding the incoming track's head via ffmpeg → numpy
3. Time-stretching the incoming track if BPMs differ by >2
4. Applying equal-power crossfade in the overlap region
5. Outputting: body_a → mixed_overlap → body_b as continuous PCM
"""

from __future__ import annotations

import subprocess
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import numpy as np

SAMPLE_RATE = 44100
CHANNELS = 2
BYTES_PER_SAMPLE = 2  # s16le
FRAME_SIZE = CHANNELS * BYTES_PER_SAMPLE  # 4 bytes per frame
CHUNK_SIZE = 8192


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[mixer {ts}] {msg}", flush=True)


@dataclass
class TrackInfo:
    """Metadata for a track being mixed."""
    path: Path
    bpm: float = 120.0
    mix_in_pt: float = 0.0      # seconds: where to start playing (after intro)
    mix_out_pt: float = 0.0     # seconds: where the outro begins
    duration: float = 0.0       # total duration
    key: str = ""


# =============================================================================
# AUDIO DECODING
# =============================================================================

def decode_segment(
    filepath: Path,
    start_time: float = 0,
    duration: float | None = None,
    target_lufs: float = -16,
) -> np.ndarray:
    """Decode a segment of audio to numpy array (float32, stereo, 44100Hz).

    Returns shape (samples, 2) array.
    """
    cmd = ["ffmpeg", "-v", "warning"]

    if start_time > 0:
        cmd.extend(["-ss", str(start_time)])

    cmd.extend(["-i", str(filepath)])

    if duration is not None:
        cmd.extend(["-t", str(duration)])

    cmd.extend([
        "-vn",
        "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11,aresample={SAMPLE_RATE}",
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "-ar", str(SAMPLE_RATE),
        "-ac", str(CHANNELS),
        "-",
    ])

    proc = subprocess.run(cmd, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg decode failed: {proc.stderr[:200]}")

    # Convert s16le bytes to float32 numpy array
    raw = np.frombuffer(proc.stdout, dtype=np.int16)
    if len(raw) == 0:
        return np.zeros((0, CHANNELS), dtype=np.float32)

    # Reshape to (samples, channels)
    samples = raw.reshape(-1, CHANNELS).astype(np.float32) / 32768.0
    return samples


def numpy_to_pcm_bytes(audio: np.ndarray) -> bytes:
    """Convert float32 numpy array back to s16le bytes for piping."""
    clamped = np.clip(audio, -1.0, 1.0)
    pcm = (clamped * 32767).astype(np.int16)
    return pcm.tobytes()


# =============================================================================
# CROSSFADE ENGINE
# =============================================================================

def equal_power_curve(length: int) -> tuple[np.ndarray, np.ndarray]:
    """Generate equal-power crossfade curves.

    Returns (fade_out, fade_in) arrays of shape (length, 1).
    Equal power: out = cos(t * pi/2), in = sin(t * pi/2)
    This maintains constant perceived loudness during the transition.
    """
    t = np.linspace(0, 1, length, dtype=np.float32)
    fade_out = np.cos(t * np.pi / 2).reshape(-1, 1)
    fade_in = np.sin(t * np.pi / 2).reshape(-1, 1)
    return fade_out, fade_in


def time_stretch_simple(audio: np.ndarray, factor: float) -> np.ndarray:
    """Simple time-stretch via linear interpolation.

    factor > 1 = slower (more samples), factor < 1 = faster (fewer samples).
    For small adjustments (<5%), this is nearly transparent.
    """
    if abs(factor - 1.0) < 0.001:
        return audio

    original_len = len(audio)
    target_len = int(original_len * factor)

    indices = np.linspace(0, original_len - 1, target_len)
    floor_idx = np.floor(indices).astype(int)
    ceil_idx = np.minimum(floor_idx + 1, original_len - 1)
    frac = (indices - floor_idx).reshape(-1, 1).astype(np.float32)

    stretched = audio[floor_idx] * (1 - frac) + audio[ceil_idx] * frac
    return stretched.astype(np.float32)


def compute_crossfade(
    track_a_tail: np.ndarray,
    track_b_head: np.ndarray,
    bpm_a: float,
    bpm_b: float,
    crossfade_beats: int = 32,
) -> np.ndarray:
    """Mix the overlap region between two tracks.

    Args:
        track_a_tail: Numpy audio from outgoing track's end region
        track_b_head: Numpy audio from incoming track's start region
        bpm_a: BPM of outgoing track
        bpm_b: BPM of incoming track
        crossfade_beats: Number of beats to overlap

    Returns:
        Mixed numpy audio for the crossfade region
    """
    # Calculate crossfade duration in samples at track A's tempo
    beat_duration_a = 60.0 / bpm_a
    crossfade_seconds = crossfade_beats * beat_duration_a
    crossfade_samples = int(crossfade_seconds * SAMPLE_RATE)

    # Time-stretch track B's head if BPMs differ by more than 2
    bpm_diff = abs(bpm_a - bpm_b)
    if bpm_diff > 2.0:
        stretch_factor = bpm_b / bpm_a  # >1 means B is faster, stretch to slow down
        log(f"  Time-stretch: {bpm_b:.1f} → {bpm_a:.1f} BPM (factor {stretch_factor:.3f})")
        track_b_head = time_stretch_simple(track_b_head, stretch_factor)

    # Ensure both arrays are long enough for the crossfade
    cf_len = min(crossfade_samples, len(track_a_tail), len(track_b_head))
    if cf_len < SAMPLE_RATE:  # Less than 1 second — just concatenate
        return np.concatenate([track_a_tail, track_b_head])

    # Extract crossfade regions
    a_fade = track_a_tail[-cf_len:]
    b_fade = track_b_head[:cf_len]

    # Apply equal-power crossfade
    fade_out, fade_in = equal_power_curve(cf_len)
    mixed = a_fade * fade_out + b_fade * fade_in

    return mixed


# =============================================================================
# HIGH-LEVEL MIXING API
# =============================================================================

def mix_transition(
    track_a: TrackInfo,
    track_b: TrackInfo,
    encoder_stdin: BinaryIO,
    crossfade_beats: int = 32,
    overlap_seconds: float | None = None,
) -> bool:
    """Perform a DJ-style crossfade transition between two tracks.

    Pipes the following sequence to the encoder:
    1. Track A body (from mix_in to just before crossfade region)
    2. Crossfade overlap (A fading out + B fading in, beat-matched)
    3. Track B will continue from after the crossfade (caller handles next transition)

    Args:
        track_a: Outgoing track info
        track_b: Incoming track info
        encoder_stdin: File-like object to write PCM bytes to
        crossfade_beats: Number of beats for the crossfade
        overlap_seconds: Override crossfade duration (seconds). If None, calculated from beats.

    Returns:
        True if successful, False if encoder died or error occurred.
    """
    try:
        bpm_a = track_a.bpm or 120.0
        bpm_b = track_b.bpm or 120.0

        # Calculate overlap duration
        if overlap_seconds is None:
            beat_duration = 60.0 / bpm_a
            overlap_seconds = crossfade_beats * beat_duration

        # Determine track A's playback region
        a_start = track_a.mix_in_pt or 0.0
        a_end = track_a.mix_out_pt or track_a.duration
        if a_end <= a_start:
            a_end = track_a.duration or 300.0

        # Body of track A (everything before the crossfade zone)
        a_body_duration = max(0, (a_end - a_start) - overlap_seconds)

        # Decode and pipe track A body
        if a_body_duration > 1.0:
            log(f"  Piping track A body: {a_body_duration:.0f}s")
            _pipe_decoded_stream(track_a.path, encoder_stdin, a_start, a_body_duration)

        # Decode track A tail for crossfade
        a_tail_start = a_start + a_body_duration
        a_tail = decode_segment(track_a.path, a_tail_start, overlap_seconds)

        # Decode track B head for crossfade
        b_start = track_b.mix_in_pt or 0.0
        b_head = decode_segment(track_b.path, max(0, b_start - overlap_seconds * 0.5), overlap_seconds)

        if len(a_tail) > 0 and len(b_head) > 0:
            # Compute and pipe crossfade
            log(f"  Crossfade: {overlap_seconds:.1f}s ({crossfade_beats} beats)")
            mixed = compute_crossfade(a_tail, b_head, bpm_a, bpm_b, crossfade_beats)
            pcm = numpy_to_pcm_bytes(mixed)
            encoder_stdin.write(pcm)
        else:
            # Fallback: just pipe what we have
            if len(a_tail) > 0:
                encoder_stdin.write(numpy_to_pcm_bytes(a_tail))
            if len(b_head) > 0:
                encoder_stdin.write(numpy_to_pcm_bytes(b_head))

        encoder_stdin.flush()
        return True

    except (BrokenPipeError, OSError):
        log("  Encoder pipe broken during crossfade")
        return False
    except Exception as e:
        log(f"  Crossfade error: {e}")
        return False


def pipe_track_body(
    track: TrackInfo,
    encoder_stdin: BinaryIO,
    skip_head: float = 0.0,
    skip_tail: float = 0.0,
) -> bool:
    """Pipe the body of a track (between crossfade regions) to the encoder.

    Used for piping the remainder of track B after the incoming crossfade
    and before the outgoing crossfade with the next track.

    Args:
        track: Track to pipe
        encoder_stdin: Encoder stdin to write to
        skip_head: Seconds to skip at the start (already played in crossfade)
        skip_tail: Seconds to reserve at end (for next crossfade)
    """
    start = (track.mix_in_pt or 0.0) + skip_head
    end = track.mix_out_pt or track.duration
    if end <= start:
        end = (track.duration or 300.0)

    body_duration = max(0, end - start - skip_tail)
    if body_duration < 1.0:
        return True

    log(f"  Piping body: {body_duration:.0f}s (from {start:.0f}s)")
    return _pipe_decoded_stream(track.path, encoder_stdin, start, body_duration)


def pipe_dj_segment(
    filepath: Path,
    encoder_stdin: BinaryIO,
) -> bool:
    """Pipe a DJ dialogue segment to the encoder.

    Uses speech-optimized loudness (-14 LUFS).
    """
    return _pipe_decoded_stream(filepath, encoder_stdin, 0, None, target_lufs=-14)


def _pipe_decoded_stream(
    filepath: Path,
    encoder_stdin: BinaryIO,
    start_time: float = 0,
    duration: float | None = None,
    target_lufs: float = -16,
) -> bool:
    """Stream-decode audio and pipe to encoder without loading into memory."""
    cmd = ["ffmpeg", "-v", "warning"]

    if start_time > 0:
        cmd.extend(["-ss", str(start_time)])

    cmd.extend(["-i", str(filepath)])

    if duration is not None:
        cmd.extend(["-t", str(duration)])

    cmd.extend([
        "-vn",
        "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11,aresample={SAMPLE_RATE}",
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "-ar", str(SAMPLE_RATE),
        "-ac", str(CHANNELS),
        "-",
    ])

    decoder = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    try:
        while True:
            chunk = decoder.stdout.read(CHUNK_SIZE)
            if not chunk:
                break
            encoder_stdin.write(chunk)
        encoder_stdin.flush()
        decoder.wait(timeout=5)
        return True
    except (BrokenPipeError, OSError):
        log("  Encoder pipe broken during stream")
        decoder.kill()
        return False
    except Exception as e:
        log(f"  Stream pipe error: {e}")
        decoder.kill()
        return False
