"""Completing generated shots: normalise, repair, fall back.

A generated (or regenerated) shot is never saved with an empty angle, movement or lens:

1. `normalize_spec` trims values and maps them onto the vocabularies in web/ai.py
   (casing, common synonyms), and writes lenses as "NNmm".
2. `fill_blanks` sends the shots that still have gaps to one repair call
   (`AIService.complete_shots`), applying its answers only to fields that are empty.
3. Whatever is still empty gets a deterministic value: Eye level, Static, and a lens
   chosen from the shot size.

Both steps only ever touch the specs passed in: new shots from the current call, or, for
"Fill missing details", copies of existing shots whose blank fields are then written back.
A field that already has a value is never changed by `fill_blanks`.
"""
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from planyourshot.web.ai import ANGLES, MOVEMENTS, AIError, ShotFill

log = logging.getLogger("planyourshot.shotfix")

FIELDS = ("angle", "movement", "lens")
BLANKS = {"", "-", "–", "—", "n/a", "na", "none", "null"}
LENS_BY_SIZE = {"EWS": "16mm", "WS": "24mm", "MLS": "35mm", "MS": "50mm", "MCU": "65mm", "CU": "85mm", "ECU": "100mm"}
FALLBACK = {"angle": "Eye level", "movement": "Static"}


def is_blank(value) -> bool:
    return value is None or str(value).strip().lower() in BLANKS


def missing(shot) -> bool:
    """Does this shot (a spec or a saved Shot) lack an angle, movement or lens?"""
    return any(is_blank(getattr(shot, f, "")) for f in FIELDS)


