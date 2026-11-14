"""
Title analysis: what is each title on the disc?

MakeMKV reports, per title, its length, chapters, size, source (DVD title set or Blu-ray
playlist), the list of video segments it plays, a metadata language (some Blu-rays) and
its audio/subtitle streams. From that:

- duplicate: plays exactly the same segments as another title (copy protection, menus)
- play all:  on TV discs, a title that plays several other titles back to back
- main:      the movie, in your preferred language, the cut closest to TMDb's runtime
- cut:       the same movie with extra or fewer segments (extended / theatrical cut)
- language:  the same cut with a few segments swapped (localized title cards and credits)
- feature:   a second, unrelated long title (double-feature discs)
- extra:     everything else long enough to rip
- episode:   TV discs: the episode-length titles, in disc order
- short:     shorter than MINLENGTH; ARM doesn't rip these
"""
import dataclasses
import logging

import arm.config.config as cfg
from arm.automation import settings, store

# MakeMKV attribute ids (apdefs.h)
NAME = 2
LANG_CODE = 3
LANG_NAME = 4
CHAPTERS = 8
DURATION = 9
SIZE_BYTES = 11
ANGLE = 15
SOURCE = 16
STREAM_FLAGS = 22
SEGMENTS_MAP = 26
METADATA_LANG_CODE = 28
METADATA_LANG_NAME = 29
STREAM_TYPE = 1
FLAG_COMMENTARY = 1 | 2
FLAG_FORCED = 4096

SAME_LENGTH_SECONDS = 90
LANGUAGE_NAMES = {
    "eng": "English", "fra": "French", "fre": "French", "deu": "German", "ger": "German", "spa": "Spanish",
    "ita": "Italian", "por": "Portuguese", "nld": "Dutch", "dut": "Dutch", "jpn": "Japanese", "kor": "Korean",
    "zho": "Chinese", "chi": "Chinese", "rus": "Russian", "pol": "Polish", "swe": "Swedish", "dan": "Danish",
    "nor": "Norwegian", "fin": "Finnish", "ces": "Czech", "cze": "Czech", "hun": "Hungarian", "tur": "Turkish",
    "ell": "Greek", "gre": "Greek", "heb": "Hebrew", "ara": "Arabic", "hin": "Hindi", "tha": "Thai",
}


@dataclasses.dataclass
class Title:
    tid: int
    seconds: int = 0
    chapters: int = 0
    size: int = 0
    name: str = ""
    source: str = ""
    segments: tuple = ()
    metadata_lang: str = ""
    angle: str = ""
    audio: list = dataclasses.field(default_factory=list)
    subtitles: list = dataclasses.field(default_factory=list)

    @property
    def language(self):
        """Metadata language if the disc has one, otherwise the first audio track's"""
        if self.metadata_lang:
            return self.metadata_lang
        for stream in self.audio:
            if stream.get("lang") and not stream.get("commentary"):
                return stream["lang"]
        return ""


def language_name(code):
    return LANGUAGE_NAMES.get(str(code or "").lower(), str(code or "").upper() or "Unknown language")


def parse_segments(value):
    """'1-3,7,9-10' -> (1, 2, 3, 7, 9, 10)"""
    result = []
    for part in str(value or "").replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            start, _, end = part.partition("-")
            try:
                result.extend(range(int(start), int(end) + 1))
            except ValueError:
                continue
        else:
            try:
                result.append(int(part))
            except ValueError:
                continue
    return tuple(result)


def _seconds(value):
    parts = [int(p) for p in str(value or "0").strip().split(":") if p.isdigit()]
    total = 0
    for part in parts:
        total = total * 60 + part
    return total


def from_makemkv(raw):
    """
    :param raw: {tid: {"tinfo": {id: value}, "streams": [{id: value}, ...]}} collected from MakeMKV's info run
    :return: list of Title
    """
    titles = []
    for tid in sorted(raw):
        info = raw[tid].get("tinfo", {})
        title = Title(
            tid=int(tid),
            seconds=_seconds(info.get(DURATION)),
            chapters=int(info.get(CHAPTERS) or 0) if str(info.get(CHAPTERS) or "0").isdigit() else 0,
            size=int(info.get(SIZE_BYTES) or 0) if str(info.get(SIZE_BYTES) or "0").isdigit() else 0,
            name=str(info.get(NAME) or ""),
            source=str(info.get(SOURCE) or ""),
            segments=parse_segments(info.get(SEGMENTS_MAP)),
            metadata_lang=str(info.get(METADATA_LANG_CODE) or "").lower(),
            angle=str(info.get(ANGLE) or ""),
        )
        for stream in raw[tid].get("streams", []):
            kind = str(stream.get(STREAM_TYPE, ""))
            try:
                flags = int(stream.get(STREAM_FLAGS) or 0)
            except ValueError:
                flags = 0
            entry = {"lang": str(stream.get(LANG_CODE) or "").lower(), "name": str(stream.get(NAME) or ""),
                     "commentary": bool(flags & FLAG_COMMENTARY) or "comment" in str(stream.get(NAME) or "").lower()}
            if kind.startswith("Audio") or kind == "6202":
                title.audio.append(entry)
            elif kind.startswith("Subtitle") or kind == "6203":
                entry["forced"] = bool(flags & FLAG_FORCED)
                title.subtitles.append(entry)
        titles.append(title)
    return titles


