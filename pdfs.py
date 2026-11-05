"""PDF generation: payslips and EPF / ESI challan worksheets."""
import io
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak

ss = getSampleStyleSheet()
H = ParagraphStyle("H", parent=ss["Title"], fontSize=15, spaceAfter=2)
SUB = ParagraphStyle("SUB", parent=ss["Normal"], alignment=1, fontSize=9, textColor=colors.grey)
SM = ParagraphStyle("SM", parent=ss["Normal"], fontSize=8, textColor=colors.HexColor("#555555"))
GRID = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")),
        ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]


def month_label(month):
    return datetime.strptime(month, "%Y-%m").strftime("%B %Y")


def inr(x):
    return f"{x:,.2f}"


def _payslip_flow(s, S):
    e = s.employee
    f = [Paragraph(S["company_name"], H),
         Paragraph(S["company_address"], SUB),
         Paragraph(f"<b>PAY SLIP - {month_label(s.month).upper()}</b>", ParagraphStyle("c", parent=ss["Normal"], alignment=1, spaceBefore=6, spaceAfter=6))]
    info = [
        ["Employee Code", e.emp_code, "Name", e.name],
        ["Designation", e.designation or "-", "Category", s.category_name],
        ["UAN", e.uan or "-", "ESIC No", e.esic_no or "-"],
        ["Bank A/c", e.bank_acct or "-", "IFSC", e.ifsc or "-"],
        ["Days in Month", s.days_in_month, "Paid Days", f"{s.paid_days:g}"],
    ]
    t = Table(info, colWidths=[32 * mm, 55 * mm, 28 * mm, 55 * mm])
    t.setStyle(TableStyle(GRID + [("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f0f0")),
                                  ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f0f0f0"))]))
    f += [t, Spacer(1, 4 * mm)]
    earn = [("Basic", s.basic), ("Dearness Allowance", s.da), ("HRA", s.hra), ("Other Allowance", s.other),
            ("Overtime", s.ot_amount)]
    ded = [("Provident Fund (12%)", s.pf_ee), ("ESI (0.75%)", s.esi_ee), ("Advance", s.advance),
           ("Other Deduction", s.other_deduction)]
    rows = [["Earnings", "Amount (Rs)", "Deductions", "Amount (Rs)"]]
    for i in range(max(len(earn), len(ded))):
        left = earn[i] if i < len(earn) else ("", None)
        r = ded[i] if i < len(ded) else ("", None)
        rows.append([left[0], inr(left[1]) if left[1] is not None else "", r[0], inr(r[1]) if r[1] is not None else ""])
    rows.append(["Gross Earnings", inr(s.gross), "Total Deductions", inr(s.total_deduction)])
    rows.append(["", "", "NET PAY", inr(s.net)])
    t2 = Table(rows, colWidths=[55 * mm, 32 * mm, 55 * mm, 28 * mm])
    t2.setStyle(TableStyle(GRID + [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dde6f0")),
                                   ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("ALIGN", (3, 0), (3, -1), "RIGHT"),
                                   ("FONTNAME", (0, -2), (-1, -1), "Helvetica-Bold"),
                                   ("BACKGROUND", (2, -1), (3, -1), colors.HexColor("#e8f3e8"))]))
    f += [t2, Spacer(1, 3 * mm)]
    if s.epf_wages:
        f.append(Paragraph(f"Employer contribution: PF Rs {s.pf_ee} (EPF Rs {s.pf_er_diff} + EPS Rs {s.eps_er})"
                           + (f"; ESI Rs {s.esi_er}" if s.esi_er else ""), SM))
    elif s.esi_er:
        f.append(Paragraph(f"Employer ESI contribution: Rs {s.esi_er}", SM))
    f += [Spacer(1, 12 * mm), Paragraph("This is a computer generated payslip.", SM)]
    return f


def payslips_pdf(slips, S):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm)
    flow = []
    for i, s in enumerate(slips):
        flow += _payslip_flow(s, S)
        if i < len(slips) - 1:
            flow.append(PageBreak())
    doc.build(flow)
    buf.seek(0)
    return buf


def _challan_head(title, S, month, code_label, code):
    return [Paragraph(S["company_name"], H), Paragraph(S["company_address"], SUB),
            Paragraph(f"<b>{title}</b>", ParagraphStyle("c", parent=ss["Normal"], alignment=1, fontSize=12, spaceBefore=8, spaceAfter=6)),
            Table([[ "Wage Month", month_label(month), code_label, code or "(set in Settings)"]],
                  colWidths=[30 * mm, 50 * mm, 40 * mm, 50 * mm], style=TableStyle(GRID))]


NOTE = ("Reference worksheet for payment. The official challan (TRRN / ESIC challan) is generated on the "
        "government portal after uploading the ECR / contribution file. Due date: 15th of the following month.")


def epf_challan_pdf(month, T, S):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=15 * mm)
    f = _challan_head("EPF COMBINED CHALLAN WORKSHEET (ECR)", S, month, "PF Establishment Code", S["pf_code"])
    f += [Spacer(1, 4 * mm),
          Table([["Total members", T["pf_members"], "Total gross wages", inr(T["pf_gross"])],
                 ["EPF wages", inr(T["epf_wages"]), "EPS wages", inr(T["eps_wages"])],
                 ["EDLI wages", inr(T["edli_wages"]), "", ""]],
                colWidths=[40 * mm, 40 * mm, 45 * mm, 45 * mm], style=TableStyle(GRID)),
          Spacer(1, 5 * mm)]
    rows = [["Account", "Description", "Employee Share", "Employer Share", "Total"],
            ["A/c 01", "EPF (EE 12%; ER 3.67%)", T["acc01_ee"], T["acc01_er"], T["acc01_ee"] + T["acc01_er"]],
            ["A/c 10", "EPS (ER 8.33%)", 0, T["acc10"], T["acc10"]],
            ["A/c 02", "EPF Admin charges (0.5%, min Rs 500)", 0, T["acc02"], T["acc02"]],
            ["A/c 21", "EDLI (0.5%)", 0, T["acc21"], T["acc21"]],
            ["A/c 22", "EDLI Admin charges", 0, 0, 0],
            ["", "GRAND TOTAL", T["acc01_ee"], T["acc01_er"] + T["acc10"] + T["acc02"] + T["acc21"], T["pf_total"]]]
    t = Table(rows, colWidths=[20 * mm, 70 * mm, 30 * mm, 30 * mm, 30 * mm])
    t.setStyle(TableStyle(GRID + [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dde6f0")),
                                  ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                                  ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")]))
    f += [t, Spacer(1, 6 * mm), Paragraph(NOTE, SM)]
    doc.build(f)
    buf.seek(0)
    return buf


def esi_challan_pdf(month, T, S):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=15 * mm)
    f = _challan_head("ESI CONTRIBUTION CHALLAN WORKSHEET", S, month, "ESI Employer Code", S["esi_code"])
    f += [Spacer(1, 4 * mm)]
    rows = [["Particulars", "Rate", "Amount (Rs)"],
            ["Insured persons (IPs)", "", T["esi_members"]],
            ["Total wages on which contribution payable", "", inr(T["esi_wages"])],
            ["Employee contribution", f"{S['esi_ee_rate']}%", T["esi_ee"]],
            ["Employer contribution", f"{S['esi_er_rate']}%", T["esi_er"]],
            ["TOTAL PAYABLE", "", T["esi_total"]]]
    t = Table(rows, colWidths=[90 * mm, 30 * mm, 50 * mm])
    t.setStyle(TableStyle(GRID + [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dde6f0")),
                                  ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                                  ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")]))
    f += [t, Spacer(1, 6 * mm), Paragraph(NOTE, SM)]
    doc.build(f)
    buf.seek(0)
    return buf
