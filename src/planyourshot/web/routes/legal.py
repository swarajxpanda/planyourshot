"""Public legal pages. The markdown lives in legal/*.md and is rendered by the frontend."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from planyourshot.web.settings import get_settings

router = APIRouter(prefix="/api/legal", tags=["legal"])

DOCUMENTS = {"privacy": "Privacy Policy", "terms": "Terms of Service"}


@router.get("/{name}")
def legal_document(name: str):
    if name not in DOCUMENTS:
        raise HTTPException(404, "Not found")
    path = get_settings().legal_dir / f"{name}.md"
    try:
        markdown = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(404, "This document isn't available.") from exc
    return JSONResponse({"name": name, "title": DOCUMENTS[name], "markdown": markdown},
                        headers={"Cache-Control": "public, max-age=300"})
