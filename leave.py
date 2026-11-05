"""Leave management engine: policies, the leave ledger, accrual, year-end rollover, applications and approvals,
comp off, holidays / weekly offs, the per-employee attendance calendar, attendance corrections and employee
portal logins.

Design rules
* Balances are never stored; balance = SUM of immutable ledger rows. Automatic credits carry a `ref`, so running a
  job twice cannot credit twice.
* Everything runs inside the active organization (models.py filters all queries); employee-facing code additionally
  filters by the employee in the views.
* Functions that change data commit; functions that only look never write (dry_run=True returns the plan).
"""
import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP
from decimal import Decimal as D
from types import SimpleNamespace

from sqlalchemy import func
from werkzeug.security import generate_password_hash

import payroll
from models import (Attendance, AttendanceRequest, CompOffGrant, DailyAttendance, Employee, EmployeeLeaveOverride,
                    Holiday, LeaveApplication, LeaveLedger, LeavePolicy, LeaveRollover, LeaveType, Notification,
                    Setting, User, WeeklyOffRule, db, get_settings)

ATT_STATUSES = {"P": "Present", "HD": "Half day", "A": "Absent", "WO": "Weekly off", "H": "Paid holiday",
                "L": "Paid leave", "HL": "Half-day paid leave", "U": "Unpaid leave", "HU": "Half-day unpaid leave"}
MANUAL_STATUSES = ("P", "HD", "A", "WO", "H", "L", "U")        # what an admin may mark by hand
REQUEST_STATUSES = ("P", "HD")                                  # what an employee may ask for
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class LeaveError(ValueError):
    pass


# ------------------------------------------------------------------ small helpers
def half_round(x):
    return float((D(str(x)) * 2).quantize(D(1), ROUND_HALF_UP) / 2)


def floor_half(x):
    return float((D(str(x)) * 2).quantize(D(1), ROUND_DOWN) / 2)


def r2(x):
    return float(D(str(x)).quantize(D("0.01"), ROUND_HALF_UP))


def add_months(dt, n):
    y, m = divmod(dt.month - 1 + n, 12)
    y, m = dt.year + y, m + 1
    return date(y, m, min(dt.day, calendar.monthrange(y, m)[1]))


def month_key(dt):
    return f"{dt.year}-{dt.month:02d}"


def daterange(a, b):
    for i in range((b - a).days + 1):
        yield a + timedelta(days=i)


# ------------------------------------------------------------------ leave year
def year_start_month():
    return int(get_settings().get("leave_year_start") or 1)


def leave_year_of(dt, sm=None):
    sm = sm or year_start_month()
    return dt.year if (sm == 1 or dt.month >= sm) else dt.year - 1


def year_bounds(y, sm=None):
    sm = sm or year_start_month()
    return date(y, sm, 1), date(y + 1, sm, 1) - timedelta(days=1)


def year_label(y, sm=None):
    sm = sm or year_start_month()
    return str(y) if sm == 1 else f"{y}-{str(y + 1)[2:]}"


def go_live(today=None, save=False):
    """First day leave is managed from. Blank = start of the current leave year (saved at the first real run)."""
    v = get_settings().get("leave_start_date")
    if v:
        return date.fromisoformat(v)
    sm = year_start_month()
    gl = year_bounds(leave_year_of(today or date.today(), sm), sm)[0]
    if save:
        row = db.query(Setting).filter_by(key="leave_start_date").first() or Setting(key="leave_start_date")
        row.value = gl.isoformat()
        db.add(row)
        db.flush()
    return gl


# ------------------------------------------------------------------ policies
class Ctx:
    """Per-run cache of leave types, policies and per-employee overrides."""

    def __init__(self):
        self.types = {t.id: t for t in db.query(LeaveType).order_by(LeaveType.sort_order, LeaveType.id)}
        pols = db.query(LeavePolicy).all()
        self.by_cat = {p.category_id: p for p in pols if p.category_id}
        self.default = next((p for p in pols if p.is_default), None)
        self.over = {(o.emp_id, o.leave_type_id): o for o in db.query(EmployeeLeaveOverride)}

    def policy(self, emp):
        return self.by_cat.get(emp.category_id) or self.default

    def rule(self, emp, lt_id):
        pol = self.policy(emp)
        if not pol:
            return None
        r = next((x for x in pol.rules if x.leave_type_id == lt_id), None)
        if not r:
            return None
        o = self.over.get((emp.id, lt_id))
        return SimpleNamespace(
            quota=o.quota if o and o.quota is not None else (r.quota or 0), accrual=r.accrual,
            accrual_days=r.accrual_days or 20, wait_months=r.wait_months or 0, prorate=r.prorate,
            carry_mode=r.carry_mode, carry_limit=r.carry_limit or 0,
            max_balance=o.max_balance if o and o.max_balance is not None else r.max_balance,
            expires_days=r.expires_days, allow_negative=r.allow_negative,
            overridden=bool(o and (o.quota is not None or o.max_balance is not None)))


