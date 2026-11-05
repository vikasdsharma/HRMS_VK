from sqlalchemy.orm import joinedload

from common import *  # noqa: F401,F403
from web import route, template_filter

FORM_FIELDS = [k for k, _ in importers.EMP_COLUMNS if k != "category"]
SENSITIVE = ("aadhaar", "pan", "uan", "esic_no", "bank_acct")   # shown half-hidden unless an admin reveals
SORTS = {"code": Employee.emp_code, "name": Employee.name, "status": Employee.status, "doj": Employee.doj}


@template_filter("mask")
def mask(v):
    """Hide the first half of an ID number, e.g. 123412341234 -> XXXXXX341234."""
    if not v:
        return "-"
    s = str(v)
    k = len(s) // 2
    return "X" * k + s[k:]


@route("/employees")
def employees():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "Active")
    cat = request.args.get("cat", type=int)
    sort = request.args.get("sort", "code")
    direction = "desc" if request.args.get("dir") == "desc" else "asc"
    per = request.args.get("per", 25, type=int)
    per = per if per in (25, 50, 100) else 25
    query = db.query(Employee).options(joinedload(Employee.category))
    if status in ("Active", "Left"):
        query = query.filter(Employee.status == status)
    if cat:
        query = query.filter(Employee.category_id == cat)
    if q:
        like = f"%{q}%"
        conds = [Employee.name.ilike(like), Employee.emp_code.ilike(like), Employee.uan.like(like),
                 Employee.esic_no.like(like), Employee.mobile.like(like)]
        digits = re.sub(r"\D", "", q)
        if len(digits) == 12:                       # Aadhaar / PAN are encrypted: exact match through the blind index
            conds.append(Employee.aadhaar_idx == blind_index("aadhaar", digits))
        if re.fullmatch(r"[A-Za-z]{5}\d{4}[A-Za-z]", q.strip()):
            conds.append(Employee.pan_idx == blind_index("pan", q))
        query = query.filter(or_(*conds))
    col = SORTS.get(sort, Employee.emp_code)
    if sort == "cat":
        query = query.outerjoin(Category, Employee.category_id == Category.id)
        col = Category.name
    query = query.order_by(col.desc() if direction == "desc" else col.asc(), Employee.id)
    p = paginate(query, request.args.get("page", 1, type=int), per)
    reveal = request.args.get("reveal") == "1" and is_admin()
    if reveal:
        audit("employee.reveal", "employee", None, f"list view, page {p.page}, status={status}")
    args = {k: v for k, v in dict(q=q, status=status, cat=cat, sort=sort, dir=direction, per=per).items() if v not in ("", None)}
    return render_template("employees.html", p=p, q=q, status=status, cat=cat, sort=sort, direction=direction,
                           per=per, reveal=reveal, args=args)


@route("/employees/<int:eid>")
def employee_detail(eid):
    e = db.get(Employee, eid) or abort(404)
    reveal = request.args.get("reveal") == "1" and is_admin()
    if reveal:
        audit("employee.reveal", "employee", e.id, "profile")
    slips = db.query(Payslip).filter_by(emp_id=e.id).order_by(Payslip.month.desc()).limit(12).all()
    atts = {a.month: a for a in db.query(Attendance).filter_by(emp_id=e.id)}
    rate = payroll.rate_for(e.category, date.today().strftime("%Y-%m")) if e.category else None
    events = []
    if is_admin():
        events = db.query(AuditLog).filter(AuditLog.org_id == current_org_id(), AuditLog.entity == "employee",
                                           AuditLog.entity_id == str(e.id)).order_by(AuditLog.at.desc()).limit(15).all()
    return render_template("employee_detail.html", e=e, slips=slips, atts=atts, rate=rate, reveal=reveal, events=events)


def _form_data():
    data = {}
    for k in FORM_FIELDS:
        v = (request.form.get(k) or "").strip()
        data[k] = v or None
    errs = []
    for f in importers.DATE_FIELDS:
        try:
            data[f] = importers.parse_date(data[f])
        except ValueError as e:
            errs.append(f"{f}: {e}")
            data[f] = None
    if data.get("basic_override_day"):
        try:
            data["basic_override_day"] = float(data["basic_override_day"])
            if data["basic_override_day"] < 0:
                raise ValueError
        except ValueError:
            errs.append("Personal Daily Basic must be a number")
            data["basic_override_day"] = None
    return data, errs


