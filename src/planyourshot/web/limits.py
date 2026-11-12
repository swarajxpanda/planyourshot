"""Per-plan limits.

Every user is on "free" for now. "pro" exists only as configuration so a paid
plan can be switched on later by setting users.plan — nothing else changes.

Each number can be overridden with an environment variable named
<PLAN>_<FIELD>, e.g. FREE_MESSAGES_PER_DAY=30 or PRO_CSV_EXPORT=0.
Overrides are read on every lookup. A malformed number is ignored.
"""
import os
from dataclasses import dataclass, fields, replace


@dataclass(frozen=True)
class Limits:
    plan: str
    max_scripts: int
    max_script_chars: int
    messages_per_day: int  # chat + edit + shot_chat, rolling 24 hours
    full_generations_per_month: int  # whole shot-list runs, rolling 30 days
    scene_generations_per_day: int  # single-scene runs, rolling 24 hours
    shot_regenerations_per_day: int  # single-shot regenerations, rolling 24 hours
    requests_per_minute: int  # any model request
    csv_export: bool


PLANS: dict[str, Limits] = {
    "free": Limits(
        plan="free", max_scripts=2, max_script_chars=45_000, messages_per_day=15, full_generations_per_month=2,
        scene_generations_per_day=5, shot_regenerations_per_day=10, requests_per_minute=5, csv_export=False,
    ),
    "pro": Limits(
        plan="pro", max_scripts=20, max_script_chars=400_000, messages_per_day=200, full_generations_per_month=30,
        scene_generations_per_day=50, shot_regenerations_per_day=100, requests_per_minute=20, csv_export=True,
    ),
}
DEFAULT_PLAN = "free"
PLAN_NAMES = {"free": "Free", "pro": "Pro"}


def _override(plan: str, base: Limits) -> Limits:
    changes: dict = {}
    for f in fields(Limits):
        if f.name == "plan":
            continue
        raw = os.getenv(f"{plan.upper()}_{f.name.upper()}")
        if raw is None or not raw.strip():
            continue
        if f.type in (bool, "bool"):
            changes[f.name] = raw.strip().lower() in {"1", "true", "yes", "on"}
        else:
            try:
                changes[f.name] = max(0, int(raw.strip().replace("_", "").replace(",", "")))
            except ValueError:
                continue  # ignore a malformed override rather than failing every request
    return replace(base, **changes) if changes else base


def plan_name(limits: Limits) -> str:
    return PLAN_NAMES.get(limits.plan, limits.plan.title())


def too_long(limits: Limits, chars: int) -> str:
    return (f"This screenplay is too long for the {plan_name(limits)} plan: {chars:,} characters, "
            f"and the limit is {limits.max_script_chars:,}.")


def check_edit_length(limits: Limits, before: int, after: int) -> str | None:
    """Error message if an edit takes a screenplay past the plan's length limit, else None.
    A screenplay that was already longer (from before the limit, or a plan change) can
    still be edited as long as the edit doesn't make it longer."""
    if after > limits.max_script_chars and after > before:
        return (f"This change would make the screenplay too long for the {plan_name(limits)} plan "
                f"({after:,} characters; the limit is {limits.max_script_chars:,}).")
    return None


def limits_for(user) -> Limits:
    """The limits that apply to `user` (anything with a `.plan`), looked up by plan.
    An unknown plan falls back to the default plan."""
    plan = getattr(user, "plan", None) or DEFAULT_PLAN
    if plan not in PLANS:
        plan = DEFAULT_PLAN
    return _override(plan, PLANS[plan])
