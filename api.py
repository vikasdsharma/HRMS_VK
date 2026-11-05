"""JSON API v1.  Auth: 'Authorization: Bearer hrms_...' (create tokens under Administration -> API access).

Scopes:  read (default)  |  write (create/update employees, attendance, generate payroll)
         pii  (include Aadhaar / PAN / bank account in employee responses; otherwise they are omitted)
Every token belongs to ONE organization and can never see another's data. Writes are audited as api:<token name>.
"""
import hashlib
import secrets
import time
from collections import defaultdict, deque
from datetime import date, datetime
from functools import wraps

from flask import Blueprint, g, jsonify, request

import importers
import payroll
from models import ApiToken, Attendance, Category, Employee, Payslip, db, set_org

bp = Blueprint("api", __name__, url_prefix="/api/v1")
RATE_PER_MIN = 120
_hits = defaultdict(deque)          # per-token sliding window (per worker process - a soft limit)


def new_token():
    raw = "hrms_" + secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def err(status, code, message, **extra):
    return jsonify(error=dict(code=code, message=message, **extra)), status


@bp.before_request
def _authenticate():
    set_org(None)
    g.user = g.org = None
    g.api_token = None
    if request.endpoint == "api.openapi":
        return None
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return err(401, "unauthorized", "Send 'Authorization: Bearer <token>'.")
    tok = db.query(ApiToken).filter_by(token_hash=hash_token(header[7:].strip())).first()
    if not tok or tok.revoked_at:
        return err(401, "unauthorized", "Unknown or revoked token.")
    from models import Organization
    org = db.get(Organization, tok.org_id)
    if not org or not org.is_active:
        return err(403, "org_inactive", "This organization is inactive.")
    now = time.monotonic()
    q = _hits[tok.id]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_PER_MIN:
        r = jsonify(error=dict(code="rate_limited", message=f"More than {RATE_PER_MIN} requests per minute."))
        r.status_code = 429
        r.headers["Retry-After"] = "30"
        return r
    q.append(now)
    g.api_token, g.org = tok, org
    set_org(org.id)
    tok.last_used_at = datetime.now()
    db.commit()
    return None


def scope(name):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if name not in (g.api_token.scopes or "").split(","):
                return err(403, "forbidden", f"This token lacks the '{name}' scope.")
            return fn(*a, **kw)
        return wrapper
    return deco


def _audit(action, entity, entity_id, detail):
    from models import AuditLog
    db.add(AuditLog(org_id=g.org.id, username=f"api:{g.api_token.name}", action=action, entity=entity,
                    entity_id=str(entity_id), detail=detail[:2000], ip=request.remote_addr))
    db.commit()


def _page():
    page = max(request.args.get("page", 1, type=int), 1)
    per = min(max(request.args.get("per", 50, type=int), 1), 200)
    return page, per


def _emp_json(e, pii):
    d = dict(id=e.id, code=e.emp_code, name=e.name, father_name=e.father_name, gender=e.gender,
             dob=e.dob.isoformat() if e.dob else None, doj=e.doj.isoformat() if e.doj else None,
             dol=e.dol.isoformat() if e.dol else None, status=e.status, designation=e.designation,
             department=e.department, mobile=e.mobile, uan=e.uan, esic_no=e.esic_no, ifsc=e.ifsc,
             category=e.category.name if e.category else None, eps_exempt=bool(e.eps_exempt))
    if pii:
        d.update(aadhaar=e.aadhaar, pan=e.pan, bank_account=e.bank_acct)
    return d


def _has(scope_name):
    return scope_name in (g.api_token.scopes or "").split(",")


# ---------------------------------------------------------------- endpoints
@bp.get("/me")
def me():
    t = g.api_token
    return jsonify(organization=g.org.name, token=t.name, scopes=t.scopes.split(","))


