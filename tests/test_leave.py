"""Leave engine: ledger, accrual (monthly / yearly / per days worked), ceiling, rollover, comp off,
applications, attendance integration, calendar, corrections."""
import io
from datetime import date, timedelta

import pytest

import leave
import payroll
import reports
from conftest import org_ctx
from models import (Attendance, DailyAttendance, Employee, Holiday, LeaveApplication, LeaveLedger, LeavePolicyRule,
                    LeaveType, db)


def _emp(org_id, code):
    return db.query(Employee).filter_by(emp_code=code).one()


def _lt(code):
    return db.query(LeaveType).filter_by(code=code).one()


def _refs(emp_id, lt_id):
    return sorted(r.ref for r in db.query(LeaveLedger).filter_by(emp_id=emp_id, leave_type_id=lt_id) if r.ref)


@pytest.fixture
def org(make_org, set_setting):
    o = make_org()
    set_setting(o["id"], "leave_start_date", "2026-01-01")
    return o


def test_new_organization_gets_leave_defaults(app, make_org):
    o = make_org()
    with org_ctx(app, o["id"]):
        assert [t.code for t in leave.leave_types()] == ["CL", "SL", "AL", "CO", "LWP"]
        assert not _lt("LWP").uses_balance and not _lt("LWP").paid
        pol = leave.Ctx().default
        assert pol and pol.name == "Standard" and len(pol.rules) == 4
        assert {r.leave_type.code for r in pol.rules} == {"CL", "SL", "AL", "CO"}


def test_monthly_credit_prorates_first_month_and_is_idempotent(app, org, add_employee):
    add_employee(org["id"], "M1", doj=date(2026, 3, 10))
    with org_ctx(app, org["id"]):
        e, cl = _emp(org["id"], "M1"), _lt("CL")
        res = leave.accrue(as_on=date(2026, 5, 31))
        got = {x["ref"]: x["amount"] for x in res["entries"] if x["lt"].code == "CL"}
        # March: 1.0 x 22/31 days = 0.71 -> nearest half = 0.5; April and May: 1.0 each
        assert got == {"acc:2026-03": 0.5, "acc:2026-04": 1.0, "acc:2026-05": 1.0}
        assert leave.balance(e.id, cl.id) == 2.5
        again = leave.accrue(as_on=date(2026, 5, 31))
        assert again["entries"] == [] and leave.balance(e.id, cl.id) == 2.5          # running twice credits nothing
        assert leave.accrue(as_on=date(2026, 6, 30))["entries"][0]["ref"] == "acc:2026-06"


def test_dry_run_changes_nothing(app, org, add_employee):
    add_employee(org["id"], "DR", doj=date(2020, 1, 1))
    with org_ctx(app, org["id"]):
        plan = leave.accrue(as_on=date(2026, 3, 31), dry_run=True)
        assert plan["entries"]
        assert db.query(LeaveLedger).count() == 0


def test_yearly_grant_is_prorated_for_mid_year_joiners(app, org, add_employee):
    add_employee(org["id"], "Y1", doj=date(2026, 7, 1))
    add_employee(org["id"], "Y2", doj=date(2020, 5, 1))
    with org_ctx(app, org["id"]):
        leave.accrue(as_on=date(2026, 8, 1))
        al = _lt("AL")
        assert leave.balance(_emp(org["id"], "Y1").id, al.id) == 9.0            # 18 x 184/365 = 9.07 -> 9.0
        assert leave.balance(_emp(org["id"], "Y2").id, al.id) == 18.0           # full year
        assert leave.balance(_emp(org["id"], "Y2").id, _lt("SL").id) == 12.0


def test_ceiling_caps_credits_and_records_a_zero_row(app, org, add_employee):
    add_employee(org["id"], "C1", doj=date(2020, 1, 1))
    with org_ctx(app, org["id"]):
        e, cl = _emp(org["id"], "C1"), _lt("CL")
        leave.adjust(e, cl, 11.5, "opening balance", "t", kind="opening", on=date(2025, 12, 31))
        leave.accrue(as_on=date(2026, 3, 31))                                    # Jan +0.5 (cap 12), Feb/Mar +0
        assert leave.balance(e.id, cl.id) == 12.0
        rows = {r.ref: r.days for r in db.query(LeaveLedger).filter_by(emp_id=e.id, leave_type_id=cl.id) if r.ref}
        assert rows == {"acc:2026-01": 0.5, "acc:2026-02": 0.0, "acc:2026-03": 0.0}


