# Leave & Attendance Management: plan and build prompt

Status: **L1 (foundations), the application/approval parts of L2 that the portal needs, and L3 (employee portal) are built.** Still open from the plan: supervisor role, automatic split of excess leave into LWP, leave balances on the payslip, PDF muster roll, API endpoints for leave, e-mail/WhatsApp notifications. Items tagged **[You asked]** are your requirements;
items tagged **[Recommended]** are my additions, each with the reason. Strike out anything you do not want.

Sections: 1 Requirements · 2 Recommended additions · 3 Decisions to confirm · 4 How it fits the current system ·
5 Data model · 6 Rules (with worked examples) · 7 Permissions · 8 Phases and acceptance criteria ·
9 Legal and policy notes · 10 **The build prompt** (copy-paste)

---

## 1. Requirements recap [You asked]

| # | Requirement |
|---|-------------|
| R1 | Leave types: **Sick Leave, Annual Leave, Casual Leave, Comp Off** (comp off given by an admin). |
| R2 | An admin sets a **fixed number of leaves per employee** (per leave type). |
| R3 | The admin chooses **when leave is credited: annually or monthly**, counting from the employee's **date of joining**. |
| R4 | Balances are **managed year to year**, with a **carry-forward policy** and a **ceiling** on the number of leaves an employee can hold. |
| R5 | Leave is taken **by application** (apply, then approve). |
| R6 | **Attendance per employee, per day of the year, from the joining date onward**, managed by **the employee and by the admin**. |

## 2. Recommended additions

| # | Addition | Why |
|---|----------|-----|
| A1 | **Leave ledger**: every change to a balance is an immutable row (opening, credit, debit, adjustment, carry-forward, lapse, comp-off grant/expiry). Balance = sum of rows. | Balances are disputed more than any other HR figure. A ledger shows exactly how a number arose, can be audited, and is rebuilt safely if a job runs twice. A single mutable "balance" column cannot do that. |
| A2 | **Leave policies**: one policy per salary category (rules for each leave type), with a **per-employee override** for R2. | Setting 4 types x every employee by hand does not scale; overrides still allow "this person gets 14". |
| A3 | **Accrual choices per leave type**: (a) fixed yearly grant, (b) monthly credit, (c) **1 day per N days worked** (the Factories-Act style rule used for daily wagers), (d) manual only (comp off). | Daily-wage workers earn annual leave by days actually worked, not by calendar time. |
| A4 | **Holiday calendar and weekly-off rules** per organization (and per category, e.g. Sunday off). | Needed so a leave spanning a holiday is counted correctly and so attendance pre-fills WO/H days. |
| A5 | **Unpaid leave (Loss of Pay)** as a built-in type with no balance. | Absence beyond balance needs a defined, payroll-visible outcome instead of an informal "A". |
| A6 | **Approval workflow**: pending -> approved / rejected / cancelled; half-day (0.5); back-dating limit; cancel after approval (balance restored); medical document required for sick leave longer than N days; optional "sandwich rule" for holidays between leave days. | These are the situations that actually come up. Defining them now avoids rework. |
| A7 | **Employee self-service login** (role `employee`, sees only their own data). | R5 and R6 say the *employee* applies and manages attendance. Today employees have **no login** at all; only organization users do. This is the biggest structural change in the module. |
| A8 | **Attendance regularisation**: an employee asks to correct a day ("I was present on the 12th"); an approver accepts or rejects it. | The honest way for an employee to "manage" attendance without being able to rewrite it. |
| A9 | **Year-end rollover as a reviewed, repeatable job**: dry-run preview, then apply; idempotent; writes carry-forward and lapse rows; fully audited. | A silent annual job that changes everyone's balance must be inspectable and safe to re-run. |
| A10 | **Comp off detail**: reason, date worked, days granted (0.5 steps), **expiry** (lapses automatically), granted by whom. | Comp off without an expiry accumulates forever. |
| A11 | **Eligibility and proration**: waiting period before a type is usable (probation), proration for mid-year joiners, rounding to 0.5. | R3 says "after joining"; these rules say exactly how. |
| A12 | **Exit settlement view**: leave still owed (encashable types) and leave that lapses when someone leaves. | Needed at full-and-final settlement; also gives a **leave liability** figure for accounts. |
| A13 | **Reports**: balance report (all employees), leave register, monthly leave summary, comp-off ledger, leave liability, printable **attendance register (muster roll)** for a month. | What auditors and inspectors ask for. |
| A14 | **Notifications**: in-app first (pending approvals, decisions); email/WhatsApp later. | Applications sit unseen otherwise. |
| A15 | **Audit and locking**: every grant, adjustment, approval and rollover is audited; approved leave cannot change a **finalised** payroll month without an unlock. | Reuses the controls already in the system. |
| A16 | **API endpoints** (balance, apply, approve) in a later phase. | A mobile app or biometric device will want them; not needed first. |

