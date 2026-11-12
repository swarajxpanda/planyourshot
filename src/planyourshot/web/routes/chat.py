from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from planyourshot.web import retrieval, serialize
from planyourshot.web.ai import REFUSAL, AIError
from planyourshot.web.db import get_db
from planyourshot.web.deps import current_user, ready_script
from planyourshot.web.edits import add_revision, apply_edit, current_revision, locate
from planyourshot.web.limits import check_edit_length, limits_for
from planyourshot.web.models import Message, Proposal, Script, User, utcnow
from planyourshot.web.parsing import clean_text
from planyourshot.web.scenes import split_scenes
from planyourshot.web.usage import ModelGuard, ai_error, model_access

router = APIRouter(prefix="/api/scripts/{script_id}", tags=["chat"])

HISTORY_TURNS = 16


def recent_history(db: Session, script_id: str, channel: str) -> list[tuple[str, str]]:
    """The last turns of a conversation, as sent back to the model. Refused (off-topic)
    exchanges are left out so they can't steer later answers."""
    rows = db.scalars(
        select(Message).where(Message.script_id == script_id, Message.channel == channel, Message.refused.is_(False))
        .order_by(Message.id.desc()).limit(HISTORY_TURNS)
    ).all()
    return [(m.role, m.content) for m in reversed(rows)]


def _messages(db: Session, script_id: str, channel: str) -> list[dict]:
    msgs = db.scalars(
        select(Message).where(Message.script_id == script_id, Message.channel == channel).order_by(Message.id)
    ).all()
    props = {p.message_id: p for p in db.scalars(select(Proposal).where(Proposal.script_id == script_id))}
    return [serialize.message(m, props.get(m.id)) for m in msgs]


@router.get("/messages")
def list_messages(channel: str = "script", script: Script = Depends(ready_script), db: Session = Depends(get_db)):
    if channel not in ("script", "shots"):
        raise HTTPException(422, "Unknown channel.")
    return {"messages": _messages(db, script.id, channel)}


@router.delete("/messages", status_code=204)
def clear_messages(channel: str = "script", script: Script = Depends(ready_script), db: Session = Depends(get_db)):
    """Clear a conversation. Applied changes stay in the revision history."""
    ids = select(Message.id).where(Message.script_id == script.id, Message.channel == channel)
    db.execute(delete(Proposal).where(Proposal.message_id.in_(ids)))
    db.execute(delete(Message).where(Message.script_id == script.id, Message.channel == channel))
    db.commit()


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    mode: str = Field(default="discuss", pattern="^(discuss|edit)$")


def save_exchange(db: Session, script_id: str, channel: str, mode: str, text: str, reply: str,
                  refused: bool) -> tuple[Message, Message]:
    user_msg = Message(script_id=script_id, channel=channel, role="user", mode=mode, content=text, refused=refused)
    bot_msg = Message(script_id=script_id, channel=channel, role="assistant", mode=mode, content=reply, refused=refused)
    db.add(user_msg)
    db.flush()
    db.add(bot_msg)
    db.flush()
    return user_msg, bot_msg


@router.post("/chat")
def chat(body: ChatIn, guard: ModelGuard = Depends(model_access("chat")), db: Session = Depends(get_db)):
    script, ai = guard.script, guard.ai
    text = clean_text(body.message).strip()
    if not text:
        raise HTTPException(422, "Message can't be empty.")
    rev = current_revision(db, script)
    context = retrieval.screenplay_context(rev.content, split_scenes(rev.content), text)
    history = recent_history(db, script.id, "script")

    edits: list[dict] = []
    try:
        result = (ai.propose_edits if body.mode == "edit" else ai.discuss)(script.title, context, history, text)
    except AIError as exc:
        raise ai_error(exc) from exc

    refused = not result.on_topic
    if refused:
        reply = REFUSAL  # the model's own text is discarded, and so are any edits
    elif body.mode == "edit":
        reply = result.reply.strip()
        dropped = 0
        for e in result.edits:
            if e.find.strip() == e.replace.strip():
                continue
            if locate(rev.content, e.find) is None:
                dropped += 1
                continue
            edits.append({"find": e.find.strip(), "replace": e.replace.strip("\n"), "reason": e.reason.strip(), "status": "pending"})
        if dropped:
            reply += (
                f"\n\n({dropped} suggested change{'s' if dropped > 1 else ''} couldn't be matched to the current "
                "text and was left out. Point me to the scene or quote the line and I'll try again.)"
            )
        reply = reply or ("Here's what I'd change." if edits else "I don't have a change to suggest for that.")
    else:
        reply = result.reply.strip()
        if not reply:
            raise HTTPException(502, "The AI returned an empty response. Please try again.")

    user_msg, bot_msg = save_exchange(db, script.id, "script", body.mode, text, reply, refused)
    prop = None
    if edits:
        prop = Proposal(script_id=script.id, message_id=bot_msg.id, base_revision_id=rev.id, edits=edits)
        db.add(prop)
    db.commit()
    return {"messages": [serialize.message(user_msg), serialize.message(bot_msg, prop)]}


def _proposal(db: Session, script: Script, proposal_id: int) -> Proposal:
    prop = db.get(Proposal, proposal_id)
    if prop is None or prop.script_id != script.id:
        raise HTTPException(404, "Suggestion not found.")
    if prop.status != "pending":
        raise HTTPException(409, "This suggestion has already been resolved.")
    return prop


class ApplyIn(BaseModel):
    indices: list[int] | None = None  # which edits to apply; default all


@router.post("/proposals/{proposal_id}/apply")
def apply_proposal(proposal_id: int, body: ApplyIn, script: Script = Depends(ready_script),
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
    prop = _proposal(db, script, proposal_id)
    chosen = set(range(len(prop.edits))) if body.indices is None else set(body.indices)
    if not chosen or not chosen <= set(range(len(prop.edits))):
        raise HTTPException(422, "Select at least one change to apply.")

    before = content = current_revision(db, script).content
    edits, applied = [dict(e) for e in prop.edits], 0
    for i, e in enumerate(edits):
        if i not in chosen:
            e["status"] = "rejected"
            continue
        updated = apply_edit(content, e["find"], e["replace"])
        if updated is None:
            e["status"] = "failed"  # the passage changed since this was suggested
        else:
            content, e["status"], applied = updated, "applied", applied + 1
    if applied == 0:
        raise HTTPException(409, "These changes no longer match the screenplay — the text has changed since they were suggested.")
    if error := check_edit_length(limits_for(user), len(before), len(content)):
        raise HTTPException(422, error)

    reasons = [e["reason"] for e in edits if e["status"] == "applied" and e["reason"]]
    summary = reasons[0] if len(reasons) == 1 else f"{applied} AI edits" + (f": {reasons[0]}" if reasons else "")
    rev = add_revision(db, script, content, "ai_edit", summary)
    prop.edits, prop.status, prop.result_revision_id, prop.resolved_at = edits, "applied", rev.id, utcnow()
    db.commit()
    return {"proposal": serialize.proposal(prop), "revision": serialize.revision(rev, content=True),
            "scenes": [s.outline() for s in split_scenes(rev.content)]}


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: int, script: Script = Depends(ready_script), db: Session = Depends(get_db)):
    prop = _proposal(db, script, proposal_id)
    prop.edits = [{**e, "status": "rejected"} for e in prop.edits]
    prop.status, prop.resolved_at = "rejected", utcnow()
    db.commit()
    return {"proposal": serialize.proposal(prop)}
