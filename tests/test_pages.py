"""Every page renders (no template errors) for each role, and the new features behave end to end."""
import io

import pytest

from conftest import PW

MONTH = "2026-10"


@pytest.fixture
def seeded(app, make_org, login, add_employee, raw):
    org = make_org()
    add_employee(org["id"], "E1", name="Page Person", uan="100200300400", esic_no="1234567890",
                 aadhaar="111122223333", doj=__import__("datetime").date(2019, 3, 1))
    admin = login(org["admin"])
    admin.post("/attendance", data={"month": MONTH, "file": (io.BytesIO(b"emp_code,present_days,ot_hours\nE1,24,4\n"), "a.csv")},
               content_type="multipart/form-data")
    admin.post("/payroll/generate", data={"month": MONTH})
    org["eid"] = raw("select id from employees where org_id=:o", o=org["id"])[0][0]
    org["catid"] = raw("select id from categories where org_id=:o limit 1", o=org["id"])[0][0]
    return org


ADMIN_PAGES = ["/", "/employees", "/employees?q=Page&status=All&sort=name&dir=desc&per=50", "/employees/new",
               "/employees/upload", "/categories", "/categories/new", "/attendance", f"/attendance?month={MONTH}",
               "/attendance/daily", "/attendance/daily/month", f"/payroll?month={MONTH}", f"/compliance?month={MONTH}",
               "/compliance?month=2026-01", "/reports", "/reports/bonus", "/reports/gratuity", "/settings", "/users",
               "/users/new", "/audit", "/password"]


@pytest.mark.parametrize("url", ADMIN_PAGES)
def test_admin_pages_render(seeded, login, url):
    r = login(seeded["admin"]).get(url)
    assert r.status_code == 200, f"{url} -> {r.status_code}"
    assert b"Traceback" not in r.data and b"UndefinedError" not in r.data


def test_detail_edit_and_category_pages(seeded, login):
    c = login(seeded["admin"])
    for url in (f"/employees/{seeded['eid']}", f"/employees/{seeded['eid']}/edit", f"/employees/{seeded['eid']}?reveal=1",
                f"/categories/{seeded['catid']}/edit"):
        assert c.get(url).status_code == 200, url
    assert b"111122223333" in c.get(f"/employees/{seeded['eid']}?reveal=1").data
    assert b"111122223333" not in c.get(f"/employees/{seeded['eid']}").data


def test_clerk_pages_render_without_admin_links(seeded, login):
    c = login(seeded["clerk"])
    for url in ("/", "/employees", "/attendance", "/attendance/daily", f"/payroll?month={MONTH}", f"/compliance?month={MONTH}"):
        r = c.get(url)
        assert r.status_code == 200, url
        assert b"/audit" not in r.data and b"/settings" not in r.data
    assert c.get("/reports").status_code == 403


def test_platform_pages_render(seeded, login):
    c = login("admin", org_id=seeded["id"])
    for url in ("/orgs", "/orgs/new", f"/orgs/{seeded['id']}/edit", "/", "/audit?scope=all"):
        assert c.get(url).status_code == 200, url


def test_error_pages_are_friendly(seeded, login, app):
    c = login(seeded["admin"])
    r = c.get("/employees/999999")
    assert r.status_code == 404 and b"Not found" in r.data and b"Traceback" not in r.data
    assert c.get("/payroll?month=banana").status_code == 400
    assert app.test_client().get("/nope").status_code == 302          # anonymous -> login first


def test_inline_field_errors_on_employee_form(seeded, login):
    c = login(seeded["admin"])
    r = c.post("/employees/new", data={"emp_code": "", "name": "", "aadhaar": "12", "uan": "abc",
                                       "category_id": seeded["catid"], "status": "Active"})
    assert r.status_code == 200
    for needle in (b"Employee code is required", b"Name is required", b"AADHAAR must be", b"UAN must be"):
        assert needle in r.data
    assert b'aria-invalid="true"' in r.data


def test_employee_list_paginates_and_sorts(app, make_org, login, add_employee):
    org = make_org()
    for i in range(30):
        add_employee(org["id"], f"Z{i:02d}", name=f"Worker {i:02d}")
    c = login(org["admin"])
    p1, p2 = c.get("/employees?per=25").data, c.get("/employees?per=25&page=2").data
    assert b"Worker 00" in p1 and b"Worker 29" not in p1 and b"Worker 29" in p2
    desc = c.get("/employees?sort=name&dir=desc&per=25").data
    assert desc.index(b"Worker 29") < desc.index(b"Worker 10")


def test_daily_attendance_rollup_end_to_end(app, make_org, login, add_employee, raw):
    from datetime import date
    org = make_org()
    add_employee(org["id"], "D1", name="Daily Dee")
    eid = raw("select id from employees where org_id=:o", o=org["id"])[0][0]
    c = login(org["admin"])
    today = date.today()
    # mark three days this month: Present, Half day (+2 OT), Weekly off
    for d, st, ot in ((1, "P", ""), (2, "HD", "2"), (3, "WO", "")):
        day = date(today.year, today.month, d)
        if day > today:
            continue
        c.post(f"/attendance/daily?date={day.isoformat()}", data={f"s_{eid}": st, f"o_{eid}": ot})
    month = today.strftime("%Y-%m")
    marked = raw("select count(*) from daily_attendance where org_id=:o", o=org["id"])[0][0]
    assert marked >= 1
    assert c.get(f"/attendance/daily/month?month={month}").status_code == 200
    r = c.post("/attendance/daily/apply", data={"month": month}, follow_redirects=True)
    assert b"updated from daily marks" in r.data
    present, paid_off, ot = raw("select present_days, paid_holidays, ot_hours from attendance where org_id=:o", o=org["id"])[0]
    exp_present = sum({1: 1.0, 2: 0.5}.get(d, 0) for d in (1, 2, 3) if date(today.year, today.month, d) <= today)
    assert present == exp_present
    # validation
    assert c.post(f"/attendance/daily?date={today.isoformat()}", data={f"s_{eid}": "ZZ"}, follow_redirects=True).status_code == 200
    assert raw("select count(*) from daily_attendance where status='ZZ'")[0][0] == 0
    assert c.get("/attendance/daily?date=2999-01-01").status_code == 400
