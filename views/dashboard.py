from common import *  # noqa: F401,F403
from web import route


def _prev_month(today):
    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return f"{y}-{m:02d}"


def _due_date(month):
    """EPF and ESI for wage month M are due on the 15th of the next month."""
    y, m = map(int, month.split("-"))
    y, m = (y, m + 1) if m < 12 else (y + 1, 1)
    return date(y, m, 15)


@route("/")
def dashboard():
    today = date.today()
    active = db.query(Employee).filter_by(status="Active").count()
    left = db.query(Employee).filter_by(status="Left").count()
    prev = _prev_month(today)

    # headcount by category
    by_cat = db.query(Category.name, func.count(Employee.id)).outerjoin(
        Employee, (Employee.category_id == Category.id) & (Employee.status == "Active")).group_by(Category.name).all()
    top = max([n for _, n in by_cat] + [1])

    # payroll trend (last 6 processed months)
    trend = []
    months = [m for (m,) in db.query(Payslip.month).distinct().order_by(Payslip.month.desc()).limit(6)]
    for m in reversed(months):
        t = payroll.month_totals(m)
        trend.append(dict(month=m, gross=t["gross"], net=t["net"], statutory=t["pf_total"] + t["esi_total"]))
    peak = max([x["gross"] for x in trend] + [1])
    last_month = months[0] if months else None
    last = payroll.month_totals(last_month) if last_month else None

    # statutory due dates: wage month = last month with payslips
    due = None
    if last_month:
        dd = _due_date(last_month)
        due = dict(month=last_month, date=dd, days=(dd - today).days, final=payroll.is_final(last_month))

    # things that need attention
    todo = []
    if active:
        if not db.query(Attendance).filter_by(month=prev).count():
            todo.append(dict(msg=f"Attendance for {pdfs.month_label(prev)} has not been uploaded", url=url_for("attendance", month=prev)))
        elif not db.query(Payslip).filter_by(month=prev).count():
            todo.append(dict(msg=f"Payroll for {pdfs.month_label(prev)} has not been generated", url=url_for("payroll_list", month=prev)))
        elif not payroll.is_final(prev):
            todo.append(dict(msg=f"Payroll for {pdfs.month_label(prev)} is not finalised", url=url_for("payroll_list", month=prev)))
    no_uan = db.query(Employee).join(Category).filter(Employee.status == "Active", Employee.uan.is_(None), Category.pf_applicable.is_(True)).count()
    no_esic = db.query(Employee).join(Category).filter(Employee.status == "Active", Employee.esic_no.is_(None), Category.esi_applicable.is_(True)).count()
    no_cat = db.query(Employee).filter(Employee.status == "Active", Employee.category_id.is_(None)).count()
    if no_uan:
        todo.append(dict(msg=f"{no_uan} active employee(s) with PF have no UAN", url=url_for("employees")))
    if no_esic:
        todo.append(dict(msg=f"{no_esic} active employee(s) with ESI have no ESIC number", url=url_for("employees")))
    if no_cat:
        todo.append(dict(msg=f"{no_cat} employee(s) have no salary category", url=url_for("employees")))

    if is_admin():
        n = db.query(LeaveApplication).filter_by(status="pending").count()
        if n:
            todo.append(dict(msg=f"{n} leave application(s) waiting for approval", url=url_for("leave_applications")))
        n = db.query(AttendanceRequest).filter_by(status="pending").count()
        if n:
            todo.append(dict(msg=f"{n} attendance correction request(s) waiting", url=url_for("attendance_requests")))
        no_doj = db.query(Employee).filter(Employee.status == "Active", Employee.doj.is_(None)).count()
        if no_doj:
            todo.append(dict(msg=f"{no_doj} active employee(s) have no joining date, so leave cannot accrue for them", url=url_for("employees")))
    S = get_settings()
    checklist = [
        ("Set the PF establishment code and ESI employer code", bool(S["pf_code"] and S["esi_code"]), url_for("settings")),
        ("Review salary structures against the notified minimum wage",
         db.query(Category).filter(Category.min_wage_day.isnot(None)).count() > 0, url_for("categories")),
        ("Add employees", active + left > 0, url_for("employee_upload")),
        ("Upload attendance for a month", db.query(Attendance).count() > 0, url_for("attendance")),
        ("Generate payroll", db.query(Payslip).count() > 0, url_for("payroll_list")),
    ] if is_admin() else []
    setup_done = all(c[1] for c in checklist) if checklist else True
    return render_template("dashboard.html", active=active, left=left, by_cat=by_cat, top=top, trend=trend, peak=peak,
                           last=last, last_month=last_month, due=due, todo=todo, checklist=checklist,
                           setup_done=setup_done)
