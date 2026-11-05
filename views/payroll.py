from common import *  # noqa: F401,F403
from payroll import PayrollLocked
from web import route


def _slips(month):
    return db.query(Payslip).join(Employee).filter(Payslip.month == month).order_by(Employee.emp_code).all()


@route("/payroll")
def payroll_list():
    month = valid_month(request.args.get("month") or date.today().strftime("%Y-%m"))
    slips = _slips(month)
    att_count = db.query(Attendance).filter_by(month=month).count()
    run = payroll.get_run(month)
    return render_template("payroll.html", month=month, slips=slips, att_count=att_count,
                           T=payroll.month_totals(month) if slips else None, run=run,
                           final=bool(run and run.status == "final"),
                           warnings=payroll.payroll_warnings(month), stale=payroll.stale_slips(month),
                           month_opts=month_options(24))


@route("/payroll/generate", methods=["POST"])
def payroll_generate():
    month = valid_month(request.form.get("month"))
    try:
        n, skipped = payroll.generate_month(month)
    except PayrollLocked as e:
        flash(str(e), "err")
        return redirect(url_for("payroll_list", month=month))
    audit("payroll.generate", "month", month, f"{n} payslip(s), {len(skipped)} skipped")
    flash(f"Generated {n} payslip(s) for {pdfs.month_label(month)}", "ok" if n else "err")
    for s in skipped:
        flash(s, "err")
    return redirect(url_for("payroll_list", month=month))


@route("/payroll/finalize", methods=["POST"])
@admin_required
def payroll_finalize():
    month = valid_month(request.form.get("month"))
    if not _slips(month):
        flash("Nothing to finalise - generate payslips first.", "err")
    elif payroll.stale_slips(month):
        flash("Some payslips are out of date. Re-generate payslips, review the list, then finalise.", "err")
    else:
        payroll.finalize(month, g.user.username)
        audit("payroll.finalize", "month", month, f"{len(_slips(month))} payslip(s) frozen")
        flash(f"{pdfs.month_label(month)} payroll finalised. Attendance and payslips for this month are now locked.", "ok")
    return redirect(url_for("payroll_list", month=month))


@route("/payroll/unlock", methods=["POST"])
@admin_required
def payroll_unlock():
    month = valid_month(request.form.get("month"))
    reason = (request.form.get("reason") or "").strip()
    if len(reason) < 5:
        flash("Give a reason (at least 5 characters) - it is recorded in the audit log.", "err")
    else:
        payroll.unlock(month, g.user.username, reason)
        audit("payroll.unlock", "month", month, f"reason: {reason[:200]}")
        flash(f"{pdfs.month_label(month)} unlocked for correction. Remember to finalise it again.", "ok")
    return redirect(url_for("payroll_list", month=month))


@route("/payroll/payslips.pdf")
def payslips_bulk_pdf():
    month = valid_month(request.args.get("month"))
    slips = _slips(month)
    if not slips:
        abort(404, "No payslips for this month")
    return pdf_response(pdfs.payslips_pdf(slips, get_settings()), f"payslips_{month}.pdf")


@route("/payroll/payslips.zip")
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
    return send_file(buf, download_name=f"payslips_{month}.zip", mimetype="application/zip",
                     as_attachment=True)


@route("/payroll/slip/<int:sid>.pdf")
def payslip_pdf(sid):
    s = db.get(Payslip, sid) or abort(404)
    return pdf_response(pdfs.payslips_pdf([s], get_settings()), f"payslip_{s.employee.emp_code}_{s.month}.pdf")


@route("/payroll/register.xlsx")
@admin_required
def salary_register():
    month = valid_month(request.args.get("month"))
    audit("payroll.register", "month", month, "salary register (bank details) downloaded")
    return xlsx_response(compliance.payment_register_xlsx(month), f"salary_register_{month}.xlsx")
