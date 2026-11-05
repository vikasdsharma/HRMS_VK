"""Admin side of leave & attendance: balances, applications, policies, holidays, accrual, reports,
per-employee yearly calendar, attendance corrections and employee portal logins."""
import csv
import secrets

from common import *  # noqa: F401,F403
from leave import LeaveError
from security import password_problem
from web import route

ACCRUALS = [("yearly", "Yearly grant"), ("monthly", "Monthly credit"), ("per_days_worked", "1 day per N days worked"),
            ("manual", "Manual only")]
CARRY = [("none", "None (lapses)"), ("limit", "Up to a limit"), ("all", "All")]


def _date(raw, default=None):
    try:
        return datetime.strptime((raw or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return default


def _emp(eid):
    return db.get(Employee, eid) or abort(404)


def _num(raw, default=None, lo=0.0, hi=100000.0):
    raw = (raw or "").strip()
    if raw == "":
        return default
    v = float(raw)
    if not lo <= v <= hi:
        raise ValueError
    return v


# ---------------------------------------------------------------- hub: balances of everyone
@route("/leave")
@admin_required
def leave_home():
    q = request.args.get("q", "").strip().lower()
    lts, rows = leave.balances_matrix()
    if q:
        rows = [r for r in rows if q in r["emp"].name.lower() or q in r["emp"].emp_code.lower()]
    pending = db.query(LeaveApplication).filter_by(status="pending").count()
    reqs = db.query(AttendanceRequest).filter_by(status="pending").count()
    return render_template("leave_home.html", lts=lts, rows=rows, q=q, pending=pending, reqs=reqs,
                           go_live=leave.go_live(), year=leave.year_label(leave.leave_year_of(date.today())))


@route("/leave/balances.xlsx")
@admin_required
def leave_balances_xlsx():
    from openpyxl import Workbook
    lts, rows = leave.balances_matrix()
    wb = Workbook()
    ws = wb.active
    ws.title = "Leave balances"
    ws.append(["Emp Code", "Name", *[t.name for t in lts], "Pending (days)"])
    for r in rows:
        ws.append([r["emp"].emp_code, r["emp"].name, *[r["bal"][t.id] for t in lts], r["pending"]])
    audit("leave.export", "report", "balances", f"{len(rows)} employees")
    return xlsx_response(xl.to_buffer(wb), f"leave_balances_{date.today():%Y%m%d}.xlsx")


# ---------------------------------------------------------------- one employee
@route("/leave/employee/<int:eid>")
@admin_required
def leave_employee(eid):
    e = _emp(eid)
    cx = leave.Ctx()
    lts = [t for t in cx.types.values() if t.active]
    bal = leave.balances_of(e.id)
    info = []
    for t in lts:
        rule = cx.rule(e, t.id)
        info.append(dict(lt=t, balance=bal.get(t.id, 0.0), pending=leave.pending_days(e.id, t.id), rule=rule))
    type_filter = request.args.get("type", type=int)
    q = db.query(LeaveLedger).filter_by(emp_id=e.id)
    if type_filter:
        q = q.filter_by(leave_type_id=type_filter)
    ledger = q.order_by(LeaveLedger.on_date.desc(), LeaveLedger.id.desc()).limit(150).all()
    apps = db.query(LeaveApplication).filter_by(emp_id=e.id).order_by(LeaveApplication.from_date.desc()).limit(30).all()
    grants = db.query(CompOffGrant).filter_by(emp_id=e.id).order_by(CompOffGrant.granted_at.desc()).limit(10).all()
    return render_template("leave_employee.html", e=e, info=info, lts=lts, ledger=ledger, apps=apps, grants=grants,
                           type_filter=type_filter, policy=cx.policy(e),
                           default_expiry=get_settings()["comp_off_expiry_days"], year=date.today().year)


@route("/leave/employee/<int:eid>/adjust", methods=["POST"])
@admin_required
def leave_adjust(eid):
    e = _emp(eid)
    lt = db.get(LeaveType, request.form.get("leave_type_id", type=int) or 0) or abort(404)
    try:
        days = float(request.form.get("days") or 0)
        leave.adjust(e, lt, days, request.form.get("reason", ""), g.user.username,
                     kind="opening" if request.form.get("kind") == "opening" else "adjustment",
                     on=_date(request.form.get("on_date"), date.today()))
    except (ValueError, LeaveError) as ex:
        flash(str(ex) if isinstance(ex, LeaveError) else "Enter the days as a number.", "err")
    else:
        audit("leave.adjust", "employee", e.id, f"{lt.code} {days:+g} day(s): {request.form.get('reason', '')[:120]}")
        flash(f"{lt.name} balance changed by {days:+g} day(s).", "ok")
    return redirect(url_for("leave_employee", eid=eid))


@route("/leave/employee/<int:eid>/comp-off", methods=["POST"])
@admin_required
def leave_comp_off(eid):
    e = _emp(eid)
    try:
        g_ = leave.grant_comp_off(e, _date(request.form.get("worked_on"), date.max), float(request.form.get("days") or 0),
                                  request.form.get("reason", ""), g.user.username, request.form.get("expiry_days"))
    except (ValueError, LeaveError) as ex:
        flash(str(ex) if isinstance(ex, LeaveError) else "Enter valid numbers.", "err")
    else:
        audit("leave.comp_off", "employee", e.id, f"{g_.days:g} day(s) for {g_.worked_on:%d-%b-%Y}, expires {g_.expires_on:%d-%b-%Y}")
        flash(f"Comp off of {g_.days:g} day(s) granted (expires {g_.expires_on:%d-%b-%Y}).", "ok")
    return redirect(url_for("leave_employee", eid=eid))


@route("/leave/employee/<int:eid>/override", methods=["POST"])
@admin_required
def leave_override(eid):
    """Fixed number of leaves for THIS employee (replaces the policy quota / ceiling)."""
    e = _emp(eid)
    lt = db.get(LeaveType, request.form.get("leave_type_id", type=int) or 0) or abort(404)
    try:
        quota, ceiling = _num(request.form.get("quota")), _num(request.form.get("max_balance"))
    except ValueError:
        flash("Quota and ceiling must be numbers.", "err")
        return redirect(url_for("leave_employee", eid=eid))
    row = db.query(EmployeeLeaveOverride).filter_by(emp_id=e.id, leave_type_id=lt.id).first()
    if quota is None and ceiling is None:
        if row:
            db.delete(row)
        msg = f"{lt.name}: back to the policy values."
    else:
        row = row or EmployeeLeaveOverride(emp_id=e.id, leave_type_id=lt.id)
        row.quota, row.max_balance = quota, ceiling
        db.add(row)
        msg = f"{lt.name}: quota {quota if quota is not None else 'per policy'}, ceiling {ceiling if ceiling is not None else 'per policy'} for this employee."
    db.commit()
    audit("leave.override", "employee", e.id, f"{lt.code} quota={quota} ceiling={ceiling}")
    flash(msg, "ok")
    return redirect(url_for("leave_employee", eid=eid))


@route("/leave/employee/<int:eid>/apply", methods=["POST"])
@admin_required
def leave_apply_for(eid):
    e = _emp(eid)
    lt = db.get(LeaveType, request.form.get("leave_type_id", type=int) or 0)
    d1, d2 = _date(request.form.get("from_date")), _date(request.form.get("to_date")) or _date(request.form.get("from_date"))
    if not d1 or not d2:
        flash("Choose the leave dates.", "err")
        return redirect(url_for("leave_employee", eid=eid))
    app, errs = leave.apply_leave(e, lt, d1, d2, "from_half" in request.form, "to_half" in request.form,
                                  request.form.get("reason", ""), "doc" in request.form, g.user.username)
    if errs:
        for m in errs:
            flash(m, "err")
        return redirect(url_for("leave_employee", eid=eid))
    audit("leave.apply", "application", app.id, f"{e.emp_code} {lt.code} {d1}..{d2} {app.days:g}d (by admin)")
    if "approve_now" in request.form:
        errs = leave.approve(app, g.user.username, "Entered and approved by admin")
        for m in errs:
            flash(m, "err")
        if not errs:
            audit("leave.approve", "application", app.id, f"{e.emp_code} {app.days:g} day(s)")
            flash(f"Leave recorded and approved: {app.days:g} day(s).", "ok")
    else:
        flash(f"Leave application of {app.days:g} day(s) created (pending).", "ok")
    return redirect(url_for("leave_employee", eid=eid))


# ---------------------------------------------------------------- applications
@route("/leave/applications")
@admin_required
def leave_applications():
    status = request.args.get("status", "pending")
    q = db.query(LeaveApplication).order_by(LeaveApplication.applied_at.desc())
    if status in ("pending", "approved", "rejected", "cancelled"):
        q = q.filter_by(status=status)
    p = paginate(q, request.args.get("page", 1, type=int), 30)
    return render_template("leave_applications.html", p=p, status=status, args=dict(status=status))


@route("/leave/applications/<int:aid>/<action>", methods=["POST"])
@admin_required
def leave_decide(aid, action):
    app = db.get(LeaveApplication, aid) or abort(404)
    note = (request.form.get("note") or "").strip()
    if action == "approve":
        errs = leave.approve(app, g.user.username, note)
    elif action == "reject":
        errs = leave.reject(app, g.user.username, note)
    elif action == "cancel":
        errs = leave.cancel(app, g.user.username, note)
    else:
        abort(404)
    if errs:
        for m in errs:
            flash(m, "err")
    else:
        audit(f"leave.{action}", "application", app.id, f"{app.employee.emp_code} {app.leave_type.code} "
              f"{app.from_date}..{app.to_date} {app.days:g}d {note[:80]}")
        flash("Application " + {"approve": "approved", "reject": "rejected", "cancel": "cancelled"}[action] + ".", "ok")
    return redirect(request.referrer or url_for("leave_applications"))


# ---------------------------------------------------------------- types & policies
@route("/leave/policies")
@admin_required
def leave_policies():
    return render_template("leave_policies.html", types=leave.leave_types(active_only=False),
                           policies=db.query(LeavePolicy).order_by(LeavePolicy.is_default.desc(), LeavePolicy.name).all(),
                           cats=db.query(Category).order_by(Category.name).all())


@route("/leave/types/save", methods=["POST"])
@admin_required
def leave_type_save():
    tid = request.form.get("id", type=int)
    code = (request.form.get("code") or "").strip().upper()
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Give the leave type a name.", "err")
        return redirect(url_for("leave_policies"))
    lt = db.get(LeaveType, tid) if tid else None
    if not lt:
        if not re.fullmatch(r"[A-Z]{1,6}", code):
            flash("Code must be 1-6 capital letters (e.g. ML).", "err")
            return redirect(url_for("leave_policies"))
        if leave.type_by_code(code):
            flash(f"Code {code} already exists.", "err")
            return redirect(url_for("leave_policies"))
        lt = LeaveType(code=code, sort_order=(db.query(func.max(LeaveType.sort_order)).scalar() or 0) + 1)
        db.add(lt)
    lt.name = name[:60]
    f = request.form
    lt.paid = "paid" in f
    lt.uses_balance = "uses_balance" in f
    lt.half_day_ok = "half_day_ok" in f
    lt.encashable = "encashable" in f
    lt.active = "active" in f
    try:
        lt.doc_after_days = int(f["doc_after_days"]) if f.get("doc_after_days") else None
    except ValueError:
        flash("Certificate days must be a whole number.", "err")
        db.rollback()
        return redirect(url_for("leave_policies"))
    db.commit()
    audit("leave.type", "leave_type", lt.id, f"{lt.code} {lt.name} paid={lt.paid} balance={lt.uses_balance} active={lt.active}")
    flash(f"{lt.name} saved.", "ok")
    return redirect(url_for("leave_policies"))


@route("/leave/policies/new", methods=["POST"])
@admin_required
def leave_policy_new():
    name = (request.form.get("name") or "").strip()
    cid = request.form.get("category_id", type=int)
    if not name:
        flash("Give the policy a name.", "err")
        return redirect(url_for("leave_policies"))
    if cid and db.query(LeavePolicy.id).filter_by(category_id=cid).first():
        flash("That salary category already has a policy.", "err")
        return redirect(url_for("leave_policies"))
    pol = LeavePolicy(name=name[:60], category_id=cid or None, is_default=False)
    db.add(pol)
    db.flush()
    base = db.query(LeavePolicy).filter_by(is_default=True).first()
    for r in (base.rules if base else []):                      # start from a copy of the default policy
        db.add(LeavePolicyRule(policy_id=pol.id, leave_type_id=r.leave_type_id, quota=r.quota, accrual=r.accrual,
                               accrual_days=r.accrual_days, wait_months=r.wait_months, prorate=r.prorate,
                               carry_mode=r.carry_mode, carry_limit=r.carry_limit, max_balance=r.max_balance,
                               expires_days=r.expires_days, allow_negative=r.allow_negative))
    db.commit()
    audit("leave.policy_new", "policy", pol.id, pol.name)
    flash("Policy created from a copy of the default. Adjust its rules below.", "ok")
    return redirect(url_for("leave_policy_edit", pid=pol.id))


@route("/leave/policies/<int:pid>", methods=["GET", "POST"])
@admin_required
def leave_policy_edit(pid):
    pol = db.get(LeavePolicy, pid) or abort(404)
    types = [t for t in leave.leave_types() if t.uses_balance]
    if request.method == "POST":
        f = request.form
        pol.name = (f.get("name") or pol.name).strip()[:60]
        if "is_default" in f and not pol.is_default:
            for other in db.query(LeavePolicy).filter_by(is_default=True):
                other.is_default = False
            pol.is_default, pol.category_id = True, None
        try:
            for t in types:
                p = f"r{t.id}_"
                rule = next((r for r in pol.rules if r.leave_type_id == t.id), None)
                if p + "use" not in f:                         # untick = no rule for this type in this policy
                    if rule:
                        db.delete(rule)
                    continue
                rule = rule or LeavePolicyRule(policy_id=pol.id, leave_type_id=t.id)
                rule.quota = _num(f.get(p + "quota"), 0.0, 0, 366)
                rule.accrual = f.get(p + "accrual") if f.get(p + "accrual") in dict(ACCRUALS) else "yearly"
                rule.accrual_days = _num(f.get(p + "accrual_days"), 20.0, 1, 366)
                rule.wait_months = int(_num(f.get(p + "wait_months"), 0, 0, 60))
                rule.prorate = (p + "prorate") in f
                rule.carry_mode = f.get(p + "carry_mode") if f.get(p + "carry_mode") in dict(CARRY) else "none"
                rule.carry_limit = _num(f.get(p + "carry_limit"), 0.0, 0, 366)
                rule.max_balance = _num(f.get(p + "max_balance"), None, 0, 1000)
                exp = _num(f.get(p + "expires_days"), None, 1, 3650)
                rule.expires_days = int(exp) if exp else None
                rule.allow_negative = (p + "allow_negative") in f
                db.add(rule)
        except ValueError:
            db.rollback()
            flash("Check the numbers: quota, limits and days must be valid, non-negative values.", "err")
            return redirect(url_for("leave_policy_edit", pid=pid))
        db.commit()
        audit("leave.policy", "policy", pol.id, f"{pol.name} rules saved")
        flash("Policy saved. New rules apply to credits created from now on; existing ledger entries are not changed.", "ok")
        return redirect(url_for("leave_policy_edit", pid=pid))
    rules = {r.leave_type_id: r for r in pol.rules}
    return render_template("leave_policy_edit.html", pol=pol, types=types, rules=rules, accruals=ACCRUALS, carry=CARRY)


@route("/leave/policies/<int:pid>/delete", methods=["POST"])
@admin_required
def leave_policy_delete(pid):
    pol = db.get(LeavePolicy, pid) or abort(404)
    if pol.is_default:
        flash("The default policy cannot be deleted. Make another policy the default first.", "err")
    else:
        db.delete(pol)
        db.commit()
        audit("leave.policy_delete", "policy", pid, pol.name)
        flash("Policy deleted; its employees now follow the default policy.", "ok")
    return redirect(url_for("leave_policies"))


# ---------------------------------------------------------------- holidays & weekly offs
@route("/leave/holidays", methods=["GET", "POST"])
@admin_required
def leave_holidays():
    year = request.values.get("year", date.today().year, type=int)
    if request.method == "POST":
        d = _date(request.form.get("on_date"))
        name = (request.form.get("name") or "").strip()
        if not d or not name:
            flash("Enter the date and the holiday name.", "err")
        elif db.query(Holiday.id).filter_by(on_date=d).first():
            flash(f"{d:%d-%b-%Y} is already a holiday.", "err")
        else:
            db.add(Holiday(on_date=d, name=name[:80]))
            db.commit()
            audit("holiday.add", "holiday", d.isoformat(), name[:80])
            flash("Holiday added.", "ok")
        return redirect(url_for("leave_holidays", year=d.year if d else year))
    hols = db.query(Holiday).filter(Holiday.on_date >= date(year, 1, 1), Holiday.on_date <= date(year, 12, 31)).order_by(Holiday.on_date).all()
    rules = db.query(WeeklyOffRule).all()
    return render_template("leave_holidays.html", year=year, hols=hols, rules=rules, weekdays=leave.WEEKDAYS,
                           cats=db.query(Category).order_by(Category.name).all())


@route("/leave/holidays/<int:hid>/delete", methods=["POST"])
@admin_required
def leave_holiday_delete(hid):
    h = db.get(Holiday, hid) or abort(404)
    y = h.on_date.year
    db.delete(h)
    db.commit()
    audit("holiday.delete", "holiday", h.on_date.isoformat(), h.name)
    flash("Holiday removed.", "ok")
    return redirect(url_for("leave_holidays", year=y))


@route("/leave/weekly-off", methods=["POST"])
@admin_required
def leave_weekly_off():
    cid = request.form.get("category_id", type=int) or None
    days = sorted({int(x) for x in request.form.getlist("weekday") if x.isdigit() and 0 <= int(x) <= 6})
    rule = db.query(WeeklyOffRule).filter_by(category_id=cid).first()
    if not days and cid:                                        # clearing a category falls back to the default
        if rule:
            db.delete(rule)
    else:
        rule = rule or WeeklyOffRule(category_id=cid)
        rule.weekdays = ",".join(map(str, days))
        db.add(rule)
    db.commit()
    audit("weekly_off.save", "category", cid or "default", ",".join(leave.WEEKDAYS[d] for d in days) or "none")
    flash("Weekly off saved.", "ok")
    return redirect(url_for("leave_holidays"))


# ---------------------------------------------------------------- accrual / rollover / comp-off expiry
@route("/leave/run", methods=["GET", "POST"])
@admin_required
def leave_run():
    action = request.form.get("action")
    if request.method == "POST":
        if action == "accrue":
            res = leave.accrue(dry_run=False, by=g.user.username)
            audit("leave.accrue", "leave", None, f"{len(res['entries'])} credit(s) created")
            flash(f"Credited {len(res['entries'])} entr{'y' if len(res['entries']) == 1 else 'ies'}.", "ok")
            for m in res["skipped"]:
                flash(m, "err")
        elif action == "rollover":
            try:
                year = int(request.form.get("year"))
                res = leave.rollover(year, dry_run=False, by=g.user.username)
                audit("leave.rollover", "leave_year", year, f"{len(res['rows'])} balances checked, {res['total_lapsed']:g} day(s) lapsed")
                flash(f"Leave year {leave.year_label(year)} closed: {res['total_lapsed']:g} day(s) lapsed.", "ok")
            except (ValueError, LeaveError) as ex:
                flash(str(ex), "err")
        elif action == "expire":
            out = leave.expire_comp_offs(dry_run=False)
            audit("leave.comp_expiry", "leave", None, f"{len(out)} grant(s) expired")
            flash(f"{len(out)} expired comp-off grant(s) lapsed.", "ok")
        return redirect(url_for("leave_run"))
    plan = leave.accrue(dry_run=True)
    rv_year = request.args.get("year", type=int)
    roll = None
    if rv_year:
        try:
            roll = leave.rollover(rv_year, dry_run=True)
        except LeaveError as ex:
            flash(str(ex), "err")
    return render_template("leave_run.html", plan=plan, years=leave.rollover_years(), roll=roll, rv_year=rv_year,
                           expiring=leave.expire_comp_offs(dry_run=True), sm=leave.year_start_month())


# ---------------------------------------------------------------- reports
@route("/leave/reports")
@admin_required
def leave_reports():
    as_on = _date(request.args.get("as_on"), date.today())
    d1 = _date(request.args.get("from"), date(date.today().year, date.today().month, 1))
    d2 = _date(request.args.get("to"), date.today())
    return render_template("leave_reports.html", liab=leave.liability(as_on), as_on=as_on, d1=d1, d2=d2,
                           ledger=leave.register(d1, d2)[:500])


@route("/leave/liability.xlsx")
@admin_required
def leave_liability_xlsx():
    from openpyxl import Workbook
    liab = leave.liability(_date(request.args.get("as_on"), date.today()))
    wb = Workbook()
    ws = wb.active
    ws.title = "Leave liability"
    ws.append([f"Encashable leave liability as on {liab['as_on']:%d-%m-%Y} (daily Basic+DA x balance)"])
    ws.append(["Emp Code", "Name", "Leave type", "Days", "Daily wage", "Amount"])
    for r in liab["rows"]:
        ws.append([r["emp"].emp_code, r["emp"].name, r["lt"].name, r["days"], r["daily"], r["amount"]])
    ws.append(["", "TOTAL", "", "", "", liab["total"]])
    audit("leave.export", "report", "liability", f"total {liab['total']}")
    return xlsx_response(xl.to_buffer(wb), f"leave_liability_{liab['as_on']:%Y%m%d}.xlsx")


@route("/leave/register.xlsx")
@admin_required
def leave_register_xlsx():
    from openpyxl import Workbook
    d1, d2 = _date(request.args.get("from"), date.today().replace(day=1)), _date(request.args.get("to"), date.today())
    wb = Workbook()
    ws = wb.active
    ws.title = "Leave register"
    ws.append(["Date", "Emp Code", "Name", "Leave type", "Kind", "Days", "Note", "By"])
    for r in leave.register(d1, d2):
        ws.append([r.on_date.strftime("%d-%m-%Y"), r.employee.emp_code, r.employee.name, r.leave_type.name, r.kind,
                   r.days, r.note, r.created_by])
    audit("leave.export", "report", "register", f"{d1}..{d2}")
    return xlsx_response(xl.to_buffer(wb), f"leave_register_{d1:%Y%m%d}_{d2:%Y%m%d}.xlsx")


@route("/attendance/muster.xlsx")
@admin_required
def attendance_muster_xlsx():
    """Attendance register for a month: one row per employee, one column per day, plus totals."""
    from openpyxl import Workbook
    month = valid_month(request.args.get("month") or date.today().strftime("%Y-%m"))
    first, last = payroll.month_start(month), payroll.month_end(month)
    marks = {}
    for m in db.query(DailyAttendance).filter(DailyAttendance.day >= first, DailyAttendance.day <= last):
        marks.setdefault(m.emp_id, {})[m.day.day] = m.status
    hol = leave.holiday_dates(first, last)
    wb = Workbook()
    ws = wb.active
    ws.title = f"Attendance {month}"
    ws.append(["Emp Code", "Name", *range(1, last.day + 1), "Present", "Paid off", "Absent/unpaid"])
    for e in db.query(Employee).filter(Employee.status == "Active").order_by(Employee.emp_code):
        offs, row, pres, paid, unp = leave.weekly_off_days(e), [], 0.0, 0.0, 0.0
        for d in leave.daterange(first, last):
            code = marks.get(e.id, {}).get(d.day, "")
            if not code and leave._in_service(e, d):
                code = "H" if d in hol else ("WO" if d.weekday() in offs else "")
            row.append(code)
            pres += reports.PRESENT_VALUE.get(code, 0.0) + (1.0 if code == "P" else 0.0)
            paid += reports.PAID_OFF_VALUE.get(code, 0.0)
            unp += 1.0 if code in ("A", "U") else (0.5 if code == "HU" else 0.0)
        ws.append([e.emp_code, e.name, *row, pres, paid, unp])
    audit("attendance.muster", "month", month, "attendance register downloaded")
    return xlsx_response(xl.to_buffer(wb), f"attendance_register_{month}.xlsx")


# ---------------------------------------------------------------- per-employee yearly attendance calendar
@route("/attendance/employee/<int:eid>", methods=["GET", "POST"])
@admin_required
def attendance_employee(eid):
    e = _emp(eid)
    year = request.values.get("year", date.today().year, type=int)
    year = min(max(year, 2000), date.today().year + 1)
    if request.method == "POST":
        act = request.form.get("action")
        if act == "day":
            d = _date(request.form.get("date"))
            st = request.form.get("status", "")
            if not d:
                flash("Choose a date.", "err")
            elif st == "":
                err = leave.clear_day(e, d)
                flash(err or f"{d:%d-%b-%Y} cleared.", "err" if err else "ok")
                if not err:
                    audit("attendance.employee_day", "employee", e.id, f"{d} cleared")
            else:
                try:
                    ot = float(request.form.get("ot") or 0)
                except ValueError:
                    ot = -1
                err = leave.mark_day(e, d, st, ot, g.user.username)
                if err:
                    db.rollback()
                    flash(err, "err")
                else:
                    db.commit()
                    audit("attendance.employee_day", "employee", e.id, f"{d} {st}")
                    flash(f"{d:%d-%b-%Y} marked {st}.", "ok")
        elif act == "range":
            d1, d2 = _date(request.form.get("from")), _date(request.form.get("to"))
            st = request.form.get("status", "P")
            if not d1 or not d2:
                flash("Choose both dates.", "err")
            else:
                n, problems = leave.mark_range(e, d1, d2, st, 0.0, "include_offs" not in request.form, g.user.username)
                if problems:
                    for m in problems:
                        flash(m, "err")
                else:
                    audit("attendance.employee_range", "employee", e.id, f"{d1}..{d2} {st} ({n} days)")
                    flash(f"Marked {n} day(s) as {st}.", "ok")
        return redirect(url_for("attendance_employee", eid=eid, year=year))
    cal = leave.year_calendar(e, year)
    bal = leave.balances_of(e.id)
    return render_template("attendance_employee.html", e=e, cal=cal, year=year, statuses=leave.ATT_STATUSES,
                           manual=leave.MANUAL_STATUSES, lts=leave.leave_types(), bal=bal, today=date.today(),
                           admin_view=True)


# ---------------------------------------------------------------- attendance corrections asked by employees
@route("/requests")
@admin_required
def attendance_requests():
    status = request.args.get("status", "pending")
    q = db.query(AttendanceRequest).order_by(AttendanceRequest.requested_at.desc())
    if status in ("pending", "approved", "rejected"):
        q = q.filter_by(status=status)
    p = paginate(q, request.args.get("page", 1, type=int), 30)
    return render_template("attendance_requests.html", p=p, status=status, args=dict(status=status),
                           statuses=leave.ATT_STATUSES)


@route("/requests/<int:rid>/<action>", methods=["POST"])
@admin_required
def attendance_request_decide(rid, action):
    req = db.get(AttendanceRequest, rid) or abort(404)
    if action not in ("approve", "reject"):
        abort(404)
    err = leave.decide_correction(req, action == "approve", g.user.username, (request.form.get("note") or "").strip())
    if err:
        flash(err, "err")
    else:
        audit(f"attendance.request_{action}", "request", rid, f"{req.employee.emp_code} {req.on_date} {req.requested_status}")
        flash("Request " + {"approve": "approved", "reject": "rejected"}[action] + ".", "ok")
    return redirect(url_for("attendance_requests"))


# ---------------------------------------------------------------- employee portal logins
@route("/portal-access")
@admin_required
def portal_access():
    q = request.args.get("q", "").strip()
    query = db.query(Employee).filter(Employee.status == "Active").order_by(Employee.emp_code)
    if q:
        query = query.filter(Employee.name.ilike(f"%{q}%") | Employee.emp_code.ilike(f"%{q}%"))
    p = paginate(query, request.args.get("page", 1, type=int), 40)
    users = {u.employee_id: u for u in db.query(User).filter(User.employee_id.isnot(None), User.org_id == current_org_id())}
    return render_template("portal_access.html", p=p, users=users, q=q, args=dict(q=q) if q else {},
                           now=datetime.now())


def _temp_password():
    return "Hrms-" + secrets.token_urlsafe(6) + "9"


@route("/portal-access/<int:eid>/grant", methods=["POST"])
@admin_required
def portal_grant(eid):
    e = _emp(eid)
    username = (request.form.get("username") or "").strip() or f"{e.emp_code.lower()}.{g.org.id}"
    pw = request.form.get("password") or _temp_password()
    problem = password_problem(pw, username)
    if problem:
        flash(problem, "err")
        return redirect(url_for("portal_access"))
    try:
        leave.grant_portal_access(e, username, pw, g.org.id)
    except LeaveError as ex:
        flash(str(ex), "err")
        return redirect(url_for("portal_access"))
    audit("portal.grant", "employee", e.id, f"login {username}")
    flash(f"Portal login created for {e.name}. Username: {username}   Temporary password: {pw}   "
          "(shown only now; they must change it at first sign-in).", "ok")
    return redirect(url_for("portal_access"))


@route("/portal-access/<int:eid>/<action>", methods=["POST"])
@admin_required
def portal_manage(eid, action):
    e = _emp(eid)
    u = leave.portal_user(e) or abort(404)
    if u.org_id != current_org_id():
        abort(404)
    if action == "reset":
        pw = request.form.get("password") or _temp_password()
        problem = password_problem(pw, u.username)
        if problem:
            flash(problem, "err")
            return redirect(url_for("portal_access"))
        u.pw_hash, u.must_change_password, u.failed_logins, u.locked_until = generate_password_hash(pw), True, 0, None
        flash(f"New temporary password for {u.username}: {pw}   (shown only now).", "ok")
    elif action == "disable":
        u.is_active = False
        flash(f"{u.username} can no longer sign in.", "ok")
    elif action == "enable":
        u.is_active = True
        flash(f"{u.username} can sign in again.", "ok")
    elif action == "unlock":
        u.failed_logins, u.locked_until = 0, None
        flash(f"{u.username} unlocked.", "ok")
    else:
        abort(404)
    db.commit()
    audit(f"portal.{action}", "employee", e.id, u.username)
    return redirect(url_for("portal_access"))


@route("/portal-access/bulk", methods=["POST"])
@admin_required
def portal_bulk():
    """Create logins (username = <code>.<org id>, random temporary password) for every active employee without one,
    and return the credentials ONCE as a CSV to hand out. Passwords are not kept in readable form."""
    have = {u.employee_id for u in db.query(User).filter(User.employee_id.isnot(None))}
    rows = [["Employee code", "Name", "Username", "Temporary password"]]
    for e in db.query(Employee).filter_by(status="Active").order_by(Employee.emp_code):
        if e.id in have:
            continue
        username, pw = f"{e.emp_code.lower()}.{g.org.id}", _temp_password()
        try:
            leave.grant_portal_access(e, username, pw, g.org.id)
        except LeaveError:
            db.rollback()
            continue
        rows.append([e.emp_code, e.name, username, pw])
    if len(rows) == 1:
        flash("Everyone active already has a login (or the generated usernames were taken).", "err")
        return redirect(url_for("portal_access"))
    audit("portal.bulk_grant", "organization", g.org.id, f"{len(rows) - 1} login(s) created")
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return send_file(io.BytesIO(buf.getvalue().encode("utf-8-sig")), mimetype="text/csv", as_attachment=True,
                     download_name=f"portal_logins_{date.today():%Y%m%d}.csv")
