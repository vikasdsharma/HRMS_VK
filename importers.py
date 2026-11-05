"""Employee validation, Excel bulk upload, attendance CSV upload."""
import csv
import io
import re
from datetime import date, datetime
from openpyxl import Workbook, load_workbook
from crypto import blind_index
from models import db, Employee, Category, Attendance
import xl

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
            if f in ("aadhaar", "pan"):             # encrypted columns: compare through the keyed blind index
                q = db.query(Employee).filter(getattr(Employee, f + "_idx") == blind_index(f, data[f]))
            else:
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


# ---- error codes shown in the downloadable upload report -------------------------------
# (regex on the message, code, meaning / how to fix)
ERROR_CODES = [
    (r"^Employee code is required", "E101", "Employee Code is blank. Every row needs a unique code."),
    (r"^Name is required", "E102", "Name is blank."),
    (r"^AADHAAR must be", "E103", "Aadhaar must be exactly 12 digits (no letters; spaces/hyphens are ignored)."),
    (r"^UAN must be", "E104", "UAN must be exactly 12 digits."),
    (r"^PAN format", "E105", "PAN must look like ABCDE1234F (5 letters, 4 digits, 1 letter)."),
    (r"^ESIC number must", "E106", "ESIC IP number must be 10 or 17 digits."),
    (r"^Mobile must", "E107", "Mobile must be a 10-digit Indian number starting 6-9."),
    (r"^IFSC format", "E108", "IFSC must look like SBIN0001234 (4 letters, a zero, 6 letters/digits)."),
    (r"^Gender must", "E109", "Gender must be M, F or O."),
    (r"^(dob|doj): ", "E110", "Date not understood. Use DD-MM-YYYY."),
    (r"^Personal Daily Basic", "E111", "Personal Daily Basic must be a number."),
    (r"^unknown salary category", "E112", "Salary Category does not match any category of this organization (see the Categories sheet)."),
    (r"^Salary Category is required", "E113", "New employees need a Salary Category."),
    (r"^duplicate Employee Code inside", "E114", "The same Employee Code appears twice in this file."),
    (r"^Aadhaar .* already belongs", "E201", "This Aadhaar is already used by another employee."),
    (r"^PAN .* already belongs", "E202", "This PAN is already used by another employee."),
    (r"^UAN .* already belongs", "E203", "This UAN is already used by another employee."),
    (r"^ESIC No .* already belongs", "E204", "This ESIC number is already used by another employee."),
    (r"^Employee Code .* already belongs", "E205", "This Employee Code is already used by another employee."),
]
OTHER_CODE = ("E999", "Other problem - see the message.")


def code_for(msg):
    for rx, code, _ in ERROR_CODES:
        if re.search(rx, msg):
            return code
    return OTHER_CODE[0]


CODE_FIELD = {"E101": "emp_code", "E102": "name", "E103": "aadhaar", "E104": "uan", "E105": "pan", "E106": "esic_no",
              "E107": "mobile", "E108": "ifsc", "E109": "gender", "E111": "basic_override_day", "E113": "category_id",
              "E201": "aadhaar", "E202": "pan", "E203": "uan", "E204": "esic_no", "E205": "emp_code"}


def split_errors(messages):
    """Map validation messages to form fields -> ({field: message}, [messages not tied to one field])."""
    by_field, general = {}, []
    for m in messages:
        code = code_for(m)
        field = CODE_FIELD.get(code) or (m.split(":")[0] if code == "E110" else None)
        if field:
            by_field.setdefault(field, m)
        else:
            general.append(m)
    return by_field, general


def pick_sheet(wb):
    return wb["Employees"] if "Employees" in wb.sheetnames else wb.worksheets[0]


