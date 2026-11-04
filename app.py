import io
import os
import secrets
import zipfile
from datetime import date
from functools import wraps

from flask import (Flask, render_template, request, redirect, url_for, session, flash, send_file, abort)
from werkzeug.security import check_password_hash, generate_password_hash

import compliance
import pdfs
import importers
import payroll
from models import (db, init_db, User, Setting, Category, Employee, Attendance, Payslip, get_settings,
                    DEFAULT_SETTINGS, BASE_DIR)

app = Flask(__name__)
_key_file = os.path.join(BASE_DIR, ".secret_key")
if not os.path.exists(_key_file):
    with open(_key_file, "w") as fh:
        fh.write(secrets.token_hex(32))
app.secret_key = open(_key_file).read().strip()
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
init_db()


@app.teardown_appcontext
def _shutdown(exc=None):
    db.remove()


@app.before_request
def _auth():
    if request.endpoint in ("login", "static"):
        return
    if "uid" not in session:
        return redirect(url_for("login", next=request.path))


@app.context_processor
def _ctx():
    return dict(S=get_settings(), today=date.today(), cur_month=date.today().strftime("%Y-%m"),
                month_label=pdfs.month_label,
                cats_all=db.query(Category).order_by(Category.name).all() if "uid" in session else [])


def valid_month(m):
    try:
        y, mo = map(int, (m or "").split("-"))
        assert 2000 < y < 2100 and 1 <= mo <= 12
        return m
    except Exception:
        abort(400, "Invalid month")


def pdf_response(buf, name):
    return send_file(buf, mimetype="application/pdf", download_name=name)


# ---------- auth ----------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = db.query(User).filter_by(username=request.form["username"].strip()).first()
        if u and check_password_hash(u.pw_hash, request.form["password"]):
            session["uid"] = u.id
            session["uname"] = u.username
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard"))
        flash("Invalid username or password", "err")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/password", methods=["GET", "POST"])
def change_password():
    if request.method == "POST":
        u = db.get(User, session["uid"])
        if not check_password_hash(u.pw_hash, request.form["old"]):
            flash("Current password is wrong", "err")
        elif len(request.form["new"]) < 8:
            flash("New password must be at least 8 characters", "err")
        else:
            u.pw_hash = generate_password_hash(request.form["new"])
            db.commit()
            flash("Password changed", "ok")
            return redirect(url_for("dashboard"))
    return render_template("password.html")


# ---------- dashboard ----------
@app.route("/")
def dashboard():
    total = db.query(Employee).count()
    active = db.query(Employee).filter_by(status="Active").count()
    months = [m for (m,) in db.query(Payslip.month).distinct().order_by(Payslip.month.desc()).limit(6)]
    last = payroll.month_totals(months[0]) if months else None
    default_pw = check_password_hash(db.get(User, session["uid"]).pw_hash, "admin123")
    missing_uan = db.query(Employee).filter(Employee.status == "Active", Employee.uan.is_(None)).count()
    return render_template("dashboard.html", total=total, active=active, months=months,
                           last=last, last_month=months[0] if months else None,
                           default_pw=default_pw, missing_uan=missing_uan)


# ---------- employees ----------
FORM_FIELDS = [k for k, _ in importers.EMP_COLUMNS if k not in ("category",)]


@app.route("/employees")
def employees():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "Active")
    query = db.query(Employee)
    if status in ("Active", "Left"):
        query = query.filter_by(status=status)
    if q:
        like = f"%{q}%"
        query = query.filter(Employee.name.ilike(like) | Employee.emp_code.ilike(like) |
                             Employee.aadhaar.like(like) | Employee.uan.like(like) |
                             Employee.pan.ilike(like) | Employee.esic_no.like(like) | Employee.mobile.like(like))
    return render_template("employees.html", emps=query.order_by(Employee.emp_code).all(), q=q, status=status)


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
        except ValueError:
            errs.append("Personal daily basic must be a number")
            data["basic_override_day"] = None
    return data, errs


@app.route("/employees/new", methods=["GET", "POST"])
@app.route("/employees/<int:eid>/edit", methods=["GET", "POST"])
def employee_form(eid=None):
    emp = db.get(Employee, eid) if eid else None
    if eid and not emp:
        abort(404)
    cats = db.query(Category).order_by(Category.name).all()
    if request.method == "POST":
        data, errs = _form_data()
        errs += importers.validate(data, eid)
        cid = request.form.get("category_id", type=int)
        if not cid:
            errs.append("Select a salary category")
        status = request.form.get("status", "Active")
        if errs:
            for e in errs:
                flash(e, "err")
            form = type("F", (), {**data, "category_id": cid, "status": status, "id": eid})()
            return render_template("employee_form.html", emp=form, cats=cats, editing=bool(eid))
        if not emp:
            emp = Employee()
            db.add(emp)
        for k, v in data.items():
            setattr(emp, k, v)
        emp.category_id = cid
        emp.status = status
        if status == "Left" and not emp.dol:
            emp.dol = date.today()
        db.commit()
        flash(f"Saved {emp.name}", "ok")
        return redirect(url_for("employees"))
    return render_template("employee_form.html", emp=emp, cats=cats, editing=bool(eid))


