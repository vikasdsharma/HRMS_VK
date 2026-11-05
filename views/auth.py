from common import *  # noqa: F401,F403
from security import password_problem, safe_next
from web import route

# Used to burn the same time for unknown usernames as for real ones (prevents user enumeration by timing)
_DUMMY_HASH = generate_password_hash("not-a-real-password")


@route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        uname = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        u = db.query(User).filter_by(username=uname).first()
        now = datetime.now()
        generic = "Invalid username or password"
        if u and u.locked_until and u.locked_until > now:
            mins = int((u.locked_until - now).total_seconds() // 60) + 1
            audit("auth.login_blocked", "user", u.id, f"locked, {mins} min left", org_id=u.org_id)
            flash(f"Too many failed attempts. This account is locked for about {mins} more minute(s).", "err")
            return render_template("login.html")
        if u and check_password_hash(u.pw_hash, pw):
            if not u.is_active:
                audit("auth.login_disabled", "user", u.id, org_id=u.org_id)
                flash("This account is disabled", "err")
                return render_template("login.html")
            if u.role != "superadmin" and not (u.organization and u.organization.is_active):
                audit("auth.login_org_inactive", "user", u.id, org_id=u.org_id)
                flash("Your organization is inactive", "err")
                return render_template("login.html")
            if u.role == "employee":
                emp = db.get(Employee, u.employee_id) if u.employee_id else None
                if not emp or emp.status != "Active":
                    audit("auth.login_employee_inactive", "user", u.id, org_id=u.org_id)
                    flash("Your portal access is not active. Please contact HR.", "err")
                    return render_template("login.html")
            session.clear()
            session.permanent = True
            session["uid"] = u.id
            u.failed_logins = 0
            u.locked_until = None
            u.last_login_at = now
            db.commit()
            g.user = u
            audit("auth.login", "user", u.id, org_id=u.org_id)
            return redirect(safe_next(request.args.get("next", ""), url_for("me_home" if u.role == "employee" else "dashboard")))
        if u:
            u.failed_logins = (u.failed_logins or 0) + 1
            if u.failed_logins >= config.LOGIN_MAX_FAILS:
                u.locked_until = now + timedelta(minutes=config.LOGIN_LOCK_MINUTES)
                u.failed_logins = 0
                audit("auth.locked", "user", u.id, f"after {config.LOGIN_MAX_FAILS} failures", org_id=u.org_id, commit=False)
            db.commit()
            audit("auth.login_failed", "user", u.id, org_id=u.org_id)
        else:
            check_password_hash(_DUMMY_HASH, pw)
            audit("auth.login_failed", "user", None, f"unknown username length {len(uname)}")
        flash(generic, "err")
    return render_template("login.html")


@route("/logout", methods=["POST"])
def logout():
    audit("auth.logout", "user", g.user.id if g.user else None)
    session.clear()
    return redirect(url_for("login"))


@route("/password", methods=["GET", "POST"])
def change_password():
    if request.method == "POST":
        u = db.get(User, session["uid"])
        new = request.form["new"]
        problem = None
        if not check_password_hash(u.pw_hash, request.form["old"]):
            problem = "Current password is wrong"
        else:
            problem = password_problem(new, u.username, request.form["old"])
            if not problem and new != request.form.get("confirm", new):
                problem = "The two new passwords do not match"
        if problem:
            flash(problem, "err")
        else:
            u.pw_hash = generate_password_hash(new)
            u.must_change_password = False
            u.password_changed_at = datetime.now()
            db.commit()
            audit("auth.password_changed", "user", u.id)
            flash("Password changed", "ok")
            return redirect(url_for("dashboard"))
    return render_template("password.html")