def leave_types(active_only=True):
    q = db.query(LeaveType).order_by(LeaveType.sort_order, LeaveType.id)
    return q.filter(LeaveType.active.is_(True)).all() if active_only else q.all()


def type_by_code(code):
    return db.query(LeaveType).filter_by(code=code).first()


# ------------------------------------------------------------------ ledger
def add_entry(emp_id, lt_id, on, kind, days, note="", ref=None, application_id=None, by=None):
    """Append a ledger row. With a `ref` this is idempotent: an existing row with the same ref is left alone."""
    if ref and db.query(LeaveLedger.id).filter_by(emp_id=emp_id, leave_type_id=lt_id, ref=ref).first():
        return None
    row = LeaveLedger(emp_id=emp_id, leave_type_id=lt_id, on_date=on, kind=kind, days=r2(days),
                      leave_year=leave_year_of(on), note=(note or "")[:200], ref=ref, application_id=application_id,
                      created_by=by)
    db.add(row)
    return row


def balance(emp_id, lt_id, as_on=None):
    q = db.query(func.coalesce(func.sum(LeaveLedger.days), 0.0)).filter(
        LeaveLedger.emp_id == emp_id, LeaveLedger.leave_type_id == lt_id)
    if as_on:
        q = q.filter(LeaveLedger.on_date <= as_on)
    return r2(q.scalar() or 0)


def balances_of(emp_id):
    rows = db.query(LeaveLedger.leave_type_id, func.sum(LeaveLedger.days)).filter(
        LeaveLedger.emp_id == emp_id).group_by(LeaveLedger.leave_type_id).all()
    return {lt: r2(v or 0) for lt, v in rows}


def pending_days(emp_id, lt_id, exclude_id=None):
    q = db.query(func.coalesce(func.sum(LeaveApplication.days), 0.0)).filter(
        LeaveApplication.emp_id == emp_id, LeaveApplication.leave_type_id == lt_id, LeaveApplication.status == "pending")
    if exclude_id:
        q = q.filter(LeaveApplication.id != exclude_id)
    return r2(q.scalar() or 0)


def available(emp_id, lt_id):
    return r2(balance(emp_id, lt_id) - pending_days(emp_id, lt_id))


def adjust(emp, lt, days, reason, by, kind="adjustment", on=None):
    """Manual correction / opening balance. A reason is mandatory; the audit log records it too."""
    if not (reason or "").strip():
        raise LeaveError("A reason is required for a manual change.")
    if not days:
        raise LeaveError("Enter the number of days (positive to add, negative to remove).")
    if abs(days * 2 - round(days * 2)) > 1e-9:
        raise LeaveError("Use whole or half days (0.5 steps).")
    add_entry(emp.id, lt.id, on or date.today(), kind, days, reason.strip(), by=by)
    db.commit()


# ------------------------------------------------------------------ accrual
def _rolled_years():
    return {r.leave_year for r in db.query(LeaveRollover)}


def _ledger_years():
    return {y for (y,) in db.query(LeaveLedger.leave_year).distinct()}


def _plan_credits(emp, rule, gl, end_on, sm):
    """[(on_date, amount, ref, note)] that SHOULD exist for the schedule between go-live and end_on."""
    if not emp.doj or rule.accrual in ("manual", "per_days_worked") or not rule.quota:
        return []
    elig = add_months(emp.doj, rule.wait_months)
    out = []
    if rule.accrual == "yearly":
        for y in range(leave_year_of(elig, sm), leave_year_of(end_on, sm) + 1):
            ys, ye = year_bounds(y, sm)
            if ye < gl:
                continue
            amount = rule.quota
            from_day = max(ys, elig)
            if rule.prorate and from_day > ys:
                amount = half_round(rule.quota * (((ye - from_day).days + 1) / ((ye - ys).days + 1)))
            on = max(ys, elig, gl)
            if on <= end_on:
                out.append((on, amount, f"grant:{y}", f"Yearly grant {year_label(y, sm)}"))
    elif rule.accrual == "monthly":
        per = r2(rule.quota / 12)
        start = max(elig, gl)
        m = date(start.year, start.month, 1)
        last = date(end_on.year, end_on.month, 1)
        while m <= last:
            if elig <= m:
                on, amount = m, per
            elif rule.prorate:
                dim = calendar.monthrange(m.year, m.month)[1]
                on, amount = elig, half_round(per * (dim - elig.day + 1) / dim)
            else:
                m = add_months(m, 1)
                continue
            if on <= end_on:
                out.append((on, amount, f"acc:{month_key(m)}", f"Monthly credit {month_key(m)}"))
            m = add_months(m, 1)
    return out


