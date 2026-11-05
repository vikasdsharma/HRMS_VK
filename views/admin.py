from common import *  # noqa: F401,F403
from security import password_problem
from web import route

NUMERIC_SETTINGS = {"pf_wage_ceiling": (0, 10_000_000), "esi_wage_ceiling": (0, 10_000_000),
                    "pf_ee_rate": (0, 100), "eps_rate": (0, 100), "edli_rate": (0, 100),
                    "pf_admin_rate": (0, 100), "pf_admin_min": (0, 1_000_000),
                    "esi_ee_rate": (0, 100), "esi_er_rate": (0, 100),
                    "bonus_rate": (0, 100), "bonus_wage_ceiling": (0, 10_000_000), "bonus_eligibility_ceiling": (0, 10_000_000),
                    "gratuity_cap": (0, 100_000_000), "gratuity_min_years": (0, 50),
                    "leave_backdate_days": (0, 365), "comp_off_expiry_days": (1, 3650), "attendance_request_days": (0, 365)}
CHOICES = {"leave_year_start": {"1", "4"}, "leave_sandwich": {"0", "1"}}


# ---------- settings ----------
@route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    if request.method == "POST":
        current = get_settings()
        new, errors = {}, []
        for k in DEFAULT_SETTINGS:
            if k not in request.form:
                continue
            v = request.form[k].strip()
            if k in CHOICES and v not in CHOICES[k]:
                errors.append(f"{k.replace('_', ' ')} has an invalid value")
                continue
            if k == "leave_start_date" and v:
                try:
                    datetime.strptime(v, "%Y-%m-%d")
                except ValueError:
                    errors.append("Leave start date must look like 2026-01-01")
                    continue
            if k in NUMERIC_SETTINGS:
                lo, hi = NUMERIC_SETTINGS[k]
                try:
                    num = float(v)
                    if not lo <= num <= hi:
                        raise ValueError
                    v = ("%g" % num)
                except ValueError:
                    errors.append(f"{k.replace('_', ' ')} must be a number between {lo:g} and {hi:g}")
                    continue
            new[k] = v
        if errors:
            for e in errors:
                flash(e, "err")
            return render_template("settings.html")
        changed = []
        for k, v in new.items():
            if current.get(k) != v:
                changed.append(f"{k}: {current.get(k)!r} -> {v!r}")
                row = db.query(Setting).filter_by(key=k).first() or Setting(key=k)
                row.value = v
                db.add(row)
        db.commit()
        if changed:
            audit("settings.update", "settings", None, "; ".join(changed))
        flash("Settings saved", "ok")
        return redirect(url_for("settings"))
    return render_template("settings.html")


# ---------- organizations (platform admin) ----------
@route("/orgs")
@role_required("superadmin")
def orgs():
    with unscoped():
        rows = db.query(Organization).order_by(Organization.name).all()
        emp = dict(db.query(Employee.org_id, func.count(Employee.id)).group_by(Employee.org_id).all())
        usr = dict(db.query(User.org_id, func.count(User.id)).group_by(User.org_id).all())
    return render_template("orgs.html", rows=rows, emp=emp, usr=usr)


@route("/orgs/new", methods=["GET", "POST"])
@role_required("superadmin")
def org_new():
    f = request.form
    if request.method == "POST":
        name, uname, pw = f["name"].strip(), f["username"].strip(), f["password"]
        err = None
        with unscoped():
            if not name:
                err = "Organization name is required"
            elif db.query(Organization).filter(func.lower(Organization.name) == name.lower()).first():
                err = "An organization with this name already exists"
            elif not uname or db.query(User).filter_by(username=uname).first():
                err = "Choose a unique username for the organization admin"
            else:
                err = password_problem(pw, uname)
            if not err:
                org = Organization(name=name, address=f.get("address", "").strip() or None, is_active=True)
                db.add(org)
                db.flush()
                db.add(User(org_id=org.id, username=uname, pw_hash=generate_password_hash(pw), role="admin",
                            must_change_password=True))
                seed_org_defaults(org.id)
                db.add(Setting(org_id=org.id, key="company_name", value=name))
                db.add(Setting(org_id=org.id, key="company_address", value=org.address or ""))
                db.commit()
        if err:
            flash(err, "err")
        else:
            audit("org.create", "organization", org.id, f"{name}; first admin {uname}", org_id=org.id)
            session["org_id"] = org.id
            flash(f"Organization '{name}' created with admin '{uname}' (who must change the password at first login). "
                  "You are now working inside it.", "ok")
            return redirect(url_for("dashboard"))
    return render_template("org_form.html", o=None)


@route("/orgs/<int:oid>/switch", methods=["POST"])
@role_required("superadmin")
def org_switch(oid):
    org = db.get(Organization, oid)
    if not org:
        abort(404)
    session["org_id"] = org.id
    audit("org.enter", "organization", org.id, org.name, org_id=org.id)
    flash(f"Now working in: {org.name}", "ok")
    return redirect(url_for("dashboard"))


