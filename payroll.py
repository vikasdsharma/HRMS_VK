"""Salary + statutory (EPF / ESI) computation, month finalisation and consistency checks.

All money arithmetic uses Decimal (no binary-float surprises). Statutory amounts are whole rupees:
EPF rounds half-up (EPFO convention), ESI rounds UP to the next rupee (ESIC convention).
"""
import calendar
from collections import namedtuple
from datetime import date, datetime
from decimal import ROUND_CEILING, ROUND_HALF_UP
from decimal import Decimal as D

from models import Attendance, PayrollRun, Payslip, db, get_settings

CENT = D("0.01")
EPS_STOP_AGE = 58
Rate = namedtuple("Rate", "basic da hra other ot_mult effective_from")


class PayrollLocked(Exception):
    pass


def d(x):
    return D(str(x if x is not None else 0))


def q2(x):
    return d(x).quantize(CENT, ROUND_HALF_UP)


def rnd(x):
    """Whole rupee, half-up (EPFO)."""
    return int(d(x).quantize(D(1), ROUND_HALF_UP))


def ceil_rupee(x):
    """ESIC rounds contribution UP to the next rupee."""
    return int(d(x).quantize(D(1), ROUND_CEILING))


def days_in(month):
    y, m = map(int, month.split("-"))
    return calendar.monthrange(y, m)[1]


def month_start(month):
    y, m = map(int, month.split("-"))
    return date(y, m, 1)


def month_end(month):
    y, m = map(int, month.split("-"))
    return date(y, m, calendar.monthrange(y, m)[1])


def age_on(dob, ref):
    return ref.year - dob.year - ((ref.month, ref.day) < (dob.month, dob.day))


# ------------------------------------------------------------------ rates (effective dated)
def rate_for(cat, month):
    """Rates in force for `month`: newest CategoryRate with effective_from <= month (else the oldest row,
    else the category's own columns for databases that have no rate history)."""
    rows = sorted(cat.rates, key=lambda r: r.effective_from)
    chosen = None
    for r in rows:
        if r.effective_from <= month:
            chosen = r
    if chosen is None and rows:
        chosen = rows[0]
    if chosen:
        return Rate(d(chosen.basic_day), d(chosen.da_day), d(chosen.hra_day), d(chosen.other_day),
                    d(chosen.ot_multiplier or 2), chosen.effective_from)
    return Rate(d(cat.basic_day), d(cat.da_day), d(cat.hra_day), d(cat.other_day), d(cat.ot_multiplier or 2), "-")


# ------------------------------------------------------------------ ESI contribution periods
def esi_period_start(month):
    """ESIC contribution periods are Apr-Sep and Oct-Mar. Returns the first month (YYYY-MM) of the period."""
    y, m = map(int, month.split("-"))
    if 4 <= m <= 9:
        return f"{y}-04"
    return f"{y}-10" if m >= 10 else f"{y - 1}-10"


def esi_continuing_ids(month):
    """Employees who already contributed to ESI earlier in the same contribution period: once covered, an
    employee stays covered for the rest of the period even if wages rise above the ceiling."""
    start = esi_period_start(month)
    rows = db.query(Payslip.emp_id).filter(Payslip.month >= start, Payslip.month < month,
                                           Payslip.esi_wages > 0).distinct()
    return {r[0] for r in rows}


