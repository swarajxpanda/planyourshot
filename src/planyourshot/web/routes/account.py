"""The signed-in user's own account: deletion and data export."""
import io
import json
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from planyourshot.web import jobs, serialize, storage
from planyourshot.web.db import get_db
from planyourshot.web.deps import current_user
from planyourshot.web.models import Job, Lease, Message, Proposal, RateLimitHit, Revision, Script, Shot, UsageEvent, User, utcnow
from planyourshot.web.ratelimit import enforce
from planyourshot.web.routes.shots import safe_name, shots_csv
from planyourshot.web.serialize import ts

router = APIRouter(prefix="/api/me", tags=["account"])


def purge_script(db: Session, script_id: str) -> None:
    """Delete a screenplay and everything under it, children first (explicit, so it
    doesn't depend on ORM cascade order or on SQLite's foreign-key pragma)."""
    jobs.cancel_for_script(db, script_id)
    db.execute(delete(Proposal).where(Proposal.script_id == script_id))
    db.execute(delete(Message).where(Message.script_id == script_id))
    db.execute(delete(Shot).where(Shot.script_id == script_id))
    db.execute(delete(Revision).where(Revision.script_id == script_id))
    db.execute(delete(Script).where(Script.id == script_id))


class DeleteAccountIn(BaseModel):
    confirm: str


@router.delete("", status_code=204)
def delete_account(body: DeleteAccountIn, request: Request, user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    """Delete the account and everything it owns: screenplays and their files, versions,
    conversations, suggestions, shots, jobs and usage history."""
    if body.confirm != "DELETE":
        raise HTTPException(422, 'Type DELETE to confirm.')
    uid = user.id
    scripts = list(db.scalars(select(Script).where(Script.user_id == uid)))
    keys = [s.storage_key for s in scripts]
    for s in scripts:
        purge_script(db, s.id)
    db.execute(delete(Job).where(Job.user_id == uid))  # a running worker stops at its next checkpoint
    db.execute(delete(UsageEvent).where(UsageEvent.user_id == uid))
    db.execute(delete(RateLimitHit).where(RateLimitHit.key.like(f"%:{uid}")))
    db.execute(delete(Lease).where(Lease.name == f"user:{uid}"))
    db.execute(delete(User).where(User.id == uid))
    db.commit()
    for key in keys:
        storage.delete(key)
    storage.delete_user_dir(uid)
    request.session.clear()


def _conversation(db: Session, script_id: str, channel: str) -> list[dict]:
    msgs = db.scalars(select(Message).where(Message.script_id == script_id, Message.channel == channel)
                      .order_by(Message.id)).all()
    props = {p.message_id: p for p in db.scalars(select(Proposal).where(Proposal.script_id == script_id))}
    out = []
    for m in msgs:
        d = serialize.message(m, props.get(m.id))
        d.pop("id", None)
        out.append(d)
    return out


@router.get("/export")
def export_data(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """A zip of everything: each screenplay's current text, its shot list as CSV, and its
    conversations as JSON. Available on every plan."""
    enforce(f"export:{user.id}", 10, 3600, "Too many exports in the last hour. Try again in {wait}.")
    scripts = list(db.scalars(select(Script).where(Script.user_id == user.id).order_by(Script.created_at)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("account.json", json.dumps({
            "name": user.name, "email": user.email, "plan": user.plan, "created_at": ts(user.created_at),
            "terms_accepted_at": ts(user.terms_accepted_at), "exported_at": ts(utcnow()),
            "screenplays": len(scripts),
        }, indent=2, ensure_ascii=False))
        for i, s in enumerate(scripts, 1):
            folder = f"{i:02d} {safe_name(s.title, 'screenplay')}/"
            rev = db.get(Revision, s.current_revision_id) if s.current_revision_id else None
            if rev is not None:
                z.writestr(folder + "screenplay.txt", rev.content)
            z.writestr(folder + "shots.csv", shots_csv(db, s.id))
            z.writestr(folder + "conversations.json", json.dumps({
                "title": s.title, "original_filename": s.original_filename, "status": s.status,
                "current_version": rev.number if rev else None,
                "screenplay": _conversation(db, s.id, "script"),
                "shot_list": _conversation(db, s.id, "shots"),
            }, indent=2, ensure_ascii=False))
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="plan-your-shot-export.zip"'})
