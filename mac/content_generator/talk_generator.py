#!/usr/bin/env python3
"""
Deep House Radio — DJ Dialogue Generator

Generates short DJ interjections for the music-first radio format.
Uses Claude CLI for scripts and Kokoro TTS for rendering.

Segment types (all short-form, 30-250 words):
    track_intro      - Track ID or "coming up next..." (15-30s)
    set_intro        - Show opening, vibe setting (30-60s)
    set_outro        - Show closing, what's next (20-40s)
    festival_update  - Festival/event news (45-90s)
    artist_spotlight  - Artist feature, label news (60-120s)
    anthem_announce  - Anthem introduction with context (15-30s)
    station_id       - Quick station identification (10-15s)

Usage:
    uv run python talk_generator.py                                # Current show
    uv run python talk_generator.py --show the_deep --count 5
    uv run python talk_generator.py --type festival_update
    uv run python talk_generator.py --all --count 3
    uv run python talk_generator.py --status
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from helpers import log, preprocess_for_tts, fetch_headlines, format_headlines, run_claude

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEDULE_PATH = PROJECT_ROOT / "config" / "schedule.yaml"
OUTPUT_DIR = PROJECT_ROOT / "output" / "talk_segments"
SCRIPTS_DIR = PROJECT_ROOT / "output" / "scripts"

sys.path.insert(0, str(PROJECT_ROOT / "mac"))
from schedule import load_schedule, StationSchedule

from persona import HOSTS, get_host, build_host_prompt, STATION_NAME

# =============================================================================
# SEGMENT TYPE DEFINITIONS
# =============================================================================

SEGMENT_WORD_TARGETS = {
    "track_intro": (30, 60),
    "set_intro": (60, 120),
    "set_outro": (50, 100),
    "festival_update": (100, 200),
    "artist_spotlight": (120, 250),
    "anthem_announce": (30, 60),
    "station_id": (15, 30),
}

SEGMENT_PROMPTS = {
    "track_intro": """Write a brief DJ interjection identifying a track or teasing what's coming.
Keep it natural — like a DJ talking between tracks in a club.
Reference the artist, track name, label, or a specific detail about the song.
Be specific: mention a remix, a release year, a set where this track was played.
30-60 words max. Output ONLY the spoken words.""",

    "set_intro": """Write a show opening for the DJ set.
Set the mood — what time is it, what's the energy, what should listeners expect.
Reference the genre/vibe naturally. Mention 1-2 artists or tracks coming up.
Keep it conversational, not scripted. Like opening a club night.
60-120 words. Output ONLY the spoken words.""",

    "set_outro": """Write a set closing.
Thank the listeners. Reference what was played. Tease what's coming next.
Keep the energy appropriate to the show vibe.
50-100 words. Output ONLY the spoken words.""",

    "festival_update": """Write a brief festival/events update for the electronic music world.
Reference real festivals, venues, scenes, and events:
- Festivals: Tomorrowland, Ultra Miami, ADE (Amsterdam Dance Event), Creamfields, EDC, Burning Man, Coachella, Printworks, Fabric
- Scenes: Ibiza, Berlin, Amsterdam, Miami, Tulum, London, Barcelona
- Events: label nights, B2B sets, album launches, tour announcements

Talk about what's happening in the scene: upcoming festivals, memorable sets from recent events,
new venue openings, artist tour announcements, label showcases.
Be enthusiastic but informed. Like an insider sharing news with fellow fans.
100-200 words. Output ONLY the spoken words.""",

    "artist_spotlight": """Write a brief artist or label spotlight.
Pick from these artists/labels and go deep on one:

ANJUNADEEP FAMILY: Lane 8, Ben Böhmer, Yotto, Tinlicker, Rufus Du Sol, Nora En Pure,
Eli & Fur, Marsh, Jody Wisternoff, Luttrell, CRi, During, Oliver Smith

