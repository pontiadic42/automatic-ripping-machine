"""
Scheduled housekeeping, run every few minutes by the host:

    docker exec -u arm -w /opt/arm arm-rippers python3 -m arm.automation.maintenance tick

Each run: stuck-job watchdog and the Jellyfin match check. Once a day at the configured
times: database backup, cleanup, daily summary. Single tasks can be run by name.
"""
import datetime
import glob
import logging
import os
import shutil
import sqlite3
import sys
import tarfile
import time

import psutil

import arm.config.config as cfg
from arm.automation import confidence, jellyfin, notify, settings, store

ACTIVE = ("ripping", "transcoding")


def _now():
    """Clock the database uses (ARM stores naive times in the container's time zone, UTC in Docker)"""
    return datetime.datetime.now()


def _local_now():
    """
    The user's wall clock, for the daily times (backup, summary). The container runs on UTC,
    so the host passes its time zone in ARM_LOCAL_TZ.
    """
    name = os.environ.get("ARM_LOCAL_TZ", "")
    if name:
        try:
            from zoneinfo import ZoneInfo
            return datetime.datetime.now(ZoneInfo(name)).replace(tzinfo=None)
        except Exception as error:  # noqa: BLE001
            logging.warning(f"Unknown time zone {name!r} ({error}); using the container's clock")
    return datetime.datetime.now()


def due(name, hhmm, now=None):
    """True once per day, from the configured time on"""
    now = now or _now()
    hours, minutes = (int(part) for part in hhmm.split(":"))
    if now < now.replace(hour=hours, minute=minutes, second=0, microsecond=0):
        return False
    return store.load_state().get(f"last_{name}") != now.date().isoformat()


def mark_done(name, now=None):
    store.update_state(**{f"last_{name}": (now or _now()).date().isoformat()})


# ---------------------------------------------------------------- watchdog

def last_activity(job):
    """Newest change to anything the job writes: its log, MakeMKV's progress file, its output folders"""
    logpath = str(cfg.arm_config["LOGPATH"])
    candidates = [os.path.join(logpath, str(job.logfile)), os.path.join(logpath, "progress", f"{job.job_id}.log")]
    for base in (cfg.arm_config.get("RAW_PATH"), cfg.arm_config.get("TRANSCODE_PATH")):
        if base and job.title:
            candidates.extend(glob.glob(os.path.join(str(base), "**", f"{glob.escape(str(job.title))}*", "*"),
                                        recursive=True))
    times = []
    for path in candidates:
        try:
            times.append(os.path.getmtime(path))
        except OSError:
            continue
    return max(times) if times else None


def stop_job(job):
    """Stop the job's ripper and everything it started (MakeMKV, HandBrake)"""
    if job.pid is None:
        return False
    try:
        process = psutil.Process(job.pid)
        if job.pid_hash is not None and hash(process) != job.pid_hash:
            logging.warning(f"Job {job.job_id}: PID {job.pid} isn't this job's ripper any more; not stopping it")
            return False
        family = process.children(recursive=True) + [process]
        for member in family:
            member.terminate()
        _gone, alive = psutil.wait_procs(family, timeout=20)
        for member in alive:
            member.kill()
        return True
    except psutil.NoSuchProcess:
        return False


