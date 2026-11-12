import csv
import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from planyourshot.web import jobs, retrieval, serialize, tasks, usage
from planyourshot.web.ai import REFUSAL, SHOT_SIZES, AIError, AIService, ShotPatch, get_ai
from planyourshot.web.db import get_db
from planyourshot.web.deps import current_user, ready_script
from planyourshot.web.edits import current_revision
from planyourshot.web.limits import limits_for, plan_name
from planyourshot.web.models import Script, Shot, User, utcnow
from planyourshot.web.parsing import clean_text
from planyourshot.web.routes.chat import recent_history, save_exchange
from planyourshot.web.scenes import SceneInfo, split_scenes
from planyourshot.web.usage import ModelGuard, ai_error, model_access

router = APIRouter(prefix="/api/scripts/{script_id}/shots", tags=["shots"])

MAX_SCENES_PER_RUN = 150


def _scenes(db: Session, script: Script) -> list[SceneInfo]:
    return split_scenes(current_revision(db, script).content)


def _scene_shots(db: Session, script_id: str, scene_number: int) -> list[Shot]:
    return list(db.scalars(
        select(Shot).where(Shot.script_id == script_id, Shot.scene_number == scene_number).order_by(Shot.position, Shot.id)
    ))


def _all(db: Session, script_id: str) -> list[Shot]:
    return list(db.scalars(select(Shot).where(Shot.script_id == script_id).order_by(Shot.scene_number, Shot.position, Shot.id)))


def _resequence(shots: list[Shot]) -> None:
    for i, s in enumerate(shots, 1):
        s.position = i


def _insert(db: Session, shot: Shot, index: int | None) -> None:
    """Place `shot` at 0-based `index` within its scene (None = end)."""
    siblings = [s for s in _scene_shots(db, shot.script_id, shot.scene_number) if s is not shot and s.id != shot.id]
    index = len(siblings) if index is None else max(0, min(index, len(siblings)))
    siblings.insert(index, shot)
    _resequence(siblings)


def _shot(db: Session, script: Script, shot_id: int) -> Shot:
    shot = db.get(Shot, shot_id)
    if shot is None or shot.script_id != script.id:
        raise HTTPException(404, "Shot not found.")
    return shot


def _scene(scenes: list[SceneInfo], number: int) -> SceneInfo:
    for s in scenes:
        if s.number == number:
            return s
    raise HTTPException(422, f"Scene {number} doesn't exist in this screenplay.")


def _payload(db: Session, script: Script) -> dict:
    return {
        "shots": [serialize.shot(s) for s in _all(db, script.id)],
        "status": script.shotlist_status,
        "error": script.shotlist_error,
        "done": script.shotlist_done,
        "total": script.shotlist_total,
    }


def _line(s: Shot) -> str:
    return (
        f"#{s.id} | scene {s.scene_number} shot {s.position} | {s.size} | {s.angle or '-'} | {s.movement or '-'} | "
        f"{s.lens or '-'} | {s.description} | composition: {s.composition or '-'} | notes: {s.notes or '-'}"
    )


def _touch(script: Script) -> None:
    script.updated_at = utcnow()
    if script.shotlist_status in ("none", "failed"):
        script.shotlist_status, script.shotlist_error = "ready", ""


@router.get("")
def list_shots(script: Script = Depends(ready_script), db: Session = Depends(get_db)):
    return _payload(db, script)


class GenerateIn(BaseModel):
    scene_number: int | None = None  # None = the whole screenplay


def generation_access(
    body: GenerateIn, script: Script = Depends(ready_script), user: User = Depends(current_user),
    db: Session = Depends(get_db), ai: AIService = Depends(get_ai),
):
    """model_access for /generate, whose quota depends on the body: a whole shot list
    or a single scene."""
    kind = "shot_generate_full" if body.scene_number is None else "shot_generate_scene"
    yield from usage.admit(db, user, script, ai, kind, body=body)


