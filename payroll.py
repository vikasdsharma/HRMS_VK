"""Salary + statutory (EPF / ESI) computation."""
import calendar
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING
from models import db, Attendance, Employee, Payslip, get_settings


def rnd(x):
    """Round half up to whole rupee (EPFO convention)."""
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def ceil_rupee(x):
    """ESIC rounds contribution UP to next rupee."""
    return int(Decimal(str(round(x, 6))).quantize(Decimal("1"), rounding=ROUND_CEILING))


def days_in(month):
    y, m = map(int, month.split("-"))
    return calendar.monthrange(y, m)[1]


def compute(emp, att, S, month):
    cat = emp.category
    dim = days_in(month)
    paid = min((att.present_days or 0) + (att.paid_holidays or 0), dim)
    ncp = max(dim - paid, 0)

    basic_d = emp.basic_day
    basic = round(basic_d * paid, 2)
    da = round((cat.da_day or 0) * paid, 2)
    hra = round((cat.hra_day or 0) * paid, 2)
    other = round((cat.other_day or 0) * paid, 2)
    ot = round((att.ot_hours or 0) * ((basic_d + (cat.da_day or 0)) / 8.0) * (cat.ot_multiplier or 2), 2)
    gross_exact = basic + da + hra + other + ot
    gross = rnd(gross_exact)

    pf_ceiling = float(S["pf_wage_ceiling"])
    esi_ceiling = float(S["esi_wage_ceiling"])

    epf_w = eps_w = edli_w = pf_ee = eps_er = diff = 0
    if cat.pf_applicable and paid > 0:
        base = basic + da                      # PF wages = Basic + DA (excl. OT/HRA)
        epf_w = rnd(min(base, pf_ceiling) if cat.pf_ceiling else base)
        eps_w = rnd(min(epf_w, pf_ceiling))
        edli_w = rnd(min(base, pf_ceiling))
        pf_ee = rnd(epf_w * float(S["pf_ee_rate"]) / 100)
        eps_er = rnd(eps_w * float(S["eps_rate"]) / 100)
        diff = pf_ee - eps_er                  # employer share to A/c 01 = 12% - EPS

    esi_w = esi_ee = esi_er = 0
    # eligibility judged on wages excluding overtime; contribution on total gross
    if cat.esi_applicable and paid > 0 and (gross_exact - ot) <= esi_ceiling:
        esi_w = gross
        esi_ee = ceil_rupee(gross * float(S["esi_ee_rate"]) / 100)
        esi_er = ceil_rupee(gross * float(S["esi_er_rate"]) / 100)

    adv = att.advance or 0
    od = att.other_deduction or 0
    total_ded = pf_ee + esi_ee + adv + od
    net = rnd(gross_exact - total_ded)

    return dict(category_name=cat.name, days_in_month=dim, paid_days=paid, ncp_days=ncp,
                basic=basic, da=da, hra=hra, other=other, ot_amount=ot, gross=gross,
                epf_wages=epf_w, eps_wages=eps_w, edli_wages=edli_w, pf_ee=pf_ee,
                eps_er=eps_er, pf_er_diff=diff, esi_wages=esi_w, esi_ee=esi_ee, esi_er=esi_er,
                advance=adv, other_deduction=od, total_deduction=total_ded, net=net)


def generate_month(month):
    """(Re)generate payslips for every employee with attendance in `month`. Returns (count, skipped)."""
    S = get_settings()
    count, skipped = 0, []
    rows = db.query(Attendance).filter_by(month=month).all()
    for att in rows:
        emp = att.employee
        if not emp.category:
            skipped.append(f"{emp.emp_code}: no salary category assigned")
            continue
        vals = compute(emp, att, S, month)
        slip = db.query(Payslip).filter_by(emp_id=emp.id, month=month).first()
        if not slip:
            slip = Payslip(emp_id=emp.id, month=month)
            db.add(slip)
        for k, v in vals.items():
            setattr(slip, k, v)
        count += 1
    db.commit()
    return count, skipped


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
    t["acc02"] = max(rnd(t["epf_wages"] * float(S["pf_admin_rate"]) / 100), float(S["pf_admin_min"])) if pf else 0
    t["acc02"] = rnd(t["acc02"])
    t["acc21"] = rnd(t["edli_wages"] * float(S["edli_rate"]) / 100)
    t["acc01_ee"] = t["pf_ee"]
    t["acc01_er"] = t["pf_er_diff"]
    t["acc10"] = t["eps_er"]
    t["pf_total"] = t["acc01_ee"] + t["acc01_er"] + t["acc10"] + t["acc02"] + t["acc21"]
    t["esi_total"] = t["esi_ee"] + t["esi_er"]
    return t