def watchdog(now=None):
    minutes = settings.get("STUCK_JOB_MINUTES")
    if minutes <= 0:
        return []
    from arm.models.job import Job
    from arm.ui import db
    now_ts = (now or _now()).timestamp()
    flagged = store.load_state().get("stuck", {})
    stuck = []
    for job in Job.query.filter(Job.status.in_(ACTIVE)).all():
        last = last_activity(job)
        if last is None or now_ts - last < minutes * 60:
            flagged.pop(str(job.job_id), None)
            continue
        idle = int((now_ts - last) // 60)
        if str(job.job_id) in flagged:
            continue
        flagged[str(job.job_id)] = idle
        stuck.append(job.job_id)
        name = job.title_manual or job.title or job.label or f"job {job.job_id}"
        if settings.get("STUCK_JOB_ACTION") == "abandon":
            stopped = stop_job(job)
            job.status = "fail"
            job.errors = f"Stopped by the watchdog: no progress for {idle} minutes"
            db.session.commit()
            try:
                job.eject()
                db.session.commit()
            except Exception as error:  # noqa: BLE001
                logging.warning(f"Couldn't eject after stopping job {job.job_id}: {error}")
            message = (f"{name} made no progress for {idle} minutes and was stopped"
                       f"{'' if stopped else ' (its process was already gone)'}. The disc was ejected.")
        else:
            message = f"{name} has made no progress for {idle} minutes. It may be stuck."
        logging.warning(message)
        store.add_attention(job.job_id, "stuck", message, level="bad")
        notify.in_app("Job stuck", message)
        notify.send("ARM job stuck", message, priority="high", tags=["warning"], click=notify.job_link(job.job_id))
    store.update_state(stuck=flagged)
    return stuck


# ---------------------------------------------------------------- backup

def backup_dir():
    return settings.get("DB_BACKUP_DIR") or os.path.join(os.path.dirname(str(cfg.arm_config["DBFILE"])), "backups")


def backup(now=None):
    """Consistent copy of the database (SQLite online backup) plus the automation records"""
    now = now or _now()
    folder = backup_dir()
    os.makedirs(folder, exist_ok=True)
    stamp = now.strftime("%Y%m%d-%H%M")
    target = os.path.join(folder, f"arm-{stamp}.db")
    source = sqlite3.connect(str(cfg.arm_config["DBFILE"]), timeout=60)
    try:
        destination = sqlite3.connect(target)
        with destination:
            source.backup(destination)
        destination.close()
    finally:
        source.close()
    with tarfile.open(os.path.join(folder, f"automation-{stamp}.tar.gz"), "w:gz") as archive:
        archive.add(store.base_dir(), arcname="automation", filter=lambda info: None if info.name.endswith("secret")
                    else info)
    keep = settings.get("DB_BACKUP_KEEP")
    for pattern in ("arm-*.db", "automation-*.tar.gz"):
        files = sorted(glob.glob(os.path.join(folder, pattern)))
        for old in files[:-keep] if keep > 0 else []:
            os.remove(old)
    logging.info(f"Database backed up to {target}")
    return target


# ---------------------------------------------------------------- cleanup

def leftover_folders(now=None):
    """Raw/transcode folders untouched for CLEANUP_LEFTOVER_DAYS that no active job uses"""
    from arm.models.job import Job
    cutoff = (now or _now()).timestamp() - settings.get("CLEANUP_LEFTOVER_DAYS") * 86400
    active_titles = {str(job.title) for job in Job.query.filter(Job.status.notin_(["success", "fail"])).all()}
    found = []
    for key in ("RAW_PATH", "TRANSCODE_PATH"):
        base = str(cfg.arm_config.get(key) or "")
        if not base or not os.path.isdir(base):
            continue
        for entry in glob.glob(os.path.join(base, "*")) + glob.glob(os.path.join(base, "*", "*")):
            if not os.path.isdir(entry) or os.path.dirname(entry.rstrip("/")) == base.rstrip("/") and \
                    os.path.basename(entry) in ("movies", "tv", "unidentified", "music"):
                continue
            if any(os.path.basename(entry).startswith(title) for title in active_titles if title):
                continue
            newest = max((os.path.getmtime(os.path.join(root, name))
                          for root, _dirs, files in os.walk(entry) for name in files), default=os.path.getmtime(entry))
            if newest < cutoff:
                found.append(entry)
    return found


def superseded_failures(now=None):
    """Failed jobs older than the cutoff whose disc was later ripped successfully"""
    from arm.models.job import Job
    cutoff = (now or _now()) - datetime.timedelta(days=settings.get("CLEANUP_LEFTOVER_DAYS"))
    result = []
    for job in Job.query.filter(Job.status == "fail").all():
        if job.stop_time and job.stop_time > cutoff:
            continue
        later = Job.query.filter(Job.status == "success", Job.job_id > job.job_id)
        if job.crc_id:
            match = later.filter(Job.crc_id == job.crc_id).first()
        elif job.label and confidence.clean_label(job.label):
            match = later.filter(Job.label == job.label).first()
        else:
            match = None
        if match:
            result.append((job, match))
    return result


def cleanup(now=None):
    from arm.models.config import Config
    from arm.models.track import Track
    from arm.ui import db
    removed = []
    for folder in leftover_folders(now):
        shutil.rmtree(folder, ignore_errors=True)
        removed.append(folder)
        logging.info(f"Removed leftover folder {folder}")
    deleted = []
    if settings.get("CLEANUP_FAILED_DUPLICATE_JOBS"):
        for job, match in superseded_failures(now):
            logging.info(f"Removing failed job {job.job_id} ({job.title}); job {match.job_id} ripped the same disc")
            Track.query.filter_by(job_id=job.job_id).delete()
            Config.query.filter_by(job_id=job.job_id).delete()
            db.session.delete(job)
            store.delete_job(job.job_id)
            deleted.append(job.job_id)
        db.session.commit()
    return removed, deleted


# ---------------------------------------------------------------- summary

def summary(now=None):
    """Text of the last 24 hours, or None when nothing happened"""
    from arm.models.job import Job
    from arm.ui.json_api import _is_identified
    now = now or _now()
    since = now - datetime.timedelta(days=1)
    jobs = Job.query.filter(Job.stop_time >= since).order_by(Job.job_id).all()
    attention = []
    for job in Job.query.filter(Job.start_time >= now - datetime.timedelta(days=7)).all():
        for item in store.load_job(job.job_id).get("attention", []):
            attention.append(f"{job.title_manual or job.title or job.label}: {item['message']}")
    if not jobs and not attention:
        return None
    done = [j for j in jobs if j.status == "success" and (_is_identified(j) or j.disctype == "music")]
    unknown = [j for j in jobs if j.status == "success" and j not in done]
    failed = [j for j in jobs if j.status == "fail"]

    def names(items):
        return ", ".join(f"{j.title_manual or j.title or j.label}" for j in items)

    lines = []
    if done:
        lines.append(f"Ripped {len(done)}: {names(done)}")
    if unknown:
        lines.append(f"Unidentified {len(unknown)}: {names(unknown)}")
    if failed:
        lines.append(f"Failed {len(failed)}: {names(failed)}")
    if attention:
        lines.append("Needs you: " + " | ".join(attention[:5]))
    return "\n".join(lines)


def send_summary(now=None):
    text = summary(now)
    if text:
        notify.send("ARM daily summary", text, priority="low", tags=["dvd"], click=notify.arm_url("/"))
    return text


# ---------------------------------------------------------------- runner

def tick(now=None, local=None):
    """
    :param now: database clock (job times)
    :param local: the user's wall clock for the daily times
    """
    from arm.models.job import Job
    now = now or _now()
    local = local or _local_now()
    watchdog(now)
    if settings.jellyfin_enabled() and settings.get("JELLYFIN_VERIFY_MATCH"):
        for job in Job.query.filter(Job.stop_time >= now - datetime.timedelta(days=1)).all():
            jellyfin.verify(job.job_id, job)
    if settings.get("DB_BACKUP_ENABLED") and due("backup", settings.get("DB_BACKUP_TIME"), local):
        backup(local)
        mark_done("backup", local)
    if settings.get("CLEANUP_ENABLED") and due("cleanup", settings.get("DB_BACKUP_TIME"), local) and \
            store.load_state().get("last_backup") == local.date().isoformat():
        # Only after today's backup, so anything removed from the database is in a backup
        cleanup(now)
        mark_done("cleanup", local)
    if settings.get("DAILY_SUMMARY_ENABLED") and due("summary", settings.get("DAILY_SUMMARY_TIME"), local):
        send_summary(now)
        mark_done("summary", local)


TASKS = {"tick": tick, "watchdog": watchdog, "backup": backup, "cleanup": cleanup, "summary": send_summary}


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    name = (argv or sys.argv[1:] or ["tick"])[0]
    if name not in TASKS:
        print(f"usage: python3 -m arm.automation.maintenance [{'|'.join(TASKS)}]")
        return 2
    from arm.ui import app
    started = time.time()
    with app.app_context():
        TASKS[name]()
    logging.info(f"maintenance {name} done in {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
