"""Expanded music bumper/jingle caption pools for Deep House Radio.

These are SHORT musical elements (5-30 seconds) used for:
- Station jingles and IDs
- Transition sweeps and risers
- Show intro/outro beds (to play under DJ voice)
- Brief musical stingers between DJ dialogue and tracks

NOT full tracks — those come from the local music library.
"""

# Station jingles and IDs (5-10 seconds)
station_jingles = [
    "deep house station jingle, warm synth pad swell, subtle kick pattern, lush reverb, 5 seconds",
    "melodic house radio ID, piano chord with analog synth sweep, soft hi-hat, elegant and brief",
    "progressive house station ident, deep bassline pulse, filtered vocal chop 'deep house radio', atmospheric",
    "electronic radio jingle, Moog bass note into shimmering pad, 808 snap, sophisticated and minimal",
    "ambient station ID, granular texture dissolving into warm pad, gentle sub bass, 7 seconds",
    "deep house bumper, classic Roland Juno pad chord, subtle groove, filtered and dreamy, brief",
    "station sweep, rising synth arpeggio with reverb tail, tension building then resolving, 6 seconds",
    "lo-fi house jingle, vinyl crackle intro, warm chord stab, tape-saturated kick, nostalgic feel",
]

# Transition sweeps and risers (3-8 seconds)
transitions = [
    "EDM riser, white noise sweep building tension, filter opening slowly, 5 seconds",
    "deep house transition, reverb wash of synth pad, low-pass filter sweep up, atmospheric",
    "progressive house build, delayed synth pluck accelerating, tension riser, 7 seconds",
    "ambient transition pad, lush stereo reverb swell, strings-like texture, emotional bridge",
    "techno sweep, industrial riser, metallic texture building, dark and driving, 4 seconds",
    "melodic house bridge, piano note with long reverb tail, shimmer delay, graceful transition",
    "club transition, kick drum roll building intensity, hi-hat pattern accelerating, 6 seconds",
    "downtempo transition sweep, tape delay feedback building, warm analog saturation, dreamy",
]

# Show intro/outro beds (15-30 seconds, to play under DJ voice)
the_deep_beds = [
    "Anjunadeep style melodic deep house bed, warm synth pads, gentle kick pattern at 120 BPM, lush and emotional, 25 seconds",
    "deep house ambient bed, filtered piano chords over soft groove, Rufus Du Sol inspired warmth, 20 seconds",
    "melodic house intro bed, Lane 8 style evolving pad, subtle bass pulse, introspective and warm, 25 seconds",
    "organic house bed, acoustic guitar sample over electronic groove, Nora En Pure inspired, nature and technology, 20 seconds",
    "Anjunadeep showcase intro, layered synth pads building gently, emotional and uplifting, no drop, 30 seconds",
    "deep house sunset bed, golden-hour synths, muted kick, Ben Böhmer inspired floating atmosphere, 25 seconds",
    "melodic progressive bed, Yotto-style dark warmth, hypnotic arpeggio, moody but inviting, 20 seconds",
]

frequencies_beds = [
    "progressive house bed, Digweed-style hypnotic groove, dark bassline, minimal percussion, 25 seconds",
    "Bedrock Records style intro, deep rolling bass, filtered atmosphere, 122 BPM, underground feel, 20 seconds",
    "dark progressive bed, Sasha-inspired layered synths, brooding and deep, 25 seconds",
    "marathon set intro bed, minimal percussion building, deep sub bass, warehouse atmosphere, 30 seconds",
    "Hernan Cattaneo style bed, Argentine progressive, warm and deep, slow-building groove, 25 seconds",
    "late night progressive bed, Guy J style hypnotic arpeggio, deep and driving, 3am energy, 20 seconds",
    "underground club bed, muted kick and bass, filtered vocal whisper texture, dark and seductive, 25 seconds",
]

anthem_hour_beds = [
    "festival intro bed, Eric Prydz style build, soaring synths, anticipation and tension, 25 seconds",
    "main stage energy bed, driving kick pattern, euphoric pad swell, Tomorrowland energy, 20 seconds",
    "anthem hour opener, Above & Beyond style emotional build, trance-influenced progressive, 30 seconds",
    "peak time bed, CamelPhat style groovy tension, vocal chop atmospherics, building energy, 25 seconds",
    "festival sunset bed, golden-hour euphoria, Deadmau5 style evolving chord progression, majestic, 25 seconds",
    "closing set bed, Stephan Bodzin style hypnotic build, analog synths, emotional peak, 30 seconds",
    "EDC main stage feel, laser synths and driving beat, Boris Brejcha influenced, high energy, 20 seconds",
]

# Combined pools per show (for the bumper generator)
SHOW_BUMPERS: dict[str, list[str]] = {
    "the_deep": station_jingles + transitions + the_deep_beds,
    "frequencies": station_jingles + transitions + frequencies_beds,
    "anthem_hour": station_jingles + transitions + anthem_hour_beds,
}