def _plan_days_worked(emp, rule, gl, end_on, finals, att_by_month):
    """1 day per N days worked, credited when the month is finalised (so it can never exceed what was worked)."""
    if not emp.doj or rule.accrual != "per_days_worked":
        return []
    out = []
    start = max(add_months(emp.doj, rule.wait_months), gl)
    m = date(start.year, start.month, 1)
    while m <= date(end_on.year, end_on.month, 1):
        key = month_key(m)
        month_end = date(m.year, m.month, calendar.monthrange(m.year, m.month)[1])
        if key in finals and month_end <= end_on:
            worked = att_by_month.get((emp.id, key), 0.0)
            out.append((month_end, floor_half(worked / rule.accrual_days), f"dw:{key}",
                        f"{worked:g} days worked / {rule.accrual_days:g} in {key}"))
        m = add_months(m, 1)
    return out


def accrue(as_on=None, dry_run=False, by=None):
    """Create every credit that is due up to `as_on`. Idempotent. Returns dict(entries, skipped, go_live).
    Credits stop at the leave ceiling; zero-day rows are recorded for credits that were fully capped."""
    as_on = as_on or date.today()
    sm = year_start_month()
    gl = go_live(as_on, save=not dry_run)
    cx = Ctx()
    finals = payroll.finalized_months()
    rolled, ledger_years = _rolled_years(), _ledger_years()
    att = {(a.emp_id, a.month): a.present_days or 0.0 for a in db.query(Attendance)}
    result = dict(entries=[], skipped=[], go_live=gl)
    blocked_years = set()

    def blocked(y):
        return (y - 1) in ledger_years and (y - 1) not in rolled and y - 1 >= leave_year_of(gl, sm)

    for emp in db.query(Employee).filter(Employee.doj.isnot(None), Employee.doj <= as_on).order_by(Employee.emp_code):
        end_on = min(as_on, emp.dol) if emp.dol else as_on
        pol = cx.policy(emp)
        if end_on < gl or not pol:
            continue
        for lt_id in [r.leave_type_id for r in pol.rules]:
            lt = cx.types.get(lt_id)
            rule = cx.rule(emp, lt_id)
            if not lt or not lt.active or not lt.uses_balance or not rule or rule.accrual == "manual":
                continue
            plans = _plan_credits(emp, rule, gl, end_on, sm) + _plan_days_worked(emp, rule, gl, end_on, finals, att)
            refs = {r for (r,) in db.query(LeaveLedger.ref).filter(
                LeaveLedger.emp_id == emp.id, LeaveLedger.leave_type_id == lt_id, LeaveLedger.ref.isnot(None))}
            running = balance(emp.id, lt_id)
            year_credit = defaultdict(float)
            for y, v in db.query(LeaveLedger.leave_year, func.sum(LeaveLedger.days)).filter(
                    LeaveLedger.emp_id == emp.id, LeaveLedger.leave_type_id == lt_id,
                    (LeaveLedger.ref.like("acc:%")) | (LeaveLedger.ref.like("dw:%"))).group_by(LeaveLedger.leave_year):
                year_credit[y] = v or 0.0
            for on, amount, ref, note in sorted(plans):
                if ref in refs:
                    continue
                y = leave_year_of(on, sm)
                if blocked(y):
                    if y not in blocked_years:
                        blocked_years.add(y)
                        result["skipped"].append(
                            f"Credits for {year_label(y, sm)} are waiting: run the year-end rollover for "
                            f"{year_label(y - 1, sm)} first.")
                    continue
                if ref.startswith(("acc:", "dw:")) and rule.quota:
                    amount = min(amount, max(0.0, r2(rule.quota - year_credit[y])))
                if rule.max_balance is not None:
                    room = max(0.0, r2(rule.max_balance - running))
                    if amount > room:
                        note += f" (capped from {amount:g} by the ceiling of {rule.max_balance:g})"
                        amount = room
                entry = dict(emp=emp, lt=lt, on=on, amount=r2(amount), ref=ref, note=note)
                result["entries"].append(entry)
                if not dry_run:
                    add_entry(emp.id, lt_id, on, "credit", amount, note, ref=ref, by=by or "system")
                running = r2(running + amount)
                if ref.startswith(("acc:", "dw:")):
                    year_credit[y] += amount
    if not dry_run:
        db.commit()
    return result


# ------------------------------------------------------------------ year-end rollover
def rollover_years():
    """Leave years that have data, with whether they are closed."""
    rolled = _rolled_years()
    today_year = leave_year_of(date.today())
    return [dict(year=y, label=year_label(y), done=y in rolled, over=y < today_year)
            for y in sorted(_ledger_years())]


