"""File storage behind an interface. Local disk now; an S3/MinIO class can implement the same."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from app.core.config import settings
from app.core.security import utcnow


class Storage(Protocol):
    def save(self, data: bytes, suffix: str) -> str: ...
    def open_chunks(self, key: str, chunk_size: int = 65536) -> Iterator[bytes]: ...
    def exists(self, key: str) -> bool: ...


class LocalStorage:
    """Files live outside any web root and are only ever streamed by the API."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("storage key escapes the storage root")
        return path

    def save(self, data: bytes, suffix: str) -> str:
        now = utcnow()
        key = f"{now:%Y}/{now:%m}/{uuid.uuid4().hex}{suffix}"
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # "x" mode: never overwrite an existing file.
        with open(path, "xb") as fh:
            fh.write(data)
        return key

    def open_chunks(self, key: str, chunk_size: int = 65536) -> Iterator[bytes]:
        with open(self._path(key), "rb") as fh:
            while chunk := fh.read(chunk_size):
                yield chunk

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


def scan_for_malware(data: bytes) -> bool:
    """Antivirus hook. NOT IMPLEMENTED: always passes. Wire ClamAV/ICAP here (Phase 3)."""
    return True


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = LocalStorage(settings.upload_path)
    return _storage