@route("/orgs/<int:oid>/edit", methods=["GET", "POST"])
@role_required("superadmin")
def org_edit(oid):
    org = db.get(Organization, oid) or abort(404)
    if request.method == "POST":
        name = request.form["name"].strip()
        dup = db.query(Organization).filter(func.lower(Organization.name) == name.lower(), Organization.id != oid).first()
        if not name or dup:
            flash("Name is empty or already used", "err")
        else:
            was_active = org.is_active
            org.name = name
            org.address = request.form.get("address", "").strip() or None
            org.is_active = "is_active" in request.form
            db.commit()
            audit("org.update", "organization", oid, f"{name}; active {was_active}->{org.is_active}", org_id=oid)
            flash("Organization updated", "ok")
            return redirect(url_for("orgs"))
    return render_template("org_form.html", o=org)


# ---------- users of the active organization ----------
@route("/users")
@admin_required
def users():
    rows = db.query(User).filter(User.org_id == current_org_id(), User.role != "employee").order_by(User.username).all()
    return render_template("users.html", rows=rows)


@route("/users/new", methods=["GET", "POST"])
@route("/users/<int:uid>/edit", methods=["GET", "POST"])
@admin_required
def user_form(uid=None):
    u = None
    if uid:
        u = db.get(User, uid)
        if not u or u.org_id != current_org_id() or u.role == "employee":
            abort(404)                                  # employee logins are managed under Employee portal access
    if request.method == "POST":
        f = request.form
        role = f.get("role", "user")
        pw = f.get("password", "")
        err = None
        if role not in ("admin", "user"):
            err = "Invalid role"
        elif not u:
            uname = f["username"].strip()
            if not uname or db.query(User).filter_by(username=uname).first():
                err = "Username is empty or already taken"
            else:
                err = password_problem(pw, uname)
        elif pw:
            err = password_problem(pw, u.username)
        if not err and u and u.id == g.user.id and ("is_active" not in f or role != "admin"):
            err = "You cannot disable or demote your own account"
        if err:
            flash(err, "err")
        else:
            new = not u
            if new:
                u = User(org_id=current_org_id(), username=uname)
                db.add(u)
            old = (u.role, u.is_active) if not new else None
            u.role = role
            u.is_active = "is_active" in f
            if pw:
                u.pw_hash = generate_password_hash(pw)
                u.must_change_password = True          # admin-set passwords are temporary
                u.failed_logins, u.locked_until = 0, None
            db.commit()
            audit("user.create" if new else "user.update", "user", u.id,
                  f"{u.username} role={u.role} active={u.is_active}"
                  + ("" if new else f" (was role={old[0]} active={old[1]})") + (" password reset" if pw and not new else ""))
            flash("User saved" + (" - they must change the password at first login" if pw else ""), "ok")
            return redirect(url_for("users"))
    return render_template("user_form.html", u=u)


@route("/users/<int:uid>/unlock", methods=["POST"])
@admin_required
def user_unlock(uid):
    u = db.get(User, uid)
    if not u or u.org_id != current_org_id():
        abort(404)
    u.failed_logins, u.locked_until = 0, None
    db.commit()
    audit("user.unlock", "user", u.id, u.username)
    flash(f"{u.username} unlocked", "ok")
    return redirect(url_for("users"))


# ---------- audit log ----------
@route("/audit")
@admin_required
def audit_log():
    page = max(request.args.get("page", 1, type=int), 1)
    per = 50
    q = db.query(AuditLog)
    scope = request.args.get("scope", "org")
    if g.user.role == "superadmin" and scope == "all":
        pass
    else:
        q = q.filter(AuditLog.org_id == current_org_id())
    action = request.args.get("action", "").strip()
    who = request.args.get("user", "").strip()
    if action:
        q = q.filter(AuditLog.action.like(action + "%"))
    if who:
        q = q.filter(AuditLog.username == who)
    total = q.count()
    rows = q.order_by(AuditLog.at.desc(), AuditLog.id.desc()).offset((page - 1) * per).limit(per).all()
    return render_template("audit.html", rows=rows, page=page, pages=max((total + per - 1) // per, 1), total=total,
                           action=action, who=who, scope=scope)


# ---------- API access ----------
@route("/api-tokens")
@admin_required
def api_tokens():
    rows = db.query(ApiToken).order_by(ApiToken.created_at.desc()).all()
    return render_template("api_tokens.html", rows=rows, new_token=None)


@route("/api-tokens/new", methods=["POST"])
@admin_required
def api_token_new():
    import api as api_mod
    name = (request.form.get("name") or "").strip()[:60]
    if not name:
        flash("Give the token a name (what will use it).", "err")
        return redirect(url_for("api_tokens"))
    scopes = ["read"] + [s for s in ("write", "pii") if s in request.form]
    raw, digest = api_mod.new_token()
    db.add(ApiToken(name=name, token_hash=digest, prefix=raw[:10], scopes=",".join(scopes), created_by=g.user.username))
    db.commit()
    audit("api_token.create", "api_token", name, f"scopes={','.join(scopes)}")
    rows = db.query(ApiToken).order_by(ApiToken.created_at.desc()).all()
    return render_template("api_tokens.html", rows=rows, new_token=raw, new_name=name)


@route("/api-tokens/<int:tid>/revoke", methods=["POST"])
@admin_required
def api_token_revoke(tid):
    t = db.get(ApiToken, tid) or abort(404)
    if not t.revoked_at:
        t.revoked_at = datetime.now()
        db.commit()
        audit("api_token.revoke", "api_token", t.name)
        flash(f"Token '{t.name}' revoked.", "ok")
    return redirect(url_for("api_tokens"))