def rollover(year, dry_run=True, by=None):
    """Close leave year `year`: carry forward per policy and lapse the rest. Returns dict(rows, total_lapsed)."""
    sm = year_start_month()
    ys, ye = year_bounds(year, sm)
    if ye >= date.today():
        raise LeaveError(f"Leave year {year_label(year, sm)} has not ended yet.")
    if year in _rolled_years():
        raise LeaveError(f"Leave year {year_label(year, sm)} is already closed.")
    cx = Ctx()
    rows, total = [], 0.0
    for emp in db.query(Employee).filter(Employee.doj.isnot(None)).order_by(Employee.emp_code):
        for lt_id, lt in cx.types.items():
            rule = cx.rule(emp, lt_id)
            if not lt.uses_balance or not rule or rule.expires_days:       # expiring types (comp off) lapse by expiry
                continue
            bal = balance(emp.id, lt_id, as_on=ye)
            if bal <= 0:
                continue
            carry = 0.0 if rule.carry_mode == "none" else (bal if rule.carry_mode == "all" else min(bal, rule.carry_limit))
            lapse = r2(bal - carry)
            rows.append(dict(emp=emp, lt=lt, balance=bal, carry=r2(carry), lapse=lapse))
            if lapse > 0:
                total += lapse
                if not dry_run:
                    add_entry(emp.id, lt_id, ye, "lapse", -lapse,
                              f"Year-end {year_label(year, sm)}: carried {carry:g}, lapsed {lapse:g}",
                              ref=f"lapse:{year}", by=by)
    if not dry_run:
        db.add(LeaveRollover(leave_year=year, run_by=by, summary=f"{len(rows)} balance(s), {total:g} day(s) lapsed"))
        db.commit()
    return dict(rows=rows, total_lapsed=r2(total), year=year)


# ------------------------------------------------------------------ comp off
def grant_comp_off(emp, worked_on, days, reason, by, expiry_days=None):
    lt = type_by_code("CO")
    if not lt or not lt.active:
        raise LeaveError("The Comp Off leave type is not active.")
    if not (reason or "").strip():
        raise LeaveError("Say why the comp off is granted (e.g. worked on a holiday).")
    if days <= 0 or abs(days * 2 - round(days * 2)) > 1e-9:
        raise LeaveError("Days must be a positive whole or half number.")
    if not emp.doj or worked_on < emp.doj:
        raise LeaveError("The worked date is before the employee joined.")
    if worked_on > date.today():
        raise LeaveError("The worked date is in the future.")
    exp_days = int(expiry_days if expiry_days not in (None, "") else get_settings()["comp_off_expiry_days"])
    g = CompOffGrant(emp_id=emp.id, worked_on=worked_on, days=days, reason=reason.strip()[:200], granted_by=by,
                     expires_on=date.today() + timedelta(days=exp_days))
    db.add(g)
    db.flush()
    add_entry(emp.id, lt.id, date.today(), "comp_grant", days,
              f"Comp off for {worked_on:%d-%b-%Y}: {reason.strip()[:80]} (expires {g.expires_on:%d-%b-%Y})",
              ref=f"comp:{g.id}", by=by)
    notify(emp.id, f"{days:g} day comp off granted for {worked_on:%d %b %Y}. Use it before {g.expires_on:%d %b %Y}.", "/me/leave")
    db.commit()
    return g


def expire_comp_offs(as_on=None, dry_run=False):
    """Lapse unused comp-off days of grants past their expiry. FIFO: usage is charged to the oldest grants."""
    as_on = as_on or date.today()
    lt = type_by_code("CO")
    out = []
    if not lt:
        return out
    by_emp = defaultdict(list)
    for g in db.query(CompOffGrant).order_by(CompOffGrant.expires_on, CompOffGrant.id):
        by_emp[g.emp_id].append(g)
    for emp_id, grants in by_emp.items():
        used = -(db.query(func.coalesce(func.sum(LeaveLedger.days), 0.0)).filter(
            LeaveLedger.emp_id == emp_id, LeaveLedger.leave_type_id == lt.id,
            LeaveLedger.kind.in_(("debit", "debit_reversal"))).scalar() or 0.0)
        for g in grants:
            take = min(g.days, max(used, 0.0))
            used -= take
            remaining = r2(g.days - take)
            if g.expires_on < as_on and remaining > 0:
                ref = f"compexp:{g.id}"
                if not db.query(LeaveLedger.id).filter_by(emp_id=emp_id, leave_type_id=lt.id, ref=ref).first():
                    out.append(dict(emp=g.employee, grant=g, lapsed=remaining))
                    if not dry_run:
                        add_entry(emp_id, lt.id, g.expires_on, "comp_expiry", -remaining,
                                  f"Comp off of {g.worked_on:%d-%b-%Y} expired unused", ref=ref, by="system")
    if not dry_run:
        db.commit()
    return out