# ------------------------------------------------------------------ the calculation
def compute(emp, att, S, month, rate=None, esi_continuing=False):
    cat = emp.category
    rate = rate or rate_for(cat, month)
    dim = days_in(month)
    paid = min(d(att.present_days) + d(att.paid_holidays), D(dim))
    ncp = max(D(dim) - paid, D(0))

    basic_d = d(emp.basic_override_day) if emp.basic_override_day else rate.basic
    basic = q2(basic_d * paid)
    da = q2(rate.da * paid)
    hra = q2(rate.hra * paid)
    other = q2(rate.other * paid)
    ot = q2(d(att.ot_hours) * ((basic_d + rate.da) / D(8)) * rate.ot_mult)
    gross_exact = basic + da + hra + other + ot
    gross = rnd(gross_exact)

    pf_ceiling = d(S["pf_wage_ceiling"])
    esi_ceiling = d(S["esi_wage_ceiling"])

    epf_w = eps_w = edli_w = pf_ee = eps_er = diff = 0
    if cat.pf_applicable and paid > 0:
        base = basic + da                                   # PF wages = Basic + DA (no OT / HRA / other)
        epf_w = rnd(min(base, pf_ceiling) if cat.pf_ceiling else base)
        edli_w = rnd(min(base, pf_ceiling))
        eps_blocked = bool(emp.eps_exempt) or (
            emp.dob is not None and age_on(emp.dob, month_start(month)) >= EPS_STOP_AGE)
        eps_w = 0 if eps_blocked else rnd(min(d(epf_w), pf_ceiling))
        pf_ee = rnd(d(epf_w) * d(S["pf_ee_rate"]) / 100)
        eps_er = rnd(d(eps_w) * d(S["eps_rate"]) / 100)
        diff = pf_ee - eps_er                               # employer's EPF share (A/c 01) = 12% - EPS

    esi_w = esi_ee = esi_er = 0
    if cat.esi_applicable and paid > 0 and ((gross_exact - ot) <= esi_ceiling or esi_continuing):
        esi_w = gross                                       # eligibility excludes OT; contribution is on total wages
        esi_ee = ceil_rupee(d(gross) * d(S["esi_ee_rate"]) / 100)
        esi_er = ceil_rupee(d(gross) * d(S["esi_er_rate"]) / 100)

    adv, od = rnd(att.advance), rnd(att.other_deduction)    # recoveries are whole rupees
    total_ded = pf_ee + esi_ee + adv + od
    net = gross - total_ded                                 # always exactly Gross - Deductions

    return dict(category_name=cat.name, days_in_month=dim, paid_days=float(paid), ncp_days=float(ncp),
                basic=float(basic), da=float(da), hra=float(hra), other=float(other), ot_amount=float(ot),
                gross=gross, epf_wages=epf_w, eps_wages=eps_w, edli_wages=edli_w, pf_ee=pf_ee,
                eps_er=eps_er, pf_er_diff=diff, esi_wages=esi_w, esi_ee=esi_ee, esi_er=esi_er,
                advance=adv, other_deduction=od, total_deduction=total_ded, net=net)


# ------------------------------------------------------------------ month status
def get_run(month):
    return db.query(PayrollRun).filter_by(month=month).first()


def is_final(month):
    r = get_run(month)
    return bool(r and r.status == "final")


def finalized_months():
    return {r.month for r in db.query(PayrollRun).filter_by(status="final")}


def finalize(month, username):
    if is_final(month):
        return
    r = get_run(month) or PayrollRun(month=month)
    r.status, r.finalized_by, r.finalized_at = "final", username, datetime.now()
    db.add(r)
    db.commit()


def unlock(month, username, reason):
    r = get_run(month)
    if not r or r.status != "final":
        return
    r.status, r.unlocked_by, r.unlocked_at, r.note = "draft", username, datetime.now(), (reason or "")[:200]
    db.commit()


# ------------------------------------------------------------------ generation
def generate_month(month):
    """(Re)generate payslips for every employee with attendance in `month`. Returns (count, skipped).
    Refuses if the month is finalised."""
    if is_final(month):
        raise PayrollLocked(f"{month} is finalised. Unlock it first if a correction is really needed.")
    S = get_settings()
    count, skipped = 0, []
    continuing = esi_continuing_ids(month)
    rate_cache = {}
    for att in db.query(Attendance).filter_by(month=month).all():
        emp = att.employee
        if not emp.category:
            skipped.append(f"{emp.emp_code}: no salary category assigned")
            continue
        rate = rate_cache.setdefault(emp.category_id, rate_for(emp.category, month))
        vals = compute(emp, att, S, month, rate, emp.id in continuing)
        slip = db.query(Payslip).filter_by(emp_id=emp.id, month=month).first()
        if not slip:
            slip = Payslip(emp_id=emp.id, month=month)
            db.add(slip)
        for k, v in vals.items():
            setattr(slip, k, v)
        count += 1
    db.commit()
    return count, skipped


