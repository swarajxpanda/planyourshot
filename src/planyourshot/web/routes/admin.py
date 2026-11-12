"""Operator dashboard data. Only emails listed in ADMIN_EMAILS get anything back;
everyone else, signed in or not, gets 404 as if the routes didn't exist."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from planyourshot.web import jobs
from planyourshot.web.db import get_db
from planyourshot.web.models import Job, Script, UsageEvent, User, utcnow
from planyourshot.web.serialize import ts
from planyourshot.web.settings import get_settings

router = APIRouter(prefix="/api/admin", tags=["admin"], include_in_schema=False)

# shot_generate_full is a bookkeeping row for a whole run; its scenes are the model calls.
MODEL_CALL = UsageEvent.kind != "shot_generate_full"


def admin_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if user is None or user.disabled or user.email.lower() not in get_settings().admin_emails:
        raise HTTPException(404, "Not found")
    return user


def _user_row(u: User, calls: int = 0, chars: int = 0) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "plan": u.plan, "disabled": u.disabled,
            "calls": int(calls or 0), "chars": int(chars or 0), "created_at": ts(u.created_at),
            "last_login_at": ts(u.last_login_at)}


@router.get("/overview")
def overview(days: int = 14, _: User = Depends(admin_user), db: Session = Depends(get_db)):
    days = max(1, min(days, 90))
    since = utcnow() - timedelta(days=days)
    day = func.date(UsageEvent.created_at)
    per_day = db.execute(
        select(day, func.count(), func.sum(case((UsageEvent.status == "failed", 1), else_=0)),
               func.sum(case((UsageEvent.status == "refused", 1), else_=0)))
        .where(UsageEvent.created_at >= since, MODEL_CALL).group_by(day).order_by(day)
    ).all()

    week = utcnow() - timedelta(days=7)
    chars = func.sum(UsageEvent.input_chars + UsageEvent.output_chars)
    top = db.execute(
        select(User, func.count(UsageEvent.id), chars).join(UsageEvent, UsageEvent.user_id == User.id)
        .where(UsageEvent.created_at >= week, MODEL_CALL).group_by(User.id)
        .order_by(func.count(UsageEvent.id).desc()).limit(20)
    ).all()

    failed = db.execute(
        select(Job, User.email).join(User, User.id == Job.user_id, isouter=True)
        .where(Job.status == "failed").order_by(Job.updated_at.desc()).limit(20)
    ).all()
    status_counts = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())

    return {
        "days": [{"day": str(d), "calls": int(n), "failed": int(f or 0), "refused": int(r or 0)}
                 for d, n, f, r in per_day],
        "queue": {**jobs.queue_health(db), "failed_total": status_counts.get("failed", 0),
                  "by_status": status_counts},
        "failed_jobs": [{"id": j.id, "kind": j.kind, "user_email": email, "script_id": j.script_id,
                         "attempts": j.attempts, "error": j.error, "updated_at": ts(j.updated_at)}
                        for j, email in failed],
        "top_users": [_user_row(u, n, c) for u, n, c in top],
        "totals": {"users": db.scalar(select(func.count()).select_from(User)) or 0,
                   "scripts": db.scalar(select(func.count()).select_from(Script)) or 0},
    }


@router.get("/users")
def find_users(q: str = "", _: User = Depends(admin_user), db: Session = Depends(get_db)):
    q = q.strip()
    query = select(User).order_by(User.created_at.desc()).limit(25)
    if q:
        like = f"%{q.lower()}%"
        query = query.where(or_(func.lower(User.email).like(like), func.lower(User.name).like(like)))
    return {"users": [_user_row(u) for u in db.scalars(query)]}


class DisabledIn(BaseModel):
    disabled: bool


@router.post("/users/{user_id}/disabled")
def set_disabled(user_id: str, body: DisabledIn, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "User not found.")
    if target.id == admin.id and body.disabled:
        raise HTTPException(400, "You can't disable your own account.")
    target.disabled = body.disabled
    db.commit()
    return _user_row(target)