PROGRESSIVE: John Digweed, Sasha, Deep Dish, Hernan Cattaneo, Guy J, Henry Saiz,
Nick Warren, Eelke Kleijn, Patrice Baumel, Jeremy Olander, Cid Inc

PEAK TIME: Eric Prydz/Pryda/Cirez D, Deadmau5, Above & Beyond, CamelPhat, Artbat,
Boris Brejcha, Maceo Plex, Stephan Bodzin, Tale Of Us, Anyma, Solomun

LABELS: Anjunadeep, Bedrock, Yoshitoshi, Last Night on Earth, Sudbeat, Diynamic,
Innervisions, mau5trap, Afterlife, Kompakt

Share a specific detail: a legendary set, a breakthrough track, their production style,
a collaboration, their journey. Make it personal, not Wikipedia.
120-250 words. Output ONLY the spoken words.""",

    "anthem_announce": """Write a brief anthem introduction.
You're about to play a track that everyone knows — a track that defines moments.
Build a tiny bit of anticipation. Reference where this track has been played,
what it means to the scene, why it's an anthem.
Be reverent but not cheesy. 30-60 words. Output ONLY the spoken words.""",

    "station_id": """Write a 15-30 word station ID for Deep House Radio.
Reference the music, the frequency, the vibe. Keep it smooth and brief.
Output ONLY the spoken text.""",
}

# =============================================================================
# TOPIC POOLS
# =============================================================================

TOPIC_POOLS = {
    "melodic_house": [
        "Anjunadeep and the rise of melodic house",
        "Lane 8's This Never Happened concept — no phones, pure music",
        "Ben Böhmer's live streams from hot air balloons and mountaintops",
        "Yotto's dark melodic style and his Odd One Out label",
        "Tinlicker and the art of the vocal progressive track",
        "Rufus Du Sol's journey from Sydney to the world",
        "The Anjunadeep Explorations compilations and why they matter",
        "Nora En Pure and the Purified concept",
        "Eli & Fur's evolution from DJs to producers",
        "Jody Wisternoff's 30 years in electronic music",
        "The art of the sunrise set — when deep house meets dawn",
        "Marsh and the new wave of Anjunadeep artists",
        "Why melodic house works on both headphones and dancefloors",
        "The label that changed everything: Anjunadeep's first 500 releases",
        "Terrace culture and the architecture of the outdoor set",
    ],
    "progressive_house": [
        "Sasha & Digweed's Northern Exposure — the mix that defined a generation",
        "Bedrock Records and the sound of progressive house",
        "Deep Dish and the Washington DC progressive sound",
        "Hernan Cattaneo's Resident podcast — 20 years of progressive",
        "Guy J and the art of the 8-hour set",
        "The Global Underground series — a city, a DJ, a moment in time",
        "Yoshitoshi Records and the Deep Dish legacy",
        "Last Night on Earth — Sasha's label and vision",
        "The Sudbeat sound and the Buenos Aires scene",
        "Nick Warren's Way Out West and the Bristol connection",
        "The art of the marathon DJ set — endurance as art form",
        "Henry Saiz and the Spanish progressive scene",
        "Eelke Kleijn's cinematic approach to progressive house",
        "The progressive house revival — what changed and what stayed the same",
        "John Digweed's Transitions — the longest running DJ show",
    ],
    "anthems": [
        "Eric Prydz's Opus — the 9 minutes that changed festival closings forever",
        "Deadmau5's Strobe — why a 10-minute track became the definitive anthem",
        "Above & Beyond's Sun & Moon — the track that makes festivals cry",
        "The history of the festival anthem — from Tiesto to Prydz",
        "CamelPhat's Cola — from underground to main stage",
        "Stephan Bodzin's Powers of Ten — techno meets transcendence",
        "The art of the closing track — how DJs choose the last song",
        "Sasha's Xpander — the track that defined late 90s progressive",
        "Deep Dish's Flashdance — the anthem that built a legacy",
        "Artbat's rise from Ukraine to headlining Afterlife",
        "Boris Brejcha and the invention of high-tech minimal",
        "Tale Of Us and the Afterlife movement",
        "Anyma and the future of audio-visual electronic music",
        "Pryda vs Cirez D — Eric Prydz's two musical personalities",
        "Tomorrowland's greatest ever closing sets",
    ],
    "festivals": [
        "Tomorrowland 2026 — what to expect this year",
        "Ultra Miami and the birth of the modern festival",
        "Amsterdam Dance Event — the industry's annual gathering",
        "Burning Man and electronic music in the desert",
        "Creamfields and the UK festival tradition",
        "EDC Las Vegas — the scale of the spectacle",
        "Printworks London — the warehouse that became a cathedral",
        "Fabric London — 25 years of the world's most important club",
        "DC-10 Ibiza — Circoloco and the real Ibiza",
        "Hï Ibiza and the modern superclub",
        "Berlin's club scene — Berghain and beyond",
        "The Tulum scene — paradise or parody?",
        "Awakenings and the Amsterdam techno tradition",
        "Sonar Barcelona — where music meets technology",
        "The return of the warehouse party — underground in 2026",
    ],
}


# =============================================================================
# CORE GENERATION
# =============================================================================

def select_topic(topic_focus: str, segment_type: str) -> str:
    """Pick a topic from the pool matching the show's focus."""
    pool = TOPIC_POOLS.get(topic_focus, [])
    if not pool:
        all_topics = []
        for topics in TOPIC_POOLS.values():
            all_topics.extend(topics)
        pool = all_topics
    return random.choice(pool)


