import re
from dataclasses import dataclass

HEADING_RE = re.compile(
    r"^\s*(INT\.?/EXT\.?|INT\.?|EXT\.?|EST\.?|I/E\.?)\s+.+$",
    re.IGNORECASE | re.MULTILINE,
)

# Editing transitions: CUT TO:, CUT TO BLACK, MATCH CUT TO:, DISSOLVE TO:, FADE OUT, etc.
TRANSITION_RE = re.compile(
    r"^\s*(?:"
    r"(?:SMASH |MATCH )?CUT TO\b.*"
    r"|DISSOLVE TO\b.*"
    r"|FADE (?:IN|OUT|TO BLACK)\b.*"
    r"|.*\bTO:\s*"
    r")$",
    re.IGNORECASE,
)

# Camera / framing directions: CLOSE UP ON X, ESTABLISHING SHOT, WIDE, POV, ANGLE ON, etc.
CAMERA_RE = re.compile(
    r"^\s*(?:"
    r"(?:EXTREME )?CLOSE ?UP(?: ON)?\b.*"
    r"|ESTABLISHING SHOT\b.*"
    r"|SHOT OF\b.*"
    r"|(?:EXTREME )?WIDE(?: SHOT)?\b.*"
    r"|MASTER SHOT\b.*"
    r"|POV\b.*"
    r"|ANGLE ON\b.*"
    r"|INSERT\b.*"
    r"|(?:SLOW )?(?:PAN|TILT|DOLLY|ZOOM|TRACKING|CRANE)\b.*"
    r")$",
    re.IGNORECASE,
)


@dataclass
class RawScene:
    scene_number: int
    heading: str
    body: str                       # cleaned: action + dialogue only
    int_ext: str | None
    location: str | None
    time_of_day: str | None
    camera_directions: list[str]    # code-extracted, not LLM
    transitions: list[str]          # code-extracted, not LLM


def parse_heading(line: str) -> tuple[str | None, str | None, str | None]:
    m = re.match(r"\s*(INT\.?/EXT\.?|INT\.?|EXT\.?|EST\.?|I/E\.?)\s+(.*)", line, re.IGNORECASE)
    if not m:
        return None, None, None
    int_ext = m.group(1).rstrip(".").upper()
    rest = m.group(2).strip()
    if " - " in rest:
        location, _, tod = rest.rpartition(" - ")
        return int_ext, location.strip() or None, tod.strip() or None
    return int_ext, rest or None, None


def classify_body_lines(body: str) -> tuple[str, list[str], list[str]]:
    """Split a scene body into (clean_body_for_llm, camera_directions, transitions)."""
    action_lines: list[str] = []
    camera: list[str] = []
    transitions: list[str] = []
    for raw_line in body.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if TRANSITION_RE.match(line):
            transitions.append(line)
        elif CAMERA_RE.match(line):
            camera.append(line)
        else:
            action_lines.append(line)
    return "\n".join(action_lines), camera, transitions


def split_scenes(text: str) -> list[RawScene]:
    matches = list(HEADING_RE.finditer(text))
    scenes: list[RawScene] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        lines = block.split("\n")
        heading = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        int_ext, location, tod = parse_heading(heading)
        clean_body, camera, transitions = classify_body_lines(body)
        scenes.append(RawScene(
            i + 1, heading, clean_body, int_ext, location, tod, camera, transitions
        ))
    return scenes