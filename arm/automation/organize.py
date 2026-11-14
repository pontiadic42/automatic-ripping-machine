"""
Putting finished files where they belong, with names Jellyfin understands:

    Movies/It Chapter Two (2019)/It Chapter Two (2019).mkv
    Movies/It Chapter Two (2019)/It Chapter Two (2019) - Extended cut (+11 min).mkv
    Movies/It Chapter Two (2019)/It Chapter Two (2019) - French.mkv
    Movies/It Chapter Two (2019)/extras/Making of.mkv
    TV Shows/The Office (2005)/Season 02/The Office S02E07.mkv

Each file's length is checked against its disc title first. Also: moving a finished
job's files when its title is corrected later.
"""
import json
import logging
import os
import re
import shutil
import subprocess

import arm.config.config as cfg
from arm.automation import settings, store

TID = re.compile(r"_t(\d+)\.[A-Za-z0-9]+$")
VIDEO = (".mkv", ".mp4", ".m4v", ".avi")
SEASON = re.compile(r"(?:^|[^A-Z0-9])S(?:EASON)?[ _.-]?(\d{1,2})(?![0-9])", re.IGNORECASE)
DISC = re.compile(r"(?:^|[^A-Z])D(?:ISC|ISK)?[ _.-]?(\d{1,2})(?![0-9])", re.IGNORECASE)
UNSAFE = re.compile(r'[\\/:*?"<>|]+')


def safe_name(name):
    return re.sub(r"\s+", " ", UNSAFE.sub(" ", str(name))).strip(" .") or "Untitled"


def unique_path(path):
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    number = 2
    while os.path.exists(f"{stem} ({number}){ext}"):
        number += 1
    return f"{stem} ({number}){ext}"


def duration_seconds(path):
    """Length of a video file from ffprobe, or None"""
    try:
        output = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
                                capture_output=True, text=True, timeout=120).stdout
        return float(json.loads(output)["format"]["duration"])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None


def check_length(path, expected_seconds):
    """None when fine or unknown, otherwise a message"""
    if not settings.get("VERIFY_OUTPUT") or not expected_seconds:
        return None
    actual = duration_seconds(path)
    if actual is None:
        return f"{os.path.basename(path)}: couldn't read its length"
    if expected_seconds - actual > settings.get("VERIFY_TOLERANCE_SECONDS"):
        return (f"{os.path.basename(path)} is {int(actual // 60)}:{int(actual % 60):02d} long but the disc title is "
                f"{int(expected_seconds // 60)}:{int(expected_seconds % 60):02d}")
    return None


def season_and_disc(*texts):
    season = disc = None
    for text in texts:
        text = str(text or "")
        if season is None and (match := SEASON.search(text)):
            season = int(match.group(1))
        if disc is None and (match := DISC.search(text)):
            disc = int(match.group(1))
    return season, disc


def match_episodes(job, episodes):
    """
    Number the episode titles (sorted by title number) using TMDb's episode runtimes.
    :return: ({tid: (season, episode)}, message) - empty dict when it isn't sure
    """
    from arm.automation import tmdb
    if not settings.get("TV_EPISODE_MATCHING") or not episodes:
        return {}, "Episode matching is off"
    found = tmdb.find_by_imdb(job.imdb_id)
    if not found or found[0] != "tv":
        return {}, "The show isn't identified on TMDb"
    tv_id = found[1]
    season, disc = season_and_disc(job.label, job.title_manual or job.title)
    if season is None:
        details = tmdb.tv_details(tv_id) or {}
        real_seasons = [s for s in details.get("seasons") or [] if s.get("season_number")]
        if len(real_seasons) == 1:
            season = real_seasons[0]["season_number"]
        else:
            return {}, "The disc label doesn't say which season this is"
    data = tmdb.season(tv_id, season) or {}
    tmdb_episodes = sorted(data.get("episodes") or [], key=lambda e: e.get("episode_number") or 0)
    if not tmdb_episodes:
        return {}, f"TMDb has no episodes for season {season}"
    key = f"tv:{tv_id}:{season}"
    progress = store.load_state().get("tv_progress", {})
    if disc in (None, 1) and not (disc is None and key in progress):
        start = 1
    elif key in progress:
        start = progress[key]
    else:
        start = (disc - 1) * len(episodes) + 1
    runtimes = {e["episode_number"]: e.get("runtime") for e in tmdb_episodes}
    numbers = {}
    checked = matched = 0
    for offset, title in enumerate(sorted(episodes, key=lambda d: d["tid"])):
        number = start + offset
        if number not in runtimes:
            return {}, f"Season {season} has no episode {number} on TMDb"
        numbers[title["tid"]] = (season, number)
        if runtimes[number]:
            checked += 1
            minutes = title["seconds"] / 60
            if abs(minutes - runtimes[number]) <= max(5, 0.2 * runtimes[number]):
                matched += 1
    if checked and matched / checked < 0.6:
        return {}, f"Title lengths don't line up with season {season}'s episodes from episode {start}"
    progress[key] = start + len(episodes)
    store.update_state(tv_progress=progress)
    first, last = min(n for _s, n in numbers.values()), max(n for _s, n in numbers.values())
    return numbers, f"Season {season}, episodes {first}-{last}"