@bp.get("/employees")
@scope("read")
def list_employees():
    page, per = _page()
    q = db.query(Employee).order_by(Employee.emp_code)
    status = request.args.get("status")
    if status in ("Active", "Left"):
        q = q.filter(Employee.status == status)
    total = q.count()
    rows = q.limit(per).offset((page - 1) * per).all()
    return jsonify(page=page, per=per, total=total, items=[_emp_json(e, _has("pii")) for e in rows])


@bp.get("/employees/<code>")
@scope("read")
def get_employee(code):
    e = db.query(Employee).filter_by(emp_code=code).first()
    if not e:
        return err(404, "not_found", f"No employee with code {code}.")
    return jsonify(_emp_json(e, _has("pii")))


def _save_employee(code, body, creating):
    body = dict(body or {})
    data = {}
    for k in ("name", "father_name", "gender", "designation", "department", "mobile", "address", "aadhaar", "pan",
              "uan", "esic_no", "bank_name", "bank_acct", "ifsc"):
        if k in body:
            data[k] = importers.clean(body[k])
    if "bank_account" in body:
        data["bank_acct"] = importers.clean(body["bank_account"])
    errors = []
    for f in ("dob", "doj"):
        if f in body:
            try:
                data[f] = importers.parse_date(body[f])
            except ValueError as e:
                errors.append(f"{f}: {e}")
    emp = None if creating else db.query(Employee).filter_by(emp_code=code).first()
    if not creating and not emp:
        return err(404, "not_found", f"No employee with code {code}.")
    data["emp_code"] = code
    if not creating:
        data.setdefault("name", emp.name)
    cat = None
    if "category" in body:
        cat = db.query(Category).filter(Category.name == body["category"]).first()
        if not cat:
            errors.append(f"unknown salary category '{body['category']}'")
    elif creating:
        errors.append("Salary Category is required for new employees")
    errors += importers.validate(data, emp.id if emp else None)
    if errors:
        by_field, general = importers.split_errors(errors)
        return err(422, "validation_failed", "Some fields are invalid.", fields=by_field, other=general)
    if creating:
        emp = Employee(**{k: v for k, v in data.items() if v not in (None, "")})
        emp.category = cat
        db.add(emp)
    else:
        for k, v in data.items():
            setattr(emp, k, v)
        if cat:
            emp.category = cat
        if "status" in body and body["status"] in ("Active", "Left"):
            emp.status = body["status"]
    db.commit()
    _audit("api.employee.create" if creating else "api.employee.update", "employee", emp.id, f"code={code}")
    return jsonify(_emp_json(emp, _has("pii"))), (201 if creating else 200)


@bp.post("/employees")
@scope("write")
def create_employee():
    body = request.get_json(silent=True) or {}
    code = importers.clean(body.get("code"))
    if not code:
        return err(422, "validation_failed", "'code' is required.", fields={"code": "required"})
    if db.query(Employee).filter_by(emp_code=code).first():
        return err(409, "conflict", f"Employee {code} already exists. Use PATCH to change it.")
    return _save_employee(code, body, creating=True)


@bp.patch("/employees/<code>")
@scope("write")
def update_employee(code):
    return _save_employee(code, request.get_json(silent=True), creating=False)


@bp.get("/attendance/<month>")
@scope("read")
def get_attendance(month):
    m = _month(month)
    if m is None:
        return err(400, "bad_request", "month must be YYYY-MM")
    rows = db.query(Attendance).filter_by(month=m).all()
    return jsonify(month=m, finalised=payroll.is_final(m), items=[dict(
        code=a.employee.emp_code, present_days=a.present_days, paid_holidays=a.paid_holidays, ot_hours=a.ot_hours,
        advance=a.advance, other_deduction=a.other_deduction) for a in rows])


def _month(m):
    try:
        y, mo = map(int, m.split("-"))
        return f"{y}-{mo:02d}" if 2000 < y < 2100 and 1 <= mo <= 12 else None
    except ValueError:
        return None