@router.post("/generate", status_code=202)
def generate(guard: ModelGuard = Depends(generation_access), db: Session = Depends(get_db)):
    script, body = guard.script, guard.body
    if script.shotlist_status == "generating":
        raise HTTPException(409, "A shot list is already being generated.")
    scenes = _scenes(db, script)
    if body.scene_number is not None:
        _scene(scenes, body.scene_number)
    elif len(scenes) > MAX_SCENES_PER_RUN:
        raise HTTPException(
            422, f"This screenplay has {len(scenes)} scenes. A shot list can be generated for up to "
            f"{MAX_SCENES_PER_RUN} scenes at a time — generate scenes one by one instead.")
    # The run is counted against the monthly quota as soon as it is accepted, and
    # given back if no scene succeeds.
    run_id = usage.record(guard.user.id, script.id, "shot_generate_full", "ok") if body.scene_number is None else None
    script.shotlist_status, script.shotlist_error = "generating", ""
    script.shotlist_done, script.shotlist_total = 0, 1 if body.scene_number else len(scenes)
    jobs.enqueue(db, "generate_shots", user_id=guard.user.id, script_id=script.id, total=script.shotlist_total,
                 payload=tasks.shots_payload(script.current_revision_id, body.scene_number, run_id))
    db.commit()
    # The job takes the user's slot per scene; free it now so the run can start.
    guard.release()
    jobs.notify()
    db.refresh(script)
    return _payload(db, script)


class ShotIn(BaseModel):
    scene_number: int
    index: int | None = None
    size: str = "MS"
    angle: str = Field(default="", max_length=80)
    movement: str = Field(default="", max_length=80)
    lens: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=4000)
    composition: str = Field(default="", max_length=4000)
    characters: list[str] = []
    props: list[str] = []
    notes: str = Field(default="", max_length=4000)


def _check_size(size: str) -> str:
    size = size.strip().upper()
    if size not in SHOT_SIZES:
        raise HTTPException(422, f"Shot size must be one of {', '.join(SHOT_SIZES)}.")
    return size


def _clean_list(items: list[str]) -> list[str]:
    return [clean_text(i).strip()[:80] for i in items if i and i.strip()][:30]


@router.post("", status_code=201)
def add_shot(body: ShotIn, script: Script = Depends(ready_script), db: Session = Depends(get_db)):
    scene = _scene(_scenes(db, script), body.scene_number)
    shot = Shot(script_id=script.id, scene_number=scene.number, scene_heading=scene.heading[:300], position=0,
                location=(scene.location or "")[:300], characters=[], props=[])
    _apply_fields(shot, body.model_dump(exclude={"scene_number", "index"}))
    db.add(shot)
    db.flush()
    _insert(db, shot, body.index)
    _touch(script)
    db.commit()
    return _payload(db, script)


class ShotUpdate(BaseModel):
    size: str | None = None
    angle: str | None = Field(default=None, max_length=80)
    movement: str | None = Field(default=None, max_length=80)
    lens: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=4000)
    composition: str | None = Field(default=None, max_length=4000)
    characters: list[str] | None = None
    props: list[str] | None = None
    notes: str | None = Field(default=None, max_length=4000)


def _apply_fields(shot: Shot, data: dict) -> None:
    for key, value in data.items():
        if value is None:
            continue
        if key == "size":
            value = _check_size(value)
        elif key in ("characters", "props"):
            value = _clean_list(value)
        elif isinstance(value, str):
            value = clean_text(value)
            if key in ("angle", "movement", "lens"):
                value = value[:80]
        setattr(shot, key, value)
    shot.updated_at = utcnow()


@router.patch("/{shot_id}")
def update_shot(shot_id: int, body: ShotUpdate, script: Script = Depends(ready_script), db: Session = Depends(get_db)):
    shot = _shot(db, script, shot_id)
    _apply_fields(shot, body.model_dump(exclude_unset=True))
    script.updated_at = utcnow()
    db.commit()
    return serialize.shot(shot)


