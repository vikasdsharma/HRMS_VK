"""Employee validation, Excel bulk upload, attendance CSV upload."""
import csv
import io
import re
from datetime import date, datetime
from openpyxl import Workbook, load_workbook
from models import db, Employee, Category, Attendance

EMP_COLUMNS = [
    ("emp_code", "Employee Code*"), ("name", "Name*"), ("father_name", "Father/Husband Name"),
    ("gender", "Gender (M/F/O)"), ("dob", "DOB (DD-MM-YYYY)"), ("doj", "Date of Joining (DD-MM-YYYY)"),
    ("designation", "Designation"), ("department", "Department"), ("mobile", "Mobile"),
    ("address", "Address"), ("aadhaar", "Aadhaar (12 digit)"), ("pan", "PAN"), ("uan", "UAN (12 digit)"),
    ("esic_no", "ESIC IP No (10/17 digit)"), ("bank_name", "Bank Name"), ("bank_acct", "Bank Account No"),
    ("ifsc", "IFSC"), ("category", "Salary Category*"), ("basic_override_day", "Personal Daily Basic (optional)"),
]
DATE_FIELDS = {"dob", "doj"}
UNIQUE_FIELDS = [("aadhaar", "Aadhaar"), ("pan", "PAN"), ("uan", "UAN"), ("esic_no", "ESIC No"),
                 ("emp_code", "Employee Code")]


def parse_date(v):
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"bad date '{s}' (use DD-MM-YYYY)")


def clean(v):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip()
    return s or None


def validate(data, existing_id=None):
    """data: dict of cleaned values. Returns list of error strings (also normalises in place)."""
    errs = []
    if not data.get("name"):
        errs.append("Name is required")
    if not data.get("emp_code"):
        errs.append("Employee code is required")
    for f in ("aadhaar", "uan"):
        if data.get(f):
            data[f] = re.sub(r"\s|-", "", str(data[f]))
            if not re.fullmatch(r"\d{12}", data[f]):
                errs.append(f"{f.upper()} must be exactly 12 digits")
    if data.get("pan"):
        data["pan"] = data["pan"].upper().replace(" ", "")
        if not re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", data["pan"]):
            errs.append("PAN format invalid (e.g. ABCDE1234F)")
    if data.get("esic_no"):
        data["esic_no"] = re.sub(r"\s|-", "", str(data["esic_no"]))
        if not re.fullmatch(r"\d{10}|\d{17}", data["esic_no"]):
            errs.append("ESIC number must be 10 or 17 digits")
    if data.get("mobile"):
        data["mobile"] = re.sub(r"[\s\-+]|^91(?=\d{10}$)", "", str(data["mobile"]))
        if not re.fullmatch(r"[6-9]\d{9}", data["mobile"]):
            errs.append("Mobile must be a 10-digit Indian number")
    if data.get("ifsc"):
        data["ifsc"] = data["ifsc"].upper().replace(" ", "")
        if not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", data["ifsc"]):
            errs.append("IFSC format invalid")
    if data.get("gender"):
        g = data["gender"].strip().upper()[:1]
        if g not in "MFO":
            errs.append("Gender must be M, F or O")
        data["gender"] = g
    for f, label in UNIQUE_FIELDS:
        if data.get(f):
            q = db.query(Employee).filter(getattr(Employee, f) == data[f])
            if existing_id:
                q = q.filter(Employee.id != existing_id)
            if q.first():
                errs.append(f"{label} {data[f]} already belongs to another employee")
    return errs


