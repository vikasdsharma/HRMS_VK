"""EPFO ECR text file + ESIC monthly contribution Excel generators, and the pre-submission checks."""
import re

from openpyxl import Workbook
from models import db, Payslip, Employee
import xl


def ecr_text(month):
    """EPFO ECR 2.0 text format: fields separated by '#~#'. One line per member with UAN."""
    lines, missing = [], []
    for s in db.query(Payslip).join(Employee).filter(Payslip.month == month, Payslip.epf_wages > 0) \
            .order_by(Employee.emp_code):
        e = s.employee
        if not e.uan:
            missing.append(f"{e.emp_code} {e.name}")
            continue
        name = " ".join(re.sub(r"[^A-Z .]", " ", e.name.upper()).split())   # ECR accepts letters, space, dot only
        ncp = int(round(s.ncp_days))
        lines.append("#~#".join(str(x) for x in [
            e.uan, name, s.gross, s.epf_wages, s.eps_wages, s.edli_wages,
            s.pf_ee, s.eps_er, s.pf_er_diff, ncp, 0]))
    return "\n".join(lines) + ("\n" if lines else ""), missing


def esi_xlsx(month):
    """ESIC portal 'Monthly Contribution' upload template (MC template)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["IP Number", "IP Name", "No of Days for which wages paid/payable during the month",
               "Total Monthly Wages", "Reason Code for Zero workings days(numeric only; provide 0 for all other reasons- Click on the link for reference)",
               "Last Working Day"])
    missing = []
    q = db.query(Payslip).join(Employee).filter(Payslip.month == month).order_by(Employee.emp_code)
    for s in q:
        e = s.employee
        covered = s.esi_wages > 0 or (e.esic_no and s.category_name and s.paid_days == 0)
        if not covered:
            continue
        if not e.esic_no:
            missing.append(f"{e.emp_code} {e.name}")
            continue
        zero = s.paid_days == 0
        last_day = ""
        if e.dol and e.dol.strftime("%Y-%m") == month:
            last_day = e.dol.strftime("%d/%m/%Y")
        ws.append([e.esic_no, e.name.upper(), int(s.paid_days) if float(s.paid_days).is_integer() else s.paid_days,
                   s.esi_wages if not zero else 0, 1 if zero else 0, last_day])
    for col, w in zip("ABCDEF", (18, 30, 22, 18, 30, 16), strict=True):
        ws.column_dimensions[col].width = w
    return xl.to_buffer(wb), missing


def payment_register_xlsx(month):
    """Bank / salary register for internal use."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Salary Register"
    ws.append(["Emp Code", "Name", "Category", "Paid Days", "Gross", "PF (EE)", "ESI (EE)", "Advance",
               "Other Ded.", "Net Pay", "Bank", "Account No", "IFSC"])
    for s in db.query(Payslip).join(Employee).filter(Payslip.month == month).order_by(Employee.emp_code):
        e = s.employee
        ws.append([e.emp_code, e.name, s.category_name, s.paid_days, s.gross, s.pf_ee, s.esi_ee, s.advance,
                   s.other_deduction, s.net, e.bank_name, e.bank_acct, e.ifsc])
    return xl.to_buffer(wb)


def precheck(month):
    """Problems to look at BEFORE uploading the ECR / ESI files to the portals.
    Returns (status, issues) where status is 'pass' | 'warn' | 'fail' and issues = [{level, msg}]."""
    import payroll
    from models import get_settings
    issues = []

    def add(level, msg):
        issues.append(dict(level=level, msg=msg))

    slips = db.query(Payslip).join(Employee).filter(Payslip.month == month).order_by(Employee.emp_code).all()
    if not slips:
        return "fail", [dict(level="error", msg="No payslips for this month - generate payroll first.")]
    S = get_settings()
    if not payroll.is_final(month):
        add("warn", "This month is not finalised yet - figures may still change. Finalise it on the Payroll page first.")
    for m in payroll.stale_slips(month):
        add("error", m)
    if not S["pf_code"]:
        add("warn", "PF establishment code is not set in Settings (shown on the EPF challan worksheet).")
    if not S["esi_code"]:
        add("warn", "ESI employer code is not set in Settings (shown on the ESI challan worksheet).")
    uans = {}
    for s in slips:
        e = s.employee
        who = f"{e.emp_code} {e.name}"
        if s.epf_wages:
            if not e.uan:
                add("warn", f"{who}: no UAN - left out of the ECR file")
            elif not re.fullmatch(r"\d{12}", e.uan):
                add("error", f"{who}: UAN '{e.uan}' is not 12 digits")
            else:
                uans.setdefault(e.uan, []).append(who)
            if re.search(r"[^A-Za-z .]", e.name):
                add("warn", f"{who}: name has characters EPFO may reject - the ECR file uses a cleaned version")
            if s.epf_wages > s.gross:
                add("error", f"{who}: EPF wages {s.epf_wages} exceed gross {s.gross}")
            if s.eps_wages > s.epf_wages:
                add("error", f"{who}: EPS wages exceed EPF wages")
            if s.ncp_days > s.days_in_month or s.ncp_days < 0:
                add("error", f"{who}: NCP days {s.ncp_days:g} is impossible for a {s.days_in_month}-day month")
        if s.esi_wages:
            if not e.esic_no:
                add("warn", f"{who}: no ESIC IP number - left out of the ESI file")
            elif not re.fullmatch(r"\d{10}|\d{17}", e.esic_no):
                add("error", f"{who}: ESIC number '{e.esic_no}' must be 10 or 17 digits")
    for uan, who in uans.items():
        if len(who) > 1:
            add("error", f"UAN {uan} is used by more than one employee: {', '.join(who)}")
    status = "fail" if any(i["level"] == "error" for i in issues) else ("warn" if issues else "pass")
    return status, issues