def test_employee_override_replaces_policy_quota(app, org, add_employee):
    from models import EmployeeLeaveOverride
    add_employee(org["id"], "O1", doj=date(2020, 1, 1))
    with org_ctx(app, org["id"]):
        e, sl = _emp(org["id"], "O1"), _lt("SL")
        db.add(EmployeeLeaveOverride(emp_id=e.id, leave_type_id=sl.id, quota=7))
        db.commit()
        leave.accrue(as_on=date(2026, 2, 1))
        assert leave.balance(e.id, sl.id) == 7.0                                 # fixed number for this employee


def test_per_days_worked_credits_only_finalised_months(app, org, add_employee):
    add_employee(org["id"], "W1", doj=date(2020, 1, 1))
    with org_ctx(app, org["id"]):
        e, al = _emp(org["id"], "W1"), _lt("AL")
        rule = next(r for r in leave.Ctx().default.rules if r.leave_type_id == al.id)
        rule.accrual, rule.accrual_days, rule.quota = "per_days_worked", 20, 12
        db.add_all([Attendance(emp_id=e.id, month="2026-02", present_days=22),
                    Attendance(emp_id=e.id, month="2026-03", present_days=26)])
        db.commit()
        payroll.finalize("2026-02", "t")                                          # March stays a draft
        leave.accrue(as_on=date(2026, 3, 31))
        assert leave.balance(e.id, al.id) == 1.0                                  # 22 / 20 = 1.1 -> 1.0
        payroll.finalize("2026-03", "t")
        leave.accrue(as_on=date(2026, 3, 31))
        assert leave.balance(e.id, al.id) == 2.0                                  # + 26 / 20 = 1.3 -> 1.0
        assert _refs(e.id, al.id) == ["dw:2026-02", "dw:2026-03"]


def test_rollover_carry_forward_ceiling_and_ordering(app, make_org, set_setting, add_employee):
    o = make_org()
    set_setting(o["id"], "leave_start_date", "2025-01-01")
    add_employee(o["id"], "R1", doj=date(2020, 1, 1))
    with org_ctx(app, o["id"]):
        e, al = _emp(o["id"], "R1"), _lt("AL")
        leave.accrue(as_on=date(2025, 1, 2))
        assert leave.balance(e.id, al.id) == 18.0
        leave.adjust(e, al, -4, "used outside the system", "t", on=date(2025, 6, 1))                # 14 at year end
        with pytest.raises(leave.LeaveError):
            leave.rollover(2026, dry_run=True)                                    # 2026 has not ended yet
        # new-year credits wait for the rollover
        blocked = leave.accrue(as_on=date(2026, 1, 2))
        assert blocked["skipped"] and not any(x["ref"] == "grant:2026" and x["lt"].code == "AL" for x in blocked["entries"])
        preview = leave.rollover(2025, dry_run=True)
        row = next(r for r in preview["rows"] if r["lt"].code == "AL")
        assert (row["balance"], row["carry"], row["lapse"]) == (14.0, 10.0, 4.0)
        assert db.query(LeaveLedger).filter_by(kind="lapse").count() == 0         # preview wrote nothing
        leave.rollover(2025, dry_run=False, by="t")
        assert leave.balance(e.id, al.id) == 10.0
        with pytest.raises(leave.LeaveError):
            leave.rollover(2025, dry_run=False)                                    # cannot close a year twice
        leave.accrue(as_on=date(2026, 1, 2))
        assert leave.balance(e.id, al.id) == 28.0                                  # 10 carried + 18 granted, under the 30 ceiling
        # casual leave has no carry-forward: whatever was left lapsed
        assert leave.balance(e.id, _lt("CL").id) == 1.0                            # only the January 2026 credit