def destination(job, decision, final_dir, episode=None, ext=".mkv"):
    from arm.ripper import utils
    base = safe_name(utils.fix_job_title(job))
    extras = os.path.join(final_dir, str(cfg.arm_config.get("EXTRAS_SUB") or "extras"))
    role = decision["role"]
    if job.video_type == "series":
        show = safe_name(job.title_manual or job.title)
        if role == "episode" and episode:
            season, number = episode
            return os.path.join(final_dir, f"Season {season:02d}", f"{show} S{season:02d}E{number:02d}{ext}")
        if role == "episode":
            return os.path.join(final_dir, f"{show} - title {decision['tid']}{ext}")
        return os.path.join(extras, safe_name(decision["label"] or f"Title {decision['tid']}") + ext)
    if role == "main":
        return os.path.join(final_dir, f"{base}{ext}")
    if role in ("cut", "language", "feature"):
        return os.path.join(final_dir, f"{base} - {safe_name(decision['label'])}{ext}")
    if settings.get("ORGANIZE_EXTRAS"):
        return os.path.join(extras, safe_name(decision["label"] or f"Title {decision['tid']}") + ext)
    return None  # keep MakeMKV's name in the extras folder


def organize(job, source_dir, final_dir, decisions):
    """
    Move every video in source_dir into final_dir with its planned name.
    :return: list of length problems (empty = all good). Raises nothing for single files.
    """
    problems = []
    files = sorted(name for name in os.listdir(source_dir) if name.lower().endswith(VIDEO))
    episodes = [d for d in decisions.values() if d["role"] == "episode" and d["rip"]]
    numbers, episode_note = ({}, "")
    if job.video_type == "series":
        numbers, episode_note = match_episodes(job, episodes)
        logging.info(f"Episode matching: {episode_note}")
        if not numbers and episodes:
            store.add_attention(job.job_id, "episodes", f"Episodes not numbered: {episode_note}. "
                                                        "Files are named by title number.")
    moved = []
    for name in files:
        source = os.path.join(source_dir, name)
        match = TID.search(name)
        decision = decisions.get(int(match.group(1))) if match else None
        ext = os.path.splitext(name)[1]
        if decision is None:
            decision = {"tid": -1, "role": "extra", "label": os.path.splitext(name)[0], "seconds": None}
        target = destination(job, decision, final_dir, numbers.get(decision["tid"]), ext)
        if target is None:
            target = os.path.join(final_dir, str(cfg.arm_config.get("EXTRAS_SUB") or "extras"), name)
        problem = check_length(source, decision.get("seconds"))
        if problem:
            problems.append(problem)
        target = unique_path(target)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        logging.info(f"Moving {name} ({decision['role']}) to {target}")
        shutil.move(source, target)
        moved.append({"tid": decision["tid"], "role": decision["role"], "file": target})
    store.update_job(job.job_id, files=moved, episodes=episode_note or None, length_problems=problems)
    record_outcomes(job, moved)
    return problems


VERSION_ROLES = ("main", "language")


def with_outcomes(titles, files):
    """
    Title analysis plus what really came out of the rip: each title gets an outcome,
    "ripped" (a file was made), "missing" (planned, but no file: MakeMKV didn't write it)
    or "skipped" (not planned). When fewer than two of the language versions ARM couldn't
    tell apart made a file, there's nothing to choose between any more.
    """
    titles = dict(titles or {})
    written = {entry.get("tid") for entry in files or []}
    entries = []
    for entry in titles.get("titles") or []:
        entry = dict(entry)
        if entry.get("outcome") != "deleted":
            entry["outcome"] = ("ripped" if entry["tid"] in written else "missing") if entry.get("rip") else "skipped"
        entries.append(entry)
    titles["titles"] = entries
    if titles.get("ambiguous_language") and len(version_files({"titles": titles, "files": files})) < 2:
        titles["ambiguous_language"] = False
    return titles


def record_outcomes(job, files):
    data = store.load_job(job.job_id)
    if not data.get("titles"):
        return
    was_ambiguous = data["titles"].get("ambiguous_language")
    titles = with_outcomes(data["titles"], files)
    missing = [str(t["tid"]) for t in titles["titles"] if t["outcome"] == "missing"]
    if missing:
        logging.warning(f"Planned title(s) {', '.join(missing)} made no file (MakeMKV didn't write them)")
    if was_ambiguous and not titles["ambiguous_language"]:
        store.clear_attention(job.job_id, "language")
    store.update_job(job.job_id, titles=titles)


def version_files(data):
    """
    The finished files to choose between when a disc has language versions ARM couldn't tell
    apart: [(index in data["files"], entry)], or [] when there's no choice to make.
    """
    titles = data.get("titles") or {}
    if not titles.get("ambiguous_language"):
        return []
    candidates = {t["tid"] for t in titles.get("titles") or [] if t["role"] == "main" or t.get("ambiguous")}
    return [(index, entry) for index, entry in enumerate(data.get("files") or [])
            if entry.get("tid") in candidates and entry.get("role") in VERSION_ROLES]


