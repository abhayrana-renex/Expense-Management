"""Permission catalogue: code -> (description, module, scopes that make sense for it).

The Roles & rights screen and the seed both read this. Data scopes:
OWN (own records) < ASSIGNED (own + routed to me) < DEPARTMENT < ALL.
"""

ALL_SCOPES = ("OWN", "ASSIGNED", "DEPARTMENT", "ALL")

PERMISSIONS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "expense.create": ("Create, edit, submit and cancel own expenses", "Expenses", ("OWN",)),
    "expense.read": ("See expenses", "Expenses", ALL_SCOPES),
    "expense.approve": (
        "Approve / reject / return expenses routed to them",
        "Approvals",
        ("ASSIGNED",),
    ),
    "expense.correct": (
        "Correct bill details while it is with them (Finance check)",
        "Approvals",
        ("ASSIGNED",),
    ),
    "expense.reopen": ("Reopen a rejected expense", "Approvals", ("ALL",)),
    "payment.schedule": (
        "Schedule approved claims for payment (payment schedule)",
        "Payments",
        ("DEPARTMENT", "ALL"),
    ),
    "payment.approve": (
        "Approve scheduled payments (payment approver)",
        "Payments",
        ("ALL",),
    ),
    "payment.record": (
        "Download the bank file and record payments (UTR, paid, on hold)",
        "Payments",
        ("DEPARTMENT", "ALL"),
    ),
    "ledger.export": ("Download the general ledger", "Payments", ("ALL",)),
    "report.read": ("Run reports", "Reports", ALL_SCOPES),
    "report.export": ("Export reports to Excel / CSV", "Reports", ("OWN", "DEPARTMENT", "ALL")),
    "report.dump": ("Export the line-level admin dump", "Reports", ("ALL",)),
    "master.read": ("See master data (dropdowns)", "Masters", ("ALL",)),
    "master.manage": (
        "Create / edit / deactivate master data (shared with FAR)",
        "Masters",
        ("ALL",),
    ),
    "approval_rule.manage": ("Manage approval levels and policies", "Approvals", ("ALL",)),
    "user.manage": ("Manage users and logins", "Administration", ("ALL",)),
    "role.manage": ("Create roles and change role rights", "Administration", ("ALL",)),
    "audit.read": ("Read, export and verify the audit log", "Administration", ("ALL",)),
}
