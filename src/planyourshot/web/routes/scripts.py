import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from planyourshot.web import jobs, parsing, serialize, storage
from planyourshot.web.parsing import clean_text
from planyourshot.web.routes.account import purge_script
from planyourshot.web.db import get_db
from planyourshot.web.deps import current_user, owned_script, ready_script
from planyourshot.web.edits import add_revision, current_revision
from planyourshot.web.limits import check_edit_length, limits_for, plan_name
from planyourshot.web.models import Revision, Script, Shot, User, utcnow
from planyourshot.web.ratelimit import upload_rate_limit
from planyourshot.web.scenes import split_scenes
from planyourshot.web.settings import get_settings

router = APIRouter(prefix="/api/scripts", tags=["scripts"])

_MEDIA = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain; charset=utf-8",
}


def _counts(db: Session, ids: list[str]) -> tuple[dict, dict]:
    if not ids:
        return {}, {}
    shots = dict(db.execute(select(Shot.script_id, func.count()).where(Shot.script_id.in_(ids)).group_by(Shot.script_id)).all())
    revs = dict(db.execute(select(Revision.script_id, func.count()).where(Revision.script_id.in_(ids)).group_by(Revision.script_id)).all())
    return shots, revs


def _one(db: Session, s: Script) -> dict:
    shots, revs = _counts(db, [s.id])
    return serialize.script(s, shot_count=shots.get(s.id, 0), revision_count=revs.get(s.id, 0))


@router.get("")
def list_scripts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    scripts = db.scalars(select(Script).where(Script.user_id == user.id).order_by(Script.created_at.desc())).all()
    shots, revs = _counts(db, [s.id for s in scripts])
    return {
        "scripts": [serialize.script(s, shot_count=shots.get(s.id, 0), revision_count=revs.get(s.id, 0)) for s in scripts],
        "limit": limits_for(user).max_scripts,
    }


@router.post("", status_code=201, dependencies=[Depends(upload_rate_limit)])
async def upload_script(file: UploadFile, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = get_settings()
    name = Path(file.filename or "screenplay").name
    ext = Path(name).suffix.lower().lstrip(".")
    if ext not in parsing.SUPPORTED:
        raise HTTPException(415, "Unsupported file type. Upload a PDF, DOCX or TXT screenplay.")

    def _count() -> int:
        return db.scalar(select(func.count()).select_from(Script).where(Script.user_id == user.id)) or 0

    plan = limits_for(user)
    limit_msg = (f"You've reached the limit of {plan.max_scripts} screenplays on the {plan_name(plan)} plan. "
                 "Delete one to upload another.")
    if _count() >= plan.max_scripts:
        raise HTTPException(409, limit_msg)

    limit = s.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File is too large. The limit is {s.max_upload_mb} MB.")
    if not data:
        raise HTTPException(400, "The file is empty.")
    try:
        parsing.sniff(ext, data)
    except parsing.ParseError as exc:
        raise HTTPException(415, str(exc)) from exc

    title = re.sub(r"[_\-]+", " ", Path(name).stem).strip()[:200] or "Untitled screenplay"
    key = storage.save(user.id, ext, data)
    script = Script(
        user_id=user.id, title=title, original_filename=name[:255], file_type=ext, file_size=len(data), storage_key=key
    )
    db.add(script)
    db.commit()
    # Re-check after insert so two simultaneous uploads can't slip past the limit.
    if _count() > plan.max_scripts:
        db.delete(script)
        db.commit()
        storage.delete(key)
        raise HTTPException(409, limit_msg)

    jobs.enqueue(db, "process_upload", user_id=user.id, script_id=script.id, total=1)
    db.commit()
    jobs.notify()
    db.refresh(script)
    return _one(db, script)


@router.get("/{script_id}")
def get_script(script: Script = Depends(owned_script), db: Session = Depends(get_db)):
    out = _one(db, script)
    if script.status == "ready":
        rev = current_revision(db, script)
        out["revision"] = serialize.revision(rev, content=True)
        out["scenes"] = [s.outline() for s in split_scenes(rev.content)]
    return out


class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


@router.patch("/{script_id}")
def rename_script(body: Rename, script: Script = Depends(owned_script), db: Session = Depends(get_db)):
    title = clean_text(body.title).strip()
    if not title:
        raise HTTPException(422, "Title can't be empty.")
    script.title, script.updated_at = title, utcnow()
    db.commit()
    return _one(db, script)


@router.delete("/{script_id}", status_code=204)
def delete_script(script: Script = Depends(owned_script), db: Session = Depends(get_db)):
    key = script.storage_key
    purge_script(db, script.id)  # also cancels its queued and running jobs
    db.commit()
    storage.delete(key)


@router.post("/{script_id}/reprocess")
def reprocess(script: Script = Depends(owned_script), db: Session = Depends(get_db)):
    if script.status != "failed":
        raise HTTPException(409, "Only a screenplay that failed processing can be retried.")
    script.status, script.error = "processing", ""
    jobs.enqueue(db, "process_upload", user_id=script.user_id, script_id=script.id, total=1)
    db.commit()
    jobs.notify()
    db.refresh(script)
    return _one(db, script)


@router.get("/{script_id}/file")
def download_original(script: Script = Depends(owned_script)):
    path = storage.path_for(script.storage_key)
    if not path.exists():
        raise HTTPException(404, "The original file is no longer available.")
    return FileResponse(path, media_type=_MEDIA[script.file_type], filename=script.original_filename)


# ---------- revisions ----------

@router.get("/{script_id}/revisions")
def list_revisions(script: Script = Depends(ready_script), db: Session = Depends(get_db)):
    revs = db.scalars(select(Revision).where(Revision.script_id == script.id).order_by(Revision.number.desc())).all()
    return {"revisions": [serialize.revision(r) for r in revs], "current_revision_id": script.current_revision_id}


def _revision(db: Session, script: Script, revision_id: int) -> Revision:
    rev = db.get(Revision, revision_id)
    if rev is None or rev.script_id != script.id:
        raise HTTPException(404, "Revision not found.")
    return rev


@router.get("/{script_id}/revisions/{revision_id}")
def get_revision(revision_id: int, script: Script = Depends(ready_script), db: Session = Depends(get_db)):
    return serialize.revision(_revision(db, script, revision_id), content=True)


@router.post("/{script_id}/revisions/{revision_id}/restore")
def restore_revision(revision_id: int, script: Script = Depends(ready_script), db: Session = Depends(get_db)):
    rev = _revision(db, script, revision_id)
    if rev.id == script.current_revision_id:
        raise HTTPException(409, "This is already the current version.")
    label = "the original" if rev.number == 1 else f"version {rev.number}"
    add_revision(db, script, rev.content, "restore", f"Restored {label}")
    db.commit()
    return get_script(script, db)


class ContentUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=2_000_000)
    summary: str = Field(default="", max_length=300)


@router.put("/{script_id}/content")
def save_content(body: ContentUpdate, script: Script = Depends(ready_script), user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    content = parsing.normalize(body.content)
    if not content:
        raise HTTPException(422, "The screenplay can't be empty.")
    before = current_revision(db, script).content
    if content == before:
        raise HTTPException(409, "No changes to save.")
    if error := check_edit_length(limits_for(user), len(before), len(content)):
        raise HTTPException(422, error)
    add_revision(db, script, content, "manual", body.summary.strip() or "Manual edit")
    db.commit()
    return get_script(script, db)
