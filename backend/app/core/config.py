"""Application settings, loaded only from environment variables / backend/.env."""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # ---------- App ----------
    APP_NAME: str = "Expense Management Portal"
    APP_ENV: str = "development"
    API_V1_PREFIX: str = "/api"

    # ---------- Database ----------
    DATABASE_URL: str | None = None
    POSTGRES_USER: str = "expense_admin"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = "expense_portal"
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5433
    # Schema the portal's own tables live in (company_db: "expense"). Tests use "test_suite".
    DB_SCHEMA: str = "expense"
    # Schema holding the masters shared with FAR (company_db: "core"). Tests use "test_core".
    MASTER_SCHEMA: str = "core"

    # ---------- Auth ----------
    JWT_SECRET_KEY: str = Field(min_length=32)
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_HOURS: int = 12
    IDLE_TIMEOUT_MINUTES: int = 30
    LOGIN_MAX_FAILED_ATTEMPTS: int = 5
    LOGIN_LOCKOUT_MINUTES: int = 15
    LOGIN_RATE_LIMIT_PER_MINUTE: int = 20
    PASSWORD_MIN_LENGTH: int = 10
    PASSWORD_HISTORY_COUNT: int = 5
    PASSWORD_EXPIRY_DAYS: int = 90  # 0 disables expiry

    # ---------- Files ----------
    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE_MB: int = 10
    # Outlook .msg / .eml e-mails carrying bills (attachments are still MAX_UPLOAD_SIZE_MB)
    MAX_EMAIL_SIZE_MB: int = 25
    EMAIL_MAX_BILLS: int = 10
    # Bill uploads (each one may run OCR) per person per minute
    BILL_READ_RATE_LIMIT_PER_MINUTE: int = 40
    SIGNED_URL_TTL_SECONDS: int = 60

    # ---------- HTTP ----------
    CORS_ORIGINS: str = "http://localhost:4200"

    # ---------- Feature flags ----------
    FEATURE_TOTP_2FA: bool = False
    ENFORCE_GST_RULES: bool = False
    FEATURE_BILL_READER: bool = True
    # band: one approver by amount. chain: every level above its threshold. matrix: every level
    # whose amount range (threshold, limit] covers the total, in order (the company matrix).
    APPROVAL_ROUTING: Literal["band", "chain", "matrix"] = "band"
    # New expense pre-fill for everyone (FAR names, any case). Empty = the person's own
    # department / branch. The cost centre follows the department.
    DEFAULT_DEPARTMENT: str = ""
    DEFAULT_BRANCH: str = ""
    # Bulk approve is only offered for claims at or below this amount (and with no warnings).
    BULK_APPROVE_MAX_AMOUNT: Decimal = Decimal("50000.00")

    # ---------- Bill reader (QR + PDF text + OCR) ----------
    # Our own GSTINs (comma-separated): tells the reader which GSTIN on a bill is the buyer.
    COMPANY_GSTINS: str = ""
    # Our company name(s) (comma-separated) so they are never mistaken for the vendor.
    COMPANY_NAMES: str = ""
    # Our PAN(s): every GSTIN carrying it (any state) is ours, i.e. the buyer on a bill.
    COMPANY_PANS: str = ""
    BILL_READER_MAX_PAGES: int = 4

    # ---------- Seed ----------
    SEED_ADMIN_USERNAME: str = "admin"
    SEED_ADMIN_PASSWORD: str | None = None
    SEED_ADMIN_EMAIL: str | None = None
    SEED_DEMO_PASSWORD: str | None = None

    @property
    def sqlalchemy_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"postgresql+psycopg2://{quote_plus(self.POSTGRES_USER)}:"
            f"{quote_plus(self.POSTGRES_PASSWORD)}@{self.POSTGRES_SERVER}:"
            f"{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def company_gstins(self) -> set[str]:
        return {g.strip().upper() for g in self.COMPANY_GSTINS.split(",") if g.strip()}

    @property
    def company_pans(self) -> set[str]:
        return {p.strip().upper() for p in self.COMPANY_PANS.split(",") if p.strip()}

    @property
    def company_names(self) -> list[str]:
        return [n.strip() for n in self.COMPANY_NAMES.split(",") if n.strip()]

    @property
    def upload_path(self) -> Path:
        path = Path(self.UPLOAD_DIR)
        return path if path.is_absolute() else (BACKEND_DIR / path).resolve()

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    @property
    def max_email_bytes(self) -> int:
        return self.MAX_EMAIL_SIZE_MB * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
