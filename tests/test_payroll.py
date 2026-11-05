"""Salary / statutory calculation: golden numbers, rate history, month locking, ESI and EPS rules."""
import io
from datetime import date

import compliance
import payroll
from models import get_settings, set_org

MONTH = "2026-10"
HDR = "emp_code,present_days,paid_holidays,ot_hours,advance,other_deduction\n"


def _att(client, month, csv_text):
    return client.post("/attendance", data={"month": month, "file": (io.BytesIO(csv_text.encode()), "a.csv")},
                       content_type="multipart/form-data")


def _cat_form(**over):
    base = {"name": "Unskilled", "basic_day": "400", "da_day": "60", "hra_day": "0", "other_day": "0",
            "ot_multiplier": "2", "pf_applicable": "on", "esi_applicable": "on", "pf_ceiling": "on"}
    base.update(over)
    return base


def _fake(cat_kw=None, emp_kw=None):
    c = dict(name="X", pf_applicable=True, esi_applicable=True, pf_ceiling=True, rates=[],
             basic_day=400, da_day=60, hra_day=0, other_day=0, ot_multiplier=2)
    c.update(cat_kw or {})
    cat = type("C", (), c)()
    e = dict(category=cat, basic_override_day=None, eps_exempt=False, dob=None)
    e.update(emp_kw or {})
    return type("E", (), e)()


def test_golden_example_from_the_readme(app, make_org, login, add_employee, raw):
    """Unskilled (Basic 400 + DA 60): 22 present + 4 holidays, 8 OT hours, Rs 500 advance, October (31 days)."""
    org = make_org()
    add_employee(org["id"], "E001", name="Ramesh Kumar", uan="100123456789", esic_no="1234567890")
    c = login(org["admin"])
    assert b"Saved attendance for 1" in _att(c, MONTH, HDR + "E001,22,4,8,500,0\n").data
    c.post("/payroll/generate", data={"month": MONTH})
    row = raw("select paid_days, basic, da, ot_amount, gross, epf_wages, pf_ee, eps_er, pf_er_diff, esi_wages, "
              "esi_ee, esi_er, total_deduction, net from payslips where org_id=:o", o=org["id"])[0]
    assert tuple(row) == (26, 10400.0, 1560.0, 920.0, 12880, 11960, 1435, 996, 439, 12880, 97, 419, 2032, 10848)


def test_net_is_always_gross_minus_deductions(app):
    S = get_settings()
    emp = _fake(dict(basic_day=513.5, da_day=61.25, hra_day=7.5))
    for present in (1, 7, 13.5, 22, 26):
        for ot in (0, 1.5, 7.25):
            att = type("A", (), dict(present_days=present, paid_holidays=2, ot_hours=ot, advance=123,
                                     other_deduction=45))()
            v = payroll.compute(emp, att, S, MONTH)
            assert v["gross"] - v["total_deduction"] == v["net"]
            assert isinstance(v["net"], int)


def test_rounding_conventions():
    assert payroll.rnd(1435.5) == 1436 and payroll.rnd(1435.49) == 1435          # PF: half-up
    assert payroll.ceil_rupee(96.6) == 97 and payroll.ceil_rupee(96.0) == 96     # ESI: always up
    assert payroll.ceil_rupee(0.0075 * 12880) == 97


def test_attendance_rejects_paise_duplicates_and_absurd_overtime(app, make_org, login, add_employee):
    org = make_org()
    add_employee(org["id"], "A1")
    c = login(org["admin"])
    assert b"whole rupee" in _att(c, MONTH, HDR + "A1,20,0,0,100.50,0\n").data
    assert b"more than once" in _att(c, MONTH, HDR + "A1,20,0,0,0,0\nA1,21,0,0,0,0\n").data
    assert b"not plausible" in _att(c, MONTH, HDR + "A1,20,0,999,0,0\n").data
    assert b"exceed the 31 days" in _att(c, MONTH, HDR + "A1,30,5,0,0,0\n").data