def _overlap(first, second):
    """Share of the smaller title's segments that the other title also plays"""
    a, b = set(first.segments), set(second.segments)
    if not a or not b:
        return None
    return len(a & b) / min(len(a), len(b))


def _same_film(first, second):
    overlap = _overlap(first, second)
    if overlap is not None:
        return overlap >= 0.5
    # No segment info: only call it the same film when the lengths are close
    return abs(first.seconds - second.seconds) <= max(SAME_LENGTH_SECONDS, 0.35 * max(first.seconds, second.seconds))


def _decision(title, role, rip, label="", reason="", **extra):
    entry = {"tid": title.tid, "seconds": title.seconds, "minutes": round(title.seconds / 60, 1), "role": role,
             "rip": bool(rip), "label": label, "reason": reason, "language": title.language,
             "source": title.source, "segments": len(title.segments),
             "audio": [s["lang"] + (" (commentary)" if s["commentary"] else "") for s in title.audio if s["lang"]],
             "subtitles": [s["lang"] + (" (forced)" if s.get("forced") else "") for s in title.subtitles if s["lang"]]}
    entry.update(extra)
    return entry


def _dedupe(titles, decisions):
    """Mark exact copies; returns the titles left"""
    keep = []
    for title in sorted(titles, key=lambda t: (-len(t.audio), t.tid)):
        original = next((k for k in keep if title.segments and k.segments == title.segments
                         and abs(k.seconds - title.seconds) <= 2), None)
        if original is None:
            keep.append(title)
        else:
            decisions[title.tid] = _decision(title, "duplicate", not settings.get("SKIP_DUPLICATE_TITLES"),
                                             f"Copy of title {original.tid}",
                                             f"Plays the same segments as title {original.tid}",
                                             duplicate_of=original.tid)
    return sorted(keep, key=lambda t: t.tid)


def _split_short(titles, decisions):
    """Mark titles below MINLENGTH (ARM doesn't rip them); return the rest"""
    min_length = int(cfg.arm_config.get("MINLENGTH") or 0)
    long_titles = []
    for title in titles:
        if title.seconds < min_length:
            decisions[title.tid] = _decision(title, "short", False, reason=f"Shorter than MINLENGTH ({min_length}s)")
        else:
            long_titles.append(title)
    return long_titles


def _group(items, same):
    """Group items: an item joins the first group with a member it's `same` as"""
    groups = []
    for item in items:
        group = next((g for g in groups if any(same(item, member) for member in g)), None)
        if group is None:
            groups.append([item])
        else:
            group.append(item)
    return groups


def _pick_version(bucket, preferred):
    """Primary title of a same-length bucket, and whether the language choice was a guess"""
    if len(bucket) == 1:
        return bucket[0], False
    preferred_matches = [t for t in bucket if t.language == preferred]
    if len(preferred_matches) == 1:
        return preferred_matches[0], False
    return min(bucket, key=lambda t: t.tid), True


def _label_main_cut(main_cut, preferred, tmdb_runtime, decisions):
    """Main title plus the main cut's other language versions; returns (main, guessed)"""
    main, guessed = _pick_version(main_cut, preferred)
    reason = f"Closest to TMDb's runtime ({tmdb_runtime} min)" if tmdb_runtime else "Longest title"
    if len(main_cut) > 1 and not guessed:
        reason += f", in {language_name(preferred)}"
    decisions[main.tid] = _decision(main, "main", True, "", reason)
    keep_languages = settings.get("KEEP_OTHER_LANGUAGE_VERSIONS")
    for number, title in enumerate([t for t in main_cut if t is not main], start=2):
        if guessed:
            decisions[title.tid] = _decision(title, "language", True, f"Version {number}",
                                             "Same cut, but ARM can't tell which language version this is",
                                             ambiguous=True)
        elif title.language:
            name = language_name(title.language)
            decisions[title.tid] = _decision(title, "language", keep_languages, name, f"Same cut in {name}")
        else:
            decisions[title.tid] = _decision(title, "language", keep_languages, f"Version {number}",
                                             "Same cut, other version")
    return main, guessed


def _label_other_cuts(cuts, main, preferred, decisions):
    keep_cuts = settings.get("KEEP_ALTERNATE_CUTS")
    keep_languages = settings.get("KEEP_OTHER_LANGUAGE_VERSIONS")
    for bucket in cuts:
        primary, guessed = _pick_version(bucket, preferred)
        difference = round(primary.seconds / 60 - main.seconds / 60)
        longer = difference > 0
        label = f"Extended cut (+{difference} min)" if longer else f"Shorter cut ({difference} min)"
        decisions[primary.tid] = _decision(primary, "cut", keep_cuts, label,
                                           f"Same film, {abs(difference)} min {'longer' if longer else 'shorter'}")
        for title in bucket:
            if title is not primary:
                decisions[title.tid] = _decision(title, "language", keep_cuts and (keep_languages or guessed),
                                                 f"{label}, {language_name(title.language)}",
                                                 "Language version of the other cut")


