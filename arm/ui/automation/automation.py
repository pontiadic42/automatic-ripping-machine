"""
Automation pages:
- /automation/act       buttons in phone notifications (signed links, no login needed)
- /automation/settings  Settings > Automation
- /automation/frame     still frames from finished files (to tell language versions apart)
"""
import json
import os
import subprocess

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, session, url_for
from flask_login import login_required

import arm.config.config as cfg
from arm.automation import notify, settings, store
from arm.models.job import Job
from arm.ui import app, csrf, db

route_automation = Blueprint('route_automation', __name__, template_folder='templates', static_folder='../static')

ACTIONS = {"confirm": "Keep this title"}


def confirm_title(job):
    """The user says the automatic title is right: make it a manual value, which ends ARM's wait"""
    job.title_manual = job.title
    job.year_manual = job.year
    job.video_type_manual = job.video_type
    job.imdb_id_manual = job.imdb_id
    job.poster_url_manual = job.poster_url
    job.hasnicetitle = True
    db.session.commit()
    data = store.load_job(job.job_id)
    confidence = dict(data.get("confidence") or {}, confirmed=True)
    store.update_job(job.job_id, confidence=confidence)
    store.clear_attention(job.job_id, "confidence")


@route_automation.route('/automation/act', methods=['GET', 'POST'])
@csrf.exempt
def act():
    """Phone buttons. GET shows a page with a button (for browsers), POST does it."""
    job_id = request.values.get("job", type=int)
    action = request.values.get("action", "")
    token = request.values.get("token", "")
    if job_id is None or action not in ACTIONS or not notify.check_signature(job_id, action, token):
        abort(403)
    job = Job.query.get(job_id)
    if job is None:
        abort(404)
    if request.method == "GET":
        return render_template("automation_act.html", job=job, action=action, label=ACTIONS[action],
                               token=token, done=False)
    if action == "confirm":
        confirm_title(job)
        app.logger.info(f"Job {job_id}: title confirmed from a notification")
    if request.headers.get("Accept", "").startswith("text/html") or request.form.get("from_page"):
        return render_template("automation_act.html", job=job, action=action, label=ACTIONS[action],
                               token=token, done=True)
    return {"success": True, "job": job_id, "action": action}


def _form_value(setting):
    if setting.kind == "bool":
        return setting.key in request.form
    return request.form.get(setting.key, "")


@route_automation.route('/automation/settings', methods=['GET', 'POST'])
@login_required
def automation_settings():
    session["page_title"] = "Automation"
    if request.method == "POST":
        user_config = cfg._load_config(cfg.arm_config_path) or {}
        changed = []
        for setting in settings.SCHEMA:
            raw = _form_value(setting)
            if setting.kind == "secret" and raw == "":
                continue  # empty = keep the stored secret
            value = settings.coerce(setting, raw)
            if user_config.get(setting.key) != value:
                changed.append(setting.label)
            user_config[setting.key] = value
        with open(os.path.join(cfg.arm_config["INSTALLPATH"], "arm/ui/comments.json")) as handle:
            comments = json.load(handle)
        full = dict(cfg.template_config)
        full.update(user_config)
        cfg.write_config_file(cfg.arm_config_path, cfg._render_config(full, comments))
        cfg.arm_config.update({s.key: user_config[s.key] for s in settings.SCHEMA if s.key in user_config})
        flash(f"Saved {len(changed)} change{'s' if len(changed) != 1 else ''}. New discs use them right away."
              if changed else "No changes.", "success")
        return redirect(url_for("route_automation.automation_settings"))
    groups = []
    for group, title in settings.GROUPS:
        items = []
        for setting in settings.SCHEMA:
            if setting.group != group:
                continue
            value = settings.get(setting.key)
            items.append({"s": setting, "value": "" if setting.kind == "secret" else value,
                          "is_set": bool(value) if setting.kind == "secret" else None})
        groups.append({"id": group, "title": title, "fields": items})
    status = {"ntfy": settings.ntfy_enabled(), "jellyfin": settings.jellyfin_enabled(),
              "last": store.load_state()}
    return render_template("automation_settings.html", groups=groups, status=status)


@route_automation.route('/automation/test_ntfy', methods=['POST'])
@login_required
def test_ntfy():
    ok = notify.send("ARM test notification", "Phone notifications from ARM work.", tags=["dvd"],
                     click=notify.arm_url("/"))
    flash("Test notification sent." if ok else "Couldn't send it. Check the server and topic.",
          "success" if ok else "danger")
    return redirect(url_for("route_automation.automation_settings"))


@route_automation.route('/automation/frame')
@login_required
def frame():
    """A JPEG still from one of a job's finished files, at a given second"""
    job_id = request.args.get("job", type=int)
    index = request.args.get("file", type=int)
    at = max(0, request.args.get("at", default=60, type=int))
    files = store.load_job(job_id).get("files") or [] if job_id is not None else []
    if index is None or not 0 <= index < len(files):
        abort(404)
    path = files[index]["file"]
    completed = os.path.normpath(str(cfg.arm_config["COMPLETED_PATH"]))
    if not os.path.normpath(path).startswith(completed + os.sep) or not os.path.isfile(path):
        abort(404)
    try:
        image = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", path, "-frames:v", "1",
                                "-vf", "scale=480:-2", "-f", "image2", "-c:v", "mjpeg", "pipe:1"],
                               capture_output=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        abort(500)
    if not image:
        abort(404)
    return Response(image, mimetype="image/jpeg", headers={"Cache-Control": "max-age=86400"})