## 3. Decisions to confirm (with my recommended default)

| # | Question | Recommended default |
|---|----------|---------------------|
| D1 | Do **employees log in** themselves? | **Yes, staged.** First, admins/supervisors enter and approve everything (usable immediately). Then add employee login: employee code + password set by the admin; mobile-OTP later. Many daily wagers have no email or smartphone, so the admin-managed path must always work. |
| D2 | Leave year | **Organization setting**, default **calendar year** (Jan-Dec). Financial year (Apr-Mar) selectable. |
| D3 | Who approves? | Admin by default. Optional **supervisor** role limited to chosen departments (phase 3). |
| D4 | Sick leave documents | Medical certificate required beyond **2 consecutive days** (configurable). |
| D5 | Comp off expiry | **60 days** after it is granted (configurable per grant). |
| D6 | Unpaid leave | **Include** (type "LWP"), reduces paid days in payroll. |
| D7 | Holidays and weekly off | Per-organization holiday list; weekly off default **Sunday**, configurable per category. Sandwich rule **off** by default. |
| D8 | Carry-forward defaults | Casual: none. Sick: none. Annual: carry up to a limit. All editable per policy. |
| D9 | Back-dated applications | Allowed up to **7 days** for employees, unlimited for admins (but never into a finalised month). |
| D10 | Encashment | Show the figure in reports and exit settlement; **no payroll posting** in the first version. |

## 4. How it fits the current system

| Existing piece | What changes |
|----------------|--------------|
| `DailyAttendance` (status P, HD, A, WO, H, L; OT hours) | Gets a **source** (manual / leave / holiday / auto) and a link to the **leave application**. `L` becomes "leave of a specific type". Marks written by approved leave cannot be silently overwritten. |
| `reports.rollup_month` / "Apply to monthly attendance" | Extended so paid leave counts as a paid day, **LWP counts as unpaid**, and per-type leave days are summarised. |
| Payroll finalise / unlock | Leave that touches a **finalised** month is refused until an admin unlocks (same rule as attendance today). |
| Roles `superadmin / admin / user` | Add `employee` (own data only) and, later, `supervisor`. Every employee-facing query is scoped to the **employee**, on top of the existing organization scope. |
| `Employee` | Gains a nullable login link; `date of joining` becomes **mandatory** for leave and the per-day attendance calendar; date of leaving ends the calendar. |
| Audit log, CSRF, lockout, tenant isolation, migrations, tests | Reused unchanged. New tables follow the same pattern. |
| `payslip` | Optionally prints leave balances at the bottom (phase 4). |

## 5. Data model (sketch)

All tables below are **organization-scoped** (the same automatic isolation as today).

| Table | Purpose / key columns |
|-------|----------------------|
| `leave_types` | code (SL, AL, CL, CO, LWP), name, paid?, uses_balance?, half_day_ok?, doc_required_after_days, active |
| `leave_policies` | name, linked salary category (optional), leave year start month |
| `leave_policy_rules` | policy, leave type, **quota per year**, **accrual** (yearly / monthly / per_days_worked / manual), accrual parameter (e.g. 20 days), first-credit rule (from joining / after probation N months), proration on/off, **carry_forward** (none / all / up to N), **max balance (ceiling)**, expires_after_days, negative balance allowed? |
| `employee_leave_overrides` | employee, leave type, quota override and/or ceiling override (R2) |
| `leave_ledger` | employee, leave type, date, **kind** (opening, credit, debit, adjustment, carry_forward, lapse, comp_grant, comp_expiry, encash), days (+/-), leave year, note, source application/grant id, created_by. **Unique key makes accrual jobs idempotent.** |
| `leave_applications` | employee, type, from, to, half-day flags, days (computed), reason, document?, status, applied_by, decided_by/at, decision note, cancelled_at |
| `comp_off_grants` | employee, worked date, days, reason, granted_by, expires_on |
| `holidays` | date, name, optional/restricted?; `weekly_off_rules` (category, weekday) |
| `attendance_requests` | employee, date, requested status, reason, status, decided_by |
| `daily_attendance` (existing) | + `source`, + `leave_application_id` |
| `users` (existing) | + `employee_id` (for role `employee`) |