@bp.put("/attendance/<month>")
@scope("write")
def put_attendance(month):
    """Body: {"rows": [{"emp_code": "E1", "present_days": 22, "paid_holidays": 4, "ot_hours": 0, "advance": 0,
    "other_deduction": 0}, ...]}.  All-or-nothing, same rules as the CSV upload."""
    m = _month(month)
    if m is None:
        return err(400, "bad_request", "month must be YYYY-MM")
    if m > date.today().strftime("%Y-%m"):
        return err(422, "validation_failed", "Attendance cannot be set for a future month.")
    if payroll.is_final(m):
        return err(409, "month_locked", f"{m} is finalised; an admin must unlock it in the web app first.")
    rows = (request.get_json(silent=True) or {}).get("rows")
    if not isinstance(rows, list) or not rows:
        return err(422, "validation_failed", "Send {'rows': [...]} with at least one row.")
    cols = ["emp_code", "present_days", "paid_holidays", "ot_hours", "advance", "other_deduction"]
    lines = [",".join(cols)]
    for r in rows:
        if not isinstance(r, dict):
            return err(422, "validation_failed", "Each row must be an object.")
        lines.append(",".join(str(r.get(c, 0) if c != "emp_code" else r.get(c, "")).replace(",", " ") for c in cols))
    result = importers.import_attendance("\n".join(lines), m)
    if result["errors"]:
        return err(422, "validation_failed", "Nothing was saved.",
                   rows=[dict(line=ln, problem=msg) for ln, msg in result["errors"]])
    _audit("api.attendance.put", "month", m, f"{result['saved']} row(s)")
    return jsonify(month=m, saved=result["saved"], warnings=result["warnings"])


@bp.post("/payroll/<month>/generate")
@scope("write")
def generate_payroll(month):
    m = _month(month)
    if m is None:
        return err(400, "bad_request", "month must be YYYY-MM")
    try:
        n, skipped = payroll.generate_month(m)
    except payroll.PayrollLocked as e:
        return err(409, "month_locked", str(e))
    _audit("api.payroll.generate", "month", m, f"{n} payslip(s)")
    return jsonify(month=m, generated=n, skipped=skipped)


@bp.get("/payroll/<month>")
@scope("read")
def get_payroll(month):
    m = _month(month)
    if m is None:
        return err(400, "bad_request", "month must be YYYY-MM")
    run = payroll.get_run(m)
    slips = db.query(Payslip).filter_by(month=m).all()
    return jsonify(month=m, status=run.status if run else "draft", totals=payroll.month_totals(m) if slips else None,
                   payslips=[dict(code=s.employee.emp_code, name=s.employee.name, paid_days=s.paid_days, gross=s.gross,
                                  pf_employee=s.pf_ee, esi_employee=s.esi_ee, deductions=s.total_deduction, net=s.net)
                             for s in slips])


@bp.get("/openapi.json")
def openapi():
    sec = [{"bearer": []}]
    p = lambda summary, tag="HRMS": {"summary": summary, "tags": [tag], "security": sec}   # noqa: E731
    return jsonify({
        "openapi": "3.0.3", "info": {"title": "HRMS API", "version": "1.0",
                                     "description": "Bearer-token JSON API. One token = one organization."},
        "servers": [{"url": "/api/v1"}],
        "components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}},
        "paths": {
            "/me": {"get": p("Who am I (organization, scopes)")},
            "/employees": {"get": p("List employees (page, per, status)"), "post": p("Create an employee (write)")},
            "/employees/{code}": {"get": p("Get one employee"), "patch": p("Update an employee (write)")},
            "/attendance/{month}": {"get": p("Monthly attendance"), "put": p("Replace attendance rows (write)")},
            "/payroll/{month}": {"get": p("Payroll status, totals and payslips")},
            "/payroll/{month}/generate": {"post": p("Generate payslips (write)")},
        }})