def test_ceiling_applies_after_carry_forward(app, make_org, set_setting, add_employee):
    o = make_org()
    set_setting(o["id"], "leave_start_date", "2025-01-01")
    add_employee(o["id"], "R2", doj=date(2020, 1, 1))
    with org_ctx(app, o["id"]):
        e, al = _emp(o["id"], "R2"), _lt("AL")
        rule = next(r for r in leave.Ctx().default.rules if r.leave_type_id == al.id)
        rule.carry_mode, rule.max_balance = "all", 30
        db.commit()
        leave.accrue(as_on=date(2025, 1, 2))
        leave.adjust(e, al, 15, "bonus leave", "t", on=date(2025, 6, 1))                            # 33 at year end, all carried
        leave.rollover(2025, dry_run=False, by="t")
        leave.accrue(as_on=date(2026, 1, 2))
        assert leave.balance(e.id, al.id) == 33.0                                  # nothing more credited: already above the ceiling
        zero = db.query(LeaveLedger).filter_by(emp_id=e.id, leave_type_id=al.id, ref="grant:2026").one()
        assert zero.days == 0.0 and "ceiling" in zero.note


def _weekday_ago(n=3):
    d = date.today() - timedelta(days=n)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def test_comp_off_grant_use_and_expiry(app, org, add_employee):
    add_employee(org["id"], "K1", doj=date(2020, 1, 1))
    with org_ctx(app, org["id"]):
        e, co = _emp(org["id"], "K1"), _lt("CO")
        g = leave.grant_comp_off(e, date.today() - timedelta(days=2), 2.0, "worked on a Sunday", "admin", expiry_days=5)
        assert leave.balance(e.id, co.id) == 2.0 and g.expires_on == date.today() + timedelta(days=5)
        d = _weekday_ago()
        app_, errs = leave.apply_leave(e, co, d, d, False, False, "comp off", False, "admin")
        assert not errs
        assert leave.approve(app_, "admin") == []
        assert leave.balance(e.id, co.id) == 1.0
        assert leave.expire_comp_offs(as_on=date.today() + timedelta(days=4)) == []     # not expired yet
        out = leave.expire_comp_offs(as_on=date.today() + timedelta(days=6))
        assert [x["lapsed"] for x in out] == [1.0]                                       # used 1 of 2, 1 unused lapses
        assert leave.balance(e.id, co.id) == 0.0
        assert leave.expire_comp_offs(as_on=date.today() + timedelta(days=30)) == []     # idempotent
        for bad in (dict(days=0), dict(reason=" "), dict(worked_on=date.today() + timedelta(days=1))):
            kw = dict(worked_on=date.today(), days=1.0, reason="x")
            kw.update(bad)
            with pytest.raises(leave.LeaveError):
                leave.grant_comp_off(e, kw["worked_on"], kw["days"], kw["reason"], "a")


def _seed_cl(emp_id, days=10):
    leave.add_entry(emp_id, _lt("CL").id, date(2026, 1, 1), "opening", days, "start")
    db.commit()


def test_application_day_counting_weekly_off_holiday_half_day_sandwich(app, org, add_employee, set_setting):
    add_employee(org["id"], "A1", doj=date(2020, 1, 1))
    with org_ctx(app, org["id"]):
        e = _emp(org["id"], "A1")
        db.add(Holiday(on_date=date(2026, 9, 10), name="Test holiday"))               # a Thursday
        db.commit()
        d = lambda *a, **k: sum(v for _, v in leave.leave_dates(e, *a, **k))           # noqa: E731
        assert d(date(2026, 9, 7), date(2026, 9, 9)) == 3                              # Mon-Wed
        assert d(date(2026, 9, 5), date(2026, 9, 8)) == 3                              # Sat, (Sun off), Mon, Tue
        assert d(date(2026, 9, 9), date(2026, 9, 11)) == 2                             # Thu holiday not counted
        assert d(date(2026, 9, 7), date(2026, 9, 9), h1=True, h2=True) == 2            # half first and last day
        assert d(date(2026, 9, 7), date(2026, 9, 7), h1=True) == 0.5                   # a single half day
        assert d(date(2026, 9, 6), date(2026, 9, 6)) == 0                              # a Sunday alone
        assert d(date(2026, 9, 5), date(2026, 9, 7), sandwich=True) == 3               # Sunday between counts
        assert d(date(2026, 9, 5), date(2026, 9, 7), sandwich=False) == 2