def build_generation_prompt(
    host_id: str,
    segment_type: str,
    topic: str,
    show_name: str,
    show_description: str,
    topic_focus: str,
    guest_voice: str | None = None,
) -> str:
    """Build the full prompt for DJ dialogue generation."""
    show_context = {
        "show_name": show_name,
        "show_description": show_description,
        "topic_focus": topic_focus,
        "segment_type": segment_type,
    }
    base = build_host_prompt(host_id, show_context)

    min_words, max_words = SEGMENT_WORD_TARGETS.get(segment_type, (60, 120))

    prompt_template = SEGMENT_PROMPTS.get(segment_type, SEGMENT_PROMPTS["track_intro"])

    # Handle news-based segments
    if segment_type == "festival_update":
        headlines = fetch_headlines()
        if headlines:
            headline_text = format_headlines(headlines)
            prompt_template += f"\n\nRecent headlines for context:\n{headline_text}"

    prompt = f"""{base}

SEGMENT: {segment_type}
TOPIC: {topic}
TARGET LENGTH: {min_words}-{max_words} words

IMPORTANT: Keep it SHORT. You are a DJ talking between tracks, not giving a lecture.
Be specific — name artists, tracks, venues, festivals, labels. No generic filler.

{prompt_template}"""

    return prompt


def run_generation(prompt: str, segment_type: str) -> str | None:
    """Run Claude CLI to generate the script."""
    min_words, max_words = SEGMENT_WORD_TARGETS.get(segment_type, (60, 120))
    timeout = 60  # Short segments need less time

    script = run_claude(prompt, timeout=timeout)
    if not script:
        return None

    # Quality gate: check word count (more lenient for short segments)
    word_count = len(script.split())
    min_acceptable = max(10, int(min_words * 0.6))
    if word_count < min_acceptable:
        log(f"Script too short: {word_count} words (need {min_acceptable}+)")
        return None

    # Truncate if way too long
    if word_count > max_words * 2:
        words = script.split()[:max_words]
        script = ' '.join(words)
        # Try to end at a sentence
        for end in ['. ', '! ', '? ']:
            last_idx = script.rfind(end)
            if last_idx > len(script) * 0.5:
                script = script[:last_idx + 1]
                break

    return script


# =============================================================================
# TTS RENDERING
# =============================================================================

