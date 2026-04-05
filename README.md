# Deep House Radio

A 24/7 AI-powered deep house and progressive house radio station with DJ-style beat-matched mixing, and an LLM-powered knowledge base that captures and compiles electronic music content from across the web.

## What is this?

Deep House Radio is two things:

**1. A music-first internet radio station** where:
- Tracks are beat-matched and crossfaded like a real DJ set (BPM detection, harmonic mixing, equal-power crossfades)
- Three shows cover three vibes: deep melodic, underground progressive, and festival anthems
- AI DJs speak between tracks with distinct personalities — warm and knowing, sparse and minimal, or controlled energy
- Anthems rotate every N tracks with dedicated announcements
- Everything streams 24/7 to Icecast at 192kbps

**2. A knowledge base** that captures content from any URL — Instagram, X, Reddit, LinkedIn, YouTube, SoundCloud, articles — and compiles it into a structured wiki using an LLM. Think of it as a living electronic music encyclopedia that grows every time you share a link.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  STREAMING ENGINE                                                │
│                                                                  │
│  stream_gapless.py                                               │
│    ├── track_selector.py    BPM-aware selection, Camelot key     │
│    ├── crossfade_mixer.py   Beat-matched crossfade transitions   │
│    ├── bpm_analyzer.py      librosa BPM/key/energy detection     │
│    ├── music_scanner.py     Local library → SQLite catalog       │
│    └── Pipes PCM → ffmpeg → Icecast :8000                        │
│                                                                  │
│  content_generator/                                              │
│    ├── talk_generator.py    Claude CLI → DJ dialogue scripts     │
│    ├── persona.py           3 DJ hosts, station identity         │
│    └── music_bumper_generator.py   Short AI bumpers/jingles      │
├──────────────────────────────────────────────────────────────────┤
│  KNOWLEDGE BASE                                                  │
│                                                                  │
│  knowledge/                                                      │
│    ├── capture.py     URL → extract → markdown (10+ platforms)   │
│    ├── store.py       SQLite catalog + file storage              │
│    ├── compiler.py    LLM compiles raw → wiki articles           │
│    ├── health.py      Wiki linting, broken links, gap detection  │
│    └── api.py         HTTP API on :8002                          │
├──────────────────────────────────────────────────────────────────┤
│  APIs                                                            │
│    ├── api_server.py :8001     Now playing, schedule, history    │
│    └── knowledge/api.py :8002  Capture, wiki, Q&A, health       │
└──────────────────────────────────────────────────────────────────┘
```

## Shows

| Show | Hours | BPM | Vibe | DJ |
|------|-------|-----|------|----|
| **The Deep** | 00:00–12:00 | 118–124 | Anjunadeep, melodic house — Lane 8, Ben Böhmer, Yotto, Tinlicker | DJ Cascade |
| **Frequencies** | 12:00–18:00, 22:00–00:00 | 122–128 | Underground progressive — Digweed, Sasha, Deep Dish, Hernan Cattaneo | DJ Echo |
| **Anthem Hour** | 18:00–22:00 | 124–130 | Festival anthems — Prydz, Deadmau5, Above & Beyond, CamelPhat | DJ Neon |

Weekend overrides: Friday/Saturday late night = Frequencies, Sunday afternoon = The Deep.

## Quick Start

### 1. Install

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
brew install icecast ffmpeg    # macOS
uv sync
```

### 2. Set up TTS

**Kokoro (recommended — fast, no GPU):**
```bash
cd mac/kokoro && uv venv && uv pip install kokoro soundfile
```

**Chatterbox (voice cloning, requires GPU/MPS):**
```bash
cd mac/chatterbox && uv venv --python 3.11
uv pip install chatterbox-tts torch torchaudio
```

### 3. Configure

```bash
cp config/icecast.xml.example config/icecast.xml
cp mac/config.yaml.example mac/config.yaml
# Edit mac/config.yaml — add music directories, set Icecast password
```

### 4. Add music & analyze

```bash
# Scan your music library
uv run python mac/music_scanner.py scan /path/to/music

# Analyze BPM, key, and energy for all tracks
uv run python mac/bpm_analyzer.py analyze-catalog
```

### 5. Stream

```bash
brew services start icecast
cd mac && uv run python stream_gapless.py
```

### 6. Generate DJ content

```bash
# Generate dialogue for all 3 shows (5 segments each)
cd mac/content_generator
uv run python talk_generator.py --all --count 5

# Generate for a specific show and segment type
uv run python talk_generator.py --show anthem_hour --type anthem_announce --count 10
```

## Knowledge Base

The knowledge base lets you capture content from any URL and build a structured wiki about electronic music — artists, labels, festivals, scenes, concepts.

### Capture a URL

