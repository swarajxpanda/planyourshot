"""Job handlers: parsing an upload and generating a shot list.

They run in the worker (web/worker.py) with a JobContext from web/jobs.py.
Progress and failures are also written to the script row, which the UI polls.
"""
import logging

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from planyourshot.web import ai as ai_module
from planyourshot.web import jobs, parsing, retrieval, storage, usage
from planyourshot.web.ai import AIError, ShotSpec
from planyourshot.web.limits import limits_for, too_long
from planyourshot.web.models import Job, Revision, Script, Shot, User, utcnow
from planyourshot.web.scenes import SceneInfo, split_scenes

log = logging.getLogger("planyourshot.tasks")


# ---------- upload processing ----------

def _upload_gave_up(db: Session, job: Job, message: str) -> None:
    script = db.get(Script, job.script_id) if job.script_id else None
    if script is not None and script.status == "processing":
        script.status, script.error, script.updated_at = "failed", message, utcnow()


@jobs.handler("process_upload", give_up=_upload_gave_up)
def process_upload(ctx: jobs.JobContext) -> None:
    db = ctx.db
    script = db.get(Script, ctx.job.script_id)
    if script is None:
        return
    try:
        text = parsing.extract_text(storage.path_for(script.storage_key), script.file_type)
    except parsing.ParseError as exc:  # a problem with the file itself: retrying won't help
        script.status, script.error = "failed", str(exc)
    else:  # anything unexpected propagates, and the job is retried
        limits = limits_for(db.get(User, script.user_id))
        if len(text) > limits.max_script_chars:
            script.status, script.error = "failed", too_long(limits, len(text))
        else:
            db.execute(delete(Revision).where(Revision.script_id == script.id))
            rev = Revision(script_id=script.id, number=1, source="upload", summary="Original upload", content=text)
            db.add(rev)
            db.flush()
            script.current_revision_id, script.status, script.error = rev.id, "ready", ""
    script.updated_at = utcnow()
    ctx.checkpoint(done=1, total=1)
    db.commit()


# ---------- shot generation ----------

def shot_from_spec(script_id: str, scene: SceneInfo, position: int, spec: ShotSpec) -> Shot:
    return Shot(
        script_id=script_id,
        scene_number=scene.number,
        scene_heading=scene.heading[:300],
        position=position,
        size=spec.size,
        angle=spec.angle[:80],
        movement=spec.movement[:80],
        lens=spec.lens[:80],
        description=spec.description,
        composition=spec.composition,
        characters=spec.characters,
        props=spec.props,
        location=(scene.location or "")[:300],
        notes=spec.notes,
    )


def _finish_shotlist(db: Session, script: Script, failed: dict, gave_up: str = "") -> None:
    total = db.scalar(select(func.count()).select_from(Shot).where(Shot.script_id == script.id)) or 0
    if failed:
        nums = sorted(int(n) for n in failed)
        error = f"Couldn't generate {'scene' if len(nums) == 1 else 'scenes'} {', '.join(map(str, nums))}: " \
                f"{failed[str(nums[-1])]}"
    else:
        error = ""
    error = gave_up or error
    script.shotlist_status = "failed" if total == 0 and (failed or gave_up) else "ready"
    script.shotlist_error = error
    script.updated_at = utcnow()


def _shots_gave_up(db: Session, job: Job, message: str) -> None:
    payload = job.payload or {}
    script = db.get(Script, job.script_id) if job.script_id else None
    if script is not None and script.shotlist_status == "generating":
        _finish_shotlist(db, script, payload.get("failed", {}), gave_up=message)
    if payload.get("run_id"):
        usage.finish_run(payload["run_id"], ok=bool(payload.get("done")), db=db)


def shots_payload(revision_id: int, only_scene: int | None, run_id: int | None) -> dict:
    return {"revision_id": revision_id, "only_scene": only_scene, "run_id": run_id, "done": [], "failed": {},
            "pruned": False}


@jobs.handler("generate_shots", give_up=_shots_gave_up)
def generate_shots(ctx: jobs.JobContext) -> None:
    """Generate shots scene by scene, from the revision current when the job was queued.

    Resumable: each scene's shots are committed together with the scene's number in the
    job's `done` list, so a retried job skips finished scenes and continues with the
    first one that wasn't. A scene's existing shots are replaced only once its new
    shots have come back. Each model call takes the global slot (and the user's slot)
    for that call only, so other users get a turn between scenes."""
    db, payload = ctx.db, ctx.payload
    script = db.get(Script, ctx.job.script_id)
    if script is None:
        return
    rev = db.get(Revision, payload.get("revision_id")) or db.get(Revision, script.current_revision_id)
    scenes = split_scenes(rev.content)
    only = payload.get("only_scene")
    targets = [s for s in scenes if only is None or s.number == only]
    done: list[int] = list(payload.get("done", []))
    failed: dict[str, str] = {}  # retried on a later attempt: only finished scenes are skipped

    if only is None and not payload.get("pruned"):
        # A full regeneration: drop shots for scenes that no longer exist.
        db.execute(delete(Shot).where(Shot.script_id == script.id, Shot.scene_number > len(scenes)))
        payload["pruned"] = True
    script.shotlist_status, script.shotlist_total, script.shotlist_done = "generating", len(targets), len(done)
    ctx.checkpoint(payload=payload, done=len(done), total=len(targets))
    # Commit before any model call: usage events and leases are written from their own
    # sessions, and SQLite allows one writer at a time.
    db.commit()

    ai = usage.MeteredAI(ai_module.get_ai(), ctx.job.user_id, script.id, background=True, run_id=payload.get("run_id"))
    for scene in targets:
        if scene.number in done:
            continue
        previous = scenes[scene.number - 2].text[-600:] if scene.number > 1 else ""
        try:
            specs = ai.scene_shots(script.title, scene.number, scene.text, previous,
                                   retrieval.cinematography_notes(scene.text))
            if not specs:
                raise AIError("No shots were returned.")
        except AIError as exc:  # reported on the shot list; the run goes on
            failed[str(scene.number)] = str(exc)
        else:
            db.execute(delete(Shot).where(Shot.script_id == script.id, Shot.scene_number == scene.number))
            db.add_all(shot_from_spec(script.id, scene, i + 1, spec) for i, spec in enumerate(specs))
            done.append(scene.number)
        payload["done"], payload["failed"] = done, failed
        script.shotlist_done = len(done) + len(failed)
        ctx.checkpoint(payload=payload, done=script.shotlist_done)  # raises JobLost if cancelled
        db.commit()

    _finish_shotlist(db, script, failed)
    if payload.get("run_id"):
        usage.finish_run(payload["run_id"], ok=bool(done), db=db)
    ctx.checkpoint(payload=payload)
    db.commit()