def template_xlsx():
    wb = Workbook()
    ws = wb.active
    ws.title = "Employees"
    ws.append([h for _, h in EMP_COLUMNS])
    ws.append(["E001", "Ramesh Kumar", "Suresh Kumar", "M", "15-08-1995", "01-04-2026", "Helper", "Production",
               "9876543210", "Village Kharkhoda, Sonipat", "123412341234", "ABCDE1234F", "100123456789",
               "1234567890", "State Bank of India", "30123456789", "SBIN0000123", "Unskilled", ""])
    for i, _ in enumerate(EMP_COLUMNS, 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = 22
    for col in ("K", "M", "N", "P", "I"):   # keep long numbers as text
        for r in range(2, 502):
            ws[f"{col}{r}"].number_format = "@"
    cats = wb.create_sheet("Categories")
    cats.append(["Valid Salary Categories"])
    for c in db.query(Category).order_by(Category.name):
        cats.append([c.name])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def import_employees(file_stream):
    """Create new / update existing (matched on Employee Code). Returns summary dict."""
    wb = load_workbook(file_stream, data_only=True)
    ws = wb["Employees"] if "Employees" in wb.sheetnames else wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return dict(created=0, updated=0, errors=[("-", "Empty file")])
    header_map = {}
    labels = {h.lower(): k for k, h in EMP_COLUMNS}
    keys = {k for k, _ in EMP_COLUMNS}
    for idx, h in enumerate(rows[0]):
        if h is None:
            continue
        hl = str(h).strip().lower()
        if hl in labels:
            header_map[idx] = labels[hl]
        elif hl in keys:
            header_map[idx] = hl
    if "emp_code" not in header_map.values():
        return dict(created=0, updated=0, errors=[("-", "Header row not recognised - download the template")])
    cats = {c.name.lower(): c for c in db.query(Category).all()}
    created = updated = 0
    errors = []
    seen = set()
    for rno, row in enumerate(rows[1:], start=2):
        if row is None or all(v in (None, "") for v in row):
            continue
        data = {k: clean(row[i]) if i < len(row) else None for i, k in header_map.items()}
        errs = []
        code = data.get("emp_code")
        if code in seen:
            errs.append("duplicate Employee Code inside this file")
        seen.add(code)
        for f in DATE_FIELDS:
            try:
                data[f] = parse_date(data.get(f))
            except ValueError as e:
                errs.append(f"{f}: {e}")
        cat = None
        if data.get("category"):
            cat = cats.get(data["category"].lower())
            if not cat:
                errs.append(f"unknown salary category '{data['category']}'")
        data.pop("category", None)
        if data.get("basic_override_day"):
            try:
                data["basic_override_day"] = float(data["basic_override_day"])
            except ValueError:
                errs.append("Personal Daily Basic must be a number")
                data["basic_override_day"] = None
        emp = db.query(Employee).filter_by(emp_code=code).first() if code else None
        errs += validate(data, emp.id if emp else None)
        if not emp and not cat:
            errs.append("Salary Category is required for new employees")
        if errs:
            errors.append((rno, "; ".join(errs)))
            continue
        if emp:
            for k, v in data.items():
                if v not in (None, ""):
                    setattr(emp, k, v)
            if cat:
                emp.category = cat
            updated += 1
        else:
            emp = Employee(**{k: v for k, v in data.items() if v not in (None, "")})
            emp.category = cat
            db.add(emp)
            created += 1
        db.flush()
    if errors:
        db.rollback()      # all-or-nothing keeps the register consistent
        return dict(created=0, updated=0, errors=errors, rolled_back=True)
    db.commit()
    return dict(created=created, updated=updated, errors=[])


ATT_TEMPLATE = "emp_code,present_days,paid_holidays,ot_hours,advance,other_deduction\nE001,24,4,10,0,0\n"


def import_attendance(text, month):
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return dict(saved=0, errors=[("-", "Empty file")])
    reader.fieldnames = [f.strip().lower() for f in reader.fieldnames]
    if "emp_code" not in reader.fieldnames or "present_days" not in reader.fieldnames:
        return dict(saved=0, errors=[("-", "CSV needs columns emp_code and present_days")])
    errors, parsed = [], []
    for n, row in enumerate(reader, start=2):
        code = (row.get("emp_code") or "").strip()
        if not code:
            continue
        emp = db.query(Employee).filter_by(emp_code=code).first()
        if not emp:
            errors.append((n, f"employee code '{code}' not found"))
            continue
        try:
            vals = {k: float((row.get(k) or 0) or 0) for k in
                    ("present_days", "paid_holidays", "ot_hours", "advance", "other_deduction")}
        except ValueError:
            errors.append((n, "non-numeric value"))
            continue
        if any(v < 0 for v in vals.values()):
            errors.append((n, "negative value"))
            continue
        import calendar
        y, m = map(int, month.split("-"))
        if vals["present_days"] + vals["paid_holidays"] > calendar.monthrange(y, m)[1]:
            errors.append((n, f"{code}: present + paid holidays exceed days in month"))
            continue
        parsed.append((emp, vals))
    if errors:
        return dict(saved=0, errors=errors, rolled_back=True)
    for emp, vals in parsed:
        a = db.query(Attendance).filter_by(emp_id=emp.id, month=month).first()
        if not a:
            a = Attendance(emp_id=emp.id, month=month)
            db.add(a)
        for k, v in vals.items():
            setattr(a, k, v)
    db.commit()
    return dict(saved=len(parsed), errors=[])
