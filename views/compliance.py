from common import *  # noqa: F401,F403
from web import route, template_filter  # noqa: F401


# ---------- compliance ----------
@route("/compliance")
def compliance_page():
    month = valid_month(request.args.get("month") or date.today().strftime("%Y-%m"))
    T = payroll.month_totals(month)
    status, issues = compliance.precheck(month)
    return render_template("compliance.html", month=month, T=T, status=status, issues=issues,
                           final=payroll.is_final(month), month_opts=month_options(24))


@route("/compliance/ecr.txt")
@admin_required
def ecr_download():
    month = valid_month(request.args.get("month"))
    text, _ = compliance.ecr_text(month)
    audit("compliance.ecr", "month", month, "ECR file downloaded")
    return send_file(io.BytesIO(text.encode("utf-8")), download_name=f"ECR_{month}.txt", mimetype="text/plain",
                     as_attachment=True)          # must download, not display in the browser


@route("/compliance/esi.xlsx")
@admin_required
def esi_download():
    month = valid_month(request.args.get("month"))
    buf, _ = compliance.esi_xlsx(month)
    audit("compliance.esi", "month", month, "ESI contribution file downloaded")
    return xlsx_response(buf, f"ESI_MC_{month}.xlsx")


@route("/compliance/epf-challan.pdf")
@admin_required
def epf_challan():
    month = valid_month(request.args.get("month"))
    audit("compliance.epf_challan", "month", month)
    return pdf_response(pdfs.epf_challan_pdf(month, payroll.month_totals(month), get_settings()),
                        f"EPF_challan_{month}.pdf")


@route("/compliance/esi-challan.pdf")
@admin_required
def esi_challan():
    month = valid_month(request.args.get("month"))
    audit("compliance.esi_challan", "month", month)
    return pdf_response(pdfs.esi_challan_pdf(month, payroll.month_totals(month), get_settings()),
                        f"ESI_challan_{month}.pdf")
