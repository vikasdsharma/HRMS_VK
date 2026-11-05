from common import *  # noqa: F401,F403
from web import route, template_filter  # noqa: F401


# ---------- attendance ----------
@route("/attendance", methods=["GET", "POST"])
def attendance():
    cur = date.today().strftime("%Y-%m")
    month = valid_month(request.values.get("month") or cur)
    result = None
    if request.method == "POST":
        f = request.files.get("file")
        if month > cur:
            flash("Attendance cannot be uploaded for a future month", "err")
        elif payroll.is_final(month):
            flash(f"{pdfs.month_label(month)} payroll is finalised - attendance is locked. "
                  "An admin can unlock the month on the Payroll page if a correction is really needed.", "err")
        elif not f or not f.filename.lower().endswith(".csv"):
            flash("Please choose a .csv file", "err")
        else:
            raw = f.read()
            try:
                if len(raw) > 5 * 1024 * 1024:
                    raise ValueError("CSV is larger than 5 MB")
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:          # (a ValueError subclass - must come first)
                text = None
                flash("File is not UTF-8 text. In Excel use 'Save As > CSV UTF-8'.", "err")
            except ValueError as e:
                text = None
                flash(str(e), "err")
            if text is not None:
                result = importers.import_attendance(text, month)
                errs = result["errors"]
                db.add(AttendanceUpload(
                    month=month, filename=os.path.basename(f.filename)[:200], uploaded_by=g.user.username,
                    rows_saved=result.get("saved", 0), status="Rejected" if errs else "Saved",
                    details=("; ".join(f"line {r}: {m}" for r, m in errs[:5]) + (f" (+{len(errs) - 5} more)" if len(errs) > 5 else ""))
                    if errs else ("Warnings: " + "; ".join(result["warnings"][:3]) if result.get("warnings") else None)))
                db.commit()
                audit("attendance.upload", "month", month, f"{os.path.basename(f.filename)[:100]}: "
                      f"{result.get('saved', 0)} saved, {len(errs)} error(s)")
    rows = db.query(Attendance).join(Employee).filter(Attendance.month == month).order_by(Employee.emp_code).all()
    counts = dict(db.query(Attendance.month, func.count(Attendance.id)).group_by(Attendance.month).all())
    history = db.query(AttendanceUpload).order_by(AttendanceUpload.uploaded_at.desc(), AttendanceUpload.id.desc()).limit(100).all()
    return render_template("attendance.html", month=month, rows=rows, result=result, month_opts=month_options(),
                           counts=counts, default_month=cur, history=history, finals=payroll.finalized_months())


@route("/attendance/template.csv")
def attendance_template():
    month = request.args.get("month")
    lines = ["emp_code,present_days,paid_holidays,ot_hours,advance,other_deduction"]
    for e in db.query(Employee).filter_by(status="Active").order_by(Employee.emp_code):
        lines.append(f"{e.emp_code},0,0,0,0,0")
    buf = io.BytesIO("\n".join(lines).encode("utf-8-sig"))
    return send_file(buf, download_name=f"attendance_{month or 'template'}.csv", mimetype="text/csv",
                     as_attachment=True)


@route("/attendance/<int:aid>/delete", methods=["POST"])
def attendance_delete(aid):
    a = db.get(Attendance, aid)
    if a and payroll.is_final(a.month):
        flash("This month is finalised - attendance is locked.", "err")
        return redirect(url_for("attendance", month=a.month))
    if a:
        month = a.month
        db.query(Payslip).filter_by(emp_id=a.emp_id, month=month).delete()
        db.delete(a)
        db.commit()
        audit("attendance.delete", "attendance", aid, f"employee_id={a.emp_id} month={month}")
        return redirect(url_for("attendance", month=month))
    return redirect(url_for("attendance"))


# ---------- daily attendance (mark each day; roll up into the month) ----------
def _parse_day(raw):
    try:
        day = datetime.strptime(raw, "%Y-%m-%d").date() if raw else date.today()
    except ValueError:
        abort(400, "Invalid date")
    if day > date.today():
        abort(400, "Attendance cannot be marked for a future date")
    return day


