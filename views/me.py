"""Employee self-service portal (role 'employee'). Every query below is limited to g.employee: an employee can
see and change only their own records. The request pipeline in app.py refuses every other URL for this role."""
from common import *  # noqa: F401,F403
from web import route


def _me():
    return g.employee


def _date(raw):
    try:
        return datetime.strptime((raw or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


@route("/me")
def me_home():
    e = _me()
    cx = leave.Ctx()
    bal = leave.balances_of(e.id)
    rows = [dict(lt=t, balance=bal.get(t.id, 0.0), pending=leave.pending_days(e.id, t.id)) for t in cx.types.values()
            if t.active and t.uses_balance]
    month = date.today().strftime("%Y-%m")
    summary = reports.rollup_month(month).get(e.id)
    apps = db.query(LeaveApplication).filter_by(emp_id=e.id).order_by(LeaveApplication.applied_at.desc()).limit(5).all()
    notes = db.query(Notification).filter_by(emp_id=e.id).order_by(Notification.created_at.desc()).limit(10).all()
    unread = sum(1 for n in notes if not n.read_at)
    return render_template("me_home.html", e=e, rows=rows, summary=summary, month=month, apps=apps, notes=notes,
                           unread=unread)


@route("/me/notifications/read", methods=["POST"])
def me_notifications_read():
    for n in db.query(Notification).filter_by(emp_id=_me().id, read_at=None):
        n.read_at = datetime.now()
    db.commit()
    return redirect(url_for("me_home"))


@route("/me/leave", methods=["GET", "POST"])
def me_leave():
    e = _me()
    lts = [t for t in leave.leave_types() if t.code != "CO" or leave.balance(e.id, t.id) > 0 or True]
    if request.method == "POST":
        lt = db.get(LeaveType, request.form.get("leave_type_id", type=int) or 0)
        d1 = _date(request.form.get("from_date"))
        d2 = _date(request.form.get("to_date")) or d1
        if not d1:
            flash("Choose the first day of leave.", "err")
        else:
            app, errs = leave.apply_leave(e, lt, d1, d2, "from_half" in request.form, "to_half" in request.form,
                                          request.form.get("reason", ""), "doc" in request.form,
                                          f"{g.user.username} (employee)", employee_initiated=True)
            if errs:
                for m in errs:
                    flash(m, "err")
            else:
                audit("leave.apply", "application", app.id, f"{e.emp_code} {lt.code} {d1}..{d2} {app.days:g}d (self)")
                flash(f"Your {lt.name} request for {app.days:g} day(s) was sent for approval.", "ok")
                return redirect(url_for("me_leave"))
    bal = leave.balances_of(e.id)
    info = [dict(lt=t, balance=bal.get(t.id, 0.0), pending=leave.pending_days(e.id, t.id)) for t in lts if t.uses_balance]
    apps = db.query(LeaveApplication).filter_by(emp_id=e.id).order_by(LeaveApplication.from_date.desc()).limit(40).all()
    ledger = db.query(LeaveLedger).filter_by(emp_id=e.id).order_by(LeaveLedger.on_date.desc(), LeaveLedger.id.desc()).limit(30).all()
    return render_template("me_leave.html", e=e, lts=lts, info=info, apps=apps, ledger=ledger, today=date.today(),
                           f=request.form)


@route("/me/leave/<int:aid>/cancel", methods=["POST"])
def me_leave_cancel(aid):
    app = db.query(LeaveApplication).filter_by(id=aid, emp_id=_me().id).first() or abort(404)
    errs = leave.cancel(app, f"{g.user.username} (employee)", "Cancelled by employee", own_request=True)
    if errs:
        for m in errs:
            flash(m, "err")
    else:
        audit("leave.cancel", "application", app.id, f"{_me().emp_code} {app.days:g}d (self)")
        flash("Leave cancelled.", "ok")
    return redirect(url_for("me_leave"))


@route("/me/attendance", methods=["GET", "POST"])
def me_attendance():
    e = _me()
    year = min(max(request.values.get("year", date.today().year, type=int), 2000), date.today().year + 1)
    if request.method == "POST":
        d = _date(request.form.get("date"))
        if not d:
            flash("Choose the date to correct.", "err")
        else:
            err = leave.request_correction(e, d, request.form.get("status", ""), request.form.get("reason", ""),
                                           f"{g.user.username} (employee)")
            if err:
                flash(err, "err")
            else:
                audit("attendance.request", "employee", e.id, f"{d} {request.form.get('status')}")
                flash("Your correction request was sent. You will be notified when it is decided.", "ok")
                return redirect(url_for("me_attendance", year=year))
    reqs = db.query(AttendanceRequest).filter_by(emp_id=e.id).order_by(AttendanceRequest.requested_at.desc()).limit(15).all()
    return render_template("attendance_employee.html", e=e, cal=leave.year_calendar(e, year), year=year,
                           statuses=leave.ATT_STATUSES, manual=(), lts=leave.leave_types(), bal=leave.balances_of(e.id),
                           today=date.today(), admin_view=False, reqs=reqs, f=request.form)


@route("/me/payslips")
def me_payslips():
    e = _me()
    finals = payroll.finalized_months()          # draft payslips are never shown to employees
    slips = [s for s in db.query(Payslip).filter_by(emp_id=e.id).order_by(Payslip.month.desc()).all() if s.month in finals]
    return render_template("me_payslips.html", e=e, slips=slips)


@route("/me/payslips/<int:sid>.pdf")
def me_payslip_pdf(sid):
    s = db.query(Payslip).filter_by(id=sid, emp_id=_me().id).first() or abort(404)
    if not payroll.is_final(s.month):
        abort(404)
    audit("payslip.self_download", "payslip", s.id, s.month)
    return pdf_response(pdfs.payslips_pdf([s], get_settings()), f"payslip_{s.month}.pdf")