# ------------------------------------------------------------------ holidays, weekly offs, leave dates
def weekly_off_days(emp):
    rules = db.query(WeeklyOffRule).all()
    rule = next((r for r in rules if r.category_id and r.category_id == emp.category_id), None) or \
        next((r for r in rules if r.category_id is None), None)
    txt = rule.weekdays if rule else "6"
    return {int(x) for x in txt.split(",") if x.strip().isdigit()}


def holiday_dates(a, b):
    return {h.on_date for h in db.query(Holiday).filter(Holiday.on_date >= a, Holiday.on_date <= b)}


def leave_dates(emp, d1, d2, h1=False, h2=False, sandwich=None):
    """[(date, value)] the application consumes: working days in the range (weekly offs and holidays are not
    leave days unless the sandwich rule is on and they fall between leave days). Half-day flags give 0.5."""
    if sandwich is None:
        sandwich = get_settings().get("leave_sandwich") == "1"
    offs, hol = weekly_off_days(emp), holiday_dates(d1, d2)
    days = list(daterange(d1, d2))
    working = [d for d in days if d.weekday() not in offs and d not in hol]
    if not working:
        return []
    out = {d: 1.0 for d in working}
    if sandwich:
        for d in days:
            if working[0] < d < working[-1]:
                out.setdefault(d, 1.0)
    if (h1 or h2) and d1 == d2:
        out[working[0]] = 0.5
    else:
        if h1 and working[0] == d1:
            out[d1] = 0.5
        if h2 and working[-1] == d2:
            out[d2] = 0.5
    return sorted(out.items())


# ------------------------------------------------------------------ applications
def _months_of(items):
    return {month_key(d) for d, _ in items}


def validate_application(emp, lt, d1, d2, h1, h2, doc, employee_initiated, exclude_id=None):
    """Returns (items, days, errors)."""
    errs = []
    if not lt or not lt.active:
        return [], 0.0, ["Choose a valid leave type."]
    if not emp.doj:
        errs.append("This employee has no date of joining; ask an admin to add it.")
    elif d1 < emp.doj:
        errs.append(f"Leave cannot start before the joining date ({emp.doj:%d-%b-%Y}).")
    if emp.dol and d2 > emp.dol:
        errs.append(f"Leave cannot extend beyond the leaving date ({emp.dol:%d-%b-%Y}).")
    if d2 < d1:
        errs.append("The end date is before the start date.")
        return [], 0.0, errs
    if (d2 - d1).days > 120:
        errs.append("A single application can cover at most 120 days.")
    if (h1 or h2) and not lt.half_day_ok:
        errs.append(f"{lt.name} cannot be taken as a half day.")
    if employee_initiated:
        limit = int(get_settings()["leave_backdate_days"])
        if d1 < date.today() - timedelta(days=limit):
            errs.append(f"You can apply at most {limit} day(s) back. Ask an admin to enter older leave.")
    items = leave_dates(emp, d1, d2, h1, h2)
    days = r2(sum(v for _, v in items))
    if not items and not errs:
        errs.append("Those dates are only weekly offs / holidays, so no leave is needed.")
    if errs:
        return items, days, errs
    finals = payroll.finalized_months()
    locked = sorted(_months_of(items) & finals)
    if locked:
        errs.append(f"Payroll for {', '.join(locked)} is finalised; an admin must unlock it first.")
    mine = {d for d, _ in items}
    for other in db.query(LeaveApplication).filter(
            LeaveApplication.emp_id == emp.id, LeaveApplication.status.in_(("pending", "approved")),
            LeaveApplication.from_date <= d2, LeaveApplication.to_date >= d1):
        if exclude_id and other.id == exclude_id:
            continue
        clash = mine & {d for d, _ in leave_dates(emp, other.from_date, other.to_date, other.from_half, other.to_half)}
        if clash:
            errs.append(f"Overlaps another {other.status} leave application ({other.from_date:%d-%b} to {other.to_date:%d-%b}).")
            break
    if lt.uses_balance:
        rule = Ctx().rule(emp, lt.id)
        free = r2(balance(emp.id, lt.id) - pending_days(emp.id, lt.id, exclude_id))
        if days > free + 1e-9 and not (rule and rule.allow_negative):
            errs.append(f"Only {free:g} day(s) of {lt.name} available; {days:g} requested.")
    if lt.doc_after_days and ((d2 - d1).days + 1) > lt.doc_after_days and not doc:
        errs.append(f"A medical certificate is needed for {lt.name} longer than {lt.doc_after_days} day(s); "
                    "tick 'certificate provided' once you have it.")
    return items, days, errs


def apply_leave(emp, lt, d1, d2, h1, h2, reason, doc, by, employee_initiated=False):
    items, days, errs = validate_application(emp, lt, d1, d2, h1, h2, doc, employee_initiated)
    if errs:
        return None, errs
    app = LeaveApplication(emp_id=emp.id, leave_type_id=lt.id, from_date=d1, to_date=d2, from_half=bool(h1),
                           to_half=bool(h2), days=days, reason=(reason or "").strip()[:200], doc_provided=bool(doc),
                           status="pending", applied_by=by)
    db.add(app)
    db.commit()
    return app, []