@app.route("/employees/template.xlsx")
def employee_template():
    return send_file(importers.template_xlsx(), download_name="employee_upload_template.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/employees/upload", methods=["GET", "POST"])
def employee_upload():
    result = None
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename.lower().endswith((".xlsx", ".xlsm")):
            flash("Please choose an .xlsx file", "err")
        else:
            try:
                result = importers.import_employees(f.stream)
            except Exception as e:
                db.rollback()
                flash(f"Could not read file: {e}", "err")
    return render_template("employee_upload.html", result=result)


@app.route("/employees/export.xlsx")
def employee_export():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append([h for _, h in importers.EMP_COLUMNS])
    for e in db.query(Employee).order_by(Employee.emp_code):
        row = []
        for k, _ in importers.EMP_COLUMNS:
            if k == "category":
                row.append(e.category.name if e.category else "")
            else:
                v = getattr(e, k)
                row.append(v.strftime("%d-%m-%Y") if hasattr(v, "strftime") else v)
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, download_name="employees.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------- salary categories ----------
@app.route("/categories")
def categories():
    return render_template("categories.html", cats=db.query(Category).order_by(Category.name).all())


@app.route("/categories/new", methods=["GET", "POST"])
@app.route("/categories/<int:cid>/edit", methods=["GET", "POST"])
def category_form(cid=None):
    cat = db.get(Category, cid) if cid else None
    if request.method == "POST":
        name = request.form["name"].strip()
        if not name:
            flash("Name is required", "err")
            return render_template("category_form.html", cat=cat)
        dup = db.query(Category).filter(Category.name == name)
        if cid:
            dup = dup.filter(Category.id != cid)
        if dup.first():
            flash("A category with that name exists", "err")
            return render_template("category_form.html", cat=cat)
        if not cat:
            cat = Category()
            db.add(cat)
        cat.name = name
        cat.description = request.form.get("description", "").strip()
        try:
            for k in ("basic_day", "da_day", "hra_day", "other_day"):
                setattr(cat, k, max(float(request.form.get(k) or 0), 0))
            cat.ot_multiplier = float(request.form.get("ot_multiplier") or 2)
        except ValueError:
            db.rollback()
            flash("Amounts must be numbers", "err")
            return render_template("category_form.html", cat=cat)
        cat.pf_applicable = "pf_applicable" in request.form
        cat.esi_applicable = "esi_applicable" in request.form
        cat.pf_ceiling = "pf_ceiling" in request.form
        db.commit()
        flash("Category saved. Re-generate payslips to apply changes to a month.", "ok")
        return redirect(url_for("categories"))
    return render_template("category_form.html", cat=cat)


@app.route("/categories/<int:cid>/delete", methods=["POST"])
def category_delete(cid):
    cat = db.get(Category, cid)
    if cat and cat.employees:
        flash("Cannot delete: employees are assigned to this category", "err")
    elif cat:
        db.delete(cat)
        db.commit()
        flash("Category deleted", "ok")
    return redirect(url_for("categories"))


@app.route("/categories/bulk-assign", methods=["POST"])
def bulk_assign():
    ids = request.form.getlist("emp_ids", type=int)
    cid = request.form.get("category_id", type=int)
    if ids and cid and db.get(Category, cid):
        db.query(Employee).filter(Employee.id.in_(ids)).update({"category_id": cid}, synchronize_session=False)
        db.commit()
        flash(f"Category assigned to {len(ids)} employee(s)", "ok")
    else:
        flash("Select employees and a category", "err")
    return redirect(request.referrer or url_for("employees"))


# ---------- attendance ----------
@app.route("/attendance", methods=["GET", "POST"])
def attendance():
    month = valid_month(request.values.get("month") or date.today().strftime("%Y-%m"))
    result = None
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename.lower().endswith(".csv"):
            flash("Please choose a .csv file", "err")
        else:
            try:
                text = f.read().decode("utf-8-sig")
            except UnicodeDecodeError:
                text = None
                flash("File is not UTF-8 text. In Excel use 'Save As > CSV UTF-8'.", "err")
            if text is not None:
                result = importers.import_attendance(text, month)
    rows = db.query(Attendance).join(Employee).filter(Attendance.month == month).order_by(Employee.emp_code).all()
    return render_template("attendance.html", month=month, rows=rows, result=result)


@app.route("/attendance/template.csv")
def attendance_template():
    month = request.args.get("month")
    lines = ["emp_code,present_days,paid_holidays,ot_hours,advance,other_deduction"]
    for e in db.query(Employee).filter_by(status="Active").order_by(Employee.emp_code):
        lines.append(f"{e.emp_code},0,0,0,0,0")
    buf = io.BytesIO("\n".join(lines).encode("utf-8-sig"))
    return send_file(buf, download_name=f"attendance_{month or 'template'}.csv", mimetype="text/csv")


