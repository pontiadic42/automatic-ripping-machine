"""
How sure is ARM about a disc's title? A 0-100 score with plain-language reasons, from:
the disc label vs the title, the main title's runtime vs TMDb, and earlier rips of the
same disc. When the label is vague, TMDb results are compared by runtime to find a
better match.
"""
import difflib
import logging
import re

from arm.automation import settings, store, tmdb

GENERIC_LABELS = {"", "none", "not identified", "dvd", "dvd video", "dvdvideo", "video ts", "bdrom", "bd rom",
                  "disc", "disc 1", "dvd volume", "dvdvolume", "logical volume id", "movie", "untitled",
                  "blu ray", "bluray", "no label"}
NOISE_WORDS = {"dvd", "bd", "bluray", "ws", "fs", "widescreen", "fullscreen", "ntsc", "pal", "16x9", "4x3",
               "r1", "r2", "r4", "us", "uk", "eu", "disc", "disk", "d1", "d2", "side", "a", "b"}
DISC_MARKERS = re.compile(r"\b(d|disc|disk|side)\s*\d+\b", re.IGNORECASE)


def clean_label(label):
    """DONT_BREATHE_WS_D1 -> 'dont breathe'"""
    text = str(label or "").replace("_", " ").replace(".", " ").strip().lower()
    if text in GENERIC_LABELS:
        return ""
    text = DISC_MARKERS.sub(" ", text)
    words = [word for word in re.split(r"[^a-z0-9']+", text) if word and word not in NOISE_WORDS]
    return " ".join(words)


def normalize_title(title):
    text = re.sub(r"[^a-z0-9 ]+", " ", str(title or "").lower().replace("'", ""))
    text = re.sub(r"^(the|a|an) ", "", re.sub(r"\s+", " ", text).strip())
    return text


def similarity(label, title):
    """0..1 how well the cleaned label matches the title"""
    left, right = normalize_title(clean_label(label)), normalize_title(title)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    ratio = difflib.SequenceMatcher(None, left, right).ratio()
    # Labels are often abbreviated: "it chapter two" vs "it chapter 2", "lotr fotr"
    left_words, right_words = set(left.split()), set(right.split())
    overlap = len(left_words & right_words) / max(len(left_words), 1)
    return round(max(ratio, overlap * 0.9), 3)


def score(identified, label_usable, label_sim, runtime_diff, prior):
    """
    :param identified: ARM found a title (hasnicetitle)
    :param label_usable: the label had real words in it
    :param label_sim: 0..1 label vs title
    :param runtime_diff: minutes between the main title and TMDb's runtime, or None
    :param prior: "same" (earlier rip of this disc had this title), "different", or None
    :return: (score, reasons)
    """
    reasons = []
    if not identified:
        return 5, ["ARM couldn't find a title for this disc"]
    points = 30
    if not label_usable:
        reasons.append("The disc label doesn't say what it is")
    elif label_sim >= 0.85:
        points += 30
        reasons.append("The disc label matches the title")
    elif label_sim >= 0.6:
        points += 18
        reasons.append("The disc label mostly matches the title")
    elif label_sim >= 0.4:
        points += 8
        reasons.append("The disc label only partly matches the title")
    else:
        points -= 10
        reasons.append("The disc label doesn't match the title")

    if runtime_diff is None:
        reasons.append("No runtime to compare")
    elif runtime_diff <= 3:
        points += 35
        reasons.append("The main title's length matches TMDb's runtime")
    elif runtime_diff <= 8:
        points += 15
        reasons.append(f"The main title is {runtime_diff:.0f} min off TMDb's runtime")
    elif runtime_diff <= 20:
        points -= 10
        reasons.append(f"The main title is {runtime_diff:.0f} min off TMDb's runtime (maybe another cut)")
    else:
        points -= 30
        reasons.append(f"The main title is {runtime_diff:.0f} min off TMDb's runtime")

    if prior == "same":
        points += 25
        reasons.append("An earlier rip of this disc had the same title")
    elif prior == "different":
        points -= 15
        reasons.append("An earlier rip of this disc had a different title")
    return max(0, min(100, int(points))), reasons


def level(value):
    if value >= settings.get("CONFIDENCE_SKIP_WAIT_ABOVE") and settings.get("CONFIDENCE_SKIP_WAIT_ABOVE") > 0:
        return "high"
    if value < settings.get("CONFIDENCE_NOTIFY_BELOW"):
        return "low"
    return "medium"


def main_title_seconds(job):
    """Length of the longest title, before ripping (DVD via lsdvd; Blu-ray later from MakeMKV)"""
    if job.disctype != "dvd":
        return None
    from arm.ripper import identify
    tracks = identify.lsdvd_tracks(job.devpath) or []
    lengths = [float(track.get("length") or 0) for track in tracks]
    return max(lengths) if lengths else None


