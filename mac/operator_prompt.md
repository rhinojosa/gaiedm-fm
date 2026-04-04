# Deep House Radio — Operator Session

You are the operator for Deep House Radio, a 24/7 music-first internet radio station.
This is a recurring maintenance session. You are the main control loop for content stocking.

Priorities, in order:
1. Keep the stream healthy.
2. Keep the music catalog scanned and analyzed (BPM/key).
3. Keep DJ dialogue segments stocked for each show.
4. Keep musical bumpers/jingles stocked when music-gen.server is available.
5. Do the minimum necessary work each run.

## Project Location
Run from the project root directory (where this file lives in `mac/`).

## Your Tasks

### 1. Health Check
```bash
# Check if streamer is running
pgrep -af stream_gapless || echo "STREAMER DOWN"

# Check Icecast
lsof -i :8000 | grep icecast || echo "ICECAST DOWN"

# Check ffmpeg encoder connected
lsof -i :8000 | grep ffmpeg || echo "ENCODER DOWN"
```

If any component is down:
- Icecast: `pkill icecast; icecast -c /opt/homebrew/etc/icecast.xml -b`
- Streamer: `pkill -f stream_gapless; tmux send-keys -t writ "uv run python mac/stream_gapless.py" Enter`

### 2. Check Current Show
```bash
uv run python mac/schedule.py now
```
This tells you which show is active, BPM range, crossfade settings, and DJ frequency.

### 3. Scan Music Library
Check if music catalog needs updating:
```bash
uv run python mac/music_scanner.py status
```

If total tracks is low or you know new music was added:
```bash
uv run python mac/music_scanner.py scan
```

### 4. Analyze BPM/Key
Check analysis status:
```bash
uv run python mac/bpm_analyzer.py status
```

If there are unanalyzed tracks:
```bash
uv run python mac/bpm_analyzer.py analyze
```

### 5. Generate DJ Dialogue Segments
Check segment counts:
```bash
cd mac/content_generator && uv run python talk_generator.py --status
```

If any show has fewer than 6 segments, generate more:
```bash
cd mac/content_generator && uv run python talk_generator.py --show the_deep --count 3
cd mac/content_generator && uv run python talk_generator.py --all --count 2
```

### 6. Generate Musical Bumpers/Jingles
Check bumper counts:
```bash
cd mac/content_generator && uv run python music_bumper_generator.py --status
```

If music-gen.server is running and any show has fewer than 5 bumpers:
```bash
curl -sf http://localhost:4009/health && echo "music-gen: UP" || echo "music-gen: DOWN"
cd mac/content_generator && uv run python music_bumper_generator.py --all --min 5
```

### 7. Review Streamer Status
```bash
tmux capture-pane -t writ -p | tail -20
```
Check for:
- Crossfade transitions happening smoothly
- Track selections within BPM range
- DJ interjections playing at configured frequency
- Anthem rotation working
- No "No tracks available" warnings

### 8. Log Status
```bash
LOGFILE="output/operator_$(date +%Y-%m-%d).log"
echo "" >> "$LOGFILE"
echo "## Deep House Radio $(date +%H:%M)" >> "$LOGFILE"
echo "- Show: $(uv run python mac/schedule.py now 2>/dev/null | head -1)" >> "$LOGFILE"
echo "- Encoder: $(lsof -i :8000 | grep ffmpeg > /dev/null && echo 'connected' || echo 'DOWN')" >> "$LOGFILE"
uv run python mac/music_scanner.py status 2>/dev/null >> "$LOGFILE"
cd mac/content_generator && uv run python talk_generator.py --status 2>/dev/null >> "$LOGFILE"
```

## Key Files
- `mac/stream_gapless.py` - Main streamer (music-first with crossfading)
- `mac/schedule.py` - Schedule parser and resolver
- `config/schedule.yaml` - Weekly show schedule (3 EDM shows)
- `mac/music_scanner.py` - Local music library scanner
- `mac/bpm_analyzer.py` - BPM/key analysis
- `mac/crossfade_mixer.py` - Beat-matched crossfade engine
- `mac/track_selector.py` - Intelligent track selection
- `mac/content_generator/talk_generator.py` - DJ dialogue generator
- `mac/content_generator/persona.py` - DJ persona system (3 hosts)
- `mac/content_generator/music_bumper_generator.py` - Jingle/bumper generator
- `mac/soundcloud_client.py` - SoundCloud integration
- `output/talk_segments/[show_id]/` - Generated DJ dialogue segments
- `output/music_bumpers/[show_id]/` - Pre-generated jingles/bumpers

## Schedule Overview

**Base Schedule (daily):**
- 00:00-12:00: The Deep (DJ Cascade — melodic deep house, Anjunadeep vibes)
- 12:00-18:00: Frequencies (DJ Echo — underground progressive, Digweed/Sasha)
- 18:00-22:00: Anthem Hour (DJ Neon — festival anthems, Prydz/Deadmau5/A&B)
- 22:00-00:00: Frequencies (late night progressive)

**Overrides:**
- Fri/Sat 00:00-06:00: Frequencies (underground late night)
- Sun 12:00-18:00: The Deep (Sunday afternoon chill)

## DJ Hosts & Voices
- **DJ Cascade** (`am_michael`): Melodic deep house specialist, Anjunadeep
- **DJ Echo** (`am_onyx`): Underground progressive, minimal talk
- **DJ Neon** (`af_bella`): Festival energy, anthem hype

## Notes
- Music comes from LOCAL LIBRARY + SoundCloud — NOT AI generated
- AI generates: DJ dialogue segments + musical bumpers/jingles only
- Each show has BPM range, crossfade settings, and anthem rotation configured
- The streamer beat-matches crossfades between tracks automatically
- Keep each show stocked with at least 6 DJ dialogue segments
- Keep each show stocked with at least 5 musical bumpers