@app.route("/attendance/<int:aid>/delete", methods=["POST"])
def attendance_delete(aid):
    a = db.get(Attendance, aid)
    if a:
        month = a.month
        db.query(Payslip).filter_by(emp_id=a.emp_id, month=month).delete()
        db.delete(a)
        db.commit()
        return redirect(url_for("attendance", month=month))
    return redirect(url_for("attendance"))


# ---------- payroll ----------
@app.route("/payroll")
def payroll_list():
    month = valid_month(request.args.get("month") or date.today().strftime("%Y-%m"))
    slips = db.query(Payslip).join(Employee).filter(Payslip.month == month).order_by(Employee.emp_code).all()
    att_count = db.query(Attendance).filter_by(month=month).count()
    T = payroll.month_totals(month) if slips else None
    warnings = []
    for s in slips:
        e = s.employee
        if s.epf_wages and not e.uan:
            warnings.append(f"{e.emp_code} {e.name}: PF applicable but UAN missing - excluded from ECR")
        if s.esi_wages and not e.esic_no:
            warnings.append(f"{e.emp_code} {e.name}: ESI applicable but ESIC IP number missing")
    return render_template("payroll.html", month=month, slips=slips, att_count=att_count, T=T, warnings=warnings)


@app.route("/payroll/generate", methods=["POST"])
def payroll_generate():
    month = valid_month(request.form.get("month"))
    n, skipped = payroll.generate_month(month)
    flash(f"Generated {n} payslip(s) for {pdfs.month_label(month)}", "ok" if n else "err")
    for s in skipped:
        flash(s, "err")
    return redirect(url_for("payroll_list", month=month))


@app.route("/payroll/payslips.pdf")
def payslips_bulk_pdf():
    month = valid_month(request.args.get("month"))
    slips = db.query(Payslip).join(Employee).filter(Payslip.month == month).order_by(Employee.emp_code).all()
    if not slips:
        abort(404, "No payslips for this month")
    return pdf_response(pdfs.payslips_pdf(slips, get_settings()), f"payslips_{month}.pdf")


@app.route("/payroll/payslips.zip")
def payslips_zip():
    month = valid_month(request.args.get("month"))
    S = get_settings()
    slips = db.query(Payslip).filter_by(month=month).all()
    if not slips:
        abort(404, "No payslips for this month")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for s in slips:
            z.writestr(f"payslip_{s.employee.emp_code}_{month}.pdf", pdfs.payslips_pdf([s], S).read())
    buf.seek(0)
    return send_file(buf, download_name=f"payslips_{month}.zip", mimetype="application/zip")


@app.route("/payroll/slip/<int:sid>.pdf")
def payslip_pdf(sid):
    s = db.get(Payslip, sid) or abort(404)
    return pdf_response(pdfs.payslips_pdf([s], get_settings()), f"payslip_{s.employee.emp_code}_{s.month}.pdf")


@app.route("/payroll/register.xlsx")
def salary_register():
    month = valid_month(request.args.get("month"))
    return send_file(compliance.payment_register_xlsx(month), download_name=f"salary_register_{month}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------- compliance ----------
@app.route("/compliance")
def compliance_page():
    month = valid_month(request.args.get("month") or date.today().strftime("%Y-%m"))
    T = payroll.month_totals(month)
    _, pf_missing = compliance.ecr_text(month)
    _, esi_missing = compliance.esi_xlsx(month)
    return render_template("compliance.html", month=month, T=T, pf_missing=pf_missing, esi_missing=esi_missing)


@app.route("/compliance/ecr.txt")
def ecr_download():
    month = valid_month(request.args.get("month"))
    text, _ = compliance.ecr_text(month)
    return send_file(io.BytesIO(text.encode("utf-8")), download_name=f"ECR_{month}.txt", mimetype="text/plain")


@app.route("/compliance/esi.xlsx")
def esi_download():
    month = valid_month(request.args.get("month"))
    buf, _ = compliance.esi_xlsx(month)
    return send_file(buf, download_name=f"ESI_MC_{month}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/compliance/epf-challan.pdf")
def epf_challan():
    month = valid_month(request.args.get("month"))
    return pdf_response(pdfs.epf_challan_pdf(month, payroll.month_totals(month), get_settings()),
                        f"EPF_challan_{month}.pdf")


@app.route("/compliance/esi-challan.pdf")
def esi_challan():
    month = valid_month(request.args.get("month"))
    return pdf_response(pdfs.esi_challan_pdf(month, payroll.month_totals(month), get_settings()),
                        f"ESI_challan_{month}.pdf")


# ---------- settings ----------
@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        for k in DEFAULT_SETTINGS:
            if k in request.form:
                v = request.form[k].strip()
                row = db.get(Setting, k) or Setting(key=k)
                row.value = v
                db.add(row)
        db.commit()
        flash("Settings saved", "ok")
        return redirect(url_for("settings"))
    return render_template("settings.html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
