"""Starting leave configuration for a new organization. These are PLACEHOLDERS: an admin edits them under
Leave -> Types & policies to match the company's policy and the applicable law."""

DEFAULT_TYPES = [
    dict(code="CL", name="Casual Leave", paid=True, uses_balance=True, half_day_ok=True, doc_after_days=None,
         encashable=False, active=True, sort_order=1),
    dict(code="SL", name="Sick Leave", paid=True, uses_balance=True, half_day_ok=True, doc_after_days=2,
         encashable=False, active=True, sort_order=2),
    dict(code="AL", name="Annual Leave", paid=True, uses_balance=True, half_day_ok=True, doc_after_days=None,
         encashable=True, active=True, sort_order=3),
    dict(code="CO", name="Comp Off", paid=True, uses_balance=True, half_day_ok=True, doc_after_days=None,
         encashable=False, active=True, sort_order=4),
    dict(code="LWP", name="Loss of Pay (unpaid)", paid=False, uses_balance=False, half_day_ok=True,
         doc_after_days=None, encashable=False, active=True, sort_order=5),
]

_BASE = dict(accrual_days=20, wait_months=0, prorate=True, carry_mode="none", carry_limit=0, max_balance=None,
             expires_days=None, allow_negative=False)

DEFAULT_RULES = {
    "CL": {**_BASE, "quota": 12, "accrual": "monthly", "max_balance": 12},
    "SL": {**_BASE, "quota": 12, "accrual": "yearly", "max_balance": 12},
    "AL": {**_BASE, "quota": 18, "accrual": "yearly", "carry_mode": "limit", "carry_limit": 10, "max_balance": 30},
    "CO": {**_BASE, "quota": 0, "accrual": "manual", "prorate": False, "expires_days": 60},
}