```bash
# From CLI
uv run python mac/knowledge/capture.py https://ra.co/features/1234

# Via API
curl -X POST http://localhost:8002/kb/capture \
  -H "Content-Type: application/json" \
  -d '{"url": "https://ra.co/features/1234", "tags": ["resident-advisor"]}'
```

### Supported platforms

Instagram, X/Twitter, Reddit (posts + top comments), LinkedIn, YouTube (metadata + transcripts), SoundCloud, Spotify, Bandcamp, Mixcloud, Resident Advisor, and any web article.

### Compile to wiki

```bash
# Compile new captures into wiki articles
uv run python mac/knowledge/compiler.py compile

# Rebuild the master index
uv run python mac/knowledge/compiler.py index
```

The LLM analyzes each capture, extracts entities (artists, labels, events, scenes), and creates/updates wiki articles with `[[cross-references]]`. Articles are organized into categories: concepts, artists, events, labels, scenes.

### Q&A

```bash
# Ask questions against your wiki
curl -X POST http://localhost:8002/kb/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What labels are associated with the progressive house scene?"}'
```

### Health check

```bash
# Lint the wiki for broken links, orphans, gaps
uv run python mac/knowledge/health.py
```

### API endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/kb/capture` | Capture a URL |
| POST | `/kb/capture/batch` | Capture multiple URLs |
| GET | `/kb/documents` | List raw documents |
| GET | `/kb/documents/{id}` | Get document + content |
| DELETE | `/kb/documents/{id}` | Delete a document |
| GET | `/kb/wiki` | List wiki articles |
| GET | `/kb/wiki/{slug}` | Get article content |
| POST | `/kb/compile` | Trigger compilation |
| POST | `/kb/ask` | Q&A against wiki |
| GET | `/kb/health` | Wiki health check |
| GET | `/kb/stats` | KB statistics |
| GET | `/kb/detect?url=` | Detect platform/type |

## DJ Personalities

**DJ Cascade** — The warm, knowledgeable Anjunadeep specialist. Speaks like a friend who always knows what track is playing. Smooth, unhurried. *"If you know, you know."*

**DJ Echo** — The underground progressive voice. Speaks rarely, and when he does, every word earns its place. Minimal, measured. *"You're still here. [pause] Good."*

**DJ Neon** — Festival energy with taste. Builds tension with words like a DJ builds a set. Never a generic hype machine. *"This is the one."*

## Segment Types

| Type | Words | Purpose |
|------|-------|---------|
| `track_intro` | 30–60 | ID a track, tease what's coming |
| `set_intro` | 60–120 | Open a show, set the mood |
| `set_outro` | 50–100 | Close a show, hand off |
| `artist_spotlight` | 120–250 | Deep dive on an artist or label |
| `festival_update` | 100–200 | Scene news, events, tours |
| `anthem_announce` | 30–60 | Build anticipation for an anthem |
| `station_id` | 15–30 | Quick station identification |

## Files

```
├── mac/
│   ├── stream_gapless.py          # Main streamer with crossfading
│   ├── schedule.py                # Weekly schedule parser
│   ├── music_scanner.py           # Library scanner → SQLite
│   ├── bpm_analyzer.py            # BPM, key, energy detection
│   ├── track_selector.py          # Smart track selection
│   ├── crossfade_mixer.py         # Beat-matched mixing engine
│   ├── soundcloud_client.py       # SoundCloud integration
│   ├── api_server.py              # Now-playing API (:8001)
│   ├── play_history.py            # Track dedup & history
│   ├── content_generator/
│   │   ├── talk_generator.py      # DJ dialogue (Claude CLI + TTS)
│   │   ├── persona.py             # Station identity & DJ hosts
│   │   ├── music_bumper_generator.py
│   │   ├── music_pools_expanded.py
│   │   └── helpers.py
│   ├── knowledge/                 # Knowledge base system
│   │   ├── capture.py             # URL capture & extraction
│   │   ├── store.py               # Storage layer (SQLite + files)
│   │   ├── compiler.py            # LLM wiki compiler
│   │   ├── health.py              # Wiki linting
│   │   └── api.py                 # KB API server (:8002)
│   ├── kokoro/                    # Kokoro TTS
│   └── chatterbox/                # Chatterbox TTS
├── config/
│   ├── schedule.yaml              # 3-show weekly schedule
│   └── icecast.xml.example
├── output/
│   ├── talk_segments/             # Generated DJ audio
│   ├── scripts/                   # Script metadata (JSON)
│   └── music_bumpers/             # AI-generated bumpers
└── run_operator.sh
```

## Requirements

- Python 3.11+
- ffmpeg
- Icecast2
- Claude CLI (for DJ scripts + wiki compilation)
- ~200MB for Kokoro TTS, ~4GB for Chatterbox
- Apple Silicon recommended for Chatterbox (MPS acceleration)

## License

MIT
