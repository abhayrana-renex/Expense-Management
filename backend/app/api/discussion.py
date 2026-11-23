"""Discussion on an expense: questions and replies between the people in its process."""

from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import Principal, get_principal
from app.core.errors import AppError
from app.schemas.common import ERROR_RESPONSES
from app.services import discussion_service
from app.services.storage import get_storage

# Registered before the expenses router so POST /expenses/{id}/messages is not taken for a
# workflow action.
router = APIRouter(prefix="/expenses", tags=["Discussion"], responses=ERROR_RESPONSES)


class Person(BaseModel):
    id: int
    full_name: str
    role: str


class MessageFile(BaseModel):
    id: int
    name: str
    content_type: str
    size_bytes: int


class MessageOut(BaseModel):
    id: int
    author: Person
    body: str
    created_at: datetime
    mentions: list[Person]
    files: list[MessageFile]


class ThreadOut(BaseModel):
    can_post: bool
    participants: list[Person]
    messages: list[MessageOut]


@router.get(
    "/{expense_id}/messages",
    response_model=ThreadOut,
    summary="Discussion on this expense",
    description="Only the people in the expense's process (initiator, approvers on its route, "
    "anyone acting for them) and the audit team (read only). Everyone else: 404.",
)
def get_thread(
    expense_id: int, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> dict:
    return discussion_service.thread(db, principal, expense_id)


@router.post(
    "/{expense_id}/messages",
    response_model=MessageOut,
    status_code=201,
    summary="Ask a question or reply",
    description="Multipart: `body`, `mentions` (comma-separated user ids - only people in the "
    f"process), up to {discussion_service.MAX_FILES} `files` (PDF, JPG or PNG, max "
    f"{settings.MAX_UPLOAD_SIZE_MB} MB each). Messages are permanent; each mention is notified.",
)
async def post_message(
    expense_id: int,
    body: str = Form(""),
    mentions: str = Form(""),
    files: list[UploadFile] = File(default_factory=list),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict:
    try:
        ids = [int(x) for x in mentions.split(",") if x.strip()]
    except ValueError as err:
        raise AppError(422, "VALIDATION_ERROR", "mentions must be user ids") from err
    if len(files) > discussion_service.MAX_FILES:
        raise AppError(
            422, "VALIDATION_ERROR", f"Attach at most {discussion_service.MAX_FILES} files"
        )
    data = [(f.filename or "", await f.read(settings.max_upload_bytes + 1)) for f in files]
    return discussion_service.post(db, principal, expense_id, body, ids, data)


@router.get(
    "/{expense_id}/messages/files/{file_id}",
    summary="Download a file attached to the discussion",
)
def download_file(
    expense_id: int,
    file_id: int,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    f = discussion_service.file_for_download(db, principal, expense_id, file_id)
    safe = "".join(c for c in f.original_filename if c.isalnum() or c in "._- ") or "file"
    return StreamingResponse(
        get_storage().open_chunks(f.storage_key),
        media_type=f.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{safe}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )
