#!/usr/bin/env python3
"""Generate short musical bumpers/jingles for Deep House Radio using music-gen.server.

Bumpers are SHORT (5-30 second) musical elements used for:
- Station jingles and IDs
- Transition sweeps and risers
- Show intro/outro beds (played under DJ voice)

Full music tracks come from the local music library, NOT from AI generation.

Usage:
    uv run python music_bumper_generator.py --status
    uv run python music_bumper_generator.py --show the_deep --count 3
    uv run python music_bumper_generator.py --all --min 5
"""

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "mac"))

from music_gen_client import MUSIC_GEN_BASE_URL, generate_music, is_server_available

BUMPERS_DIR = PROJECT_ROOT / "output" / "music_bumpers"

# Import expanded pools
from music_pools_expanded import SHOW_BUMPERS


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[bumper-gen {ts}] {msg}", flush=True)


def generate_bumper(show_id: str, caption: str, bumper_dir: Path) -> bool:
    """Generate a single bumper/jingle."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rand_suffix = random.randint(1000, 9999)
    filename = f"bumper_{timestamp}_{rand_suffix}"
    output_path = bumper_dir / f"{filename}.flac"

    # Short duration: 5-30 seconds depending on caption hints
    if "jingle" in caption.lower() or "ident" in caption.lower() or "id" in caption.lower():
        duration = random.uniform(5, 10)
    elif "transition" in caption.lower() or "sweep" in caption.lower() or "riser" in caption.lower():
        duration = random.uniform(3, 8)
    else:
        # Bed / intro / outro
        duration = random.uniform(15, 30)

    log(f"  Generating: {caption[:60]}... ({duration:.0f}s)")

    success = generate_music(
        caption=caption,
        output_path=output_path,
        duration=duration,
        audio_format="flac",
        instrumental=True,
        lyrics="[Instrumental]",
    )

    if success:
        # Save metadata
        meta = {
            "caption": caption,
            "display_name": f"Deep House Radio {'Jingle' if duration < 12 else 'Bed'}",
            "duration": duration,
            "show_id": show_id,
            "generated_at": datetime.now().isoformat(),
        }
        meta_path = output_path.with_suffix(".json")
        meta_path.write_text(json.dumps(meta, indent=2))
        log(f"  Saved: {output_path.name}")
    else:
        log(f"  FAILED")

    return success


def get_bumper_count(show_id: str) -> int:
    """Count existing bumpers for a show."""
    show_dir = BUMPERS_DIR / show_id
    if not show_dir.exists():
        return 0
    audio_exts = {".flac", ".mp3", ".wav"}
    return sum(1 for f in show_dir.iterdir() if f.suffix.lower() in audio_exts)


def generate_for_show(show_id: str, count: int) -> int:
    """Generate bumpers for a specific show."""
    captions = SHOW_BUMPERS.get(show_id, [])
    if not captions:
        log(f"No caption pool for show: {show_id}")
        return 0

    bumper_dir = BUMPERS_DIR / show_id
    bumper_dir.mkdir(parents=True, exist_ok=True)

    generated = 0
    for i in range(count):
        caption = random.choice(captions)
        if generate_bumper(show_id, caption, bumper_dir):
            generated += 1
        time.sleep(1)  # Brief pause between generations

    return generated


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Deep House Radio bumper/jingle generator")
    parser.add_argument("--show", type=str, help="Generate for specific show")
    parser.add_argument("--count", type=int, default=3, help="Bumpers to generate")
    parser.add_argument("--all", action="store_true", help="Generate for all shows")
    parser.add_argument("--min", type=int, default=5, help="Minimum bumpers per show (with --all)")
    parser.add_argument("--status", action="store_true", help="Show bumper counts")

    args = parser.parse_args()

    if args.status:
        print("Bumper Status:")
        for show_id in sorted(SHOW_BUMPERS.keys()):
            count = get_bumper_count(show_id)
            print(f"  {show_id:20s} {count} bumpers")
        return 0

    # Check server
    if not is_server_available():
        log(f"music-gen.server not available at {MUSIC_GEN_BASE_URL}")
        log("Start it with: bash mac/start_music_gen.sh server")
        return 1

    if args.all:
        total = 0
        for show_id in SHOW_BUMPERS:
            current = get_bumper_count(show_id)
            needed = max(0, args.min - current)
            if needed == 0:
                log(f"{show_id}: {current} bumpers (ok)")
                continue
            log(f"{show_id}: {current} bumpers, generating {needed}...")
            total += generate_for_show(show_id, needed)
        log(f"Total generated: {total}")
        return 0

    if args.show:
        generated = generate_for_show(args.show, args.count)
        log(f"Generated {generated} bumpers for {args.show}")
        return 0

    log("Use --show, --all, or --status")
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
