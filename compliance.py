"""EPFO ECR text file + ESIC monthly contribution Excel generators."""
import io
from openpyxl import Workbook
from models import db, Payslip, Employee
from payroll import days_in


def ecr_text(month):
    """EPFO ECR 2.0 text format: fields separated by '#~#'. One line per member with UAN."""
    lines, missing = [], []
    for s in db.query(Payslip).join(Employee).filter(Payslip.month == month, Payslip.epf_wages > 0) \
            .order_by(Employee.emp_code):
        e = s.employee
        if not e.uan:
            missing.append(f"{e.emp_code} {e.name}")
            continue
        name = " ".join(e.name.upper().split())
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
    for col, w in zip("ABCDEF", (18, 30, 22, 18, 30, 16)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf, missing


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
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
