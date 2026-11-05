"""Daily-attendance roll-up, Bonus worksheet and Gratuity estimate.

Bonus and gratuity here are WORKSHEETS to support your calculation: please confirm the thresholds and rules
with your labour-law consultant (the Labour Codes may change them). Rates and ceilings come from Settings.
"""
import calendar
from datetime import date

from openpyxl import Workbook

import xl
from models import Attendance, DailyAttendance, Employee, Payslip, db, get_settings
from payroll import d, days_in, month_end, month_start, q2, rate_for, rnd

STATUSES = {"P": "Present", "HD": "Half day", "A": "Absent", "WO": "Weekly off", "H": "Paid holiday", "L": "Paid leave",
            "U": "Unpaid leave"}                      # marks written by approved leave also use HL / HU (half days)
ALL_STATUSES = {**STATUSES, "HL": "Half-day paid leave", "HU": "Half-day unpaid leave"}
PRESENT_VALUE = {"P": 1.0, "HD": 0.5, "HL": 0.5, "HU": 0.5}        # the worked half of a half-day leave
PAID_OFF_VALUE = {"WO": 1.0, "H": 1.0, "L": 1.0, "HL": 0.5}        # paid but not worked


# ------------------------------------------------------------------ daily attendance
def rollup_month(month):
    """Sum a month of daily marks into {emp_id: dict(present_days, paid_holidays, ot_hours)}.
    Weekly offs and holidays from the calendar count as paid days on days that have no mark, within the
    employee's service, up to today. Employees with no marks at all that month are not included."""
    from datetime import date as _date
    import leave
    out = {}
    first, last = month_start(month), month_end(month)
    marked = {}
    q = db.query(DailyAttendance).filter(DailyAttendance.day >= first, DailyAttendance.day <= last)
    for r in q:
        o = out.setdefault(r.emp_id, dict(present_days=0.0, paid_holidays=0.0, ot_hours=0.0))
        o["present_days"] += PRESENT_VALUE.get(r.status, 0.0)
        o["paid_holidays"] += PAID_OFF_VALUE.get(r.status, 0.0)
        o["ot_hours"] += r.ot_hours or 0.0
        marked.setdefault(r.emp_id, set()).add(r.day)
    if out:
        hol = leave.holiday_dates(first, last)
        until = min(last, _date.today())
        for e in db.query(Employee).filter(Employee.id.in_(list(out))):
            offs = leave.weekly_off_days(e)
            for d in leave.daterange(max(first, e.doj or first), min(until, e.dol) if e.dol else until):
                if d not in marked[e.id] and (d.weekday() in offs or d in hol):
                    out[e.id]["paid_holidays"] += 1.0
    return out


def apply_rollup(month):
    """Write the roll-up into monthly Attendance rows (advance / other deduction are left as they were)."""
    n = 0
    for emp_id, v in rollup_month(month).items():
        a = db.query(Attendance).filter_by(emp_id=emp_id, month=month).first()
        if not a:
            a = Attendance(emp_id=emp_id, month=month, advance=0, other_deduction=0)
            db.add(a)
        a.present_days, a.paid_holidays, a.ot_hours = v["present_days"], v["paid_holidays"], v["ot_hours"]
        n += 1
    db.commit()
    return n


# ------------------------------------------------------------------ bonus
def fy_months(start_year):
    return [f"{start_year}-{m:02d}" for m in range(4, 13)] + [f"{start_year + 1}-{m:02d}" for m in range(1, 4)]


def default_fy(today=None):
    t = today or date.today()
    return t.year - 1 if t.month < 4 else t.year           # FY that has most recently completed or is running


def bonus_worksheet(start_year):
    """Per-employee bonus for the financial year start_year-04 .. start_year+1-03."""
    S = get_settings()
    rate, ceiling, elig = d(S["bonus_rate"]), d(S["bonus_wage_ceiling"]), d(S["bonus_eligibility_ceiling"])
    months = fy_months(start_year)
    rows = {}
    q = db.query(Payslip).join(Employee).filter(Payslip.month.in_(months)).order_by(Employee.emp_code)
    for s in q:
        e = s.employee
        r = rows.setdefault(e.id, dict(code=e.emp_code, name=e.name, days=d(0), months=0, wages=d(0), skipped=0, note=""))
        r["days"] += d(s.paid_days)
        min_wage_month = d(e.category.min_wage_day) * days_in(s.month) if e.category and e.category.min_wage_day else d(0)
        cap = max(ceiling, min_wage_month)
        if d(s.gross) - d(s.ot_amount) > elig:               # above the eligibility ceiling this month
            r["skipped"] += 1
            continue
        r["wages"] += min(d(s.basic) + d(s.da), cap)
        r["months"] += 1
    out = []
    for r in rows.values():
        eligible = r["days"] >= 30 and r["months"] > 0
        bonus = rnd(r["wages"] * rate / 100) if eligible else 0
        note = "" if eligible else ("worked fewer than 30 days" if r["days"] < 30 else "salary above eligibility ceiling")
        if r["skipped"] and eligible:
            note = f"{r['skipped']} month(s) excluded (salary above ceiling)"
        out.append(dict(code=r["code"], name=r["name"], days=float(r["days"]), months=r["months"],
                        wages=float(q2(r["wages"])), bonus=bonus, eligible=eligible, note=note))
    return dict(months=months, rate=float(rate), ceiling=float(ceiling), rows=out,
                total=sum(x["bonus"] for x in out))