def test_rate_history_never_rewrites_earlier_months(app, make_org, login, add_employee, raw):
    org = make_org()
    add_employee(org["id"], "H1", name="History")
    c = login(org["admin"])
    cat = raw("select id from categories where org_id=:o and name='Unskilled'", o=org["id"])[0][0]
    for m in ("2026-08", "2026-10"):
        _att(c, m, "emp_code,present_days\nH1,10\n")
    c.post("/payroll/generate", data={"month": "2026-08"})
    assert raw("select basic from payslips where org_id=:o and month='2026-08'", o=org["id"])[0][0] == 4000.0
    r = c.post(f"/categories/{cat}/edit", data=_cat_form(basic_day="500", effective_from="2026-10"))
    assert r.status_code == 302
    c.post("/payroll/generate", data={"month": "2026-08"})
    c.post("/payroll/generate", data={"month": "2026-10"})
    assert raw("select basic from payslips where org_id=:o and month='2026-08'", o=org["id"])[0][0] == 4000.0, \
        "August must still use the old rate even after regenerating"
    assert raw("select basic from payslips where org_id=:o and month='2026-10'", o=org["id"])[0][0] == 5000.0
    assert raw("select count(*) from category_rates where category_id=:c", c=cat)[0][0] == 2
    assert raw("select basic_day from categories where id=:c", c=cat)[0][0] == 500.0


def test_finalise_locks_attendance_and_generation_until_unlocked(app, make_org, login, add_employee, raw):
    org = make_org()
    add_employee(org["id"], "L1")
    admin, clerk = login(org["admin"]), login(org["clerk"])
    _att(admin, MONTH, "emp_code,present_days\nL1,20\n")
    admin.post("/payroll/generate", data={"month": MONTH})
    assert clerk.post("/payroll/finalize", data={"month": MONTH}).status_code == 403
    admin.post("/payroll/finalize", data={"month": MONTH})
    assert raw("select status from payroll_runs where org_id=:o and month=:m", o=org["id"], m=MONTH)[0][0] == "final"
    assert b"locked" in _att(admin, MONTH, "emp_code,present_days\nL1,5\n").data
    assert raw("select present_days from attendance where org_id=:o", o=org["id"])[0][0] == 20.0
    assert b"finalised" in admin.post("/payroll/generate", data={"month": MONTH}, follow_redirects=True).data
    att_id = raw("select id from attendance where org_id=:o", o=org["id"])[0][0]
    admin.post(f"/attendance/{att_id}/delete")
    assert raw("select count(*) from attendance where org_id=:o", o=org["id"])[0][0] == 1
    admin.post("/payroll/unlock", data={"month": MONTH, "reason": "x"})            # too short a reason
    assert raw("select status from payroll_runs where org_id=:o", o=org["id"])[0][0] == "final"
    admin.post("/payroll/unlock", data={"month": MONTH, "reason": "Wrong OT hours for L1"})
    assert raw("select status from payroll_runs where org_id=:o", o=org["id"])[0][0] == "draft"
    assert b"Saved attendance" in _att(admin, MONTH, "emp_code,present_days\nL1,25\n").data
    actions = {r[0] for r in raw("select action from audit_log where org_id=:o", o=org["id"])}
    assert {"payroll.finalize", "payroll.unlock"} <= actions


def test_cannot_finalise_stale_payslips(app, make_org, login, add_employee, raw):
    org = make_org()
    add_employee(org["id"], "S1")
    c = login(org["admin"])
    _att(c, MONTH, "emp_code,present_days\nS1,20\n")
    c.post("/payroll/generate", data={"month": MONTH})
    _att(c, MONTH, "emp_code,present_days\nS1,22\n")            # attendance corrected AFTER generating
    c.post("/payroll/finalize", data={"month": MONTH})
    assert raw("select count(*) from payroll_runs where org_id=:o", o=org["id"])[0][0] == 0
    c.post("/payroll/generate", data={"month": MONTH})
    c.post("/payroll/finalize", data={"month": MONTH})
    assert raw("select status from payroll_runs where org_id=:o", o=org["id"])[0][0] == "final"