def prior_rip(job):
    """'same' / 'different' / None, from successful jobs with the same disc fingerprint"""
    if not job.crc_id:
        return None
    from arm.models.job import Job
    earlier = Job.query.filter(Job.crc_id == job.crc_id, Job.status == "success", Job.job_id != job.job_id).all()
    if not earlier:
        return None
    titles = {normalize_title(other.title_manual or other.title) for other in earlier}
    return "same" if normalize_title(job.title) in titles else "different"


def evaluate(job, main_seconds):
    """Score the job's current title"""
    runtime = tmdb.runtime_minutes(job.imdb_id) if main_seconds else None
    diff = abs(main_seconds / 60 - runtime) if (main_seconds and runtime) else None
    label = clean_label(job.label)
    value, reasons = score(bool(job.hasnicetitle) and normalize_title(job.title) not in ("", "none", "not identified"),
                           bool(label), similarity(job.label, job.title), diff, prior_rip(job))
    return {"score": value, "reasons": reasons, "main_minutes": round(main_seconds / 60, 1) if main_seconds else None,
            "tmdb_runtime": runtime}


def runtime_candidate(job, main_seconds):
    """A TMDb result whose runtime matches the main title, searched by the label"""
    query = clean_label(job.label)
    if not query or not main_seconds:
        return None
    minutes = main_seconds / 60
    matches = [c for c in tmdb.search_movies(query) if c["runtime"] and abs(c["runtime"] - minutes) <= 3]
    if not matches:
        return None
    matches.sort(key=lambda c: (-similarity(job.label, c["title"]), abs(c["runtime"] - minutes), -c["popularity"]))
    return matches[0]


def assess(job):
    """
    Score the identification, try a runtime match when it's weak, save the result and
    return it. Values the user set in the UI are never replaced.
    """
    if not settings.get("CONFIDENCE_ENABLED") or job.disctype not in ("dvd", "bluray"):
        return None
    if job.title_manual:
        result = {"score": 100, "reasons": ["You set the title"], "level": "high"}
        store.update_job(job.job_id, confidence=result)
        return result
    try:
        main_seconds = main_title_seconds(job)
    except Exception as error:  # noqa: BLE001
        logging.info(f"Couldn't read the title lengths: {error}")
        main_seconds = None
    result = evaluate(job, main_seconds)

    if result["score"] < settings.get("CONFIDENCE_NOTIFY_BELOW") and settings.get("RUNTIME_MATCHING"):
        candidate = runtime_candidate(job, main_seconds)
        if candidate and normalize_title(candidate["title"]) != normalize_title(job.title):
            from arm.ripper import utils
            previous = (job.title, job.year)
            logging.info(f"Runtime match: '{candidate['title']} ({candidate['year']})' has a "
                         f"{candidate['runtime']} min "
                         f"runtime like the main title; was '{job.title}'")
            utils.database_updater({
                "title": candidate["title"], "title_auto": candidate["title"], "year": candidate["year"],
                "year_auto": candidate["year"], "imdb_id": candidate["imdb_id"], "imdb_id_auto": candidate["imdb_id"],
                "poster_url": candidate["poster_url"], "poster_url_auto": candidate["poster_url"],
                "video_type": "movie", "video_type_auto": "movie", "hasnicetitle": True,
            }, job)
            retry = evaluate(job, main_seconds)
            retry["reasons"].insert(0, f"Picked by runtime instead of '{previous[0]}'")
            result = retry
    result["level"] = level(result["score"])
    store.update_job(job.job_id, confidence=result)
    logging.info(f"Identification confidence {result['score']}/100 ({result['level']}): "
                 + "; ".join(result["reasons"]))
    return result


def ask_user(job, result, wait_seconds):
    """Low confidence: send the phone notification with Confirm / Pick another"""
    from arm.automation import notify
    store.add_attention(job.job_id, "confidence",
                        f"Not sure this is {job.title} ({result['score']}/100). Confirm or pick another title.")
    title = f"Is this {job.title}" + (f" ({job.year})" if job.year and job.year != "0000" else "") + "?"
    minutes = max(1, round(wait_seconds / 60))
    body = (f"{result['score']}/100 sure. " + "; ".join(result["reasons"][:3]) +
            f". ARM rips with this title in {minutes} min unless you change it.")
    notify.in_app(title, body)
    notify.send(title, body, priority="high", tags=["dvd", "question"], click=notify.job_link(job.job_id),
                icon=job.poster_url,
                actions=[notify.http("Yes, that's it", notify.action_url(job.job_id, "confirm")),
                         notify.view("Pick another", notify.arm_url(f"/titlesearch?job_id={job.job_id}"))])
