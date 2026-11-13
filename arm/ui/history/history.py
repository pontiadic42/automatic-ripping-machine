"""
ARM route blueprint for history pages
Covers
- history [GET]
"""

import os
from flask_login import LoginManager, login_required  # noqa: F401
from flask import render_template, request, Blueprint, session

import arm.ui.utils as ui_utils
from arm.ui import app, db
from arm.models.job import Job
import arm.config.config as cfg

route_history = Blueprint('route_history', __name__,
                          template_folder='templates',
                          static_folder='../static')

# This attaches the armui_cfg globally to let the users use any bootswatch skin from cdn
armui_cfg = ui_utils.arm_db_cfg()


@route_history.route('/history')
@login_required
def history():
    """
    Smaller much simpler output of previously run jobs

    """
    # regenerate the armui_cfg we don't want old settings
    armui_cfg = ui_utils.arm_db_cfg()
    page = request.args.get('page', 1, type=int)
    if os.path.isfile(cfg.arm_config['DBFILE']):
        # after roughly 175 entries firefox readermode will break
        # jobs = Job.query.filter_by().limit(175).all()
        jobs = Job.query.order_by(db.desc(Job.job_id)).paginate(page=page,
                                                                max_per_page=int(
                                                                    armui_cfg.database_limit),
                                                                error_out=False)
    else:
        app.logger.error('ERROR: /history database file doesnt exist')
        jobs = {}
    app.logger.debug(f"Date format - {cfg.arm_config['DATE_FORMAT']}")

    session["page_title"] = "History"

    return render_template('history.html', jobs=jobs.items,
                           date_format=cfg.arm_config['DATE_FORMAT'], pages=jobs)


LIBRARY_PAGE_SIZE = 48
LIBRARY_FILTERS = ("all", "movie", "series", "music", "unidentified", "failed")


def _library_filter(query, name):
    """Narrow a Job query to one of the library filter chips"""
    unidentified = db.and_(Job.status == "success", Job.disctype != "music", Job.title_manual.is_(None),
                           db.or_(Job.hasnicetitle.isnot(True), Job.video_type == "unknown",
                                  Job.title.is_(None), db.func.lower(Job.title).in_(["none", "not identified"])))
    if name == "movie":
        return query.filter(Job.video_type == "movie", Job.status != "fail")
    if name == "series":
        return query.filter(Job.video_type == "series", Job.status != "fail")
    if name == "music":
        return query.filter(Job.disctype == "music")
    if name == "unidentified":
        return query.filter(unidentified)
    if name == "failed":
        return query.filter(Job.status == "fail")
    return query


@route_history.route('/library')
@login_required
def library():
    """
    Poster grid of every job, with filters and a title search
    """
    from arm.ui.json_api import _job_summary
    selected = request.args.get('filter', 'all')
    if selected not in LIBRARY_FILTERS:
        selected = 'all'
    search = request.args.get('q', '').strip()[:100]
    page = request.args.get('page', 1, type=int)

    base = Job.query
    if search:
        like = f"%{search}%"
        base = base.filter(db.or_(Job.title.ilike(like), Job.label.ilike(like)))
    counts = {name: _library_filter(base, name).count() for name in LIBRARY_FILTERS}
    jobs = _library_filter(base, selected).order_by(db.desc(Job.job_id)) \
        .paginate(page=page, per_page=LIBRARY_PAGE_SIZE, error_out=False)

    session["page_title"] = "Library"
    return render_template('library.html', jobs=[_job_summary(job) for job in jobs.items], pages=jobs,
                           counts=counts, selected=selected, search=search,
                           filter_names={"all": "All", "movie": "Movies", "series": "TV", "music": "Music",
                                         "unidentified": "Unidentified", "failed": "Failed"})
