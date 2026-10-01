import os
import uuid
from typing import Protocol

ALLOWED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
_CHUNK_SIZE = 64 * 1024


class UploadError(Exception):
    """An upload was rejected or could not be stored; the message is safe to show to the client."""


class UploadedFile(Protocol):
    """The part of an uploaded file the uploader needs (Starlette's UploadFile fits)."""

    filename: str | None
    size: int | None

    async def read(self, size: int = -1) -> bytes: ...


def extension(filename: str) -> str:
    """The extension of the last path element, including the dot, like Go's filepath.Ext."""
    for i in range(len(filename) - 1, -1, -1):
        if filename[i] == "/":
            break
        if filename[i] == ".":
            return filename[i:]
    return ""


class Uploader:
    def __init__(self, base_dir: str, max_size: int) -> None:
        self._base_dir = base_dir
        self._max_size = max_size

    async def upload(self, file: UploadedFile, sub_dir: str) -> str:
        """Saves the file under sub_dir and returns its path relative to the base directory."""
        if (file.size or 0) > self._max_size:
            raise UploadError(f"file size exceeds maximum allowed size of {self._max_size} bytes")

        ext = extension(file.filename or "").lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise UploadError(f"file extension {ext} is not allowed")

        directory = os.path.join(self._base_dir, sub_dir)
        try:
            os.makedirs(directory, mode=0o755, exist_ok=True)
        except OSError as e:
            raise UploadError(f"failed to create upload directory: {e}") from e

        filename = f"{uuid.uuid4()}{ext}"
        file_path = os.path.join(directory, filename)
        try:
            dst = open(file_path, "wb")  # noqa: SIM115 - closed explicitly below, as in the Go service
        except OSError as e:
            raise UploadError(f"failed to create file: {e}") from e

        try:
            while chunk := await file.read(_CHUNK_SIZE):
                dst.write(chunk)
        except OSError as e:
            dst.close()
            os.remove(file_path)
            raise UploadError(f"failed to write file: {e}") from e

        try:
            dst.close()
        except OSError as e:
            os.remove(file_path)
            raise UploadError(f"failed to finalize file: {e}") from e

        return os.path.join(sub_dir, filename)

    def delete(self, relative_path: str) -> None:
        """Deletes a previously uploaded file; a file that is already gone is not an error."""
        try:
            os.remove(os.path.join(self._base_dir, relative_path))
        except FileNotFoundError:
            pass
        except OSError as e:
            raise UploadError(f"failed to delete file: {e}") from e