def _mark_status(paid, v):
    return ("L" if v >= 1 else "HL") if paid else ("U" if v >= 1 else "HU")


def approve(app, by, note=""):
    """Approve: re-check everything, debit the ledger and write the attendance marks."""
    if app.status != "pending":
        return [f"This application is already {app.status}."]
    emp, lt = app.employee, app.leave_type
    items, days, errs = validate_application(emp, lt, app.from_date, app.to_date, app.from_half, app.to_half,
                                             app.doc_provided, False, exclude_id=app.id)
    if lt.uses_balance and not errs:
        rule = Ctx().rule(emp, lt.id)
        if days > balance(emp.id, lt.id) + 1e-9 and not (rule and rule.allow_negative):
            errs.append(f"Balance is now {balance(emp.id, lt.id):g}; {days:g} needed.")
    if errs:
        return errs
    existing = {m.day: m for m in db.query(DailyAttendance).filter(
        DailyAttendance.emp_id == emp.id, DailyAttendance.day >= app.from_date, DailyAttendance.day <= app.to_date)}
    clash = [d for d, _ in items if d in existing and existing[d].source != "leave" and existing[d].status in ("P", "HD")]
    if clash:
        return [f"Already marked present on {', '.join(f'{d:%d-%b}' for d in clash[:5])}. Change that attendance first."]
    app.days = days
    if lt.uses_balance:
        per_year = defaultdict(float)
        for d, v in items:
            per_year[leave_year_of(d)] += v
        for y, dd in per_year.items():
            first = min(d for d, _ in items if leave_year_of(d) == y)
            add_entry(emp.id, lt.id, first, "debit", -dd, f"{lt.name} {app.from_date:%d-%b} to {app.to_date:%d-%b}",
                      ref=f"app:{app.id}:{y}", application_id=app.id, by=by)
    for d, v in items:
        m = existing.get(d) or DailyAttendance(emp_id=emp.id, day=d)
        m.status, m.ot_hours, m.source = _mark_status(lt.paid, v), 0, "leave"
        m.leave_application_id, m.leave_type_id = app.id, lt.id
        db.add(m)
    app.status, app.decided_by, app.decided_at, app.decision_note = "approved", by, datetime.now(), (note or "")[:200]
    notify(emp.id, f"Your {lt.name} ({app.from_date:%d %b} to {app.to_date:%d %b}) was approved.", "/me/leave")
    db.commit()
    return []


def reject(app, by, note=""):
    if app.status != "pending":
        return [f"This application is already {app.status}."]
    app.status, app.decided_by, app.decided_at, app.decision_note = "rejected", by, datetime.now(), (note or "")[:200]
    notify(app.emp_id, f"Your {app.leave_type.name} ({app.from_date:%d %b} to {app.to_date:%d %b}) was not approved."
           + (f" Reason: {note}" if note else ""), "/me/leave")
    db.commit()
    return []


def cancel(app, by, note="", own_request=False):
    """Cancel a pending or approved application; approved ones give the days back and clear the attendance marks."""
    if app.status not in ("pending", "approved"):
        return [f"This application is already {app.status}."]
    if app.status == "approved":
        if own_request and app.from_date <= date.today():
            return ["Leave that has started can only be cancelled by an admin."]
        items = leave_dates(app.employee, app.from_date, app.to_date, app.from_half, app.to_half)
        locked = sorted(_months_of(items) & payroll.finalized_months())
        if locked:
            return [f"Payroll for {', '.join(locked)} is finalised; an admin must unlock it first."]
        for row in db.query(LeaveLedger).filter(LeaveLedger.application_id == app.id, LeaveLedger.kind == "debit"):
            add_entry(app.emp_id, row.leave_type_id, date.today(), "debit_reversal", -row.days,
                      f"Cancelled {app.leave_type.name} {app.from_date:%d-%b} to {app.to_date:%d-%b}",
                      ref=f"appcancel:{app.id}:{row.leave_year}", application_id=app.id, by=by)
        for m in db.query(DailyAttendance).filter_by(leave_application_id=app.id).all():
            db.delete(m)
    was = app.status
    app.status, app.decided_by, app.decided_at, app.decision_note = "cancelled", by, datetime.now(), (note or "")[:200]
    if was == "approved" and not own_request:
        notify(app.emp_id, f"Your approved {app.leave_type.name} ({app.from_date:%d %b} to {app.to_date:%d %b}) was cancelled.", "/me/leave")
    db.commit()
    return []


# ------------------------------------------------------------------ attendance by day
def _in_service(emp, day):
    return bool(emp.doj) and emp.doj <= day and (emp.dol is None or day <= emp.dol)


