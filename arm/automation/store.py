"""
Small JSON records next to the ARM database: one file per job (confidence, title analysis,
checks, things that need attention) plus shared state (maintenance run times, TV episode
progress, the secret for notification buttons).

Kept outside the SQL database so no schema migration is needed, and on the same volume
as the database so it survives container rebuilds.
"""
import json
import logging
import os
import secrets
import tempfile
import threading

import arm.config.config as cfg

_lock = threading.Lock()


def base_dir():
    path = os.path.join(os.path.dirname(str(cfg.arm_config["DBFILE"])), "automation")
    if not os.path.isdir(os.path.join(path, "jobs")):
        os.makedirs(os.path.join(path, "jobs"), exist_ok=True)
        _share(path, 0o775)
        _share(os.path.join(path, "jobs"), 0o775)
    return path


def _share(path, mode):
    """
    The ripper runs as root, the web UI as the arm user: give files the database's owner
    so both can read and update them.
    """
    try:
        os.chmod(path, mode)
        if os.geteuid() == 0:
            owner = os.stat(str(cfg.arm_config["DBFILE"]))
            os.chown(path, owner.st_uid, owner.st_gid)
    except OSError as error:
        logging.debug(f"Couldn't set the owner of {path}: {error}")


def _read(path, default):
    try:
        with open(path) as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as error:
        logging.warning(f"Couldn't read {path}: {error}")
        return default


def _write(path, data):
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2, sort_keys=True, default=str)
        _share(tmp, 0o664)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _job_path(job_id):
    return os.path.join(base_dir(), "jobs", f"{int(job_id)}.json")


def load_job(job_id):
    return _read(_job_path(job_id), {})


def update_job(job_id, **fields):
    """Merge fields into the job's record and return it"""
    with _lock:
        data = load_job(job_id)
        data.update(fields)
        _write(_job_path(job_id), data)
        return data


def add_attention(job_id, kind, message, level="warn"):
    """Record something that needs the user; shown on the home page until resolved"""
    with _lock:
        data = load_job(job_id)
        items = [item for item in data.get("attention", []) if item.get("kind") != kind]
        items.append({"kind": kind, "message": message, "level": level})
        data["attention"] = items
        _write(_job_path(job_id), data)


def clear_attention(job_id, kind):
    with _lock:
        data = load_job(job_id)
        items = [item for item in data.get("attention", []) if item.get("kind") != kind]
        if len(items) != len(data.get("attention", [])):
            data["attention"] = items
            _write(_job_path(job_id), data)


def delete_job(job_id):
    try:
        os.remove(_job_path(job_id))
    except FileNotFoundError:
        pass


def load_state():
    return _read(os.path.join(base_dir(), "state.json"), {})


def update_state(**fields):
    with _lock:
        path = os.path.join(base_dir(), "state.json")
        data = _read(path, {})
        data.update(fields)
        _write(path, data)
        return data


def secret():
    """Random key for signing notification buttons; created on first use"""
    path = os.path.join(base_dir(), "secret")
    try:
        with open(path) as handle:
            value = handle.read().strip()
        if value:
            return value
    except FileNotFoundError:
        pass
    value = secrets.token_hex(32)
    try:
        # O_EXCL: if the UI and a ripper race, only one of them creates the file
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        with open(path) as handle:
            return handle.read().strip()
    with os.fdopen(fd, "w") as handle:
        handle.write(value)
    _share(path, 0o640)
    return value