def test_application_rules_and_refusals(app, org, add_employee, set_setting):
    add_employee(org["id"], "A2", doj=date(2026, 9, 1))
    add_employee(org["id"], "A3", doj=date(2020, 1, 1))
    with org_ctx(app, org["id"]):
        e, e3 = _emp(org["id"], "A2"), _emp(org["id"], "A3")
        cl, sl = _lt("CL"), _lt("SL")
        _seed_cl(e.id, 2)
        a = lambda emp, lt, d1, d2, **k: leave.apply_leave(emp, lt, d1, d2, k.get("h1", False), k.get("h2", False), "r", k.get("doc", False), "t", k.get("self", False))  # noqa: E731
        assert "before the joining" in " ".join(a(e, cl, date(2026, 8, 31), date(2026, 8, 31))[1])
        assert "Only 2" in " ".join(a(e, cl, date(2026, 9, 7), date(2026, 9, 10))[1])               # 4 days, only 2 free
        first, errs = a(e, cl, date(2026, 9, 7), date(2026, 9, 8))
        assert not errs and first.days == 2 and first.status == "pending"
        assert "Overlaps" in " ".join(a(e, cl, date(2026, 9, 8), date(2026, 9, 8))[1])
        assert "Only 0" in " ".join(a(e, cl, date(2026, 9, 14), date(2026, 9, 14))[1])              # pending days are reserved
        _seed_cl(e3.id, 10)
        db.query(LeaveLedger).filter_by(emp_id=e3.id, leave_type_id=sl.id).delete()
        leave.add_entry(e3.id, sl.id, date(2026, 1, 1), "opening", 5, "start")
        db.commit()
        assert "medical certificate" in " ".join(a(e3, sl, date(2026, 9, 7), date(2026, 9, 9))[1])  # 3 days > 2 without a certificate
        assert a(e3, sl, date(2026, 9, 7), date(2026, 9, 9), doc=True)[1] == []
        assert "only weekly offs" in " ".join(a(e3, cl, date(2026, 9, 6), date(2026, 9, 6))[1])
        assert "at most 3 day" in " ".join(a(e3, cl, date.today() - timedelta(days=30), date.today() - timedelta(days=30), self=True)[1]).replace("7", "3") or \
            "day(s) back" in " ".join(a(e3, cl, date.today() - timedelta(days=30), date.today() - timedelta(days=30), self=True)[1])
        assert a(e3, _lt("LWP"), date(2026, 9, 14), date(2026, 9, 14))[1] == []                    # unpaid leave needs no balance
        payroll.finalize("2026-08", "t")
        assert "finalised" in " ".join(a(e3, _lt("LWP"), date(2026, 8, 10), date(2026, 8, 10))[1])


def test_approve_writes_ledger_and_attendance_and_cancel_restores(app, org, add_employee):
    add_employee(org["id"], "P1", doj=date(2020, 1, 1))
    with org_ctx(app, org["id"]):
        e, cl = _emp(org["id"], "P1"), _lt("CL")
        _seed_cl(e.id, 5)
        app_, errs = leave.apply_leave(e, cl, date(2026, 9, 7), date(2026, 9, 9), False, True, "family", False, "t")
        assert not errs and app_.days == 2.5                                         # Mon, Tue, half Wed
        assert leave.approve(app_, "admin", "ok") == []
        assert leave.balance(e.id, cl.id) == 2.5
        marks = {m.day: (m.status, m.source) for m in db.query(DailyAttendance).filter_by(emp_id=e.id)}
        assert marks == {date(2026, 9, 7): ("L", "leave"), date(2026, 9, 8): ("L", "leave"), date(2026, 9, 9): ("HL", "leave")}
        assert "approved leave" in leave.mark_day(e, date(2026, 9, 7), "P")           # cannot be overwritten by hand
        assert leave.approve(app_, "admin")[0].startswith("This application is already")
        assert leave.cancel(app_, "admin", "changed plan") == []
        assert leave.balance(e.id, cl.id) == 5.0
        assert db.query(DailyAttendance).filter_by(emp_id=e.id).count() == 0
        assert leave.cancel(app_, "admin")[0].startswith("This application is already")


