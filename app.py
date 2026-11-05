"""HRMS web application: app setup, request pipeline (auth, organization scoping, security headers,
logging, error pages). The actual pages live in views/*.py."""
import logging
import time
import uuid
from datetime import date, datetime, timedelta

from flask import Flask, flash, g, jsonify, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFError, CSRFProtect
from sqlalchemy import text
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

import config
import logging_setup
import pdfs
import web
from models import (AttendanceRequest, Category, Employee, LeaveApplication, Notification, Organization, User,
                    db, get_settings, init_db, set_org, unscoped)

logging_setup.setup()
log = logging.getLogger("hrms")
access_log = logging.getLogger("hrms.access")

# Public (no login) and "no organization needed yet" endpoints
PUBLIC = {"login", "static", "healthz"}
NO_ORG_OK = PUBLIC | {"logout", "orgs", "org_new", "org_edit", "org_switch", "change_password"}
FORCED_PW_OK = {"change_password", "logout", "static", "healthz"}

csrf = CSRFProtect()


def create_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=config.SECRET_KEY,
        MAX_CONTENT_LENGTH=config.MAX_UPLOAD_MB * 1024 * 1024,
        SESSION_COOKIE_NAME="hrms_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.COOKIE_SECURE,
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=config.SESSION_MINUTES),
        WTF_CSRF_TIME_LIMIT=None,                      # token lives as long as the (idle-limited) session
    )
    if config.TRUST_PROXY:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    csrf.init_app(app)

    init_db()

    import views.auth, views.dashboard, views.employees, views.categories  # noqa: F401,E401
    import views.attendance, views.payroll, views.compliance, views.admin, views.reports  # noqa: F401,E401
    import views.leave, views.me  # noqa: F401,E401
    web.register(app)
    from api import bp as api_bp
    app.register_blueprint(api_bp)
    csrf.exempt(api_bp)                     # bearer-token API: no cookies, so no CSRF

    @app.teardown_appcontext
    def _shutdown(exc=None):
        set_org(None)
        db.remove()

    @app.before_request
    def _begin():
        g.t0 = time.perf_counter()
        g.rid = uuid.uuid4().hex[:10]
        set_org(None)
        g.user = g.org = g.employee = None
        if request.path.startswith("/api/"):
            return                           # the API authenticates itself (bearer token) in api.py
        if request.endpoint in PUBLIC:
            return
        user = db.get(User, session["uid"]) if "uid" in session else None
        if not user or not user.is_active:
            session.clear()
            return redirect(url_for("login", next=request.path))
        g.user = user
        if request.endpoint == "logout":
            return
        if user.must_change_password and request.endpoint not in FORCED_PW_OK:
            flash("Please set a new password before continuing.", "err")
            return redirect(url_for("change_password"))
        if user.role == "superadmin":
            oid = session.get("org_id")
            org = db.get(Organization, oid) if oid else None
            if not org:
                session.pop("org_id", None)
                orgs = db.query(Organization).filter_by(is_active=True).all()
                if len(orgs) == 1:                       # only one choice - enter it
                    org = orgs[0]
                    session["org_id"] = org.id
        else:
            org = db.get(Organization, user.org_id) if user.org_id else None
            if not org or not org.is_active:
                session.clear()
                flash("Your organization is inactive or missing. Contact the platform administrator.", "err")
                return redirect(url_for("login"))
        if org:
            g.org = org
            set_org(org.id)
        if org and user.role == "employee":           # self-service login: tied to one active employee, own pages only
            emp = db.get(Employee, user.employee_id) if user.employee_id else None
            if not emp or emp.status != "Active":
                session.clear()
                flash("Your portal access is no longer active. Please contact HR.", "err")
                return redirect(url_for("login"))
            g.employee = emp
            ep = request.endpoint or ""
            if not (ep.startswith("me_") or ep in FORCED_PW_OK):
                return redirect(url_for("me_home"))
        elif not org and request.endpoint not in NO_ORG_OK:
            flash("Select the organization you want to work in.", "err")
            return redirect(url_for("orgs"))

    @app.context_processor
    def _ctx():
        user = getattr(g, "user", None)
        org = getattr(g, "org", None)
        orgs = []
        if user and user.role == "superadmin":
            with unscoped():
                orgs = db.query(Organization).order_by(Organization.name).all()
        badges = {}
        if org and user and user.role in ("superadmin", "admin"):
            badges = dict(leave=db.query(LeaveApplication).filter_by(status="pending").count(),
                          requests=db.query(AttendanceRequest).filter_by(status="pending").count())
        unread = 0
        if org and user and user.role == "employee" and getattr(g, "employee", None):
            unread = db.query(Notification).filter_by(emp_id=g.employee.id, read_at=None).count()
        return dict(badges=badges, unread=unread, emp_self=getattr(g, "employee", None),
                    S=get_settings(), today=date.today(), today_dt=datetime.now(), timedelta=timedelta, cur_month=date.today().strftime("%Y-%m"),
                    month_label=pdfs.month_label, me=user, org=org, all_orgs=orgs,
                    is_admin=bool(user and user.role in ("superadmin", "admin")),
                    cats_all=db.query(Category).order_by(Category.name).all() if org else [])

    @app.after_request
    def _finish(resp):
        # --- security headers
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "frame-ancestors 'none'; form-action 'self'; base-uri 'self'")
        if config.COOKIE_SECURE:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.endpoint != "static":
            resp.headers["Cache-Control"] = "no-store"          # personal data must not sit in browser caches
        resp.headers["X-Request-ID"] = getattr(g, "rid", "-")
        # --- access log (path only: never the query string, which can hold search terms)
        if request.endpoint not in ("static", "healthz"):
            u = getattr(g, "user", None)
            o = getattr(g, "org", None)
            ms = (time.perf_counter() - getattr(g, "t0", time.perf_counter())) * 1000
            access_log.info("%s %s %s %s %.0fms user=%s org=%s ip=%s rid=%s", request.method, request.path,
                            resp.status_code, resp.calculate_content_length() or 0, ms,
                            u.username if u else "-", o.id if o else "-", request.remote_addr, getattr(g, "rid", "-"))
        return resp

    # ---- error pages
    def _error(code, title, message):
        if request.path.startswith("/api/"):
            return jsonify(error=dict(code=title.lower().replace(" ", "_"), message=message)), code
        return render_template("error.html", code=code, title=title, message=message, rid=getattr(g, "rid", "-")), code

    @app.errorhandler(CSRFError)
    def _csrf(e):
        log.warning("CSRF failure on %s: %s", request.path, e.description)
        return _error(400, "Session expired", "This form is no longer valid (the page was open too long, or "
                                              "the request did not come from this site). Go back, reload and try again.")

    @app.errorhandler(400)
    def _e400(e):
        return _error(400, "Bad request", getattr(e, "description", "The request could not be understood."))

    @app.errorhandler(403)
    def _e403(e):
        return _error(403, "Not allowed", "You do not have permission to do that.")

    @app.errorhandler(404)
    def _e404(e):
        return _error(404, "Not found", "That page or record does not exist (or belongs to another organization).")

    @app.errorhandler(413)
    def _e413(e):
        return _error(413, "File too large", f"Uploads are limited to {config.MAX_UPLOAD_MB} MB.")

    @app.errorhandler(Exception)
    def _e500(e):
        if isinstance(e, HTTPException):
            return _error(e.code or 500, e.name, e.description)
        db.rollback()
        log.exception("Unhandled error rid=%s path=%s", getattr(g, "rid", "-"), request.path)
        return _error(500, "Something went wrong", "The problem has been logged. If it keeps happening, give "
                                                   "the reference below to your administrator.")

    @app.route("/healthz")
    def healthz():
        try:
            db.execute(text("SELECT 1"))
            return {"status": "ok"}, 200
        except Exception:
            log.exception("health check failed")
            return {"status": "database unavailable"}, 503

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT, debug=False)
