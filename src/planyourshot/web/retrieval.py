"""Lightweight lexical retrieval (TF-IDF) — no embedding server needed.

Used for (a) pulling cinematography guidance from data/knowledge into shot
prompts and (b) picking relevant scenes when a screenplay is too long to send
to the model whole. The Chroma/Ollama index in planyourshot.knowledge_base
remains available for offline experiments.
"""
import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

from planyourshot.web.scenes import SceneInfo
from planyourshot.web.settings import get_settings

_WORD = re.compile(r"[a-z][a-z'-]{2,}")
_STOP = set(
    "the and for with that this from are was were his her she him they them you your their has have had not but "
    "into out about what when where who how why can could would should will then than there here over under "
    "its it's one two all any some just like looks look".split()
)


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


def rank(query: str, docs: list[str], k: int) -> list[int]:
    """Indices of the k docs most relevant to query (may return fewer)."""
    q = Counter(_tokens(query))
    if not q or not docs:
        return []
    tfs = [Counter(_tokens(d)) for d in docs]
    df = Counter(t for tf in tfs for t in tf)
    n = len(docs)
    scored = []
    for i, tf in enumerate(tfs):
        length = sum(tf.values()) or 1
        score = sum(
            min(qc, 3) * (tf[t] / (tf[t] + 1.5 * (0.25 + 0.75 * length / 400))) * math.log(1 + n / df[t])
            for t, qc in q.items()
            if t in tf
        )
        if score > 0:
            scored.append((score, i))
    return [i for _, i in sorted(scored, reverse=True)[:k]]


@lru_cache(maxsize=1)
def _kb_sections() -> list[tuple[str, str]]:
    root: Path = get_settings().knowledge_dir
    sections: list[tuple[str, str]] = []
    if not root.exists():
        return sections
    for md in sorted(root.rglob("*.md")):
        title, current, buf = md.stem, None, []
        for line in md.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
            elif line.startswith("## "):
                if current and buf:
                    sections.append((f"{title} — {current}", "\n".join(buf).strip()))
                current, buf = line[3:].strip(), []
            elif current:
                buf.append(line)
        if current and buf:
            sections.append((f"{title} — {current}", "\n".join(buf).strip()))
    return sections


def cinematography_notes(query: str, k: int = 4) -> str:
    sections = _kb_sections()
    idx = rank(query, [f"{t}\n{b}" for t, b in sections], k)
    return "\n\n".join(f"[{sections[i][0]}]\n{sections[i][1]}" for i in idx)


def screenplay_context(content: str, scenes: list[SceneInfo], query: str) -> str:
    """The whole screenplay if it fits the budget; otherwise outline + the
    scenes most relevant to the query."""
    budget = get_settings().max_context_chars
    if len(content) <= budget:
        return content
    outline = "\n".join(f"{s.number}. {s.heading}" for s in scenes)
    picked: list[SceneInfo] = []
    used = len(outline)
    for i in rank(query, [s.text for s in scenes], len(scenes)):
        if used + len(scenes[i].text) > budget:
            continue
        picked.append(scenes[i])
        used += len(scenes[i].text)
    picked.sort(key=lambda s: s.number)
    body = "\n\n".join(f"--- SCENE {s.number} ---\n{s.text}" for s in picked)
    return (
        "[The screenplay is too long to include in full. Scene outline, then the scenes most relevant to the request.]\n\n"
        f"SCENE OUTLINE\n{outline}\n\nSELECTED SCENES\n{body}"
    )