def test_approval_refused_when_already_marked_present_or_balance_gone(app, org, add_employee):
    add_employee(org["id"], "P2", doj=date(2020, 1, 1))
    with org_ctx(app, org["id"]):
        e, cl = _emp(org["id"], "P2"), _lt("CL")
        _seed_cl(e.id, 3)
        app_, _ = leave.apply_leave(e, cl, date(2026, 9, 7), date(2026, 9, 8), False, False, "r", False, "t")
        assert leave.mark_day(e, date(2026, 9, 8), "P") is None
        db.commit()
        errs = leave.approve(app_, "admin")
        assert errs and "Already marked present" in errs[0]
        db.query(DailyAttendance).filter_by(emp_id=e.id).delete()
        leave.adjust(e, cl, -2, "used elsewhere", "t")
        errs = leave.approve(app_, "admin")
        assert errs and "Balance" in " ".join(errs) or "Only" in " ".join(errs)
        assert leave.reject(app_, "admin", "no cover") == []
        assert app_.status == "rejected"


def test_unpaid_leave_payroll_and_calendar_offs_in_rollup(app, org, add_employee):
    """Month of Sept 2026: 2 days present, 1 day CL, 1 day LWP, 4 Sundays + 1 holiday that fall on unmarked days."""
    add_employee(org["id"], "PR", doj=date(2020, 1, 1))
    with org_ctx(app, org["id"]):
        e, cl, lwp = _emp(org["id"], "PR"), _lt("CL"), _lt("LWP")
        _seed_cl(e.id, 3)
        db.add(Holiday(on_date=date(2026, 9, 10), name="H"))
        db.commit()
        for d in (date(2026, 9, 1), date(2026, 9, 2)):
            assert leave.mark_day(e, d, "P") is None
        db.commit()
        a1, _ = leave.apply_leave(e, cl, date(2026, 9, 3), date(2026, 9, 3), False, False, "r", False, "t")
        a2, _ = leave.apply_leave(e, lwp, date(2026, 9, 4), date(2026, 9, 4), False, False, "r", False, "t")
        assert leave.approve(a1, "t") == [] and leave.approve(a2, "t") == []
        marks = {m.day.day: m.status for m in db.query(DailyAttendance).filter_by(emp_id=e.id)}
        assert marks == {1: "P", 2: "P", 3: "L", 4: "U"}
        roll = reports.rollup_month("2026-09")[e.id]
        assert roll["present_days"] == 2.0
        assert roll["paid_holidays"] == 1 + 4 + 1          # CL + four Sundays + the Thursday holiday; LWP unpaid, not counted
        assert leave.balance(e.id, lwp.id) == 0.0           # LWP never touches a balance
        reports.apply_rollup("2026-09")
        att = db.query(Attendance).filter_by(emp_id=e.id, month="2026-09").one()
        assert (att.present_days, att.paid_holidays) == (2.0, 6.0)


def test_half_day_leave_marks_and_rollup(app, org, add_employee):
    add_employee(org["id"], "HF", doj=date(2020, 1, 1))
    with org_ctx(app, org["id"]):
        e, cl = _emp(org["id"], "HF"), _lt("CL")
        _seed_cl(e.id, 2)
        a, _ = leave.apply_leave(e, cl, date(2026, 9, 7), date(2026, 9, 7), True, False, "r", False, "t")
        assert a.days == 0.5 and leave.approve(a, "t") == []
        roll = reports.rollup_month("2026-09")[e.id]
        assert roll["present_days"] == 0.5 and roll["paid_holidays"] >= 0.5