def stale_slips(month):
    """Payslips that no longer match a fresh calculation (attendance, rates or settings changed after generation),
    plus attendance rows that have no payslip. Finalised months are frozen, so never stale."""
    if is_final(month):
        return []
    S = get_settings()
    continuing = esi_continuing_ids(month)
    slips = {s.emp_id: s for s in db.query(Payslip).filter_by(month=month)}
    out = []
    for att in db.query(Attendance).filter_by(month=month).all():
        emp = att.employee
        if not emp.category:
            continue
        s = slips.pop(emp.id, None)
        label = f"{emp.emp_code} {emp.name}"
        if s is None:
            out.append(f"{label}: attendance uploaded but no payslip generated")
            continue
        v = compute(emp, att, S, month, esi_continuing=emp.id in continuing)
        if any(v[k] != getattr(s, k) for k in ("gross", "net", "pf_ee", "esi_ee", "esi_er", "eps_er")):
            out.append(f"{label}: payslip is out of date (net {s.net} now computes to {v['net']})")
    for s in slips.values():
        out.append(f"{s.employee.emp_code} {s.employee.name}: payslip exists but attendance was removed")
    return out


def payroll_warnings(month):
    """Human-readable issues worth a look before finalising."""
    out = []
    slips = db.query(Payslip).filter_by(month=month).all()
    ms, me = month_start(month), month_end(month)
    for s in slips:
        e = s.employee
        who = f"{e.emp_code} {e.name}"
        if s.epf_wages and not e.uan:
            out.append(f"{who}: PF applicable but UAN missing - excluded from ECR")
        if s.esi_wages and not e.esic_no:
            out.append(f"{who}: ESI applicable but ESIC IP number missing - excluded from ESI file")
        if s.net < 0:
            out.append(f"{who}: deductions exceed earnings (net {s.net})")
        if e.doj and e.doj > me and s.paid_days:
            out.append(f"{who}: paid days for {month} but joining date is {e.doj:%d-%m-%Y}")
        if e.dol and e.dol < ms and s.paid_days:
            out.append(f"{who}: paid days for {month} but left on {e.dol:%d-%m-%Y}")
    seen = set()
    for s in slips:
        c = s.employee.category
        if c and c.id not in seen and c.min_wage_day:
            seen.add(c.id)
            rate = rate_for(c, month)
            total = rate.basic + rate.da + rate.hra + rate.other
            if total < d(c.min_wage_day):
                out.append(f"Category {c.name}: wage {total}/day is below the notified minimum {c.min_wage_day}/day")
    return out


def month_totals(month):
    slips = db.query(Payslip).filter_by(month=month).all()
    S = get_settings()
    pf = [s for s in slips if s.epf_wages > 0]
    esi = [s for s in slips if s.esi_wages > 0]
    t = dict(
        slips=len(slips), gross=sum(s.gross for s in slips), net=sum(s.net for s in slips),
        pf_members=len(pf),
        pf_gross=sum(s.gross for s in pf), epf_wages=sum(s.epf_wages for s in pf),
        eps_wages=sum(s.eps_wages for s in pf), edli_wages=sum(s.edli_wages for s in pf),
        pf_ee=sum(s.pf_ee for s in pf), eps_er=sum(s.eps_er for s in pf),
        pf_er_diff=sum(s.pf_er_diff for s in pf),
        esi_members=len(esi), esi_wages=sum(s.esi_wages for s in esi),
        esi_ee=sum(s.esi_ee for s in esi), esi_er=sum(s.esi_er for s in esi),
    )
    t["acc02"] = rnd(max(d(t["epf_wages"]) * d(S["pf_admin_rate"]) / 100, d(S["pf_admin_min"]))) if pf else 0
    t["acc21"] = rnd(d(t["edli_wages"]) * d(S["edli_rate"]) / 100)
    t["acc01_ee"] = t["pf_ee"]
    t["acc01_er"] = t["pf_er_diff"]
    t["acc10"] = t["eps_er"]
    t["pf_total"] = t["acc01_ee"] + t["acc01_er"] + t["acc10"] + t["acc02"] + t["acc21"]
    t["esi_total"] = t["esi_ee"] + t["esi_er"]
    return t
