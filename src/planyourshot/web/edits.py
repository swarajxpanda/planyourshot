"""Locating and applying find/replace edits against screenplay text."""
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from planyourshot.web.models import Revision, Script, utcnow


def locate(content: str, find: str) -> tuple[int, int] | None:
    """Span of `find` in content. Exact match first; otherwise tolerate
    whitespace differences (PDF text has irregular spacing and line breaks)."""
    find = find.strip()
    if not find:
        return None
    i = content.find(find)
    if i >= 0:
        return i, i + len(find)
    parts = [re.escape(p) for p in find.split()]
    m = re.search(r"\s+".join(parts), content)
    return (m.start(), m.end()) if m else None


def apply_edit(content: str, find: str, replace: str) -> str | None:
    span = locate(content, find)
    if span is None:
        return None
    start, end = span
    replace = replace.strip("\n")
    if not replace.strip():
        # Deletion: also swallow the blank line it leaves behind.
        tail = content[end:]
        tail = tail[1:] if tail.startswith("\n") and content[:start].endswith("\n") else tail
        return re.sub(r"\n{3,}", "\n\n", content[:start] + tail)
    return content[:start] + replace + content[end:]


def current_revision(db: Session, script: Script) -> Revision:
    return db.get(Revision, script.current_revision_id)


def add_revision(db: Session, script: Script, content: str, source: str, summary: str) -> Revision:
    number = (db.scalar(select(func.max(Revision.number)).where(Revision.script_id == script.id)) or 0) + 1
    rev = Revision(script_id=script.id, number=number, source=source, summary=summary[:500], content=content)
    db.add(rev)
    db.flush()
    script.current_revision_id = rev.id
    script.updated_at = utcnow()
    return rev