## 6. Rules, with worked examples

**Balance** = sum of ledger rows for (employee, type). **Available to apply** = balance - days already pending approval.

**Monthly credit.** Casual leave, 12 a year, credited monthly = 1.0 on the 1st of each month. Joined 10 March: with
"first credit from the month after joining" the first credit is 1 April; with "pro-rated first month" March gets
round-to-0.5 of 12/12 x 22/31 = 0.5. The job is keyed by (employee, type, month): running it twice changes nothing.

**Yearly grant with proration.** Annual leave 18 a year, granted on 1 January. Joined 1 July, proration on: 18 x 6/12 = **9.0**.

**Per days worked.** Annual leave "1 day per 20 days worked": an employee with 240 paid days in the year earns **12**;
credit is computed when the month is finalised (from final attendance), so it can never exceed what was worked.

**Carry-forward and ceiling at rollover.** Annual leave, carry forward up to 10, ceiling 30, new grant 18:
balance on 31 Dec = 14 -> carry **10**, **lapse 4** -> 1 Jan: opening 10 + grant 18 = 28 (<= 30, kept).
If the sum would be 33, **3 is lapsed** to respect the ceiling (shown as its own ledger row).

**Application.** 2 days Casual leave Mon-Tue: checks overlap with other approved leave, enough balance, not in a
finalised month; on approval writes 2 ledger debits and 2 attendance marks (type CL). Cancelling after approval
restores the ledger and clears the marks. A half-day is 0.5.

**Beyond balance.** Either refuse, or (per type) allow negative balance, or split: the excess becomes **LWP**
(unpaid) with the employee's consent shown on the application.

**Attendance calendar.** For employee E and year Y: one cell per day from `max(1 Jan, joining date)` to
`min(31 Dec, leaving date, today)`. Each cell is one of Present, Half day, Absent, Weekly off, Holiday, Leave (type),
or "no mark yet". Past days outside a finalised month can be edited by an admin; the employee can only *request*
changes (regularisation).

## 7. Permissions

| Action | Employee | Supervisor (later) | Admin | Platform admin |
|--------|:-------:|:------------------:|:-----:|:--------------:|
| See own balances and calendar | yes | yes | n/a | n/a |
| Apply for own leave | yes | yes | n/a | n/a |
| Request an attendance correction (own) | yes | yes | n/a | n/a |
| Approve / reject leave and corrections | no | their departments | all | when inside an organization |
| Enter leave / attendance on someone's behalf | no | their departments | yes | yes |
| Grant comp off, adjust balances manually | no | no | yes (reason mandatory, audited) | yes |
| Edit policies, holidays, run rollover | no | no | yes | yes |
| Reports (balances, liability, register) | own only | their departments | all | all |

Employees never see Aadhaar/PAN/bank data (nothing in this module needs it) and never see another employee.

## 8. Phases and acceptance criteria

**L1: Foundations (admin-managed).** Leave types, policies, per-employee overrides, ledger, monthly/yearly/per-days-worked
accrual job, holiday calendar and weekly-off rules, balance screen per employee with manual adjustments and comp-off
grants, **per-employee yearly attendance calendar** (admin edit, from joining date), migrations, audit, tests.
*Accept:* running accrual twice gives identical balances; mid-year joiner is prorated as in section 6; balance screen
equals the ledger sum; calendar starts on the joining date; isolation tests pass for every new table.

**L2: Applications, approvals and attendance/payroll integration.** Apply on behalf, approve/reject/cancel, half-day,
overlap/balance/back-date/finalised-month checks, sick-leave document rule, approved leave writes attendance marks,
roll-up and payroll treat paid leave as paid and LWP as unpaid, year-end rollover with dry-run.
*Accept:* every rule in section 6 has a test; approving then cancelling returns balance and attendance exactly to the
starting state; finalised months refuse changes; rollover dry-run equals the applied result.