@router.delete("/{shot_id}")
def delete_shot(shot_id: int, script: Script = Depends(ready_script), db: Session = Depends(get_db)):
    shot = _shot(db, script, shot_id)
    scene_number = shot.scene_number
    db.delete(shot)
    db.flush()
    _resequence(_scene_shots(db, script.id, scene_number))
    script.updated_at = utcnow()
    db.commit()
    return _payload(db, script)


@router.post("/{shot_id}/duplicate", status_code=201)
def duplicate_shot(shot_id: int, script: Script = Depends(ready_script), db: Session = Depends(get_db)):
    src = _shot(db, script, shot_id)
    copy = Shot(
        script_id=script.id, scene_number=src.scene_number, scene_heading=src.scene_heading, position=0,
        size=src.size, angle=src.angle, movement=src.movement, lens=src.lens, description=src.description,
        composition=src.composition, characters=list(src.characters or []), props=list(src.props or []),
        location=src.location, notes=src.notes,
    )
    db.add(copy)
    db.flush()
    _insert(db, copy, src.position)  # directly after the original
    script.updated_at = utcnow()
    db.commit()
    return _payload(db, script)


class MoveIn(BaseModel):
    scene_number: int
    index: int = Field(ge=0)


@router.put("/{shot_id}/move")
def move_shot(shot_id: int, body: MoveIn, script: Script = Depends(ready_script), db: Session = Depends(get_db)):
    shot = _shot(db, script, shot_id)
    scene = _scene(_scenes(db, script), body.scene_number)
    old_scene = shot.scene_number
    shot.scene_number, shot.scene_heading, shot.location = scene.number, scene.heading[:300], (scene.location or "")[:300]
    db.flush()
    _insert(db, shot, body.index)
    if old_scene != scene.number:
        _resequence(_scene_shots(db, script.id, old_scene))
    script.updated_at = utcnow()
    db.commit()
    return _payload(db, script)


class RegenerateIn(BaseModel):
    instruction: str = Field(default="", max_length=1000)


@router.post("/{shot_id}/regenerate")
def regenerate_shot(
    shot_id: int, body: RegenerateIn, guard: ModelGuard = Depends(model_access("shot_regenerate")),
    db: Session = Depends(get_db),
):
    script, ai = guard.script, guard.ai
    shot = _shot(db, script, shot_id)
    scenes = _scenes(db, script)
    scene = next((s for s in scenes if s.number == shot.scene_number), None)
    scene_text = scene.text if scene else f"{shot.scene_heading}\n(This scene is no longer in the screenplay.)"
    siblings = "\n".join(_line(s) for s in _scene_shots(db, script.id, shot.scene_number))
    try:
        spec = ai.regenerate_shot(
            script.title, scene_text, siblings, _line(shot), clean_text(body.instruction).strip(),
            retrieval.cinematography_notes(f"{scene_text}\n{body.instruction}"),
        )
    except AIError as exc:
        raise ai_error(exc) from exc
    _apply_fields(shot, spec.model_dump())
    script.updated_at = utcnow()
    db.commit()
    return serialize.shot(shot)


class ShotChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)