def analyze_movie(titles, preferred, tmdb_runtime, rip_extras=True):
    """
    :return: (decisions by tid, ambiguous_language)
    """
    decisions = {}
    remaining = _dedupe(_split_short(titles, decisions), decisions)
    if not remaining:
        return decisions, False

    longest = max(t.seconds for t in remaining)
    feature_like = [t for t in remaining if t.seconds >= max(0.75 * longest, 40 * 60)]
    others = [t for t in remaining if t not in feature_like]
    target = tmdb_runtime * 60 if tmdb_runtime else None

    # Feature-length titles grouped into films; the main film is the one closest to TMDb's runtime
    films = _group(feature_like, _same_film)
    films.sort(key=lambda film: min(abs(t.seconds - target) for t in film) if target
               else -max(t.seconds for t in film))
    # Within the film: cuts are length buckets, language versions share a bucket
    cuts = _group(sorted(films[0], key=lambda t: -t.seconds),
                  lambda a, b: abs(a.seconds - b.seconds) <= SAME_LENGTH_SECONDS)
    cuts.sort(key=lambda c: abs(c[0].seconds - target) if target else -c[0].seconds)

    main, ambiguous = _label_main_cut(cuts[0], preferred, tmdb_runtime, decisions)
    _label_other_cuts(cuts[1:], main, preferred, decisions)

    for number, film in enumerate(films[1:], start=2):
        for title in film:
            decisions[title.tid] = _decision(title, "feature", True, f"Feature {number}",
                                             "Another feature-length title that isn't the same film")
    for number, title in enumerate(sorted(others, key=lambda t: t.tid), start=1):
        decisions[title.tid] = _decision(title, "extra", rip_extras, title.name.strip() or f"Extra {number}",
                                         "Shorter title (bonus material)")
    return decisions, ambiguous


def analyze_series(titles):
    """TV discs: episodes in disc order, minus play-all titles and copies"""
    decisions = {}
    remaining = _dedupe(_split_short(titles, decisions), decisions)
    skip_dupes = settings.get("SKIP_DUPLICATE_TITLES")
    episodes = []
    for title in remaining:
        others = [t for t in remaining if t is not title and t.seconds < title.seconds]
        covered = [t for t in others if t.segments and set(t.segments) <= set(title.segments)]
        sums_up = len(others) >= 2 and abs(sum(t.seconds for t in others) - title.seconds) <= 0.05 * title.seconds
        if len(covered) >= 2 or (not title.segments and sums_up):
            decisions[title.tid] = _decision(title, "playall", not skip_dupes, "Play all",
                                             "Plays several other titles back to back")
        else:
            episodes.append(title)
    if episodes:
        lengths = sorted(t.seconds for t in episodes)
        median = lengths[len(lengths) // 2]
        number = 1
        for title in episodes:
            if title.seconds < 0.5 * median:
                decisions[title.tid] = _decision(title, "extra", True, title.name.strip() or f"Extra {number}",
                                                 "Much shorter than the episodes")
                number += 1
            else:
                decisions[title.tid] = _decision(title, "episode", True, "", "Episode-length title")
    return decisions


def plan(job, raw):
    """
    Analyse the disc and save the result. Returns the decisions keyed by title number,
    or None when analysis is off or there's nothing to go on.
    """
    if not settings.get("TITLE_ANALYSIS") or not raw:
        return None
    titles = from_makemkv(raw)
    if not titles:
        return None
    preferred = (settings.languages("PREFERRED_LANGUAGE") or ["eng"])[0]
    if job.video_type == "series":
        decisions = analyze_series(titles)
        ambiguous = False
    else:
        from arm.automation import tmdb
        runtime = tmdb.runtime_minutes(job.imdb_id)
        decisions, ambiguous = analyze_movie(titles, preferred, runtime,
                                             rip_extras=not bool(cfg.arm_config.get("MAINFEATURE")))
    result = {"titles": [decisions[t.tid] for t in titles if t.tid in decisions], "ambiguous_language": ambiguous}
    store.update_job(job.job_id, titles=result)
    summary = ", ".join(f"{d['tid']}={d['role']}{'' if d['rip'] else '(skip)'}" for d in result["titles"])
    logging.info(f"Title analysis: {summary}")
    if ambiguous:
        store.add_attention(job.job_id, "language",
                            "This disc has the movie in several language versions ARM couldn't tell apart. "
                            "All of them were kept; pick the one to keep on the job page.")
    return {d["tid"]: d for d in result["titles"]}


def main_seconds(decisions):
    main = next((d for d in (decisions or {}).values() if d["role"] == "main"), None)
    return main["seconds"] if main else None