@route("/attendance/daily", methods=["GET", "POST"])
def attendance_daily():
    day = _parse_day(request.values.get("date"))
    month = day.strftime("%Y-%m")
    locked = payroll.is_final(month)
    emps = db.query(Employee).filter_by(status="Active").order_by(Employee.emp_code).all()
    marks = {r.emp_id: r for r in db.query(DailyAttendance).filter_by(day=day)}
    if request.method == "POST":
        if locked:
            flash(f"{pdfs.month_label(month)} payroll is finalised - daily attendance is locked.", "err")
            return redirect(url_for("attendance_daily", date=day.isoformat()))
        saved = cleared = 0
        errors = []
        for e in emps:
            st = (request.form.get(f"s_{e.id}") or "").strip().upper()
            try:
                ot = float(request.form.get(f"o_{e.id}") or 0)
            except ValueError:
                errors.append(f"{e.emp_code}: overtime must be a number")
                continue
            if st and st not in reports.STATUSES:
                errors.append(f"{e.emp_code}: unknown status {st}")
                continue
            if not 0 <= ot <= 16:
                errors.append(f"{e.emp_code}: overtime must be between 0 and 16 hours")
                continue
            cur = marks.get(e.id)
            if (cur and cur.source == "leave") or not leave.employed_on(e, day):
                continue                     # approved leave / not yet joined / already left: not editable here
            if not st:
                if cur:
                    db.delete(cur)
                    cleared += 1
                continue
            if cur is None:
                cur = DailyAttendance(emp_id=e.id, day=day)
                db.add(cur)
            cur.status, cur.ot_hours = st, ot
            saved += 1
        if errors:
            db.rollback()
            for m in errors[:8]:
                flash(m, "err")
            return redirect(url_for("attendance_daily", date=day.isoformat()))
        db.commit()
        audit("attendance.daily", "date", day.isoformat(), f"{saved} marked, {cleared} cleared")
        flash(f"Saved {day:%d-%b-%Y}: {saved} marked" + (f", {cleared} cleared" if cleared else ""), "ok")
        return redirect(url_for("attendance_daily", date=day.isoformat()))
    prefill = request.args.get("fill", "").upper()
    prefill = prefill if prefill in reports.STATUSES else ""
    return render_template("attendance_daily.html", day=day, emps=emps, marks=marks, locked=locked, month=month,
                           statuses=reports.STATUSES, statuses_all=reports.ALL_STATUSES, prefill=prefill, in_service=leave.employed_on,
                           calendar_status=lambda e: ("H" if day in leave.holiday_dates(day, day) else
                                                      "WO" if day.weekday() in leave.weekly_off_days(e) else ""))


@route("/attendance/daily/month")
def attendance_daily_month():
    month = valid_month(request.args.get("month") or date.today().strftime("%Y-%m"))
    dim = payroll.days_in(month)
    emps = db.query(Employee).filter_by(status="Active").order_by(Employee.emp_code).all()
    grid = {}
    for r in db.query(DailyAttendance).filter(DailyAttendance.day >= payroll.month_start(month),
                                              DailyAttendance.day <= payroll.month_end(month)):
        grid.setdefault(r.emp_id, {})[r.day.day] = r.status
    roll = reports.rollup_month(month)
    return render_template("attendance_daily_month.html", month=month, dim=dim, emps=emps, grid=grid, roll=roll,
                           locked=payroll.is_final(month), month_opts=month_options(24))


@route("/attendance/daily/apply", methods=["POST"])
def attendance_daily_apply():
    month = valid_month(request.form.get("month"))
    if payroll.is_final(month):
        flash("This month is finalised - attendance is locked.", "err")
    else:
        n = reports.apply_rollup(month)
        audit("attendance.daily_apply", "month", month, f"{n} employee(s) written to monthly attendance")
        flash(f"Monthly attendance for {pdfs.month_label(month)} updated from daily marks for {n} employee(s). "
              "Advance / other deductions were left untouched. Re-generate payslips to use it.", "ok")
    return redirect(url_for("attendance_daily_month", month=month))
