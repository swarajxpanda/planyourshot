"""Scene breakdown of screenplay text (deterministic, no model call).

Builds on the heading grammar in planyourshot.splitter, but keeps each scene's
original text and character offsets so the UI and the model see exactly what
is on the page.
"""
import re
from dataclasses import asdict, dataclass

from planyourshot.splitter import parse_heading

_HEADING = re.compile(
    r"^[ \t]*(?:\d+[A-Z]?\.?[ \t]+)?((?:INT\.?/EXT\.?|INT\.?|EXT\.?|EST\.?|I/E\.?)[ \t]+\S.*?)(?:[ \t]+\d+[A-Z]?\.?)?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class SceneInfo:
    number: int
    heading: str
    int_ext: str | None
    location: str | None
    time_of_day: str | None
    start: int
    end: int
    text: str

    def outline(self) -> dict:
        d = asdict(self)
        d.pop("text")
        return d


def _is_heading(line: str) -> bool:
    # Avoid prose like "Int he..." — a real slugline has the dot or is all caps.
    s = line.strip()
    return s == s.upper() or bool(re.match(r"\s*(?:\d+\S*\s+)?(?:INT|EXT|EST|I/E)\.", s, re.IGNORECASE))


def split_scenes(content: str) -> list[SceneInfo]:
    matches = [m for m in _HEADING.finditer(content) if _is_heading(m.group(0))]
    if not matches:
        return [SceneInfo(1, "Full script", None, None, None, 0, len(content), content)]
    scenes: list[SceneInfo] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        heading = re.sub(r"\s+", " ", m.group(1)).strip()
        int_ext, location, tod = parse_heading(heading)
        scenes.append(SceneInfo(i + 1, heading, int_ext, location, tod, m.start(), end, content[m.start():end].strip()))
    return scenes
