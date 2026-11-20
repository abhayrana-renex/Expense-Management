"""Import every model so Alembic and the mapper registry see them."""

from app.models.audit import AuditLog, IdempotencyRecord
from app.models.base import Base
from app.models.contract import Contract
from app.models.engagement import Delegation, Notification
from app.models.expense import (
    ApprovalStep,
    BillExtraction,
    Expense,
    ExpenseDocument,
    ExpenseHistory,
    ExpenseItem,
)
from app.models.far import Assignee, Branch, Department, Partner
from app.models.masters import (
    ApprovalLevel,
    ApprovalRule,
    BillMonth,
    BranchProfile,
    CostCentre,
    ExpenseType,
    GstRate,
    VendorProfile,
)
from app.models.rbac import Permission, Role, RolePermission
from app.models.user import AuthSession, PasswordHistory, User

__all__ = [
    "Assignee",
    "BranchProfile",
    "Partner",
    "VendorProfile",
    "ApprovalLevel",
    "ApprovalRule",
    "ApprovalStep",
    "AuditLog",
    "AuthSession",
    "Delegation",
    "Notification",
    "BillExtraction",
    "Base",
    "BillMonth",
    "Branch",
    "Contract",
    "CostCentre",
    "Department",
    "Expense",
    "ExpenseDocument",
    "ExpenseHistory",
    "ExpenseItem",
    "ExpenseType",
    "GstRate",
    "IdempotencyRecord",
    "PasswordHistory",
    "Permission",
    "Role",
    "RolePermission",
    "User",
]