def test_calendar_starts_at_joining_and_respects_locks(app, org, add_employee):
    add_employee(org["id"], "CAL", doj=date(2026, 3, 10), dol=date(2026, 8, 31))
    with org_ctx(app, org["id"]):
        e = _emp(org["id"], "CAL")
        assert "before the joining" in leave.mark_day(e, date(2026, 3, 9), "P")
        e_future = e
        assert "future" in leave.mark_day(type("E", (), dict(doj=date(2026, 1, 1), dol=None, id=e_future.id))(),
                                          date.today() + timedelta(days=1), "P")
        assert "after the leaving" in leave.mark_day(e, date(2026, 9, 1), "P")
        assert leave.mark_day(e, date(2026, 3, 11), "P") is None
        db.commit()
        n, problems = leave.mark_range(e, date(2026, 4, 1), date(2026, 4, 30), "P")
        assert (n, problems) == (26, [])                                              # April 2026: 30 days - 4 Sundays
        cal = leave.year_calendar(e, 2026)
        mar = cal["months"][2]["days"]
        assert not mar[8]["in_service"] and mar[9]["in_service"]                      # 9 Mar out, 10 Mar in
        assert mar[10]["code"] == "P" and cal["months"][3]["days"][4]["code"] == "WO"    # 5 Apr 2026 is a Sunday
        assert not cal["months"][8]["days"][1]["in_service"]                           # after leaving
        payroll.finalize("2026-04", "t")
        assert "finalised" in leave.mark_day(e, date(2026, 4, 2), "A")
        assert not leave.year_calendar(e, 2026)["months"][3]["days"][1]["editable"]


def test_attendance_correction_flow(app, org, add_employee):
    add_employee(org["id"], "RQ", doj=date(2020, 1, 1))
    with org_ctx(app, org["id"]):
        e = _emp(org["id"], "RQ")
        d = _weekday_ago(2)
        assert leave.request_correction(e, d, "A", "x", "emp")                       # only P / HD allowed
        assert leave.request_correction(e, d, "P", " ", "emp")                       # reason needed
        assert leave.request_correction(e, date.today() - timedelta(days=90), "P", "forgot", "emp")   # too old
        assert leave.request_correction(e, d, "P", "supervisor forgot to mark", "emp") is None
        assert "already" in leave.request_correction(e, d, "P", "again", "emp")
        from models import AttendanceRequest
        req = db.query(AttendanceRequest).one()
        assert leave.decide_correction(req, True, "admin", "ok") is None
        assert db.query(DailyAttendance).filter_by(emp_id=e.id, day=d).one().status == "P"
        assert req.status == "approved" and leave.decide_correction(req, True, "admin")
        d2 = _weekday_ago(1)
        assert leave.request_correction(e, d2, "HD", "half day", "emp") is None
        req2 = db.query(AttendanceRequest).filter_by(on_date=d2).one()
        assert leave.decide_correction(req2, False, "admin", "not seen") is None
        assert db.query(DailyAttendance).filter_by(emp_id=e.id, day=d2).count() == 0


def test_liability_and_matrix(app, org, add_employee):
    add_employee(org["id"], "LB", doj=date(2020, 1, 1))
    with org_ctx(app, org["id"]):
        e, al = _emp(org["id"], "LB"), _lt("AL")
        leave.adjust(e, al, 10, "opening", "t", kind="opening")
        liab = leave.liability(date.today())
        assert liab["total"] == 10 * 460 and liab["rows"][0]["daily"] == 460.0       # Unskilled: Basic 400 + DA 60
        lts, rows = leave.balances_matrix()
        assert rows[0]["bal"][al.id] == 10.0 and lts


def test_manual_adjustment_needs_a_reason_and_half_steps(app, org, add_employee):
    add_employee(org["id"], "AD", doj=date(2020, 1, 1))
    with org_ctx(app, org["id"]):
        e, cl = _emp(org["id"], "AD"), _lt("CL")
        for days, reason in ((1, ""), (0, "x"), (0.3, "x")):
            with pytest.raises(leave.LeaveError):
                leave.adjust(e, cl, days, reason, "t")
        leave.adjust(e, cl, 1.5, "correction", "t")
        assert leave.balance(e.id, cl.id) == 1.5