@router.post("/chat")
def shot_chat(body: ShotChatIn, guard: ModelGuard = Depends(model_access("shot_chat")), db: Session = Depends(get_db)):
    """Conversational shot-list revision. Operations are applied immediately
    (shot edits are cheap to undo by hand, unlike screenplay rewrites)."""
    script, ai = guard.script, guard.ai
    if script.shotlist_status == "generating":
        raise HTTPException(409, "Wait for shot generation to finish first.")
    text = clean_text(body.message).strip()
    rev = current_revision(db, script)
    scenes = split_scenes(rev.content)
    context = retrieval.screenplay_context(rev.content, scenes, text)
    shots = _all(db, script.id)
    try:
        result = ai.revise_shots(script.title, context, "\n".join(_line(s) for s in shots),
                                 recent_history(db, script.id, "shots"), text)
    except AIError as exc:
        raise ai_error(exc) from exc

    refused = not result.on_topic
    if refused:  # discard the reply and every operation
        user_msg, bot_msg = save_exchange(db, script.id, "shots", "edit", text, REFUSAL, True)
        db.commit()
        return {"messages": [serialize.message(user_msg), serialize.message(bot_msg)],
                "changes": {"added": 0, "updated": 0, "deleted": 0, "skipped": 0}, **_payload(db, script)}

    by_id = {s.id: s for s in shots}
    by_scene = {s.number: s for s in scenes}
    added = updated = deleted = skipped = 0
    for op in result.operations:
        target = by_id.get(op.shot_id) if op.shot_id is not None else None
        if op.op == "update" and target and op.fields:
            _apply_fields(target, op.fields.model_dump(exclude_none=True))
            updated += 1
        elif op.op == "delete" and target:
            scene_number = target.scene_number
            db.delete(target)
            db.flush()
            by_id.pop(target.id)
            _resequence(_scene_shots(db, script.id, scene_number))
            deleted += 1
        elif op.op == "add" and op.fields and op.fields.description:
            after = by_id.get(op.after_shot_id) if op.after_shot_id is not None else None
            scene = by_scene.get(op.scene_number or (after.scene_number if after else -1))
            if scene is None:
                skipped += 1
                continue
            new = Shot(script_id=script.id, scene_number=scene.number, scene_heading=scene.heading[:300], position=0,
                       location=(scene.location or "")[:300], characters=[], props=[])
            _apply_fields(new, ShotPatch(size="MS").model_dump(exclude_none=True) | op.fields.model_dump(exclude_none=True))
            db.add(new)
            db.flush()
            _insert(db, new, after.position if after and after.scene_number == scene.number else None)
            by_id[new.id] = new
            added += 1
        else:
            skipped += 1

    changes = {"added": added, "updated": updated, "deleted": deleted, "skipped": skipped}
    reply = result.reply.strip() or "Done."
    if skipped:
        reply += f"\n\n({skipped} change{'s' if skipped > 1 else ''} couldn't be applied because the shot or scene wasn't found.)"
    user_msg, bot_msg = save_exchange(db, script.id, "shots", "edit", text, reply, False)
    if added or updated or deleted:
        _touch(script)
    db.commit()
    return {"messages": [serialize.message(user_msg), serialize.message(bot_msg)], "changes": changes, **_payload(db, script)}


def csv_export_allowed(script: Script = Depends(ready_script), user: User = Depends(current_user)) -> Script:
    limits = limits_for(user)
    if not limits.csv_export:
        raise HTTPException(403, f"CSV export isn't available on the {plan_name(limits)} plan.")
    return script


def shots_csv(db: Session, script_id: str) -> str:
    """The shot list as CSV, with a BOM so Excel reads it as UTF-8."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Scene", "Scene heading", "Shot", "Size", "Angle", "Movement", "Lens", "Description",
                "Composition", "Characters", "Props", "Location", "Notes"])
    for s in _all(db, script_id):
        w.writerow([s.scene_number, s.scene_heading, f"{s.scene_number}.{s.position}", s.size, s.angle, s.movement,
                    s.lens, s.description, s.composition, ", ".join(s.characters or []), ", ".join(s.props or []),
                    s.location, s.notes])
    return "﻿" + buf.getvalue()


def safe_name(title: str, fallback: str) -> str:
    return "".join(c if c.isalnum() or c in " -_" else "" for c in title).strip()[:80] or fallback


@router.get("/export.csv")
def export_csv(script: Script = Depends(csv_export_allowed), db: Session = Depends(get_db)):
    return Response(
        shots_csv(db, script.id), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_name(script.title, "shot-list")} - shot list.csv"'},
    )
