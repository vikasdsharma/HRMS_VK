# HRMS for Indian Manufacturing (Haryana / Sonipat)

A multi-organization HR and payroll system for plants that employ **daily-wage workers**: employee records,
daily or monthly attendance, bulk payslips, **EPF (EPFO) and ESI (ESIC)** files and challan worksheets,
bonus and gratuity worksheets, a full audit trail, and an optional JSON API.

It runs on one computer (SQLite) or in the cloud (PostgreSQL, Docker). Python + Flask.

> Operations, deployment and incident procedures are in **[docs/RUNBOOK.md](docs/RUNBOOK.md)**.

## Contents

1. [Quick start](#1-quick-start)
2. [Organizations, users and roles](#2-organizations-users-and-roles)
3. [Monthly workflow](#3-monthly-workflow)
4. [Employees](#4-employees)
5. [Salary structures](#5-salary-structures-dated-rates)
6. [Attendance](#6-attendance-monthly-csv-or-daily)
   - [6A. Leave management](#6a-leave-management)
   - [6B. Employee portal](#6b-employee-portal-self-service)
7. [Payroll: calculation, finalising, locking](#7-payroll)
8. [EPF and ESI files and challans](#8-epf-and-esi-files-and-challans)
9. [Bonus and gratuity worksheets](#9-bonus-and-gratuity-worksheets)
10. [Security model](#10-security-model)
11. [Encryption keys and recovery](#11-encryption-keys-and-recovery)
12. [Configuration](#12-configuration)
13. [Backups, upgrades and deployment](#13-backups-upgrades-and-deployment)
14. [JSON API](#14-json-api)
15. [Development and tests](#15-development-and-tests)
16. [Project layout](#16-project-layout)
17. [Assumptions and limitations](#17-assumptions-and-limitations)

---

## 1. Quick start

**Windows (one machine)** - needs Python 3.12 or newer.

| Step | Command |
|------|---------|
| First time / after updates | double-click `setup.bat` (creates `.venv`, installs packages, upgrades the database) |
| Start | double-click `run.bat` (starts the server, opens http://127.0.0.1:5000) |
| Stop / restart | `stop.bat` / `restart.bat` |
| Run unattended (start at boot, daily backup) | `powershell -ExecutionPolicy Bypass -File scripts\install_tasks.ps1` (as Administrator) |

**First login on a brand-new database:** username `admin`. The password is **random and printed once** in the server
window and in `data\logs\app.log` (or set `HRMS_INITIAL_ADMIN_PASSWORD`). You must change it at first login.
On an existing database your current users and passwords are kept.

**Cloud:** see [Deployment](#13-backups-upgrades-and-deployment) (Docker + PostgreSQL + HTTPS).

---

## 2. Organizations, users and roles

Everything lives inside an **organization**: its employees, salary structures, attendance, payslips, settings,
upload history and users. The application filters every database read by the active organization and stamps it
on every new record, so one organization can never see or change another's data, even by typing an ID into the
address bar. This is covered by automated tests.

| Role | Belongs to | Can do |
|------|------------|--------|
| **Platform admin** (`superadmin`) | No organization | Create/edit/deactivate organizations, enter any one organization at a time (dropdown in the top bar), see the platform-wide audit log |
| **Admin** | One organization | Everything in it, plus: users, settings, finalise/unlock payroll, download statutory files and the salary register, reveal hidden ID numbers, export employees, bonus & gratuity, API tokens, audit log |
| **User** | One organization | Employees, attendance (monthly and daily), payroll drafts. Cannot reveal ID numbers or download statutory files, and has no access to leave administration |
| **Employee** | One employee record | The employee portal only: own leave balances and applications, own attendance calendar and correction requests, own **finalised** payslips. Nothing else |

* **Creating an organization:** *Organizations -> New organization*: name, address and its first admin (temporary
  password; they must change it at first login). Sample salary structures are added.
* **Deactivating an organization** blocks its users but keeps all data. Organizations are never deleted.
* Users: *Users -> Add user*. Admin-set passwords are temporary. Five wrong passwords lock the account for 15 minutes
  (an admin can unlock). Sessions end after 30 idle minutes.
* Password rules: at least 10 characters, letters and digits, not a common password, not containing the username.

---

## 3. Monthly workflow

```
1. Attendance   -> upload the CSV for the month (or mark days, then "Apply to monthly attendance")
2. Payroll      -> Generate payslips -> read the warnings -> Finalise the month   (locks it)
3. EPF / ESI    -> pre-submission check -> download ECR + ESI files -> upload to the portals -> pay by the 15th
```

The dashboard shows what needs attention (missing attendance, unfinalised payroll, missing UAN/ESIC numbers) and a
countdown to the EPF/ESI due date.

---

## 4. Employees

*Employees -> Add employee* or *Upload employees* (Excel). The list is searchable, sortable, filterable by status and
category, and paginated. Click a name for the **profile** (details, recent payslips, activity).

### Excel template

Download it from *Upload employees*. The sheet must be named `Employees`. `*` = required.

| Column | Example | Notes |
|--------|---------|-------|
| Employee Code* | `E001` | Unique within the organization. An existing code is **updated** |
| Name* | `Ramesh Kumar` | |
| Father/Husband Name, Gender (M/F/O) | | |
| DOB, Date of Joining | `15-08-1995` | DD-MM-YYYY. DOB drives the EPS age rule |
| Designation, Department, Mobile, Address | | Mobile: 10 digits starting 6-9 |
| Aadhaar (12 digit), PAN, UAN (12 digit), ESIC IP No (10/17 digit) | | UAN/ESIC are needed for the portal files |
| Bank Name, Bank Account No, IFSC | | |
| Salary Category* | `Unskilled` | Must match a category name (see the `Categories` sheet) |
| Personal Daily Basic | `450` | Optional; replaces the category basic for this person |

Rules: Employee Code, Aadhaar, PAN, UAN and ESIC must each be unique **within the organization** (the same Aadhaar may
exist in two organizations). Blank cells in an update are left unchanged. **All-or-nothing:** if any row has an error,
nothing is saved.

### Upload history and the fix-and-re-upload report

Every upload (saved or rejected) is listed with date and time, file name, uploader and counts. **Download report**
returns your own sheet plus **Status**, **Error Code** and **Error Message** columns (red = error, green = saved,
yellow = valid but not saved), an *Upload Summary* sheet and an *Error Codes* sheet. Fix the red rows and upload the
same file again; the extra columns are ignored.

| Code | Meaning | Code | Meaning |
|------|---------|------|---------|
| E101 / E102 | Employee code / name blank | E110 | Date not understood (DD-MM-YYYY) |
| E103 | Aadhaar not 12 digits | E111 | Personal daily basic not a number |
| E104 | UAN not 12 digits | E112 | Unknown salary category |
| E105 | PAN format | E113 | New employee needs a category |
| E106 | ESIC number not 10/17 digits | E114 | Employee code twice in the file |
| E107 | Mobile number | E201-E205 | Aadhaar / PAN / UAN / ESIC / code already used by another employee |
| E108 | IFSC format | E999 | Other (see the message) |
| E109 | Gender must be M, F or O | | |

The stored copy of an uploaded file is removed automatically after the retention period (default 180 days); the
history row stays.

### Protected ID numbers

* **Encrypted in the database:** Aadhaar, PAN and bank account numbers (see [section 11](#11-encryption-keys-and-recovery)).
* **Half-hidden on screen:** `123412341234` is shown as `XXXXXX341234` (Aadhaar, PAN, UAN, ESIC, bank account).
* **Admins** can click *Show hidden values* (this page view only; every reveal is logged). Other users cannot.
* On the edit form a blank ID box means "keep the current value"; in reveal mode, emptying a box removes the number.
* Search works on name, code, mobile, UAN and ESIC (partial), and on a **full** Aadhaar or PAN (exact match).

### EPS (pension) rules

EPS stops automatically for members aged 58 or more (from the first day of the month after the 58th birthday), and
for anyone ticked **Not eligible for EPS**; the whole employer 12% then goes to EPF.

---

## 5. Salary structures (dated rates)

*Salary structures* holds the categories. All amounts are **per paid day**. Overtime is paid on (Basic + DA) / 8 per
hour times the multiplier (2 = double rate, the Factories Act norm).

* **Rates are dated.** When you change a rate you choose the month it applies **from**. Earlier months keep their old
  rates even if you re-generate them; finalised months are never changed. The *Rate history* table shows every change.
* **Notified minimum wage** (optional): enter it per category. The system warns when the daily wage is below it and
  uses it for the bonus ceiling.
* The three **sample categories are placeholders**: set them to the current Haryana minimum-wage notification for your
  industry and zone.
* A category cannot be deleted while employees use it.

---

## 6. Attendance: monthly CSV or daily

**Monthly CSV** (*Monthly attendance*): pick the month (defaults to the current month; the past 24 months are
available; future months are rejected), choose the file. Download a pre-filled CSV of all active employees.

```csv
emp_code,present_days,paid_holidays,ot_hours,advance,other_deduction
E001,22,4,8,500,0
E002,26,4,0,0,0
E003,10,1,0,0,200
```

Paid days = present + paid holidays (weekly offs, holidays, paid leave), never more than the days in the month.
Advance and other deduction are **whole rupees**. The upload is all-or-nothing and refuses duplicate codes,
implausible overtime and unknown employees; it **warns** (without blocking) when an employee has paid days before
joining or after leaving. Re-uploading a month overwrites those employees' rows. Every attempt appears in the
**upload history** with the file name.

**Daily attendance** (*Daily attendance*): choose a date and mark each worker **P** present, **HD** half day,
**A** absent, **WO** weekly off, **H** paid holiday or **L** paid leave, plus overtime hours. The *Month view* shows
the grid; **Apply to monthly attendance** writes Present = P + 0.5 x HD, Paid holidays = WO + H + L and the OT total
(advance and deductions are left alone).

Finalised months are locked: uploads, daily marks and removals are refused until an admin unlocks the month.

---

## 6A. Leave management

Admins manage leave under **Leave** in the menu. Everything is policy-driven: the screens ship with placeholder
rules that you must set to your company policy and the law that applies to you.

**Leave types:** Casual (CL), Sick (SL), Annual (AL), Comp Off (CO) and Loss of Pay (LWP, unpaid, no balance). Add your own
(e.g. Maternity) under *Types & policies*; types are never deleted, only deactivated.

**Policies:** one default policy plus optional policies per salary category. For each leave type a rule sets:

| Setting | Meaning |
|---------|---------|
| Days per year | The quota. An admin can give **one employee** a different fixed quota or ceiling (on that employee's leave page) |
| How it is credited | **Yearly** (full quota at the start of the leave year), **Monthly** (quota / 12 on the 1st), **1 day per N days worked** (for daily wagers; credited when a payroll month is finalised, from the days actually worked) or **Manual** (comp off) |
| Starts after | Months after joining before leave starts to accrue (probation) |
| Prorate | The part-year / part-month after joining is prorated, in half-day steps |
| Carry forward | None, up to a limit, or all, applied at year end |
| Ceiling | The most days anyone may hold; credits stop at the ceiling |
| Expiry, allow negative | Life of a credit (comp off) and whether the balance may go below zero |

**How balances work.** Nothing is stored as a "balance". Every credit, leave taken, correction, lapse and expiry is an
immutable **ledger** row and the balance is their sum, so each number can be explained (*Leave -> an employee -> Ledger*).
Automatic credits carry a reference (e.g. `acc:2026-03`), so running the job twice never credits twice.

**Worked examples**

| Case | Result |
|------|--------|
| CL 12 a year, monthly, joined 10 March, prorate on | March = 1.0 x 22/31 = 0.7 -> **0.5**; April and May 1.0 each |
| AL 18 a year, yearly grant, joined 1 July | 18 x 184/365 = 9.07 -> **9.0** |
| AL: balance 14 at year end, carry limit 10, ceiling 30, new grant 18 | 10 carried, **4 lapse**; new year: 10 + 18 = **28** |
| CL ceiling 12, opening balance 11.5, monthly credit 1.0 | January +0.5 (capped), later months 0; recorded as zero-day rows so they are not retried |

**Setting it up (once)**

1. Make sure every employee has a **date of joining** (leave and the attendance calendar start from it; the dashboard lists who is missing one).
2. Review *Types & policies*, *Holidays & weekly off* and, in *Settings*, the **leave year** (calendar or April-March).
3. Enter existing balances per employee with *Adjust balance -> Opening balance*.
4. *Accrual & year-end* -> **preview** -> *Create credits*. Leave is managed from the date shown there (the start of the current leave year by
   default, fixed at the first real run); earlier years are not back-filled.
5. Repeat step 4 at the start of every month, and after finalising payroll if you use "1 day per N days worked".

**Year-end.** *Accrual & year-end -> Preview closing* shows, for each employee and leave type, the balance, what is carried and
what lapses. *Close year* applies it once. Credits for the new year **wait until the old year is closed**, so the ceiling is
checked against the right balance. Comp off is excluded: it lapses by its own expiry.

**Applications.** Admins record leave for anyone (optionally approving immediately); employees apply from the portal.

* A request is checked for: dates inside employment, overlap with other leave, enough balance (pending requests are reserved),
  half days only where allowed, the certificate rule (Sick leave longer than 2 days needs "certificate provided" ticked), the back-dating limit
  for employees (7 days), and **finalised payroll months** (refused until unlocked).
* Weekly offs and holidays inside the range are **not** leave days (unless the "sandwich rule" is switched on in Settings).
* **Approve** debits the ledger and writes attendance marks (**L** paid leave, **HL** half-day paid leave, **U** unpaid leave,
  **HU** half-day unpaid). **Cancel** gives the days back and clears the marks. Approval is refused if the employee is already
  marked present on one of the days.
* Marks created by leave cannot be overwritten by hand; cancel the application instead.

**Comp off.** An admin grants it with a reason, the date worked, the days (half-day steps) and a validity (default 60 days).
Unused days lapse after expiry (*Accrual & year-end -> Expired comp off*); days used are charged to the oldest grants first.

**Holidays and weekly off.** Enter holidays per year; set the weekly off (default Sunday) for everyone and optionally per salary category.

**Attendance, day by day, from the joining date.** *Employee -> Attendance calendar* shows a full-year grid starting on the joining
date (and ending at the leaving date). Weekly offs and holidays appear automatically. Admins can mark a day, mark a range, or clear a day;
future days, days outside employment and days in a finalised month are locked. The same calendar is the employee's read-only view;
employees ask for corrections instead of editing (see 6B).

**What changed in attendance and payroll**

* In *Month view -> Apply to monthly attendance*, paid leave counts as a paid day, **unpaid leave does not**, and **weekly offs and
  holidays on unmarked days count as paid days** for employees who have daily marks that month (up to today, within their employment).
  Previously only hand-marked off days counted.
* The daily attendance page shows approved leave read-only, pre-fills weekly offs/holidays from the calendar, and ignores employees who have not
  yet joined or have left (employees with *no* joining date can still be marked).

**Reports** (*Leave reports*, *Leave balances*): balances for everyone (screen + Excel), the **leave register** for any period,
**leave liability** (encashable leave x daily Basic + DA, for provisioning; the system does not post encashment to payroll), and the
**attendance register** for a month (Excel, one row per employee, one column per day).

> The Factories Act, the Haryana Shops and Establishments rules and the Labour Codes govern entitlements differently by establishment
> and may change. Treat the defaults as placeholders and confirm them with your consultant. Sick leave from the employer is separate from
> ESI sickness benefit.

---

## 6B. Employee portal (self-service)

**Giving access (admins):** *Employee portal access* lists every active employee.

* **Give access** to one person: choose a username (the suggestion is `employeecode.organizationid`, e.g. `e001.2`; usernames are
  unique across the whole system) and optionally a password. The **temporary password is shown once**; the employee must change it at first sign-in.
* **Create logins for everyone** generates a login for every active employee without one and downloads a CSV (code, name, username, temporary
  password) to hand out. Passwords are stored only as hashes; the CSV is the only readable copy, so keep it safe and discard it after handing out.
* **Reset password**, **Disable / Enable** and **Unlock** per person. Disabling signs the person out immediately. An employee marked
  **Left** loses access automatically.

**What an employee can do**

| Page | Purpose |
|------|---------|
| Home | Leave balances, this month's paid days, messages (leave decided, correction decided, comp off granted) |
| My leave | Apply (full or half days, certificate tick for sick leave), see and cancel their own requests (approved leave only before it starts), see how their balance changed |
| My attendance | The yearly calendar from their joining date; **ask for a correction** (Present or Half day, with a reason, up to 30 days back) |
| My payslips | Download their own payslip PDFs, **only for months HR has finalised** |

**What they cannot do:** open any other page (every other address sends them to their home), see another employee's data, see Aadhaar, PAN,
bank or UAN numbers, approve anything, or edit attendance directly. Leave and corrections are decided by an admin under *Applications* and
*Attendance requests* (both show a count badge in the menu and on the dashboard).

The portal uses the same sign-in protections as everyone else (lockout, idle timeout, CSRF, audit log). Every application, decision, grant
of access and payslip download is audited.

---

## 7. Payroll

*Payroll -> choose month -> Generate payslips.* Download all payslips as one PDF or a ZIP of individual PDFs, and
(admins) the salary register with bank details.

### Worked example

Category *Unskilled* (Basic 400, DA 60 per day). Employee E001: 22 days present, 4 paid holidays, 8 overtime hours,
Rs 500 advance, October (31 days).

| Step | Calculation | Result |
|------|-------------|--------|
| Paid days | 22 + 4 | **26** |
| Basic / DA | 400 x 26 / 60 x 26 | 10,400 / 1,560 |
| Overtime | 8 h x (400 + 60) / 8 x 2 | 920 |
| **Gross** | 10,400 + 1,560 + 920 | **12,880** |
| PF wages | Basic + DA (no overtime), capped at 15,000 | 11,960 |
| PF employee 12% | 11,960 x 12% = 1,435.2 | **1,435** |
| EPS employer 8.33% / EPF balance | 996.27 -> 996 / 1,435 - 996 | 996 / 439 |
| ESI eligible? | gross without overtime 11,960 <= 21,000 | yes |
| ESI employee 0.75% | 12,880 x 0.75% = 96.6, rounded **up** | **97** |
| ESI employer 3.25% | 12,880 x 3.25% = 418.6, rounded **up** | 419 |
| **Net pay** | 12,880 - 1,435 - 97 - 500 | **10,848** |

All money uses exact decimal arithmetic. PF amounts round half-up (EPFO), ESI rounds up (ESIC). Net pay is always
exactly Gross - Deductions. **ESI cover continues for the whole contribution period** (April-September,
October-March) once an employee has contributed, even if wages later exceed 21,000.

### Finalise and unlock

* **Finalise month** (admin) freezes the month's attendance and payslips. It is refused while any payslip is out of
  date (attendance or rates changed after generation); re-generate first.
* **Unlock** (admin) needs a reason (min. 5 characters) and is recorded in the audit log. Finalise again afterwards.
* The payroll page lists warnings: missing UAN/ESIC, negative net pay, paid days outside the employment dates,
  wage below the notified minimum.

---

## 8. EPF and ESI files and challans

*EPF / ESI* shows a **pre-submission check** (Pass / Warnings / Problems) before you download:

* UAN missing, not 12 digits, or shared by two employees; ESIC number missing or malformed
* names with characters EPFO rejects (the file uses a cleaned name); impossible NCP days or wages
* month not finalised, out-of-date payslips, PF/ESI codes not set

Downloads (admins): the **ECR text file**, the **ESI contribution Excel file**, and PDF **challan worksheets**.

```
UAN#~#NAME#~#GROSS#~#EPF WAGES#~#EPS WAGES#~#EDLI WAGES#~#EPF EE#~#EPS#~#EPF DIFF#~#NCP DAYS#~#REFUND
100123456789#~#RAMESH KUMAR#~#12880#~#11960#~#11960#~#11960#~#1435#~#996#~#439#~#5#~#0
```

EPF split: A/c 01 EPF (employee 12% + employer balance), A/c 10 EPS 8.33%, A/c 02 admin 0.5% (min Rs 500), A/c 21
EDLI 0.5%. ESI: employee 0.75% + employer 3.25%.

The PDFs are **reference worksheets**. The official challan (EPFO TRRN / ESIC challan) is generated on the
government portal after you upload the file. **Due date: the 15th of the following month.** Check the file layouts
against the portals' current formats before your first filing; they change from time to time.

---

## 9. Bonus and gratuity worksheets

(*Bonus & gratuity*, admins.) These are **worksheets to support your calculation**: confirm the thresholds with your
labour-law consultant, especially since the Labour Codes may change them. Rates are in *Settings*.

* **Bonus** (per financial year, from payslips): bonus rate % (default 8.33) of Basic + DA, each month capped at the
  bonus wage ceiling (default 7,000) or the notified minimum wage if higher; months where salary exceeds the
  eligibility ceiling (21,000) are excluded; at least 30 days worked.
* **Gratuity estimate** (as on a date): 15 days' wages per counted year = daily (Basic + DA) x 15 x years; eligible
  after the minimum completed years (default 5); a leftover of more than 6 months counts as a full year; capped
  (default 20,00,000). Useful for provisioning.

Both download as Excel.

---

## 10. Security model

| Area | What is in place |
|------|------------------|
| Isolation | Every query is filtered by the active organization; every write is stamped with it; tested |
| Sign-in | Lockout after 5 failures, idle timeout, password policy, forced change of temporary passwords, no user enumeration |
| Forms | CSRF tokens on every form; logout is POST |
| Browser | Strict Content-Security-Policy (no inline scripts), HttpOnly + SameSite cookies, `Secure` cookies and HSTS in production, `no-store` caching of pages |
| Personal data | Aadhaar, PAN and bank numbers encrypted at rest; half-hidden on screen; reveal/export/download are admin-only and audited |
| Files | Uploads checked for type, size and zip bombs; every Excel we produce is protected against formula injection |
| Audit | Append-only log of sign-ins, changes, reveals, exports, downloads, payroll finalise/unlock, settings, API calls. Never contains Aadhaar/PAN/bank values |
| Redirects | Login `next` parameter only accepts same-site paths |

Still your responsibility: HTTPS in front of the app when it is not on your own machine, protecting `hrms.db` /
the PostgreSQL volume and **the encryption key**, off-machine copies of backups, and legal compliance for storing
Aadhaar (DPDP Act / Aadhaar rules): consult your advisor.

---

## 11. Encryption keys and recovery

Aadhaar, PAN and bank account numbers are encrypted with **Fernet (AES-128 + HMAC)**. One key controls it:

| Where the key lives | How |
|---------------------|-----|
| Local / single machine | file `data/field.key`, created automatically on first run |
| Cloud | environment variable `HRMS_FIELD_KEY` (from your secret manager / `.env`) |

> **If the key is lost, the encrypted numbers cannot be recovered by anyone, including us.** Everything else keeps
> working, but those fields are unreadable and would have to be re-entered.

**Do this once, today**

1. Copy `data/field.key` (or the `HRMS_FIELD_KEY` value) into a password manager **and** onto an offline copy
   (USB / printed in a safe). Keep it **separate** from database backups: a backup plus its key is the whole secret.
2. Run `python manage.py check-keys`. It proves the key decrypts every protected value (`FAILED: 0`).

**Manual recovery recipes**

| Situation | What to do |
|-----------|------------|
| "A protected field could not be decrypted" error | The running key does not match the data. Find the original key (password manager, old `data/field.key.*.old`, the old server's environment), put it back, restart, run `check-keys`. |
| Read one person's numbers by hand (e.g. the web app is down) | `python manage.py show-employee --org <organization id> <employee code>`. It decrypts and prints Aadhaar, PAN and bank account, and records an audit entry. |
| Restore the database on a new machine | Install the app, copy the backup `.db` to `hrms.db`, put the **same** key in `data/field.key` (or `HRMS_FIELD_KEY`), run `python manage.py migrate`, then `check-keys`. |
| Suspect the key leaked | Rotate it: `python manage.py rotate-key` (below), then treat old backups as exposed. |
| Moving to PostgreSQL | Keep the same key. Encrypted values are copied as they are. |

**Rotating the key** (planned, or after a suspected leak)

```
python manage.py backup                 # safety copy first (rotate-key also makes one)
python manage.py rotate-key             # re-encrypts everything under a NEW key
                                        #   file-based: new key is saved, the old one kept as field.key.<timestamp>.old
                                        #   env-based:  it prints the new HRMS_FIELD_KEY=... to set; set it and restart
python manage.py check-keys             # must show FAILED: 0
```

Keep the old key until you have verified the result and old backups are no longer needed (an old backup can only be
read with the key that was current when it was made).

**Implementation note for developers:** values are stored as `enc1:<token>`. Next to each of Aadhaar and PAN a
*blind index* (keyed hash, `aadhaar_idx` / `pan_idx`) enforces "unique per organization" and powers exact search.
It is derived from the first key, so `rotate-key` recomputes it.

---

## 12. Configuration

All settings are environment variables (or a `.env` file; copy `.env.example`). Real environment variables win.

| Variable | Default | Meaning |
|----------|---------|---------|
| `HRMS_ENV` | `development` | `production` enables secure cookies + HSTS |
| `HRMS_DATABASE_URL` | SQLite `hrms.db` | e.g. `postgresql+psycopg://user:pass@host:5432/hrms` |
| `HRMS_SECRET_KEY` | generated in `data/secret_key` | session signing key |
| `HRMS_FIELD_KEY` | generated in `data/field.key` | data encryption key (comma list for rotation) |
| `HRMS_DATA_DIR` | `./data` | logs, backups, generated keys |
| `HRMS_HOST` / `HRMS_PORT` | `127.0.0.1` / `5000` | listen address |
| `HRMS_TRUST_PROXY` | `0` | `1` behind nginx/Caddy/load balancer |
| `HRMS_COOKIE_SECURE` | on in production | needs HTTPS (set `0` only to test production mode over plain http) |
| `HRMS_AUTO_MIGRATE` | `1` | run DB migrations at start-up |
| `HRMS_INITIAL_ADMIN_PASSWORD` | random, printed once | first platform admin password |
| `HRMS_SESSION_MINUTES` | `30` | idle timeout |
| `HRMS_LOGIN_MAX_FAILS` / `HRMS_LOGIN_LOCK_MINUTES` | `5` / `15` | lockout |
| `HRMS_BACKUP_KEEP` | `14` | scheduled backups to keep |
| `HRMS_UPLOAD_RETENTION_DAYS` | `180` | keep stored upload files this long |
| `HRMS_MAX_UPLOAD_MB` | `10` | upload size limit |
| `HRMS_LOG_LEVEL` | `INFO` | |

Per-organization settings (PF/ESI codes, rates, ceilings, bonus and gratuity parameters) are in the web app under
*Settings*.

---

## 13. Backups, upgrades and deployment

* **Backup now:** `python manage.py backup` (consistent even while the app runs; SQLite online backup, `pg_dump` for
  PostgreSQL) -> `data/backups/`. **Daily housekeeping:** `python manage.py maintenance` (scheduled backup + retention
  purge); `scripts\install_tasks.ps1` schedules it at 02:00. **Copy backups off the machine**, and keep the encryption
  key separate (section 11).
* **Restore (SQLite):** stop the app, `python manage.py restore data\backups\<file>.db`, start.
* **Upgrades:** replace the code, run `setup.bat` (or `python manage.py migrate`). Migrations are versioned (Alembic);
  on SQLite a safety copy `hrms_pre-migrate_*.db` is made automatically first.
* **Logs:** `data/logs/app.log`, `error.log`, `access.log` (rotating). Health check: `GET /healthz`.
* **Cloud:** `cp .env.example .env`, fill in the secrets, then `docker compose up -d --build`
  (`--profile https` adds automatic HTTPS via Caddy). Details, upgrade and rollback steps: **docs/RUNBOOK.md**.

---

## 14. JSON API

Optional, for other systems (biometric devices, accounting, mobile apps). Not used by the web app.

* *API access* (admins) creates **tokens**: one organization each, permissions `read` (default), `write`, and
  `pii` (include Aadhaar/PAN/bank account). The token is shown **once**; only a hash is stored. Revoke any time.
* `Authorization: Bearer hrms_...`; 120 requests/minute/token; JSON errors `{"error": {"code", "message"}}`;
  all writes audited as `api:<token name>`; finalised months are locked for the API too.
* Endpoints (`/api/v1`): `GET /me`, `GET/POST /employees`, `GET/PATCH /employees/{code}`,
  `GET/PUT /attendance/{YYYY-MM}`, `GET /payroll/{month}`, `POST /payroll/{month}/generate`,
  description at `/api/v1/openapi.json`.

```
curl -H "Authorization: Bearer hrms_xxx" http://127.0.0.1:5000/api/v1/employees?per=50
curl -X PUT -H "Authorization: Bearer hrms_xxx" -H "Content-Type: application/json" \
     -d '{"rows":[{"emp_code":"E001","present_days":22,"paid_holidays":4,"ot_hours":8,"advance":500}]}' \
     http://127.0.0.1:5000/api/v1/attendance/2026-10
```

---

## 15. Development and tests

```
python -m pip install -r requirements-dev.txt
python -m pytest -q          # ~115 tests: isolation, security, encryption, payroll maths, leave, portal, API, pages, migrations
python -m ruff check .
```

Tests use a throw-away database, secret key and encryption key in a temp folder; they never touch your data.
One test fails if a model changes without a matching migration; another fails if a secret or database file is
tracked by git. CI (`.github/workflows/ci.yml`) runs all of it on every push.

Changing the database: edit `models.py`, then `alembic revision --autogenerate -m "what changed"`, review the file
in `migrations/versions/` (SQLite needs batch mode, which is already configured), commit both.

---

## 16. Project layout

```
app.py            app setup, request pipeline (auth, organization scope, headers, logging, error pages)
views/            the pages: auth, dashboard, employees, categories, attendance, payroll, compliance, reports, admin,
                  leave (admin leave & attendance screens), me (employee portal)
api.py            JSON API blueprint
models.py         tables, tenant isolation, engine, migrations runner, first-run seeding
payroll.py        salary + EPF/ESI calculation, dated rates, finalise/lock, consistency checks
importers.py      validation, Excel/CSV import, error codes, fix-and-re-upload reports
compliance.py     ECR file, ESI file, salary register, pre-submission check
reports.py        daily-attendance roll-up, bonus and gratuity worksheets
leave.py          leave engine: policies, ledger, accrual, rollover, applications, comp off, calendars, corrections, portal logins
leave_defaults.py starting leave types / rules for a new organization
pdfs.py           payslip and challan PDFs
crypto.py         field encryption + blind index       xl.py  safe Excel output + upload checks
security.py       redirects, password policy           ops.py backup / restore / retention
manage.py         command-line operations              config.py all settings
migrations/       versioned database migrations (Alembic)
templates/ static/   the UI                            tests/   automated tests
scripts/ deploy/ Dockerfile docker-compose.yml   deployment      docs/RUNBOOK.md   operations guide
data/             (git-ignored) logs, backups, generated keys
```

---

## 17. Assumptions and limitations

* **Verify statutory details with your consultant**: PF/ESI rates and ceilings, EPS age rule, ESI period rule, bonus
  and gratuity parameters, and the file layouts accepted by the EPFO and ESIC portals. The Labour Codes may change the
  PF wage definition; ceilings and rates are settings, but the formulas follow the rules as understood when written.
* EPS stops from the first day of the month after the 58th birthday (an assumption; adjust `EPS_STOP_AGE` or tick
  *Not eligible for EPS* if your advisor reads it differently).
* The PostgreSQL path (Docker) is written for portability and the migrations avoid SQLite-only constructs, but it has
  **not been run against a real PostgreSQL** in this project's test environment: run `docker compose up` on a staging
  server and `python manage.py check-keys` before trusting it with production data.
* The UI was verified by automated rendering tests for every page and role, not by a human visual review in every
  browser; please look through it and report anything awkward.
* API rate limiting is per server process (a soft limit when several workers run).
* Platform-admin accounts cannot be created in the web app; use `python manage.py create-superadmin <name>`.
* Organizations can be deactivated but not deleted. No professional tax (not applicable in Haryana), shift scheduling or bank-transfer files.
* Leave: excess leave is not split automatically into paid + unpaid (an admin records the unpaid part as Loss of Pay); there is no
  supervisor / department approver role yet (admins approve); notifications are in-app only (no e-mail / WhatsApp); the attendance
  register is Excel only; leave encashment is reported but not posted to payroll.