def render_kokoro(text: str, output_path: Path, voice: str = "am_michael") -> bool:
    """Render text to speech using Kokoro TTS."""
    kokoro_dir = PROJECT_ROOT / "mac" / "kokoro"
    venv_python = kokoro_dir / ".venv" / "bin" / "python"

    if not venv_python.exists():
        log("Kokoro venv not found")
        return False

    # Use json.dumps for safe string escaping (handles \, ", {, }, newlines, etc.)
    safe_text = json.dumps(text.replace('\n', ' '))
    safe_voice = json.dumps(voice)
    safe_output = json.dumps(str(output_path))

    tts_script = f'''
import warnings
warnings.filterwarnings("ignore")
import json

from kokoro import KPipeline
import soundfile as sf
import numpy as np

pipe = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")

text = {safe_text}
voice = {safe_voice}

generator = pipe(text, voice=voice, speed=1.0)
audio_segments = []
for _, _, audio in generator:
    audio_segments.append(audio)

if len(audio_segments) == 1:
    full_audio = audio_segments[0]
else:
    full_audio = np.concatenate(audio_segments)

sf.write({safe_output}, full_audio, 24000)
print("SUCCESS")
'''

    try:
        result = subprocess.run(
            [str(venv_python), "-c", tts_script],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(kokoro_dir),
        )
        return "SUCCESS" in result.stdout
    except Exception as e:
        log(f"Kokoro error: {e}")
        return False


def render_single_voice(script: str, output_path: Path, voice: str) -> bool:
    """Render a single-voice script to audio, chunking for long content."""
    import re
    MAX_CHUNK_WORDS = 100
    words = script.split()

    if len(words) <= MAX_CHUNK_WORDS:
        return render_kokoro(script, output_path, voice)

    sentences = re.split(r'(?<=[.!?])\s+', script)
    chunks = []
    current_chunk = []
    current_words = 0

    for sentence in sentences:
        sentence_words = len(sentence.split())
        if current_words + sentence_words > MAX_CHUNK_WORDS and current_chunk:
            chunks.append(' '.join(current_chunk))
            current_chunk = [sentence]
            current_words = sentence_words
        else:
            current_chunk.append(sentence)
            current_words += sentence_words

    if current_chunk:
        chunks.append(' '.join(current_chunk))

    log(f"  Rendering {len(chunks)} chunks with voice {voice}...")

    chunk_files = []
    for i, chunk in enumerate(chunks):
        chunk_path = output_path.with_stem(f"{output_path.stem}_chunk{i:03d}")
        for attempt in range(2):
            if render_kokoro(chunk, chunk_path, voice):
                chunk_files.append(chunk_path)
                break
            time.sleep(2)

    if not chunk_files:
        log("  No chunks rendered")
        return False

    return _concatenate_audio(chunk_files, output_path)


def _concatenate_audio(chunk_files: list[Path], output_path: Path) -> bool:
    """Concatenate chunk files using ffmpeg."""
    if len(chunk_files) == 1:
        chunk_files[0].rename(output_path)
        return True

    list_file = output_path.with_suffix(".txt")
    list_file.write_text("\n".join(f"file '{f}'" for f in chunk_files))

    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "warning", "-y", "-f", "concat", "-safe", "0",
             "-i", str(list_file), "-c", "copy", str(output_path)],
            capture_output=True, timeout=30,
        )
        success = result.returncode == 0 and output_path.exists()
    except Exception:
        success = False

    # Cleanup
    list_file.unlink(missing_ok=True)
    for f in chunk_files:
        f.unlink(missing_ok=True)

    return success


# =============================================================================
# MAIN GENERATION PIPELINE
# =============================================================================


