from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from planyourshot.web.db import get_db
from planyourshot.web.models import Script, User


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if user is None:
        request.session.clear()
        raise HTTPException(401, "Sign in to continue.")
    if user.disabled:
        raise HTTPException(403, "This account has been disabled.")
    return user


def owned_script(script_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Script:
    """Every script route resolves through here, so one user can never reach
    another user's data — a foreign id is indistinguishable from a missing one."""
    script = db.get(Script, script_id)
    if script is None or script.user_id != user.id:
        raise HTTPException(404, "Screenplay not found.")
    return script


def ready_script(script: Script = Depends(owned_script)) -> Script:
    if script.status != "ready":
        raise HTTPException(409, "This screenplay hasn't finished processing.")
    return script