def import_employees(file_stream):
    """Create new / update existing (matched on Employee Code).
    Returns dict(created, updated, errors=[(row, msg)], rows=[per-row result dicts])."""
    wb = load_workbook(file_stream, data_only=True)
    ws = pick_sheet(wb)
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return dict(created=0, updated=0, errors=[("-", "Empty file")], rows=[])
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
        return dict(created=0, updated=0, errors=[("-", "Header row not recognised - download the template")], rows=[])
    cats = {c.name.lower(): c for c in db.query(Category).all()}
    created = updated = 0
    errors = []
    results = []          # one entry per data row: row, status, codes, messages
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
        row_cat = data.get("category")
        if row_cat:
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
        if not emp and not cat and not row_cat:
            errs.append("Salary Category is required for new employees")
        entry = dict(row=rno, status=None, codes=[code_for(m) for m in errs], messages=errs)
        results.append(entry)
        if errs:
            entry["status"] = "Error"
            errors.append((rno, "; ".join(errs)))
            continue
        if emp:
            for k, v in data.items():
                if v not in (None, ""):
                    setattr(emp, k, v)
            if cat:
                emp.category = cat
            updated += 1
            entry["status"] = "Updated"
        else:
            emp = Employee(**{k: v for k, v in data.items() if v not in (None, "")})
            emp.category = cat
            db.add(emp)
            created += 1
            entry["status"] = "Created"
        db.flush()
    if errors:
        db.rollback()      # all-or-nothing keeps the register consistent
        for e in results:
            if e["status"] != "Error":
                e["status"] = "Valid - not saved (fix the error rows and re-upload)"
        return dict(created=0, updated=0, errors=errors, rolled_back=True, rows=results)
    db.commit()
    return dict(created=created, updated=updated, errors=[], rows=results)


def build_upload_report(file_bytes, rows, meta):
    """Original uploaded sheet + Status / Error Code / Error Message columns, ready to fix and re-upload.
    meta: dict(filename, uploaded_by, uploaded_at, status, created, updated, details). Returns BytesIO or None."""
    from openpyxl.styles import Font, PatternFill, Alignment
    try:
        wb = load_workbook(io.BytesIO(file_bytes))
    except Exception:
        return None
    ws = pick_sheet(wb)
    header = {str(c.value).strip().lower(): c.column for c in ws[1] if c.value not in (None, "")}
    last = max(header.values()) if header else 0
    cols = {}
    for name in ("Status", "Error Code", "Error Message"):
        if name.lower() in header:            # report re-uploaded earlier: overwrite instead of duplicating
            cols[name] = header[name.lower()]
        else:
            last += 1
            cols[name] = last
    bold = Font(bold=True)
    for name, ci in cols.items():
        c = ws.cell(1, ci, name)
        c.font = bold
        c.fill = PatternFill("solid", fgColor="DDE6F0")
        ws.column_dimensions[c.column_letter].width = 16 if name != "Error Message" else 70
    ws.column_dimensions[ws.cell(1, cols["Status"]).column_letter].width = 24
    fills = {"Error": "F8D7DA", "Created": "D4EDDA", "Updated": "D4EDDA"}
    for r in rows:
        status = r["status"] or ""
        fill = PatternFill("solid", fgColor=fills.get(status, "FFF3CD"))
        vals = {"Status": status, "Error Code": ", ".join(r["codes"]), "Error Message": "; ".join(r["messages"])}
        for name, ci in cols.items():
            cell = ws.cell(r["row"], ci, vals[name])
            cell.fill = fill
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"

    for title in ("Upload Summary", "Error Codes"):
        if title in wb.sheetnames:
            del wb[title]
    s = wb.create_sheet("Upload Summary", 0)
    ts = meta["uploaded_at"].strftime("%d-%b-%Y %H:%M") if meta.get("uploaded_at") else "-"
    for line in (["File", meta["filename"]], ["Uploaded on", ts], ["Uploaded by", meta["uploaded_by"]],
                 ["Result", meta["status"]], ["Created", meta["created"]], ["Updated", meta["updated"]],
                 ["Rows with errors", sum(1 for r in rows if r["status"] == "Error")],
                 ["File-level message", meta.get("details") if not rows else ""],
                 [], ["How to use", "Fix the rows marked Error on the data sheet, then upload this same file again. "
                                    "The Status / Error columns are ignored on upload. Rows already Created/Updated "
                                    "are simply updated again with the same values."]):
        s.append(line)
    s.column_dimensions["A"].width = 20
    s.column_dimensions["B"].width = 90
    for row in s.iter_rows(min_col=1, max_col=1):
        row[0].font = bold
    ec = wb.create_sheet("Error Codes")
    ec.append(["Error Code", "Meaning / how to fix"])
    for _, code, text in ERROR_CODES + [(None,) + OTHER_CODE]:
        ec.append([code, text])
    ec.column_dimensions["A"].width = 12
    ec.column_dimensions["B"].width = 100
    for c in ec[1]:
        c.font = bold
    return xl.to_buffer(wb)


