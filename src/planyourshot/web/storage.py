"""File storage for uploaded screenplays.

Files live outside the web root under STORAGE_DIR/<user_id>/<random>.<ext>;
the key is generated server-side, never taken from the client filename.
"""
import uuid
from pathlib import Path

from planyourshot.web.settings import get_settings


def _root() -> Path:
    root = get_settings().storage_dir
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def save(user_id: str, ext: str, data: bytes) -> str:
    key = f"{user_id}/{uuid.uuid4().hex}.{ext}"
    path = _root() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return key


def path_for(key: str) -> Path:
    path = (_root() / key).resolve()
    if _root() not in path.parents:
        raise ValueError("Invalid storage key")
    return path


def delete(key: str) -> None:
    try:
        path_for(key).unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def delete_user_dir(user_id: str) -> None:
    """Remove the user's upload directory and anything left in it."""
    import shutil

    try:
        path = path_for(user_id)
    except ValueError:
        return
    if path.is_dir() and path != _root():
        shutil.rmtree(path, ignore_errors=True)