def _inside_completed(path):
    completed = os.path.realpath(str(cfg.arm_config["COMPLETED_PATH"]))
    return os.path.realpath(path).startswith(completed + os.sep)


def keep_version(job, index):
    """
    The user picked one of the language versions: delete the others and give the kept one
    the main file name (Movie (Year).mkv).
    :return: (kept path, [deleted paths]); raises ValueError when it can't be done
    """
    data = store.load_job(job.job_id)
    choices = dict(version_files(data))
    if index not in choices:
        raise ValueError("That file isn't one of the versions to choose from.")
    chosen = choices[index]
    if not _inside_completed(chosen["file"]) or not os.path.isfile(chosen["file"]):
        raise ValueError(f"{os.path.basename(chosen['file'])} isn't there any more.")
    main = next((entry for entry in choices.values() if entry["role"] == "main"), None)
    deleted = []
    for other_index, entry in choices.items():
        if other_index != index and _inside_completed(entry["file"]) and os.path.isfile(entry["file"]):
            os.remove(entry["file"])
            deleted.append(entry["file"])
    target = chosen["file"]
    if main is not None and main is not chosen and not os.path.exists(main["file"]):
        target = main["file"]
        os.rename(chosen["file"], target)
    logging.info(f"Job {job.job_id}: kept {target} (title {chosen['tid']}), deleted {len(deleted)} other version(s)")
    files = []
    for file_index, entry in enumerate(data.get("files") or []):
        if file_index == index:
            files.append(dict(entry, file=target, role="main"))
        elif file_index not in choices:
            files.append(entry)
    dropped = {entry["tid"] for i, entry in choices.items() if i != index}
    titles = dict(data["titles"], ambiguous_language=False, kept_tid=chosen["tid"])
    titles["titles"] = [dict(t, outcome="deleted") if t["tid"] in dropped else t for t in titles["titles"]]
    store.update_job(job.job_id, files=files, titles=titles)
    store.clear_attention(job.job_id, "language")
    from arm.automation import jellyfin
    jellyfin.announce(job, os.path.dirname(target))
    return target, deleted


def keep_all_versions(job):
    """The user wants every language version: stop asking"""
    data = store.load_job(job.job_id)
    if data.get("titles"):
        store.update_job(job.job_id, titles=dict(data["titles"], ambiguous_language=False, kept_tid="all"))
    store.clear_attention(job.job_id, "language")


def verify_main_file(job, final_dir):
    """Without a title plan: check the main movie file against the longest ripped track"""
    from arm.ripper import utils
    track = job.tracks.filter_by(ripped=True).order_by(None).all()
    longest = max((t.length or 0 for t in track), default=0)
    main = os.path.join(final_dir, f"{utils.fix_job_title(job)}.{cfg.arm_config.get('DEST_EXT', 'mkv')}")
    if job.video_type == "series" or not os.path.isfile(main):
        return []
    problem = check_length(main, longest)
    store.update_job(job.job_id, length_problems=[problem] if problem else [])
    return [problem] if problem else []


def report_problems(job, problems, kept_paths):
    from arm.automation import notify
    message = ("Some files look cut short, so the raw rip was kept: " + "; ".join(problems) +
               ". Raw files: " + ", ".join(p for p in kept_paths if p))
    store.add_attention(job.job_id, "length", message, level="bad")
    notify.in_app(f"{job.title}: check the files", message)
    notify.send(f"{job.title}: files look cut short", message, priority="high", tags=["warning"],
                click=notify.job_link(job.job_id))


def retitle(job, old_path, old_base):
    """
    The title of a finished job changed: move its folder to the new name (and to the
    movies/TV folder if its type changed) and rename files that start with the old title.
    :return: new folder, or None when nothing was moved
    """
    from arm.ripper import utils
    if not old_path or not os.path.isdir(old_path):
        return None
    new_base = safe_name(utils.fix_job_title(job))
    new_dir = os.path.join(str(cfg.arm_config["COMPLETED_PATH"]), utils.convert_job_type(job.video_type), new_base)
    if os.path.normpath(new_dir) == os.path.normpath(old_path):
        return None
    if os.path.exists(new_dir):
        new_dir = unique_path(new_dir)
    os.makedirs(os.path.dirname(new_dir), exist_ok=True)
    shutil.move(old_path, new_dir)
    renamed = 0
    for root, _dirs, files in os.walk(new_dir):
        for name in files:
            if old_base and name.startswith(old_base) and name != new_base:
                target = unique_path(os.path.join(root, new_base + name[len(old_base):]))
                os.rename(os.path.join(root, name), target)
                renamed += 1
    logging.info(f"Retitled job {job.job_id}: moved {old_path} to {new_dir}, renamed {renamed} file(s)")
    data = store.load_job(job.job_id)
    if data.get("files"):
        for entry in data["files"]:
            entry["file"] = entry["file"].replace(old_path, new_dir, 1)
        store.update_job(job.job_id, files=data["files"])
    store.clear_attention(job.job_id, "confidence")
    return new_dir
