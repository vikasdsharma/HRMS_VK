"""Data files must download (Content-Disposition: attachment), never render inside the browser tab."""
import io


def test_data_files_are_sent_as_attachments(app, make_org, login, add_employee):
    org = make_org()
    add_employee(org["id"], "D1", uan="100200300400", esic_no="1234567890")
    c = login(org["admin"])
    c.post("/attendance", data={"month": "2026-10", "file": (io.BytesIO(b"emp_code,present_days\nD1,20\n"), "a.csv")},
           content_type="multipart/form-data")
    c.post("/payroll/generate", data={"month": "2026-10"})
    for url, ext in (("/compliance/ecr.txt?month=2026-10", ".txt"), ("/compliance/esi.xlsx?month=2026-10", ".xlsx"),
                     ("/payroll/register.xlsx?month=2026-10", ".xlsx"), ("/payroll/payslips.zip?month=2026-10", ".zip"),
                     ("/employees/export.xlsx", ".xlsx"), ("/attendance/template.csv?month=2026-10", ".csv"),
                     ("/employees/template.xlsx", ".xlsx")):
        r = c.get(url)
        assert r.status_code == 200, url
        cd = r.headers.get("Content-Disposition", "")
        assert cd.startswith("attachment") and ext in cd, f"{url}: {cd!r}"
    ecr = c.get("/compliance/ecr.txt?month=2026-10")
    assert b"100200300400#~#" in ecr.data           # content is still the ECR text