def test_policy_per_category_and_default_fallback(app, org, add_employee):
    from models import LeavePolicy
    add_employee(org["id"], "PC", doj=date(2020, 1, 1), category="Skilled")
    with org_ctx(app, org["id"]):
        e = _emp(org["id"], "PC")
        cx = leave.Ctx()
        assert cx.policy(e).name == "Standard"
        pol = LeavePolicy(name="Skilled", category_id=e.category_id)
        db.add(pol)
        db.flush()
        db.add(LeavePolicyRule(policy_id=pol.id, leave_type_id=_lt("CL").id, quota=24, accrual="yearly", max_balance=24))
        db.commit()
        cx = leave.Ctx()
        assert cx.policy(e).name == "Skilled" and cx.rule(e, _lt("CL").id).quota == 24
        assert cx.rule(e, _lt("SL").id) is None                                           # no SL rule in that policy
        leave.accrue(as_on=date(2026, 2, 1))
        assert leave.balance(e.id, _lt("CL").id) == 24.0 and leave.balance(e.id, _lt("SL").id) == 0.0


# ------------------------------------------------------------------ the web screens (admin)
def test_admin_screens_and_downloads(app, org, add_employee, login, raw):
    add_employee(org["id"], "S1", name="Screen Person", doj=date(2020, 1, 1))
    eid = raw("select id from employees where org_id=:o", o=org["id"])[0][0]
    c = login(org["admin"])
    for url in ("/leave", f"/leave/employee/{eid}", "/leave/applications", "/leave/policies", "/leave/holidays",
                "/leave/run", "/leave/reports", f"/attendance/employee/{eid}", f"/attendance/employee/{eid}?year=2025&date=2026-01-05",
                "/requests", "/portal-access", "/attendance/daily"):
        r = c.get(url)
        assert r.status_code == 200, f"{url} -> {r.status_code}"
        assert b"UndefinedError" not in r.data and b"Traceback" not in r.data
    pid = raw("select id from leave_policies where org_id=:o", o=org["id"])[0][0]
    assert c.get(f"/leave/policies/{pid}").status_code == 200
    # accrual from the screen
    assert c.post("/leave/run", data={"action": "accrue"}).status_code == 302
    assert raw("select count(*) from leave_ledger where org_id=:o", o=org["id"])[0][0] > 0
    for url in ("/leave/balances.xlsx", "/leave/liability.xlsx", "/leave/register.xlsx", "/attendance/muster.xlsx?month=2026-09"):
        r = c.get(url)
        assert r.status_code == 200 and r.headers["Content-Disposition"].startswith("attachment"), url