ATT_TEMPLATE = "emp_code,present_days,paid_holidays,ot_hours,advance,other_deduction\nE001,24,4,10,0,0\n"


def import_attendance(text, month):
    """Validate the whole CSV, then save it all-or-nothing. Returns dict(saved, errors, warnings)."""
    import calendar
    from datetime import date as _date
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return dict(saved=0, errors=[("-", "Empty file")], warnings=[])
    reader.fieldnames = [f.strip().lower() for f in reader.fieldnames]
    if "emp_code" not in reader.fieldnames or "present_days" not in reader.fieldnames:
        return dict(saved=0, errors=[("-", "CSV needs columns emp_code and present_days")], warnings=[])
    y, m = map(int, month.split("-"))
    dim = calendar.monthrange(y, m)[1]
    first, last = _date(y, m, 1), _date(y, m, dim)
    errors, warnings, parsed, seen = [], [], [], set()
    for n, row in enumerate(reader, start=2):
        code = (row.get("emp_code") or "").strip()
        if not code:
            continue
        if code in seen:
            errors.append((n, f"employee code '{code}' appears more than once in this file"))
            continue
        seen.add(code)
        emp = db.query(Employee).filter_by(emp_code=code).first()
        if not emp:
            errors.append((n, f"employee code '{code}' not found"))
            continue
        try:
            vals = {k: float((row.get(k) or 0) or 0) for k in
                    ("present_days", "paid_holidays", "ot_hours", "advance", "other_deduction")}
        except ValueError:
            errors.append((n, f"{code}: non-numeric value"))
            continue
        if any(v < 0 for v in vals.values()):
            errors.append((n, f"{code}: negative value"))
            continue
        if vals["present_days"] + vals["paid_holidays"] > dim:
            errors.append((n, f"{code}: present + paid holidays exceed the {dim} days of the month"))
            continue
        if vals["ot_hours"] > dim * 12:
            errors.append((n, f"{code}: overtime of {vals['ot_hours']:g} hours is not plausible for one month"))
            continue
        for k in ("advance", "other_deduction"):
            if vals[k] != int(vals[k]):
                errors.append((n, f"{code}: {k} must be a whole rupee amount"))
        if vals["present_days"] + vals["paid_holidays"] > 0:
            if emp.doj and emp.doj > last:
                warnings.append(f"line {n}: {code} {emp.name} has paid days but joining date is {emp.doj:%d-%m-%Y}")
            if emp.dol and emp.dol < first:
                warnings.append(f"line {n}: {code} {emp.name} has paid days but left on {emp.dol:%d-%m-%Y}")
        parsed.append((emp, vals))
    if errors:
        return dict(saved=0, errors=errors, rolled_back=True, warnings=warnings)
    for emp, vals in parsed:
        a = db.query(Attendance).filter_by(emp_id=emp.id, month=month).first()
        if not a:
            a = Attendance(emp_id=emp.id, month=month)
            db.add(a)
        for k, v in vals.items():
            setattr(a, k, v)
    db.commit()
    return dict(saved=len(parsed), errors=[], warnings=warnings)