def test_eps_stops_at_58_and_for_exempt_members(app):
    S = get_settings()
    att = type("A", (), dict(present_days=26, paid_holidays=0, ot_hours=0, advance=0, other_deduction=0))()

    def run(dob, exempt=False):
        return payroll.compute(_fake(dict(esi_applicable=False), dict(dob=dob, eps_exempt=exempt)), att, S, MONTH)
    young = run(date(1990, 1, 1))
    assert young["eps_er"] > 0 and young["pf_er_diff"] == young["pf_ee"] - young["eps_er"]
    for v in (run(date(1960, 1, 1)), run(date(1990, 1, 1), exempt=True)):
        assert v["eps_wages"] == 0 and v["eps_er"] == 0 and v["pf_er_diff"] == v["pf_ee"] == young["pf_ee"]
    assert run(date(1968, 11, 1))["eps_er"] > 0          # turns 58 on 1 Nov 2026 -> still contributes for October
    assert run(date(1968, 10, 1))["eps_er"] == 0         # was already 58 on 1 Oct 2026 -> stopped


def test_esi_stays_covered_for_the_contribution_period(app, make_org, login, add_employee, raw):
    """Covered in April (wages <= 21,000). By September wages are above 21,000 but Apr-Sep is one contribution
    period, so cover continues. October starts a new period: above the ceiling -> not covered."""
    org = make_org()
    add_employee(org["id"], "ESI1", name="Rises", category="Skilled")
    c = login(org["admin"])
    _att(c, "2026-04", "emp_code,present_days\nESI1,26\n")                       # 26 x 600 = 15,600
    c.post("/payroll/generate", data={"month": "2026-04"})
    assert raw("select esi_wages from payslips where org_id=:o and month='2026-04'", o=org["id"])[0][0] == 15600
    cat = raw("select id from categories where org_id=:o and name='Skilled'", o=org["id"])[0][0]
    c.post(f"/categories/{cat}/edit", data=_cat_form(name="Skilled", basic_day="850", da_day="100",
                                                     effective_from="2026-05"))
    _att(c, "2026-09", "emp_code,present_days\nESI1,26\n")                       # 26 x 950 = 24,700
    c.post("/payroll/generate", data={"month": "2026-09"})
    assert raw("select esi_wages from payslips where org_id=:o and month='2026-09'", o=org["id"])[0][0] == 24700
    _att(c, "2026-10", "emp_code,present_days\nESI1,26\n")
    c.post("/payroll/generate", data={"month": "2026-10"})
    assert raw("select esi_wages from payslips where org_id=:o and month='2026-10'", o=org["id"])[0][0] == 0


def test_precheck_flags_problems_before_filing(app, make_org, login, add_employee):
    org = make_org()
    add_employee(org["id"], "P1", name="No Uan Person", esic_no="12345")
    add_employee(org["id"], "P2", name="Ok Person", uan="100200300400", esic_no="1234567890")
    c = login(org["admin"])
    _att(c, MONTH, "emp_code,present_days\nP1,20\nP2,20\n")
    c.post("/payroll/generate", data={"month": MONTH})
    with app.app_context():
        set_org(org["id"])
        status, issues = compliance.precheck(MONTH)
        set_org(None)
    msgs = " | ".join(i["msg"] for i in issues)
    assert status == "fail"                                     # P1's ESIC number is malformed
    assert "no UAN" in msgs and "must be 10 or 17 digits" in msgs and "not finalised" in msgs
    assert c.get(f"/compliance?month={MONTH}").status_code == 200


def test_min_wage_warning(app, make_org, login, add_employee, raw):
    org = make_org()
    add_employee(org["id"], "M1")
    c = login(org["admin"])
    cat = raw("select id from categories where org_id=:o and name='Unskilled'", o=org["id"])[0][0]
    c.post(f"/categories/{cat}/edit", data=_cat_form(min_wage_day="600", effective_from="2026-01"))
    _att(c, MONTH, "emp_code,present_days\nM1,10\n")
    c.post("/payroll/generate", data={"month": MONTH})
    with app.app_context():
        set_org(org["id"])
        warnings = payroll.payroll_warnings(MONTH)
        set_org(None)
    assert any("below the notified minimum" in x for x in warnings)
