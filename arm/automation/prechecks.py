"""
Checks before ripping: was this disc already ripped (and are its files still there)?
Is there enough free space?
"""
import logging
import os
import shutil
import time

import arm.config.config as cfg
from arm.automation import confidence, notify, settings, store


class JobSkipped(Exception):
    """The job ends without ripping, on purpose (e.g. already ripped). Not a failure."""


def find_previous_rip(job):
    """
    A successful earlier job for the same disc whose output folder still has video files.
    Same disc = same DVD fingerprint (CRC64), or for discs without one the same label and title.
    """
    from arm.models.job import Job
    query = Job.query.filter(Job.status == "success", Job.job_id != job.job_id)
    if job.crc_id:
        candidates = query.filter(Job.crc_id == job.crc_id).order_by(Job.job_id.desc()).all()
    elif job.label and confidence.clean_label(job.label):
        candidates = [other for other in query.filter(Job.label == job.label).order_by(Job.job_id.desc()).all()
                      if confidence.normalize_title(other.title) == confidence.normalize_title(job.title)]
    else:
        return None
    for other in candidates:
        if other.path and os.path.isdir(other.path) and _has_video(other.path):
            return other
    return None


def _has_video(path):
    for _root, _dirs, files in os.walk(path):
        if any(name.lower().endswith((".mkv", ".mp4", ".m4v", ".avi", ".iso")) for name in files):
            return True
    return False


def check_already_ripped(job):
    """Act on ALREADY_RIPPED_ACTION; raises JobSkipped for 'skip'"""
    action = settings.get("ALREADY_RIPPED_ACTION")
    if action == "rip" or job.disctype not in ("dvd", "bluray"):
        return None
    previous = find_previous_rip(job)
    if previous is None:
        return None
    name = previous.title_manual or previous.title
    folder = previous.path
    store.update_job(job.job_id, already_ripped={"job_id": previous.job_id, "title": name, "path": folder})
    if action == "skip":
        message = f"You already have {name} (job {previous.job_id}) in {folder}. Ejected without ripping."
        notify.in_app("Already ripped", message)
        notify.send(f"Already ripped: {name}", message, tags=["dvd", "repeat"], click=notify.job_link(previous.job_id))
        raise JobSkipped(message)
    message = f"You already have {name} (job {previous.job_id}) in {folder}. Ripping it again anyway."
    notify.in_app("Already ripped", message)
    notify.send(f"Ripping {name} again", message, priority="low", tags=["dvd", "repeat"],
                click=notify.job_link(job.job_id))
    return previous


def _free_gb(path):
    try:
        return shutil.disk_usage(path).free / 1024 ** 3
    except OSError:
        return None


def space_shortfall(job):
    """List of (folder, free GB) below the needed space"""
    needed = settings.get("MIN_FREE_GB_BLURAY" if job.disctype == "bluray" else "MIN_FREE_GB_DVD")
    short = []
    seen = set()
    for key in ("RAW_PATH", "TRANSCODE_PATH", "COMPLETED_PATH"):
        path = str(cfg.arm_config.get(key) or "")
        if not path or not os.path.isdir(path):
            continue
        device = os.stat(path).st_dev
        if device in seen:
            continue
        seen.add(device)
        free = _free_gb(path)
        if free is not None and free < needed:
            short.append((path, round(free, 1)))
    return needed, short


def check_disk_space(job, sleep=time.sleep, check_every=300, give_up_after=7200):
    """Act on DISK_SPACE_ACTION. Raises RipperException when the job can't go ahead."""
    from arm.ripper import utils
    action = settings.get("DISK_SPACE_ACTION")
    if action == "ignore" or job.disctype not in ("dvd", "bluray"):
        return True
    needed, short = space_shortfall(job)
    if not short:
        return True
    where = ", ".join(f"{path} has {free} GB" for path, free in short)
    message = f"Not enough space to rip {job.title}: needs {needed} GB free; {where}."
    store.add_attention(job.job_id, "disk_space", message, level="bad")
    notify.in_app("Low disk space", message)
    if action == "fail":
        notify.send("Low disk space", message, priority="high", tags=["warning"], click=notify.job_link(job.job_id))
        raise utils.RipperException(message)
    notify.send("Low disk space", message + " Waiting up to 2 hours for space.", priority="high",
                tags=["warning"], click=notify.job_link(job.job_id))
    waited = 0
    while waited < give_up_after:
        sleep(check_every)
        waited += check_every
        needed, short = space_shortfall(job)
        if not short:
            logging.info("Enough free space now; continuing")
            store.clear_attention(job.job_id, "disk_space")
            return True
    raise utils.RipperException(message + " Gave up after waiting 2 hours.")