def employed_on(emp, day):
    """Lenient check used by the daily attendance page: a missing joining date does not block marking."""
    return (emp.doj is None or emp.doj <= day) and (emp.dol is None or day <= emp.dol)


def mark_day(emp, day, status, ot=0.0, by=None):
    """Mark one day by hand. Returns an error string or None."""
    if status not in MANUAL_STATUSES:
        return f"Unknown status {status}."
    if not emp.doj:
        return "This employee has no date of joining."
    if day < emp.doj:
        return f"{day:%d-%b-%Y} is before the joining date ({emp.doj:%d-%b-%Y})."
    if emp.dol and day > emp.dol:
        return f"{day:%d-%b-%Y} is after the leaving date."
    if day > date.today():
        return "Attendance cannot be marked for a future date."
    if payroll.is_final(month_key(day)):
        return f"Payroll for {month_key(day)} is finalised; unlock it first."
    if not 0 <= ot <= 16:
        return "Overtime must be between 0 and 16 hours."
    m = db.query(DailyAttendance).filter_by(emp_id=emp.id, day=day).first()
    if m and m.source == "leave":
        return f"{day:%d-%b-%Y} comes from an approved leave application; cancel that application to change it."
    m = m or DailyAttendance(emp_id=emp.id, day=day)
    m.status, m.ot_hours, m.source = status, ot, "manual"
    db.add(m)
    return None


def mark_range(emp, d1, d2, status, ot=0.0, skip_offs=True, by=None):
    """Mark a range; returns (marked, [problems]). Weekly offs / holidays are skipped when `skip_offs`."""
    if d2 < d1:
        return 0, ["The end date is before the start date."]
    if (d2 - d1).days > 366:
        return 0, ["Choose at most one year at a time."]
    offs, hol = weekly_off_days(emp), holiday_dates(d1, d2)
    n, problems = 0, []
    for d in daterange(d1, d2):
        if skip_offs and (d.weekday() in offs or d in hol):
            continue
        err = mark_day(emp, d, status, ot, by)
        if err:
            problems.append(err)
        else:
            n += 1
    if problems:
        db.rollback()
        return 0, problems[:6]
    db.commit()
    return n, []


def clear_day(emp, day):
    if payroll.is_final(month_key(day)):
        return f"Payroll for {month_key(day)} is finalised."
    m = db.query(DailyAttendance).filter_by(emp_id=emp.id, day=day).first()
    if m and m.source == "leave":
        return "This day comes from approved leave; cancel the application instead."
    if m:
        db.delete(m)
        db.commit()
    return None


def year_calendar(emp, year):
    """Month-by-month grid for one calendar year, from the joining date. Returns dict(months, summary, start, end)."""
    today = date.today()
    first, last = date(year, 1, 1), date(year, 12, 31)
    marks = {m.day: m for m in db.query(DailyAttendance).filter(
        DailyAttendance.emp_id == emp.id, DailyAttendance.day >= first, DailyAttendance.day <= last)}
    lts = {t.id: t for t in db.query(LeaveType)}
    offs, hol = weekly_off_days(emp), {h.on_date: h.name for h in db.query(Holiday).filter(
        Holiday.on_date >= first, Holiday.on_date <= last)}
    finals = payroll.finalized_months()
    summary = defaultdict(float)
    months = []
    for mo in range(1, 13):
        days = []
        for d in daterange(date(year, mo, 1), date(year, mo, calendar.monthrange(year, mo)[1])):
            cell = dict(date=d, code="", note="", in_service=_in_service(emp, d), editable=False, source="")
            m = marks.get(d)
            if m:
                cell["code"], cell["source"] = m.status, m.source
                if m.leave_type_id and m.leave_type_id in lts:
                    cell["note"] = lts[m.leave_type_id].name
                if m.ot_hours:
                    cell["note"] += f" OT {m.ot_hours:g}h"
            elif cell["in_service"] and d in hol:
                cell["code"], cell["note"], cell["source"] = "H", hol[d], "calendar"
            elif cell["in_service"] and d.weekday() in offs:
                cell["code"], cell["source"] = "WO", "calendar"
            cell["editable"] = cell["in_service"] and d <= today and month_key(d) not in finals and cell["source"] != "leave"
            if cell["in_service"] and cell["code"]:
                summary[cell["code"]] += 1
            days.append(cell)
        months.append(dict(month=mo, name=calendar.month_name[mo], first_weekday=date(year, mo, 1).weekday(), days=days))
    return dict(months=months, summary=dict(summary), year=year)


