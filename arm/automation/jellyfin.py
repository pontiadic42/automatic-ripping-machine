"""
Tell Jellyfin about new or moved folders right away, then (from the maintenance run,
once Jellyfin had time to scan) check it matched the same movie or show ARM ripped.
"""
import datetime
import logging
import os

import requests

import arm.config.config as cfg
from arm.automation import notify, settings, store


def _headers():
    return {"X-Emby-Token": settings.get("JELLYFIN_API_KEY")}


def folder_map():
    result = {}
    for part in settings.get("JELLYFIN_FOLDER_MAP").split(";"):
        arm_name, _, nas_name = part.partition("=")
        if arm_name.strip() and nas_name.strip():
            result[arm_name.strip()] = nas_name.strip()
    return result


def jellyfin_path(arm_path):
    """/home/arm/media/completed/movies/X -> /media/Movies/X, or None if outside the library folders"""
    completed = os.path.normpath(str(cfg.arm_config["COMPLETED_PATH"]))
    path = os.path.normpath(str(arm_path or ""))
    if not path.startswith(completed + os.sep):
        return None
    sub, _, rest = path[len(completed) + 1:].partition(os.sep)
    nas = folder_map().get(sub)
    if not nas:
        return None
    return "/".join(part for part in (settings.get("JELLYFIN_MEDIA_ROOT").rstrip("/"), nas, rest) if part)


def announce(job, *paths):
    """Ask Jellyfin to scan these folders now. Returns True if Jellyfin accepted."""
    if not settings.jellyfin_enabled():
        return False
    updates = []
    for path in paths:
        mapped = jellyfin_path(path)
        if mapped:
            updates.append({"Path": mapped, "UpdateType": "Created" if os.path.exists(path) else "Deleted"})
    if not updates:
        return False
    try:
        response = requests.post(settings.get("JELLYFIN_URL").rstrip("/") + "/Library/Media/Updated",
                                 json={"Updates": updates}, headers=_headers(), timeout=20)
        ok = response.status_code < 300
        if not ok:
            logging.warning(f"Jellyfin didn't accept the update: HTTP {response.status_code}")
    except requests.RequestException as error:
        logging.warning(f"Couldn't reach Jellyfin: {error}")
        ok = False
    if ok and job is not None and settings.get("JELLYFIN_VERIFY_MATCH"):
        store.update_job(job.job_id, jellyfin={"status": "pending", "path": jellyfin_path(paths[0]),
                                               "since": datetime.datetime.now().isoformat()})
    return ok


def find_item(path):
    """The Jellyfin movie or series whose folder is path"""
    try:
        response = requests.get(settings.get("JELLYFIN_URL").rstrip("/") + "/Items", headers=_headers(), timeout=30,
                                params={"Recursive": "true", "IncludeItemTypes": "Movie,Series",
                                        "Fields": "ProviderIds,Path", "Limit": 50,
                                        "SearchTerm": os.path.basename(path).split(" (")[0][:60]})
        response.raise_for_status()
    except (requests.RequestException, ValueError) as error:
        logging.info(f"Jellyfin lookup failed: {error}")
        return None
    for item in response.json().get("Items", []):
        item_path = str(item.get("Path") or "")
        if item_path == path or item_path.startswith(path.rstrip("/") + "/"):
            return item
    return None


def verify(job_id, job):
    """Called from maintenance: compare Jellyfin's match with ARM's"""
    data = store.load_job(job_id).get("jellyfin") or {}
    if data.get("status") != "pending" or not settings.jellyfin_enabled():
        return None
    since = datetime.datetime.fromisoformat(data["since"])
    age = (datetime.datetime.now() - since).total_seconds()
    if age < 120:
        return None
    item = find_item(data["path"])
    if item is None:
        if age > 3600:
            store.update_job(job_id, jellyfin=dict(data, status="not found"))
            store.add_attention(job_id, "jellyfin", f"Jellyfin hasn't picked up {data['path']} after an hour.")
        return None
    ids = {key.lower(): str(value) for key, value in (item.get("ProviderIds") or {}).items()}
    expected = str(job.imdb_id or "")
    if expected.startswith("tt") and ids.get("imdb") and ids["imdb"] != expected:
        message = (f"Jellyfin matched {data['path']} to \"{item.get('Name')}\" ({ids['imdb']}), "
                   f"but ARM ripped {job.title} ({expected}). Fix it in Jellyfin with Identify.")
        store.update_job(job_id, jellyfin=dict(data, status="mismatch", item=item.get("Name")))
        store.add_attention(job_id, "jellyfin", message)
        notify.send("Jellyfin picked a different movie", message, tags=["warning"], click=notify.job_link(job_id))
        return False
    store.update_job(job_id, jellyfin=dict(data, status="matched", item=item.get("Name")))
    store.clear_attention(job_id, "jellyfin")
    return True
