from datetime import datetime

from planyourshot.web import shotfix
from planyourshot.web.models import Message, Proposal, Revision, Script, Shot, User


def ts(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def user(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "picture": u.picture, "plan": u.plan}


def script(s: Script, *, shot_count: int | None = None, revision_count: int | None = None) -> dict:
    return {
        "id": s.id,
        "title": s.title,
        "original_filename": s.original_filename,
        "file_type": s.file_type,
        "file_size": s.file_size,
        "status": s.status,
        "error": s.error,
        "shotlist_status": s.shotlist_status,
        "shotlist_error": s.shotlist_error,
        "shotlist_done": s.shotlist_done,
        "shotlist_total": s.shotlist_total,
        "shot_count": shot_count,
        "revision_count": revision_count,
        "created_at": ts(s.created_at),
        "updated_at": ts(s.updated_at),
    }


def revision(r: Revision, *, content: bool = False) -> dict:
    d = {
        "id": r.id,
        "number": r.number,
        "source": r.source,
        "summary": r.summary,
        "chars": len(r.content),
        "created_at": ts(r.created_at),
    }
    if content:
        d["content"] = r.content
    return d


def proposal(p: Proposal) -> dict:
    return {
        "id": p.id,
        "status": p.status,
        "edits": p.edits,
        "base_revision_id": p.base_revision_id,
        "result_revision_id": p.result_revision_id,
        "created_at": ts(p.created_at),
        "resolved_at": ts(p.resolved_at),
    }


def message(m: Message, p: Proposal | None = None) -> dict:
    return {
        "id": m.id,
        "role": m.role,
        "mode": m.mode,
        "content": m.content,
        "refused": m.refused,
        "created_at": ts(m.created_at),
        "proposal": proposal(p) if p else None,
    }


def shot(s: Shot) -> dict:
    return {
        "id": s.id,
        "scene_number": s.scene_number,
        "scene_heading": s.scene_heading,
        "position": s.position,
        "size": s.size,
        "angle": s.angle,
        "movement": s.movement,
        "lens": s.lens,
        "description": s.description,
        "composition": s.composition,
        "characters": s.characters or [],
        "props": s.props or [],
        "location": s.location,
        "notes": s.notes,
        # empty angle, movement or lens: "Fill missing details" can complete it
        "missing": shotfix.missing(s),
        "updated_at": ts(s.updated_at),
    }