def generate_segment(
    show_id: str,
    show_name: str,
    show_description: str,
    host_id: str,
    topic_focus: str,
    segment_type: str,
    voice: str,
) -> bool:
    """Generate a single DJ dialogue segment."""
    topic = select_topic(topic_focus, segment_type)
    log(f"  Generating {segment_type}: {topic[:60]}...")

    prompt = build_generation_prompt(
        host_id, segment_type, topic, show_name, show_description, topic_focus,
    )

    script = run_generation(prompt, segment_type)
    if not script:
        log(f"  Generation failed for {segment_type}")
        return False

    word_count = len(script.split())
    log(f"  Script: {word_count} words")

    # Preprocess for TTS
    tts_text = preprocess_for_tts(script)

    # Generate output paths
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = OUTPUT_DIR / show_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{segment_type}_{timestamp}.wav"

    # Save script metadata
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    script_meta = {
        "show_id": show_id,
        "segment_type": segment_type,
        "topic": topic,
        "word_count": word_count,
        "voice": voice,
        "generated_at": datetime.now().isoformat(),
        "script": script,
    }
    script_path = SCRIPTS_DIR / f"dj_{segment_type}_{timestamp}.json"
    script_path.write_text(json.dumps(script_meta, indent=2))

    # Render TTS
    log(f"  Rendering with voice {voice}...")
    if render_single_voice(tts_text, output_path, voice):
        log(f"  Saved: {output_path.name}")
        return True
    else:
        log(f"  TTS rendering failed")
        return False


def generate_for_show(show_id: str, count: int = 3, segment_type_filter: str | None = None) -> int:
    """Generate DJ segments for a specific show.

    Args:
        show_id: Show identifier
        count: Number of segments to generate
        segment_type_filter: If set, only generate this segment type
    """
    schedule = load_schedule(SCHEDULE_PATH)
    show = schedule.shows.get(show_id)
    if not show:
        log(f"Unknown show: {show_id}")
        return 0

    voice = show.voices.get("host", "am_michael")
    generated = 0

    for i in range(count):
        if segment_type_filter:
            segment_type = segment_type_filter
        else:
            segment_type = random.choice(show.segment_types)
        if generate_segment(
            show.show_id, show.name, show.description,
            show.host, show.topic_focus, segment_type, voice,
        ):
            generated += 1

    return generated


def get_status() -> dict[str, int]:
    """Get count of DJ segments per show."""
    status = {}
    if OUTPUT_DIR.exists():
        for show_dir in OUTPUT_DIR.iterdir():
            if show_dir.is_dir():
                count = len(list(show_dir.glob("*.wav")))
                status[show_dir.name] = count
    return status


# =============================================================================
# CLI
# =============================================================================

def _cli() -> int:
    parser = argparse.ArgumentParser(description="Deep House Radio DJ dialogue generator")
    parser.add_argument("--show", type=str, help="Generate for specific show")
    parser.add_argument("--count", type=int, default=3, help="Segments per show")
    parser.add_argument("--type", type=str, help="Specific segment type")
    parser.add_argument("--all", action="store_true", help="Generate for all shows")
    parser.add_argument("--status", action="store_true", help="Show segment counts")

    args = parser.parse_args()

    if args.status:
        status = get_status()
        print("DJ Segment Status:")
        for show, count in sorted(status.items()):
            print(f"  {show:20s} {count} segments")
        if not status:
            print("  (no segments)")
        return 0

    seg_type = getattr(args, 'type', None)

    if args.all:
        schedule = load_schedule(SCHEDULE_PATH)
        total = 0
        for show_id in schedule.shows:
            log(f"=== {show_id} ===")
            total += generate_for_show(show_id, args.count, segment_type_filter=seg_type)
        log(f"Total generated: {total}")
        return 0

    if args.show:
        generated = generate_for_show(args.show, args.count, segment_type_filter=seg_type)
        log(f"Generated {generated} segments for {args.show}")
        return 0

    # Default: generate for current show
    schedule = load_schedule(SCHEDULE_PATH)
    resolved = schedule.resolve()
    log(f"Current show: {resolved.name} ({resolved.show_id})")
    generated = generate_for_show(resolved.show_id, args.count, segment_type_filter=seg_type)
    log(f"Generated {generated} segments")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
