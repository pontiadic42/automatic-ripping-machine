"""
Automation pages:
- /automation/act       buttons in phone notifications (signed links, no login needed)
- /automation/settings  Settings > Automation
- /automation/frame     still frames from finished files (to tell language versions apart)
"""
import json
import os
import shutil
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


def _deletable_folder(job):
    """The job's output folder, if it's safe to delete: inside COMPLETED_PATH/<type>/ and not used by another job"""
    if not job.path:
        return None
    completed = os.path.realpath(str(cfg.arm_config["COMPLETED_PATH"]))
    folder = os.path.realpath(job.path)
    relative = os.path.relpath(folder, completed)
    if relative.startswith("..") or len(relative.split(os.sep)) < 2 or not os.path.isdir(folder):
        return None
    others = Job.query.filter(Job.path == job.path, Job.job_id != job.job_id, Job.status == "success").count()
    return None if others else folder


@route_automation.route('/automation/job_action', methods=['POST'])
@login_required
def job_action():
    """
    Buttons on the home page banner and the job page:
    accept (keep ARM's guessed title), cancel (stop a running job), delete_files (remove a
    finished job's output folder), dismiss (hide a banner item).
    """
    job_id = request.form.get("job", type=int)
    action = request.form.get("action", "")
    job = Job.query.get(job_id) if job_id is not None else None
    if job is None:
        return {"success": False, "message": "Job not found"}, 404
    name = job.title_manual or job.title or job.label or f"job {job_id}"
    if action == "accept":
        if not job.finished:
            confirm_title(job)
            message = f"Keeping {name}. ARM starts ripping when its wait ends."
        else:
            store.clear_attention(job_id, "confidence")
            message = f"Kept {name}."
    elif action == "dismiss":
        store.clear_attention(job_id, request.form.get("kind") or "confidence")
        message = "Dismissed."
    elif action == "cancel":
        if job.finished:
            return {"success": False, "message": "This job has already finished."}, 400
        from arm.ui import json_api
        result = json_api.abandon_job(str(job_id))
        if not result.get("success"):
            return {"success": False, "message": result.get("Error") or "Couldn't stop the job."}, 500
        for item in store.load_job(job_id).get("attention") or []:
            store.clear_attention(job_id, item.get("kind"))
        message = f"Stopped {name} and ejected the disc."
    elif action == "delete_files":
        if not job.finished:
            return {"success": False, "message": "Stop the job first."}, 400
        folder = _deletable_folder(job)
        if folder is None:
            return {"success": False, "message": "There's no output folder ARM can safely delete for this job."}, 400
        shutil.rmtree(folder)
        app.logger.info(f"Job {job_id}: deleted its output folder {folder} from the web UI")
        store.update_job(job_id, files_deleted=folder, attention=[])
        from arm.automation import jellyfin
        jellyfin.announce(None, folder)
        message = f"Deleted {folder}."
    else:
        return {"success": False, "message": "Unknown action"}, 400
    return {"success": True, "message": message}


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
