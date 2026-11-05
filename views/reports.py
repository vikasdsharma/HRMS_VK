from common import *  # noqa: F401,F403
from web import route


def _fy():
    try:
        return int(request.args.get("fy") or reports.default_fy())
    except ValueError:
        abort(400, "Invalid financial year")


def _as_on():
    raw = request.args.get("as_on")
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date() if raw else date.today()
    except ValueError:
        abort(400, "Invalid date")


@route("/reports")
@admin_required
def reports_home():
    return render_template("reports.html", fy=reports.default_fy(), as_on=date.today())


@route("/reports/bonus")
@admin_required
def report_bonus():
    fy = _fy()
    return render_template("report_bonus.html", fy=fy, data=reports.bonus_worksheet(fy),
                           fy_opts=range(date.today().year, date.today().year - 8, -1))


@route("/reports/bonus.xlsx")
@admin_required
def report_bonus_xlsx():
    fy = _fy()
    audit("report.bonus", "fy", fy, "bonus worksheet downloaded")
    return xlsx_response(reports.bonus_xlsx(reports.bonus_worksheet(fy), fy), f"bonus_FY{fy}-{str(fy + 1)[2:]}.xlsx")


@route("/reports/gratuity")
@admin_required
def report_gratuity():
    return render_template("report_gratuity.html", data=reports.gratuity_estimate(_as_on()))


@route("/reports/gratuity.xlsx")
@admin_required
def report_gratuity_xlsx():
    on = _as_on()
    audit("report.gratuity", "date", on.isoformat(), "gratuity estimate downloaded")
    return xlsx_response(reports.gratuity_xlsx(reports.gratuity_estimate(on)), f"gratuity_{on:%Y%m%d}.xlsx")
