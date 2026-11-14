"""
Phone notifications through ntfy (JSON publishing, so titles can contain any characters),
with buttons that link back to ARM, plus signed links for actions taken from the phone.
"""
import hashlib
import hmac
import logging
import urllib.parse

import requests

import arm.config.config as cfg
from arm.automation import settings, store

PRIORITY = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5}


def arm_url(path=""):
    """ARM's address as the phone sees it"""
    base = settings.get("ARM_PHONE_URL") or str(cfg.arm_config.get("UI_BASE_URL") or "")
    if not base:
        port = cfg.arm_config.get("WEBSERVER_PORT", 8080)
        base = f"http://localhost:{port}"
    return base.rstrip("/") + path


def sign(job_id, action):
    """Token for a phone button: proves the link came from this ARM"""
    message = f"{int(job_id)}:{action}".encode()
    return hmac.new(store.secret().encode(), message, hashlib.sha256).hexdigest()[:24]


def check_signature(job_id, action, token):
    try:
        expected = sign(job_id, action)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(expected, str(token or ""))


def action_url(job_id, action):
    query = urllib.parse.urlencode({"job": int(job_id), "action": action, "token": sign(job_id, action)})
    return arm_url(f"/automation/act?{query}")


def view(label, url, clear=False):
    return {"action": "view", "label": label, "url": url, "clear": clear}


def http(label, url, method="POST", clear=True):
    return {"action": "http", "label": label, "url": url, "method": method, "clear": clear}


def send(title, message, priority="default", tags=(), click=None, actions=(), icon=None):
    """
    Publish to ntfy. Returns True when the server accepted it.
    Never raises: a notification problem must not stop a rip.
    """
    if not settings.ntfy_enabled():
        return False
    payload = {
        "topic": settings.get("NTFY_TOPIC"),
        "title": str(title)[:250],
        "message": str(message)[:4000],
        "priority": PRIORITY.get(priority, 3),
        "tags": list(tags),
    }
    if click:
        payload["click"] = click
    if actions:
        payload["actions"] = list(actions)[:3]
    if icon and str(icon).startswith("http"):
        payload["icon"] = icon
    headers = {}
    if settings.get("NTFY_TOKEN"):
        headers["Authorization"] = f"Bearer {settings.get('NTFY_TOKEN')}"
    try:
        response = requests.post(settings.get("NTFY_SERVER").rstrip("/"), json=payload, headers=headers, timeout=15)
        if response.status_code >= 400:
            logging.warning(f"ntfy refused the notification: HTTP {response.status_code}")
            return False
        return True
    except requests.RequestException as error:
        logging.warning(f"Couldn't send the ntfy notification: {error}")
        return False


def in_app(title, message):
    """Also show it in ARM's own notification list"""
    try:
        from arm.models.notifications import Notifications
        from arm.ui import db
        db.session.add(Notifications(title, message))
        db.session.commit()
    except Exception as error:  # noqa: BLE001 - notifications must never break a rip
        logging.warning(f"Couldn't save the notification in ARM: {error}")


def job_link(job_id):
    return arm_url(f"/jobdetail?job_id={int(job_id)}")