**L3: Employee self-service.** Role `employee` with login (admin-issued credentials), own balances/calendar/applications,
attendance correction requests, in-app notifications, optional supervisor role.
*Accept:* an employee can read and change nothing outside their own records (tested with two employees in one
organization and with two organizations); lockout/CSRF/audit apply as for other users.

**L4: Reports, settlement, API.** Balance/register/summary/liability reports (screen + Excel), muster roll PDF/Excel,
exit-settlement view, leave balances on the payslip, API endpoints, README and runbook updates.

## 9. Legal and policy notes (verify with your consultant)

* The rules differ by Act: the **Factories Act** (annual leave by days worked, for covered factories), the **Haryana
  Shops and Establishments** rules, and company policy for casual/sick/comp-off. Sick-leave entitlement from the employer
  is separate from **ESI sickness benefit**. The Labour Codes may change entitlements and eligibility. Everything above
  is therefore **policy-driven configuration**, not hard-coded law; the defaults are placeholders.
* Compensatory-off and leave-encashment treatment (including tax) varies; the first version only *reports* encashable
  amounts and does not post them to payroll.
* Keep the employee's consent visible whenever excess leave is converted to unpaid leave.

---

## 10. The build prompt (copy-paste)

> **Task.** Add a Leave Management module to the existing HRMS (Flask + SQLAlchemy + Alembic, multi-organization,
> described in README.md). Implement it in the phases L1, L2, L3, L4 of `docs/LEAVE_MODULE_PLAN.md`, one phase at a
> time, showing me the result and test evidence after each phase before starting the next.
>
> **Product requirements.**
> 1. Leave types: Sick, Annual, Casual, Comp Off (admin-granted), plus Loss of Pay (unpaid, no balance).
> 2. Policies per salary category with a per-employee override of quota and ceiling. Each rule defines: quota per year;
>    accrual = yearly grant, monthly credit, 1 day per N days worked, or manual; first credit from joining or after a
>    waiting period; proration of the first year; carry-forward (none / all / up to N); maximum balance (ceiling);
>    optional expiry; whether negative balance is allowed.
> 3. Balances live in an append-only ledger; accrual and rollover jobs are idempotent, previewable (dry-run) and audited.
> 4. Leave year is an organization setting (calendar or April-March).
> 5. Leave works by application: apply (employee or admin on behalf) -> approve / reject / cancel; half-days (0.5);
>    overlap, balance, back-date and finalised-month checks; sick-leave document rule; optional sandwich rule.
> 6. Comp off: admin grants with reason, worked date, days, expiry; expired grants lapse automatically.
> 7. Holiday calendar and weekly-off rules per organization/category.
> 8. Attendance: per employee, per day of the year **from the date of joining**, editable by admins; approved leave writes
>    attendance marks (source = leave) and cancelling removes them; the monthly roll-up and payroll treat paid leave as
>    paid days and LWP as unpaid; employees can request corrections that an approver accepts or rejects.
> 9. Employee self-service role (`employee`) that sees and acts only on its own data; optional supervisor role.
> 10. Reports: balances, register, monthly summary, comp-off ledger, leave liability, printable attendance register.
>
> **Constraints.** Follow the existing patterns: tenant-scoped models, Alembic migrations (and the migration-matches-models
> test), `audit()` for every state change (no personal data in audit text), CSRF on all forms, admin-only checks via
> `admin_required`, the finalised-month lock, the `_macros.html` UI components and the existing design system, whole-rupee
> and half-day arithmetic with `Decimal`/0.5 steps, no new external services. Employee-facing queries must be scoped to the
> employee in addition to the organization.
>
> **Deliverables per phase.** Models + migration; services (accrual, rollover, application rules) separated from views;
> views and templates; tests for every rule in section 6 including isolation, idempotency, rollback of approve/cancel and
> finalised-month refusal; README and RUNBOOK updates; `ruff` clean; full suite green. Report anything not verified.
>
> **Out of scope for now.** Payroll posting of leave encashment, biometric integration, e-mail/WhatsApp delivery,
> shift scheduling.
