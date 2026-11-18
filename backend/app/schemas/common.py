"""Shared schema types: money as 2-decimal strings, pagination, error envelope."""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer

TWO_PLACES = Decimal("0.01")


def to_money(value: Any) -> Decimal:
    """Parse to Decimal(2dp, ROUND_HALF_UP). Floats are rejected: send strings or integers."""
    if isinstance(value, float):
        raise ValueError('money must be sent as a string (e.g. "1234.50"), not a float')
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid amount") from exc
    if not dec.is_finite():
        raise ValueError("invalid amount")
    if dec.as_tuple().exponent < -2:  # type: ignore[operator]
        raise ValueError("amount may have at most 2 decimal places")
    return dec.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def money_str(value: Decimal) -> str:
    return f"{Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP):.2f}"


Money = Annotated[
    Decimal,
    BeforeValidator(to_money),
    PlainSerializer(money_str, return_type=str, when_used="json"),
    Field(json_schema_extra={"type": "string", "pattern": r"^-?\d+\.\d{2}$", "example": "1250.00"}),
]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorItem(BaseModel):
    loc: list[str | int] | None = None
    msg: str | None = None
    type: str | None = None


class ErrorResponse(BaseModel):
    detail: str
    code: str
    errors: list[dict[str, Any]] = []
    request_id: str | None = None


class Message(BaseModel):
    message: str


class Ref(ORMModel):
    id: int
    code: str | int | None = None
    name: str | None = None


class UserRef(ORMModel):
    id: int
    employee_code: str
    full_name: str


SortOrder = Literal["asc", "desc"]


class PageParams(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(25, ge=1, le=200)
    sort: str | None = None
    order: SortOrder = "desc"

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class Timestamped(ORMModel):
    created_at: datetime
    updated_at: datetime


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Not authenticated"},
    403: {"model": ErrorResponse, "description": "Forbidden or password change required"},
    422: {"model": ErrorResponse, "description": "Validation error"},
}