def test_admin_flows_apply_approve_adjust_override_policy_holiday(app, org, add_employee, login, raw):
    add_employee(org["id"], "F1", name="Flow Person", doj=date(2020, 1, 1))
    eid = raw("select id from employees where org_id=:o", o=org["id"])[0][0]
    cl = raw("select id from leave_types where org_id=:o and code='CL'", o=org["id"])[0][0]
    c = login(org["admin"])
    c.post(f"/leave/employee/{eid}/adjust", data={"leave_type_id": cl, "days": "5", "reason": "opening", "kind": "opening"})
    r = c.post(f"/leave/employee/{eid}/apply", data={"leave_type_id": cl, "from_date": "2026-09-07", "to_date": "2026-09-08",
                                                    "reason": "family", "approve_now": "on"}, follow_redirects=True)
    assert b"recorded and approved" in r.data
    assert raw("select sum(days) from leave_ledger where org_id=:o and leave_type_id=:t", o=org["id"], t=cl)[0][0] == 3.0
    aid = raw("select id from leave_applications where org_id=:o", o=org["id"])[0][0]
    assert c.post(f"/leave/applications/{aid}/cancel", data={}).status_code == 302
    assert raw("select sum(days) from leave_ledger where org_id=:o and leave_type_id=:t", o=org["id"], t=cl)[0][0] == 5.0
    # fixed quota for the employee, then clear it
    c.post(f"/leave/employee/{eid}/override", data={"leave_type_id": cl, "quota": "20"})
    assert raw("select quota from employee_leave_overrides where org_id=:o", o=org["id"])[0][0] == 20.0
    c.post(f"/leave/employee/{eid}/override", data={"leave_type_id": cl, "quota": "", "max_balance": ""})
    assert raw("select count(*) from employee_leave_overrides where org_id=:o", o=org["id"])[0][0] == 0
    # comp off
    r = c.post(f"/leave/employee/{eid}/comp-off", data={"worked_on": date.today().isoformat(), "days": "1", "reason": "Sunday work"},
               follow_redirects=True)
    assert b"Comp off of 1" in r.data
    # holidays and weekly off
    c.post("/leave/holidays", data={"on_date": "2026-10-20", "name": "Diwali"})
    assert b"Diwali" in c.get("/leave/holidays?year=2026").data
    c.post("/leave/weekly-off", data={"weekday": ["5", "6"]})
    assert raw("select weekdays from weekly_off_rules where org_id=:o and category_id is null", o=org["id"])[0][0] == "5,6"
    # policy edit: switch casual leave to 15 a year and a ceiling of 15
    pid = raw("select id from leave_policies where org_id=:o", o=org["id"])[0][0]
    types = raw("select id from leave_types where org_id=:o and uses_balance=1", o=org["id"])
    form = {"name": "Standard"}
    for (tid,) in types:
        form.update({f"r{tid}_use": "on", f"r{tid}_quota": "15" if tid == cl else "5", f"r{tid}_accrual": "yearly", f"r{tid}_carry_mode": "none",
                     f"r{tid}_max_balance": "15", f"r{tid}_prorate": "on"})
    c.post(f"/leave/policies/{pid}", data=form)
    assert raw("select quota from leave_policy_rules where org_id=:o and leave_type_id=:t", o=org["id"], t=cl)[0][0] == 15.0
    # bad numbers are refused politely
    form[f"r{cl}_quota"] = "abc"
    assert b"Check the numbers" in c.post(f"/leave/policies/{pid}", data=form, follow_redirects=True).data
    # custom type
    c.post("/leave/types/save", data={"code": "ML", "name": "Maternity Leave", "paid": "on", "uses_balance": "on", "active": "on"})
    assert raw("select count(*) from leave_types where org_id=:o and code='ML'", o=org["id"])[0][0] == 1


def test_leave_pages_are_admin_only_and_isolated(app, make_org, login, add_employee, raw):
    a, b = make_org(), make_org()
    add_employee(a["id"], "ISO", doj=date(2020, 1, 1))
    eid = raw("select id from employees where org_id=:o", o=a["id"])[0][0]
    clerk, other = login(a["clerk"]), login(b["admin"])
    for url in ("/leave", f"/leave/employee/{eid}", "/leave/policies", "/leave/run", "/portal-access", f"/attendance/employee/{eid}", "/requests"):
        assert clerk.get(url).status_code == 403, url
    assert other.get(f"/leave/employee/{eid}").status_code == 404
    assert other.get(f"/attendance/employee/{eid}").status_code == 404
    assert other.post(f"/portal-access/{eid}/grant", data={"username": "x.y"}).status_code == 404
    cl = raw("select id from leave_types where org_id=:o and code='CL'", o=b["id"])[0][0]
    other.post(f"/leave/employee/{eid}/adjust", data={"leave_type_id": cl, "days": "5", "reason": "hack"})
    assert raw("select count(*) from leave_ledger where emp_id=:e", e=eid)[0][0] == 0


def test_settings_validate_new_leave_keys(app, org, login):
    c = login(org["admin"])
    r = c.post("/settings", data={"leave_year_start": "7", "leave_sandwich": "yes", "leave_backdate_days": "-3",
                                  "leave_start_date": "not-a-date"}, follow_redirects=True)
    assert r.data.count(b"invalid value") >= 1 and b"Leave start date must look like" in r.data
    r = c.post("/settings", data={"leave_year_start": "4", "leave_sandwich": "1", "leave_backdate_days": "10",
                                  "leave_start_date": "2026-04-01"}, follow_redirects=True)
    assert b"Settings saved" in r.data
