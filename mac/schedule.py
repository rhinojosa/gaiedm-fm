#!/usr/bin/env python3
"""
Deep House Radio — Weekly Scheduling

Loads `config/schedule.yaml` and resolves the currently-active show based on:
- day of week (mon..sun)
- local time (HH:MM)

The streamer uses this to:
- pick the DJ host persona
- determine BPM range, genres, and crossfade settings
- control anthem rotation and DJ interjection frequency
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
from typing import Any

import yaml


DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_TO_INDEX = {k: i for i, k in enumerate(DAY_KEYS)}
INDEX_TO_DAY = {i: k for k, i in DAY_TO_INDEX.items()}

VALID_SEGMENT_TYPES = {
    # EDM DJ segment types
    "track_intro", "set_intro", "set_outro", "artist_spotlight",
    "festival_update", "anthem_announce", "station_id",
    # Legacy talk types (kept for backward compat)
    "deep_dive", "news_analysis", "interview", "panel", "story",
    "listener_mailbag", "listener_response", "music_essay",
    "show_intro", "show_outro",
}


class ScheduleError(RuntimeError):
    pass


def _parse_time_hhmm(value: str) -> int:
    if not isinstance(value, str):
        raise ScheduleError(f"Invalid time (expected HH:MM string): {value!r}")
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not m:
        raise ScheduleError(f"Invalid time (expected HH:MM): {value!r}")
    hour = int(m.group(1))
    minute = int(m.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ScheduleError(f"Invalid time (out of range): {value!r}")
    return hour * 60 + minute


def _normalize_day_token(token: str) -> str:
    t = token.strip().lower()
    aliases = {
        "monday": "mon",
        "tuesday": "tue",
        "wednesday": "wed",
        "thursday": "thu",
        "friday": "fri",
        "saturday": "sat",
        "sunday": "sun",
    }
    return aliases.get(t, t)


def _parse_days(value: Any) -> set[int]:
    if value is None:
        raise ScheduleError("Missing required field: days")
    if not isinstance(value, list) or not value:
        raise ScheduleError(f"Invalid days (expected non-empty list): {value!r}")

    expanded: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            raise ScheduleError(f"Invalid day token: {raw!r}")
        tok = _normalize_day_token(raw)
        if tok in ("daily", "all"):
            expanded.extend(list(DAY_KEYS))
            continue
        if tok == "weekday":
            expanded.extend(["mon", "tue", "wed", "thu", "fri"])
            continue
        if tok == "weekend":
            expanded.extend(["sat", "sun"])
            continue
        expanded.append(tok)

    days: set[int] = set()
    for tok in expanded:
        if tok not in DAY_TO_INDEX:
            raise ScheduleError(f"Invalid day token: {tok!r}")
        days.add(DAY_TO_INDEX[tok])
    return days


def _expand_minutes(start_minute: int, end_minute: int) -> list[tuple[int, int]]:
    if start_minute == end_minute:
        raise ScheduleError("Schedule block start and end cannot be the same")
    if 0 <= start_minute < 1440 and 0 <= end_minute < 1440:
        if end_minute > start_minute:
            return [(start_minute, end_minute)]
        # Cross-midnight: split into two ranges
        return [(start_minute, 1440), (0, end_minute)]
    raise ScheduleError("Schedule block times out of range")


@dataclass(frozen=True)
class Show:
    show_id: str
    name: str
    description: str
    host: str = "dj_cascade"
    topic_focus: str = ""
    segment_types: list[str] = field(default_factory=lambda: ["track_intro"])
    bumper_style: str = "deep_house"
    voices: dict[str, str] = field(default_factory=dict)
    # EDM-specific fields
    bpm_range: tuple[int, int] = (118, 128)
    music_genres: list[str] = field(default_factory=lambda: ["deep house"])
    crossfade_beats: int = 32
    dj_frequency: int = 4           # DJ speaks every N tracks
    anthem_enabled: bool = True
    anthem_frequency: int = 4       # play anthem every N tracks


@dataclass(frozen=True)
class ScheduleBlock:
    start_minute: int
    end_minute: int
    show_id: str
    days: set[int] | None = None  # None => every day (base)

    def is_cross_midnight(self) -> bool:
        return self.end_minute < self.start_minute

    def matches(self, now: datetime) -> bool:
        minute = now.hour * 60 + now.minute
        day = now.weekday()  # mon=0
        prev_day = (day - 1) % 7

        if self.days is None:
            # Base clock: day-agnostic
            if self.end_minute > self.start_minute:
                return self.start_minute <= minute < self.end_minute
            return minute >= self.start_minute or minute < self.end_minute

        # Overrides: day-aware, including cross-midnight behavior.
        if self.end_minute > self.start_minute:
            return day in self.days and self.start_minute <= minute < self.end_minute

        # Cross-midnight: belongs to the start day; continues into next day.
        return (day in self.days and minute >= self.start_minute) or (
            prev_day in self.days and minute < self.end_minute
        )


@dataclass(frozen=True)
class ResolvedShow:
    show_id: str
    name: str
    description: str
    host: str
    topic_focus: str
    segment_types: list[str]
    bumper_style: str
    voices: dict[str, str]
    # EDM-specific
    bpm_range: tuple[int, int] = (118, 128)
    music_genres: list[str] = field(default_factory=lambda: ["deep house"])
    crossfade_beats: int = 32
    dj_frequency: int = 4
    anthem_enabled: bool = True
    anthem_frequency: int = 4


@dataclass
class StationSchedule:
    shows: dict[str, Show]
    base: list[ScheduleBlock]
    overrides: list[ScheduleBlock]

    def validate(self) -> None:
        if not self.base:
            raise ScheduleError("schedule.base is empty")

        # Base coverage: every minute must be covered exactly once.
        coverage = [0] * 1440
        for block in self.base:
            for a, b in _expand_minutes(block.start_minute, block.end_minute):
                for m in range(a, b):
                    coverage[m] += 1

        uncovered = [i for i, c in enumerate(coverage) if c == 0]
        if uncovered:
            first = uncovered[0]
            raise ScheduleError(
                f"schedule.base does not cover the full day (first gap at {first // 60:02d}:{first % 60:02d})"
            )

        overlapped = [i for i, c in enumerate(coverage) if c > 1]
        if overlapped:
            first = overlapped[0]
            raise ScheduleError(
                f"schedule.base overlaps itself (first overlap at {first // 60:02d}:{first % 60:02d})"
            )

        # Show references exist
        for block in self.base + self.overrides:
            if block.show_id not in self.shows:
                raise ScheduleError(f"Schedule references unknown show: {block.show_id!r}")

        # Validate show configs
        for show in self.shows.values():
            if show.host:
                # Validate host exists in persona system (soft check - just verify non-empty)
                pass
            for st in show.segment_types:
                if st not in VALID_SEGMENT_TYPES:
                    raise ScheduleError(
                        f"Show {show.show_id}: unknown segment type {st!r}. "
                        f"Valid: {sorted(VALID_SEGMENT_TYPES)}"
                    )

    def resolve(self, now: datetime | None = None) -> ResolvedShow:
        now = now or datetime.now()

        def _resolve_show(show: Show) -> ResolvedShow:
            return ResolvedShow(
                show_id=show.show_id,
                name=show.name,
                description=show.description,
                host=show.host,
                topic_focus=show.topic_focus,
                segment_types=list(show.segment_types),
                bumper_style=show.bumper_style,
                voices=dict(show.voices),
                bpm_range=show.bpm_range,
                music_genres=list(show.music_genres),
                crossfade_beats=show.crossfade_beats,
                dj_frequency=show.dj_frequency,
                anthem_enabled=show.anthem_enabled,
                anthem_frequency=show.anthem_frequency,
            )

        for block in self.overrides:
            if block.matches(now):
                return _resolve_show(self.shows[block.show_id])

        for block in self.base:
            if block.matches(now):
                return _resolve_show(self.shows[block.show_id])

        raise ScheduleError("No matching schedule block for current time (base clock may be invalid)")


def load_schedule(path: Path) -> StationSchedule:
    try:
        payload = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        raise
    except Exception as exc:
        raise ScheduleError(f"Failed to read schedule YAML: {exc}") from exc

    if not isinstance(payload, dict):
        raise ScheduleError("Schedule YAML must be a mapping at the top level")

    shows_raw = payload.get("shows")
    if not isinstance(shows_raw, dict) or not shows_raw:
        raise ScheduleError("Missing or invalid `shows` section")

    shows: dict[str, Show] = {}
    for show_id, cfg in shows_raw.items():
        if not isinstance(show_id, str) or not show_id.strip():
            raise ScheduleError(f"Invalid show id: {show_id!r}")
        if not isinstance(cfg, dict):
            raise ScheduleError(f"Show {show_id}: config must be a mapping")
        name = str(cfg.get("name", "")).strip()
        description = str(cfg.get("description", "")).strip()
        if not name or not description:
            raise ScheduleError(f"Show {show_id}: missing name/description")

        # Talk-show fields
        host = str(cfg.get("host", "liminal_operator")).strip()
        topic_focus = str(cfg.get("topic_focus", "")).strip()
        segment_types_raw = cfg.get("segment_types", ["track_intro"])
        if not isinstance(segment_types_raw, list):
            raise ScheduleError(f"Show {show_id}: segment_types must be a list")
        segment_types = [str(s).strip() for s in segment_types_raw]
        bumper_style = str(cfg.get("bumper_style", "ambient")).strip()

        # Voice config
        voices = cfg.get("voices") if isinstance(cfg.get("voices"), dict) else {}

        # EDM-specific fields
        bpm_raw = cfg.get("bpm_range", [118, 128])
        if isinstance(bpm_raw, list) and len(bpm_raw) == 2:
            bpm_range = (int(bpm_raw[0]), int(bpm_raw[1]))
        else:
            bpm_range = (118, 128)

        music_genres_raw = cfg.get("music_genres", ["deep house"])
        if isinstance(music_genres_raw, list):
            music_genres = [str(g).strip() for g in music_genres_raw]
        else:
            music_genres = ["deep house"]

        crossfade_beats = int(cfg.get("crossfade_beats", 32))
        dj_frequency = int(cfg.get("dj_frequency", 4))
        anthem_enabled = bool(cfg.get("anthem_enabled", True))
        anthem_frequency = int(cfg.get("anthem_frequency", 4))

        shows[show_id] = Show(
            show_id=show_id,
            name=name,
            description=description,
            host=host,
            topic_focus=topic_focus,
            segment_types=segment_types,
            bumper_style=bumper_style,
            voices={str(k): str(v) for k, v in voices.items()},
            bpm_range=bpm_range,
            music_genres=music_genres,
            crossfade_beats=crossfade_beats,
            dj_frequency=dj_frequency,
            anthem_enabled=anthem_enabled,
            anthem_frequency=anthem_frequency,
        )

    sched = payload.get("schedule")
    if not isinstance(sched, dict):
        raise ScheduleError("Missing or invalid `schedule` section")

    base_raw = sched.get("base")
    if not isinstance(base_raw, list) or not base_raw:
        raise ScheduleError("schedule.base must be a non-empty list")

    overrides_raw = sched.get("overrides", [])
    if overrides_raw is None:
        overrides_raw = []
    if not isinstance(overrides_raw, list):
        raise ScheduleError("schedule.overrides must be a list")

    def _parse_block(cfg: Any, *, day_aware: bool) -> ScheduleBlock:
        if not isinstance(cfg, dict):
            raise ScheduleError(f"Schedule block must be a mapping: {cfg!r}")
        start = _parse_time_hhmm(str(cfg.get("start", "")))
        end = _parse_time_hhmm(str(cfg.get("end", "")))
        show_id = str(cfg.get("show", "")).strip()
        if not show_id:
            raise ScheduleError("Schedule block missing `show`")
        days = _parse_days(cfg.get("days")) if day_aware else None
        return ScheduleBlock(start_minute=start, end_minute=end, show_id=show_id, days=days)

    base_blocks = [_parse_block(item, day_aware=False) for item in base_raw]
    override_blocks = [_parse_block(item, day_aware=True) for item in overrides_raw]

    schedule = StationSchedule(
        shows=shows,
        base=base_blocks,
        overrides=override_blocks,
    )
    schedule.validate()
    return schedule


def _cli() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Deep House Radio schedule tools")
    parser.add_argument(
        "--schedule",
        default=str(Path(__file__).resolve().parents[1] / "config" / "schedule.yaml"),
        help="Path to schedule.yaml",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("validate", help="Validate schedule file")

    p_now = sub.add_parser("now", help="Print current show")
    p_now.add_argument("--at", help="Override time (YYYY-MM-DD HH:MM)")

    sub.add_parser("shows", help="List all shows")

    args = parser.parse_args()

    schedule = load_schedule(Path(args.schedule).expanduser())
    if args.cmd == "validate":
        print("OK")
        return 0

    if args.cmd == "shows":
        for sid, show in schedule.shows.items():
            print(f"  {sid:25s} host={show.host:20s} {show.name}")
        return 0

    when = datetime.now()
    if args.cmd == "now" and args.at:
        try:
            when = datetime.strptime(args.at, "%Y-%m-%d %H:%M")
        except Exception as exc:
            raise ScheduleError(f"Invalid --at format: {exc}") from exc

    resolved = schedule.resolve(when)
    print(f"{INDEX_TO_DAY[when.weekday()]} {when:%H:%M} -- {resolved.name} ({resolved.show_id})")
    print(f"  Host: {resolved.host}")
    print(f"  Focus: {resolved.topic_focus}")
    print(f"  BPM: {resolved.bpm_range[0]}-{resolved.bpm_range[1]}")
    print(f"  Genres: {', '.join(resolved.music_genres)}")
    print(f"  Crossfade: {resolved.crossfade_beats} beats")
    print(f"  DJ every: {resolved.dj_frequency} tracks")
    print(f"  Anthems: {'every ' + str(resolved.anthem_frequency) + ' tracks' if resolved.anthem_enabled else 'off'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