# ------------------------------------------------------------------ gratuity
def _add_months(dt, n):
    y, m = divmod(dt.month - 1 + n, 12)
    y, m = dt.year + y, m + 1
    return date(y, m, min(dt.day, calendar.monthrange(y, m)[1]))


def service_years(doj, as_on):
    """(completed_years, counted_years). Gratuity counts a leftover of more than six months as a full year."""
    done = as_on.year - doj.year - ((as_on.month, as_on.day) < (doj.month, doj.day))
    done = max(done, 0)
    anchor = _add_months(doj, done * 12)
    counted = done + (1 if as_on > _add_months(anchor, 6) else 0)
    return done, counted


def gratuity_estimate(as_on):
    """Accrued gratuity if every employee left on `as_on`:  15 x daily wage (Basic+DA) x counted years
    (15/26 x 26 days = 15 days' wages per year), capped. Eligible after the minimum completed years."""
    S = get_settings()
    cap, min_years = d(S["gratuity_cap"]), int(float(S["gratuity_min_years"]))
    month = as_on.strftime("%Y-%m")
    rows = []
    for e in db.query(Employee).filter_by(status="Active").order_by(Employee.emp_code):
        if not e.doj:
            rows.append(dict(code=e.emp_code, name=e.name, doj=None, done=0, counted=0, daily=0.0, amount=0,
                             eligible=False, note="date of joining missing"))
            continue
        if e.doj > as_on:
            continue
        done, counted = service_years(e.doj, as_on)
        rate = rate_for(e.category, month) if e.category else None
        basic = d(e.basic_override_day) if e.basic_override_day else (rate.basic if rate else d(0))
        daily = basic + (rate.da if rate else d(0))
        eligible = done >= min_years
        amount = rnd(min(d(15) * daily * counted, cap)) if eligible else 0
        rows.append(dict(code=e.emp_code, name=e.name, doj=e.doj, done=done, counted=counted, daily=float(q2(daily)),
                         amount=amount, eligible=eligible,
                         note="" if eligible else f"needs {min_years} completed years"))
    return dict(as_on=as_on, min_years=min_years, cap=float(cap), rows=rows, total=sum(r["amount"] for r in rows))


# ------------------------------------------------------------------ excel
def bonus_xlsx(ws_data, start_year):
    wb = Workbook()
    ws = wb.active
    ws.title = "Bonus"
    ws.append([f"Bonus worksheet FY {start_year}-{str(start_year + 1)[2:]}  (rate {ws_data['rate']:g}%, "
               f"monthly wage ceiling {ws_data['ceiling']:g})"])
    ws.append(["Emp Code", "Name", "Days worked", "Months counted", "Bonus wages", "Bonus", "Eligible", "Note"])
    for r in ws_data["rows"]:
        ws.append([r["code"], r["name"], r["days"], r["months"], r["wages"], r["bonus"], "Yes" if r["eligible"] else "No", r["note"]])
    ws.append(["", "TOTAL", "", "", "", ws_data["total"], "", ""])
    return xl.to_buffer(wb)


def gratuity_xlsx(data):
    wb = Workbook()
    ws = wb.active
    ws.title = "Gratuity"
    ws.append([f"Gratuity estimate as on {data['as_on']:%d-%m-%Y}  (15 days' wages per year, cap {data['cap']:,.0f})"])
    ws.append(["Emp Code", "Name", "Date of joining", "Completed years", "Counted years", "Daily wage (Basic+DA)",
               "Gratuity", "Eligible", "Note"])
    for r in data["rows"]:
        ws.append([r["code"], r["name"], r["doj"].strftime("%d-%m-%Y") if r["doj"] else "", r["done"], r["counted"],
                   r["daily"], r["amount"], "Yes" if r["eligible"] else "No", r["note"]])
    ws.append(["", "TOTAL", "", "", "", "", data["total"], "", ""])
    return xl.to_buffer(wb)