# ------------------------------------------------------------------ attendance corrections
def request_correction(emp, day, status, reason, by):
    if status not in REQUEST_STATUSES:
        return "You can ask for Present or Half day."
    if not (reason or "").strip():
        return "Say what happened (why the record is wrong)."
    if not emp.doj or day < emp.doj:
        return "That date is before your joining date."
    if emp.dol and day > emp.dol:
        return "That date is after your leaving date."
    if day > date.today():
        return "You cannot ask for a future date."
    limit = int(get_settings()["attendance_request_days"])
    if day < date.today() - timedelta(days=limit):
        return f"You can ask for corrections up to {limit} days back; ask an admin for older dates."
    if payroll.is_final(month_key(day)):
        return "Payroll for that month is finalised; ask an admin."
    m = db.query(DailyAttendance).filter_by(emp_id=emp.id, day=day).first()
    if m and m.source == "leave":
        return "That day is approved leave."
    if db.query(AttendanceRequest.id).filter_by(emp_id=emp.id, on_date=day, status="pending").first():
        return "You already have a pending request for that date."
    db.add(AttendanceRequest(emp_id=emp.id, on_date=day, requested_status=status, reason=reason.strip()[:200],
                             requested_by=by))
    db.commit()
    return None


def decide_correction(req, approve_it, by, note=""):
    if req.status != "pending":
        return f"Already {req.status}."
    if approve_it:
        err = mark_day(req.employee, req.on_date, req.requested_status, 0.0, by)
        if err:
            db.rollback()
            return err
        req.status = "approved"
    else:
        req.status = "rejected"
    req.decided_by, req.decided_at, req.decision_note = by, datetime.now(), (note or "")[:200]
    notify(req.emp_id, f"Your attendance correction for {req.on_date:%d %b %Y} was {req.status}."
           + (f" Note: {note}" if note else ""), "/me/attendance")
    db.commit()
    return None


# ------------------------------------------------------------------ notifications & portal access
def notify(emp_id, text, url=None):
    db.add(Notification(emp_id=emp_id, text=text[:240], url=url))


def portal_user(emp):
    return db.query(User).filter_by(employee_id=emp.id).first()


def grant_portal_access(emp, username, password, org_id):
    """Create the employee's login (temporary password; they must change it at first sign-in)."""
    if portal_user(emp):
        raise LeaveError("This employee already has portal access.")
    if emp.status != "Active":
        raise LeaveError("Only active employees can have portal access.")
    username = (username or "").strip()
    if len(username) < 3 or " " in username:
        raise LeaveError("Choose a username of at least 3 characters without spaces.")
    if db.query(User.id).filter(func.lower(User.username) == username.lower()).first():
        raise LeaveError("That username is already taken.")
    u = User(org_id=org_id, username=username, pw_hash=generate_password_hash(password), role="employee",
             employee_id=emp.id, must_change_password=True, is_active=True)
    db.add(u)
    db.commit()
    return u


# ------------------------------------------------------------------ reports
def balances_matrix(active_only=True):
    """(leave types, rows) with every active employee's balance per type, computed with two grouped queries."""
    lts = [t for t in leave_types()]
    bal = defaultdict(float)
    for e_id, lt_id, v in db.query(LeaveLedger.emp_id, LeaveLedger.leave_type_id, func.sum(LeaveLedger.days)).group_by(
            LeaveLedger.emp_id, LeaveLedger.leave_type_id):
        bal[(e_id, lt_id)] = r2(v or 0)
    pend = defaultdict(float)
    for e_id, v in db.query(LeaveApplication.emp_id, func.sum(LeaveApplication.days)).filter(
            LeaveApplication.status == "pending").group_by(LeaveApplication.emp_id):
        pend[e_id] = r2(v or 0)
    q = db.query(Employee).order_by(Employee.emp_code)
    if active_only:
        q = q.filter(Employee.status == "Active")
    rows = [dict(emp=e, bal={t.id: bal[(e.id, t.id)] for t in lts}, pending=pend[e.id]) for e in q]
    return lts, rows


def liability(as_on=None):
    """Value of encashable leave (balance x daily Basic+DA) for active employees: a provisioning figure."""
    as_on = as_on or date.today()
    month = month_key(as_on)
    lts = [t for t in leave_types() if t.encashable]
    rows, total = [], 0.0
    for e in db.query(Employee).filter_by(status="Active").order_by(Employee.emp_code):
        if not e.category:
            continue
        rate = payroll.rate_for(e.category, month)
        basic = D(str(e.basic_override_day)) if e.basic_override_day else rate.basic
        daily = basic + rate.da
        for t in lts:
            bal = balance(e.id, t.id, as_on)
            if bal > 0:
                amt = payroll.rnd(daily * D(str(bal)))
                rows.append(dict(emp=e, lt=t, days=bal, daily=float(daily), amount=amt))
                total += amt
    return dict(as_on=as_on, rows=rows, total=int(total))


def register(d1, d2):
    return db.query(LeaveLedger).filter(LeaveLedger.on_date >= d1, LeaveLedger.on_date <= d2).order_by(
        LeaveLedger.on_date, LeaveLedger.id).all()