def _key(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def _vocab(canonical: Sequence[str], aliases: dict[str, str]) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """(exact lookup, phrases that may also match inside a longer value). One-word aliases
    such as "eye" or "low" only match exactly, so "worm's-eye" isn't read as Eye level."""
    exact = {_key(c): c for c in canonical} | {_key(k): v for k, v in aliases.items()}
    inner = {k: v for k, v in exact.items() if k in {_key(c) for c in canonical} or " " in k}
    return exact, sorted(inner.items(), key=lambda kv: -len(kv[0]))  # longest phrase wins


_ANGLES = _vocab(ANGLES, {
    "eye": "Eye level", "eyelevel": "Eye level", "neutral": "Eye level", "straight on": "Eye level",
    "low": "Low angle", "worm s eye": "Low angle", "worms eye": "Low angle", "high": "High angle", "top down": "Overhead", "birds eye": "Overhead",
    "bird s eye": "Overhead", "aerial": "Overhead", "dutch": "Dutch angle", "canted": "Dutch angle",
    "tilted": "Dutch angle", "ots": "Over-the-shoulder", "over shoulder": "Over-the-shoulder",
    "point of view": "POV", "subjective": "POV", "side": "Profile", "side on": "Profile",
})
_MOVEMENTS = _vocab(MOVEMENTS, {
    "locked off": "Static", "locked": "Static", "fixed": "Static", "still": "Static", "tripod": "Static",
    "push in": "Push-in", "push": "Push-in", "dolly in": "Push-in", "creep in": "Push-in", "zoom in": "Push-in",
    "pull out": "Pull-out", "pull back": "Pull-out", "dolly out": "Pull-out", "zoom out": "Pull-out",
    "hand held": "Handheld", "shoulder": "Handheld", "steadicam": "Tracking", "track": "Tracking",
    "follow": "Tracking", "trucking": "Tracking", "jib": "Crane", "boom": "Crane", "focus pull": "Rack focus",
    "rack": "Rack focus", "whip pan": "Pan", "panning": "Pan", "tilting": "Tilt",
})


def _canonical(value: str, vocab: tuple[dict[str, str], list[tuple[str, str]]]) -> str:
    exact, inner = vocab
    v = " ".join(str(value).split())
    if is_blank(v):
        return ""
    k = _key(v)
    if k in exact:
        return exact[k]
    padded = f" {k} "
    for phrase, canon in inner:  # e.g. "Slow push-in" -> Push-in, "Low angle, slight tilt" -> Low angle
        if f" {phrase} " in padded:
            return canon
    return v[0].upper() + v[1:]  # outside the vocabulary: keep the model's words, tidied


def normalize_angle(value) -> str:
    return _canonical(value, _ANGLES)


def normalize_movement(value) -> str:
    return _canonical(value, _MOVEMENTS)


_MM = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(?:-\s*\d{1,3}(?:\.\d+)?\s*)?mm", re.I)
_BARE = re.compile(r"^\s*(\d{1,3}(?:\.\d+)?)\s*$")


def normalize_lens(value) -> str:
    """'35 MM', '35mm lens', '24-70mm', '50' -> '35mm', '35mm', '24mm', '50mm'. Unreadable -> ''."""
    if is_blank(value):
        return ""
    text = str(value)
    m = _MM.search(text) or _BARE.match(text)
    if not m:
        return ""
    number = float(m.group(1))
    if not 4 <= number <= 1200:
        return ""
    return f"{int(number) if number.is_integer() else number:g}mm"


NORMALIZERS: dict[str, Callable[[object], str]] = {
    "angle": normalize_angle, "movement": normalize_movement, "lens": normalize_lens,
}


def normalize_spec(spec):
    """Trim and normalise a newly generated spec in place; returns it."""
    for field, fn in NORMALIZERS.items():
        setattr(spec, field, fn(getattr(spec, field, "")))
    for field in ("description", "composition", "notes"):
        setattr(spec, field, " ".join(str(getattr(spec, field, "") or "").split()))
    return spec


@dataclass
class FixReport:
    incomplete: int = 0  # shots that had a gap before the repair
    repaired: int = 0  # fields filled by the repair call
    fallback: int = 0  # fields filled deterministically
    repair_failed: bool = False


def describe(incomplete: Sequence[tuple[int, object]]) -> str:
    """The shots sent to the repair call, numbered by their index + 1."""
    lines = []
    for i, s in incomplete:
        scene = getattr(s, "scene_number", None)
        parts = [f"[{i + 1}]"] + ([f"scene {scene}"] if scene else []) + [
            f"size {s.size}",
            *(f"{f}: {getattr(s, f) if not is_blank(getattr(s, f)) else 'MISSING'}" for f in FIELDS),
            f"description: {s.description}",
        ]
        lines.append(" | ".join(parts))
    return "\n".join(lines)


Repair = Callable[[list[tuple[int, object]]], Sequence[ShotFill]]


def fill_blanks(specs: Sequence, repair: Repair | None) -> FixReport:
    """Fill empty angle / movement / lens on `specs` in place: one repair call for the
    incomplete ones, then the deterministic fallback. Non-empty fields are never touched."""
    report = FixReport()
    incomplete = [(i, s) for i, s in enumerate(specs) if missing(s)]
    report.incomplete = len(incomplete)
    if not incomplete:
        return report
    if repair is not None:
        try:
            fills = {f.index: f for f in repair(incomplete)}
        except AIError as exc:
            log.warning("shot repair call failed: %s", exc)
            report.repair_failed, fills = True, {}
        for i, s in incomplete:
            fill = fills.get(i + 1)
            if fill is None:
                continue
            for field in FIELDS:
                if is_blank(getattr(s, field)):
                    value = NORMALIZERS[field](getattr(fill, field, ""))
                    if value:
                        setattr(s, field, value)
                        report.repaired += 1
    for _, s in incomplete:
        for field in FIELDS:
            if is_blank(getattr(s, field)):
                setattr(s, field, LENS_BY_SIZE.get(s.size, "50mm") if field == "lens" else FALLBACK[field])
                report.fallback += 1
    return report


def finish(specs: Sequence, repair: Repair | None) -> FixReport:
    """For newly generated specs: normalise, then fill_blanks."""
    for s in specs:
        normalize_spec(s)
    return fill_blanks(specs, repair)