@route("/employees/new", methods=["GET", "POST"])
@route("/employees/<int:eid>/edit", methods=["GET", "POST"])
def employee_form(eid=None):
    emp = db.get(Employee, eid) if eid else None
    if eid and not emp:
        abort(404)
    cats = db.query(Category).order_by(Category.name).all()
    reveal = (request.form.get("reveal") or request.args.get("reveal")) == "1" and is_admin()
    if reveal and request.method == "GET" and emp:
        audit("employee.reveal", "employee", emp.id, "edit form")
    if request.method == "POST":
        data, errs = _form_data()
        if emp and not reveal:
            for f in SENSITIVE:          # hidden-mode: a blank box means "keep the current value"
                if data.get(f) is None:
                    data[f] = getattr(emp, f)
        errs += importers.validate(data, eid)
        cid = request.form.get("category_id", type=int)
        if not cid or not any(c.id == cid for c in cats):
            errs.append("Salary Category is required")
        status = request.form.get("status", "Active")
        if errs:
            ferrs, general = importers.split_errors(errs)
            flash("Please correct the highlighted fields." + (" " + " ".join(general) if general else ""), "err")
            form = type("F", (), {**data, "category_id": cid, "status": status, "id": eid,
                                  "eps_exempt": "eps_exempt" in request.form})()
            return render_template("employee_form.html", emp=form, cats=cats, editing=bool(eid), reveal=reveal,
                                   stored=emp, show_vals=reveal, ferrs=ferrs)
        is_new = emp is None
        if is_new:
            emp = Employee()
            db.add(emp)
        changed = [] if is_new else [k for k, v in data.items() if getattr(emp, k) != v]
        if not is_new and emp.category_id != cid:
            changed.append("category")
        if not is_new and emp.status != status:
            changed.append(f"status {emp.status}->{status}")
        for k, v in data.items():
            setattr(emp, k, v)
        emp.category_id = cid
        emp.status = status
        emp.eps_exempt = "eps_exempt" in request.form
        if status == "Left" and not emp.dol:
            emp.dol = date.today()
        db.commit()
        # field NAMES only - never the values of personal data
        audit("employee.create" if is_new else "employee.update", "employee", emp.id,
              f"code={emp.emp_code}" + ("" if is_new else f" changed: {', '.join(changed) or 'nothing'}"))
        flash(f"Saved {emp.name}", "ok")
        return redirect(url_for("employee_detail", eid=emp.id))
    return render_template("employee_form.html", emp=emp or type("F", (), {})(), cats=cats, editing=bool(eid),
                           reveal=reveal, stored=emp, show_vals=reveal or not emp, ferrs={})


@route("/employees/template.xlsx")
def employee_template():
    return xlsx_response(importers.template_xlsx(), "employee_upload_template.xlsx")


def _log_employee_upload(fname, created, updated, errs, data, rows=None, note=None):
    if note:
        details = note
    elif errs:
        details = "; ".join(f"row {r}: {m}" for r, m in errs[:5]) + (f" (+{len(errs) - 5} more)" if len(errs) > 5 else "")
    else:
        details = None
    db.add(EmployeeUpload(filename=fname, uploaded_by=g.user.username, created=created, updated=updated,
                          status="Rejected" if (errs or note) else "Saved", details=details,
                          file_data=data, row_results=json.dumps(rows) if rows is not None else None))
    db.commit()


@route("/employees/upload/<int:uid>/report.xlsx")
def employee_upload_report(uid):
    u = db.get(EmployeeUpload, uid) or abort(404)
    if not u.file_data or u.row_results is None:
        flash("No report is available for this upload (the file could not be read, it was uploaded before "
              "reports existed, or its stored copy was removed by the retention policy).", "err")
        return redirect(url_for("employee_upload"))
    buf = importers.build_upload_report(u.file_data, json.loads(u.row_results), dict(
        filename=u.filename, uploaded_by=u.uploaded_by, uploaded_at=u.uploaded_at, status=u.status,
        created=u.created, updated=u.updated, details=u.details))
    if buf is None:
        flash("The stored file could not be opened.", "err")
        return redirect(url_for("employee_upload"))
    stem = os.path.splitext(u.filename or "upload")[0]
    return xlsx_response(buf, f"{stem}_report_{u.uploaded_at.strftime('%Y%m%d_%H%M')}.xlsx")


@route("/employees/upload", methods=["GET", "POST"])
def employee_upload():
    result = None
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename.lower().endswith((".xlsx", ".xlsm")):
            flash("Please choose an .xlsx file", "err")
        else:
            fname = os.path.basename(f.filename)[:200]
            data = f.read()
            try:
                xl.check_xlsx_bytes(data)
                result = importers.import_employees(io.BytesIO(data))
            except xl.UploadRejected as e:
                flash(str(e), "err")
                _log_employee_upload(fname, 0, 0, [], None, note=str(e))
            except Exception as e:
                db.rollback()
                log.warning("employee upload unreadable: %s", e)
                flash(f"Could not read file: {e}", "err")
                _log_employee_upload(fname, 0, 0, [], data, note=f"Could not read file: {e}"[:300])
            else:
                _log_employee_upload(fname, result["created"], result["updated"], result["errors"], data,
                                     rows=result["rows"])
                audit("employee.upload", "upload", None, f"{fname}: {result['created']} created, "
                      f"{result['updated']} updated, {len(result['errors'])} error row(s)")
    history = db.query(EmployeeUpload).order_by(EmployeeUpload.uploaded_at.desc(), EmployeeUpload.id.desc()).limit(100).all()
    return render_template("employee_upload.html", result=result, history=history)


@route("/employees/export.xlsx")
@admin_required
def employee_export():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append([h for _, h in importers.EMP_COLUMNS])
    for e in db.query(Employee).options(joinedload(Employee.category)).order_by(Employee.emp_code):
        row = []
        for k, _ in importers.EMP_COLUMNS:
            if k == "category":
                row.append(e.category.name if e.category else "")
            else:
                v = getattr(e, k)
                row.append(v.strftime("%d-%m-%Y") if hasattr(v, "strftime") else v)
        ws.append(row)
    audit("employee.export", "employee", None, f"{ws.max_row - 1} rows, full personal data")
    return xlsx_response(xl.to_buffer(wb), "employees.xlsx")
