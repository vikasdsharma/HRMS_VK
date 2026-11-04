# HRMS for Indian Manufacturing (Haryana / Sonipat)

A multi-organization HR and payroll system for plants that employ **daily-wage workers**.
It handles employee records, monthly attendance, bulk payslips, and the **EPF (EPFO) and ESI (ESIC)**
files and challan worksheets you submit to the government portals.

It runs on your own computer as a local web app (Python + Flask + SQLite). No internet or cloud account is needed.

---

## Contents

1. [Quick start](#1-quick-start)
2. [Organizations, users and roles](#2-organizations-users-and-roles)
3. [Monthly workflow](#3-monthly-workflow)
4. [Employees](#4-employees)
5. [Salary structures](#5-salary-structures)
6. [Attendance](#6-attendance)
7. [Payroll and how salary is calculated](#7-payroll-and-how-salary-is-calculated)
8. [EPF and ESI files and challans](#8-epf-and-esi-files-and-challans)
9. [Settings](#9-settings)
10. [Project layout](#10-project-layout)
11. [Backup and upgrades](#11-backup-and-upgrades)
12. [Security notes](#12-security-notes)
13. [Known limitations](#13-known-limitations)

---

## 1. Quick start

**Requirements:** Windows, Python 3.12+ (developed on 3.14).

```powershell
python -m pip install -r requirements.txt
```

| Action  | How |
|---------|-----|
| Start   | Double-click `run.bat` (starts the server and opens http://127.0.0.1:5000) |
| Stop    | Double-click `stop.bat` |
| Restart | Double-click `restart.bat` (use this after any code change) |

> `stop.bat` and `restart.bat` stop whatever is listening on port 5000. They are simple scripts; if one
> misbehaves, close the server window instead.

**First login:** username `admin`, password `admin123`.
This is the **platform admin**. Change the password straight away (top bar, click your username).

The database file `hrms.db` is created automatically on first run, next to `app.py`.

---

## 2. Organizations, users and roles

Everything starts with an **organization**. Each organization has its own employees, salary structures,
attendance, payslips, settings, upload history and users. Data is kept apart by the application, so one
organization can never see another's records, even by typing an ID into the address bar.

```
Platform
 ├── Organization: ABC Industries Pvt Ltd
 │     ├── users: abc-admin (Admin), clerk1 (User)
 │     └── employees, salary structures, attendance, payslips ...
 └── Organization: XYZ Castings
       ├── users: xyz-admin (Admin)
       └── employees, salary structures, attendance, payslips ...
```

| Role | Belongs to | Can do |
|------|------------|--------|
| **Platform admin** (`superadmin`) | No organization | Create/edit/deactivate organizations, enter any organization, switch between them |
| **Admin** | One organization | Everything inside the organization, plus manage its users and settings |
| **User** | One organization | Employees, attendance, payroll, EPF/ESI files. Cannot manage users or settings |

**Creating an organization** (platform admin): *Organizations -> New organization*. You enter the name,
address and the organization's first admin (username and password, min 8 characters). Sample salary
structures are added for you.

**Working inside an organization:** the platform admin must always have one organization selected.
Use the dropdown in the top bar to switch. A yellow banner reminds you which organization you are in.
If you have not selected one, every page redirects to the organization list. If only one active
organization exists it is selected automatically.

**Deactivating an organization** blocks its users from logging in but keeps all data. Organizations are
never deleted.

**Users of an organization** (Admin or platform admin): *Users -> Add user*. Usernames are unique across
the whole system. You can reset a password or disable an account from the same screen.

---

## 3. Monthly workflow

```
1. Employees   ->  upload Excel list (once, then only new joiners / changes)
2. Salary structures -> check daily rates, assign each employee a category
3. Attendance  ->  upload this month's CSV
4. Payroll     ->  Generate payslips -> download PDF / ZIP / salary register
5. EPF / ESI   ->  download ECR file + ESI file + challan worksheets
6. Portals     ->  upload to EPFO and ESIC portals, pay by the 15th of next month
```

---

## 4. Employees

### Adding employees

* **One at a time:** *Employees -> Add employee*.
* **In bulk:** *Employees -> Excel upload*, download the template, fill it in, upload.

### Excel template columns

The first sheet must be named `Employees` (the template has it). Columns marked `*` are required.

| Column | Example | Notes |
|--------|---------|-------|
| Employee Code* | `E001` | Unique within the organization. Existing codes are **updated** |
| Name* | `Ramesh Kumar` | |
| Father/Husband Name | `Suresh Kumar` | |
| Gender (M/F/O) | `M` | |
| DOB (DD-MM-YYYY) | `15-08-1995` | |
| Date of Joining (DD-MM-YYYY) | `01-04-2026` | |
| Designation / Department | `Helper` / `Production` | |
| Mobile | `9876543210` | 10 digits starting 6-9 |
| Address | `Village Kharkhoda, Sonipat` | |
| Aadhaar (12 digit) | `123412341234` | |
| PAN | `ABCDE1234F` | |
| UAN (12 digit) | `100123456789` | Needed for the EPF ECR file |
| ESIC IP No (10/17 digit) | `1234567890` | Needed for the ESI file |
| Bank Name / Bank Account No / IFSC | `State Bank of India` / `30123456789` / `SBIN0000123` | |
| Salary Category* | `Unskilled` | Must match a category name (see the `Categories` sheet in the template) |
| Personal Daily Basic | `450` | Optional. Overrides the category's basic for this person |

**Rules**

* Employee Code, Aadhaar, PAN, UAN and ESIC number must each be unique **within the organization**.
  The same Aadhaar can exist in two different organizations.
* On an **update** (existing Employee Code), blank cells are left unchanged.
* The upload is **all-or-nothing**: if any row has an error, nothing is saved.

### Upload history and error reports

*Employees -> Excel upload* shows an **Upload history** (newest first): date and time, file name,
who uploaded, how many were created/updated, and the result. Rejected uploads are listed too.

Every upload with a readable file has a **Download report** button. The report is your own sheet with
three extra columns:

| Employee Code | Name | ... | Salary Category | **Status** | **Error Code** | **Error Message** |
|---|---|---|---|---|---|---|
| R1 | Good Person | ... | Unskilled | Valid - not saved (fix the error rows and re-upload) | | |
| R2 | Bad Aadhaar | ... | NoSuchCat | **Error** | `E112, E103` | unknown salary category 'NoSuchCat'; AADHAAR must be exactly 12 digits |
| R3 | Bad Pan | ... | Unskilled | **Error** | `E105, E107` | PAN format invalid (e.g. ABCDE1234F); Mobile must be a 10-digit Indian number |

Rows are coloured (red = error, green = saved, yellow = valid but not saved). The workbook also has an
**Upload Summary** sheet and an **Error Codes** sheet.

**Fix and re-upload:** correct the red rows in the report and upload that same file again. The Status /
Error columns are ignored on upload. Rows that were already saved are simply updated with the same values.

| Code | Meaning |
|------|---------|
| E101 / E102 | Employee Code / Name is blank |
| E103 | Aadhaar must be exactly 12 digits |
| E104 | UAN must be exactly 12 digits |
| E105 | PAN format (e.g. `ABCDE1234F`) |
| E106 | ESIC number must be 10 or 17 digits |
| E107 | Mobile must be a 10-digit Indian number |
| E108 | IFSC format (e.g. `SBIN0001234`) |
| E109 | Gender must be M, F or O |
| E110 | Date not understood, use DD-MM-YYYY |
| E111 | Personal Daily Basic is not a number |
| E112 | Salary Category does not exist in this organization |
| E113 | New employees need a Salary Category |
| E114 | Same Employee Code twice in the file |
| E201-E205 | Aadhaar / PAN / UAN / ESIC / Employee Code already belongs to another employee |
| E999 | Other problem, see the message |

> Uploads made before report support was added show in the history without a report button.

### Hidden ID numbers

Aadhaar, UAN, ESIC, PAN and bank account numbers are shown **half-hidden** by default:

```
Aadhaar  123412341234  ->  XXXXXX341234
UAN      100123456789  ->  XXXXXX456789
ESIC     1234567890    ->  XXXXX67890
```

* **Employee list:** click **Show hidden values** to see them in full. This lasts for that page view only.
* **Edit form:** ID boxes are blank with the masked value as a hint. Leave a box blank to **keep** the
  current number, or type a new one to replace it. Click **Show hidden values** to see and edit the full
  numbers; in that mode, emptying a box removes the number.
* Search still works on the full numbers.

### Left employees

Set Status to **Left** on the edit form (the leaving date defaults to today). Left employees are hidden
from the default list; use the Status filter to see them.

### Changing many people's category

Tick employees in the list, choose a category at the bottom, click **Assign category**.

---

## 5. Salary structures

*Salary Structures* holds the categories. All amounts are **per paid day**.

| Field | Meaning |
|-------|---------|
| Basic / DA / HRA / Other per day | Daily rates |
| Overtime multiplier | `2` = double rate (Factories Act). Overtime is paid on (Basic + DA) / 8 per hour |
| PF applicable | Deduct EPF for this category |
| Limit PF wages to ceiling | Cap PF wages at the ceiling (default Rs 15,000) |
| ESI applicable | Deduct ESI while gross is within the ceiling (default Rs 21,000) |

New organizations get three **sample** categories (Unskilled 400+60, Semi-Skilled 450+70, Skilled 500+80+20).
**Replace these with the current Haryana minimum wage notification** that applies to your industry and zone.

A category cannot be deleted while employees are assigned to it. Changing a category does not alter
payslips already generated; re-generate the month to apply it.

---

## 6. Attendance

*Attendance* has an **upload form with a month dropdown**. It defaults to the **current month** and lists
the previous 23 months, so you can upload or correct past attendance. Months that already have records show
`- N records already`. Future months are rejected.

### CSV format

Download a ready-made CSV with all active employees using **Download CSV with all active employees**, or
write your own (UTF-8; in Excel use *Save As -> CSV UTF-8*):

```csv
emp_code,present_days,paid_holidays,ot_hours,advance,other_deduction
E001,22,4,8,500,0
E002,26,4,0,0,0
E003,10,1,0,0,200
```

| Column | Meaning |
|--------|---------|
| `emp_code` | Must already exist |
| `present_days` | Days worked |
| `paid_holidays` | Weekly offs, paid holidays, paid leave |
| `ot_hours` | Overtime hours in the month |
| `advance` | Advance recovered this month |
| `other_deduction` | Any other deduction |

* **Paid days = present + paid holidays** (never more than the days in the month).
* Uploading a month again **overwrites** those employees' rows. Re-generate payslips afterwards.
* Like the employee upload, it is all-or-nothing, and unknown codes are listed.
* **Upload history** at the bottom of the page lists every attempt (date and time, month, file name,
  uploader, employees saved, result) including rejected files.

---

## 7. Payroll and how salary is calculated

*Payroll -> pick month -> Generate / re-generate payslips* builds payslips for everyone who has attendance
that month. Then download:

* **All payslips (1 PDF)**, one page per employee
* **Individual PDFs (ZIP)**
* **Salary register (Excel)** with net pay and bank details for payment

### Worked example

Category *Unskilled* (Basic 400, DA 60 per day). Employee E001: 22 days present, 4 paid holidays,
8 overtime hours, Rs 500 advance. October has 31 days.

| Step | Calculation | Result |
|------|-------------|--------|
| Paid days | 22 + 4 | **26** |
| Basic | 400 x 26 | 10,400 |
| DA | 60 x 26 | 1,560 |
| Overtime | 8 h x (400 + 60) / 8 x 2 | 920 |
| **Gross** | 10,400 + 1,560 + 920 | **12,880** |
| PF wages | Basic + DA (no overtime), capped at 15,000 | 11,960 |
| PF - employee 12% | 11,960 x 12% = 1,435.2 | **1,435** |
| EPS - employer 8.33% | 11,960 x 8.33% = 996.27 | 996 |
| EPF - employer balance | 1,435 - 996 | 439 |
| ESI eligible? | Gross without overtime 11,960 <= 21,000 | yes |
| ESI - employee 0.75% | 12,880 x 0.75% = 96.6, rounded **up** | **97** |
| ESI - employer 3.25% | 12,880 x 3.25% = 418.6, rounded **up** | 419 |
| Advance | | 500 |
| **Net pay** | 12,880 - 1,435 - 97 - 500 | **10,848** |

**Rounding:** PF amounts round half-up to the rupee (EPFO convention); ESI rounds **up** (ESIC convention).

---

## 8. EPF and ESI files and challans

Open *EPF / ESI*, choose the wage month, then download.

### EPFO

* **ECR file (.txt)** for the EPFO Unified Portal. One line per member **with a UAN**, fields separated
  by `#~#`:

  ```
  UAN#~#NAME#~#GROSS#~#EPF WAGES#~#EPS WAGES#~#EDLI WAGES#~#EPF EE#~#EPS#~#EPF DIFF#~#NCP DAYS#~#REFUND
  100123456789#~#RAMESH KUMAR#~#12880#~#11960#~#11960#~#11960#~#1435#~#996#~#439#~#5#~#0
  ```

* **EPF challan worksheet (PDF)** with the account-wise split:

  | Account | What | Rate |
  |---------|------|------|
  | A/c 01 | EPF (employee 12% + employer balance 3.67%) | |
  | A/c 10 | EPS | 8.33% |
  | A/c 02 | EPF admin charges | 0.5%, minimum Rs 500 |
  | A/c 21 | EDLI | 0.5% |
  | A/c 22 | EDLI admin charges | 0 |

### ESIC

* **ESI contribution file (.xlsx)** in the ESIC monthly-contribution upload layout: IP Number, IP Name,
  days paid, total wages, reason code for zero days, last working day.
* **ESI challan worksheet (PDF):** employee 0.75% + employer 3.25%.

### Important

* The PDFs are **reference worksheets**. The official challan (EPFO TRRN / ESIC challan) is generated on the
  government portal after you upload the file. **Due date: 15th of the following month.**
* Employees with PF/ESI applicable but **no UAN / ESIC number** are left out of the files; the Payroll and
  EPF/ESI pages list them as warnings.
* Check the file layouts against the portals' current formats before your first filing; the portals change
  them from time to time.

---

## 9. Settings

*Settings* (Admin only) is per organization:

* Company name and address (printed on payslips and challans)
* PF establishment code and ESI employer code (printed on the challan worksheets)
* Statutory rates and ceilings: PF ceiling 15,000, ESI ceiling 21,000, PF 12%, EPS 8.33%, EDLI 0.5%,
  PF admin 0.5% (min 500), ESI 0.75% / 3.25%. Change these only when the law changes.

---

## 10. Project layout

```
HRMS_VK/
├── app.py            Flask app: login, organization scoping, all routes
├── models.py         Database models, tenant (organization) isolation, upgrade of older databases
├── payroll.py        Salary, PF and ESI calculation; monthly totals
├── importers.py      Employee validation, Excel/CSV import, error codes, upload reports
├── compliance.py     ECR text file, ESI Excel file, salary register
├── pdfs.py           Payslip and challan PDFs (reportlab)
├── templates/        HTML pages
├── static/style.css  Styling
├── requirements.txt  flask, sqlalchemy, openpyxl, reportlab
├── run.bat           Start the server
├── stop.bat          Stop the server
├── restart.bat       Stop and start
├── hrms.db           Your data (created on first run)
├── hrms_pre_multitenant_backup.db   One-time backup made when organizations were introduced
└── .secret_key       Session signing key (created on first run; keep private)
```

How organization isolation works: every table that belongs to an organization carries an `org_id`.
`models.py` adds that filter to **every** database read automatically and stamps it on every new row, so
individual pages cannot forget it.

---

## 11. Backup and upgrades

* **Back up by copying `hrms.db`** (with the server stopped, or at least idle). This single file holds
  everything, including the uploaded employee files kept for error reports.
* **Upgrades are automatic** at start-up: new tables and columns are added to your existing database.
  When the multi-organization version first ran on an older single-company database, it saved a full copy
  as `hrms_pre_multitenant_backup.db` and moved all existing data into the first organization.

---

## 12. Security notes

* The server listens on `127.0.0.1` only, so it is reachable from this computer alone. Do not expose it to
  a network without adding HTTPS and further hardening.
* **Change the default `admin` password** and use strong passwords for all users (minimum 8 characters).
* Aadhaar, PAN and bank numbers are stored in the database as plain text. They are masked on screen, but
  anyone with the `hrms.db` file can read them. Protect the file and its backups (for example with disk
  encryption).
* **Show hidden values** is available to every user of the organization.
* Excel export, payslip PDFs and the ECR/ESI files contain full numbers because the portals and employees need them.

---

## 13. Known limitations

* One user per login session; no password-reset email. A forgotten platform-admin password needs a manual
  reset in the database.
* The platform admin account cannot be added or changed from the screen; there is a single `admin`.
* Organizations can be deactivated but not deleted.
* ESI coverage "stays on for the contribution period" is not tracked. Eligibility is re-evaluated each month.
* Professional tax is not applicable in Haryana and is not implemented. Bonus, gratuity, leave encashment
  and bank-transfer files are not included.
* Attendance uploads keep a history but, unlike employee uploads, no downloadable error report yet.
* The EPFO/ESIC file layouts follow the formats current when this was written; confirm them on the portals.
