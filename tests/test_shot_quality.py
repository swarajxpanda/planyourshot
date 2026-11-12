"""Generated shots are complete; existing shots change only through explicit user actions;
"Fill missing details" fills blanks without overwriting anything."""
import pytest

from conftest import SCREENPLAY, usage_events
from planyourshot.web import shotfix
from planyourshot.web.ai import EditOp, ShotOp, ShotPatch, ShotSpec

TECH = ("angle", "movement", "lens")


def base(sid):
    return f"/api/scripts/{sid}/shots"


def snapshot(client, sid):
    """Every shot, every field (including updated_at), by id."""
    return {s["id"]: s for s in client.get(base(sid)).json()["shots"]}


def generate(client, sid, scene=None):
    r = client.post(f"{base(sid)}/generate", json={"scene_number": scene})
    assert r.status_code == 202, r.text
    return client.get(base(sid)).json()


@pytest.fixture
def roomy(monkeypatch):
    monkeypatch.setenv("FREE_REQUESTS_PER_MINUTE", "1000")
    monkeypatch.setenv("FREE_SCENE_GENERATIONS_PER_DAY", "1000")
    monkeypatch.setenv("FREE_SHOT_REGENERATIONS_PER_DAY", "1000")


# ---------- 1-2. complete, normalised shots ----------

def test_schema_requires_the_technical_fields():
    required = {k for k, f in ShotSpec.model_fields.items() if f.is_required()}
    assert {"size", "angle", "movement", "lens", "composition", "notes", "description"} <= required
    assert not ShotSpec.model_fields["characters"].is_required() and not ShotSpec.model_fields["props"].is_required()
    with pytest.raises(ValueError):
        ShotSpec(size="CU", description="x", composition="", notes="")  # angle/movement/lens omitted
    assert all(not f.is_required() for f in ShotPatch.model_fields.values())  # updates stay partial


def test_generation_repairs_missing_fields_with_one_call_per_scene(client, script, ai):
    data = generate(client, script["id"])
    for s in data["shots"]:
        assert all(s[f] for f in TECH), s
    close = [s for s in data["shots"] if s["size"] == "CU"]
    assert [(s["angle"], s["movement"], s["lens"]) for s in close] == [("Low angle", "Push-in", "85mm")] * 2
    wide = [s for s in data["shots"] if s["size"] == "WS"][0]
    assert (wide["angle"], wide["movement"], wide["lens"]) == ("Eye level", "Static", "24mm")  # untouched
    repairs = [c for c in ai.calls if c[0] == "repair"]
    assert len(repairs) == 2  # one per scene...
    assert all("Reaction" in c[2] and "Establish" not in c[2] for c in repairs)  # ...with only the incomplete shots
    assert all("lens: MISSING" in c[2] and "COFFEE SHOP" in repairs[0][1] for c in repairs)
    assert data["incomplete"] == 0


@pytest.mark.parametrize("mode", ["fail", "partial", "skip"])
def test_fallback_fills_what_the_repair_did_not(client, script, ai, mode):
    ai.repair = mode
    data = generate(client, script["id"])
    close = [s for s in data["shots"] if s["size"] == "CU"]
    assert [(s["angle"], s["movement"], s["lens"]) for s in close] == [("Eye level", "Static", "85mm")] * 2
    assert all(all(s[f] for f in TECH) for s in data["shots"])
    assert client.get(base(script["id"])).json()["status"] == "ready"  # a failed repair isn't a failed generation


def test_regeneration_is_repaired_and_normalised(client, script, ai, roomy):
    sid = script["id"]
    shot = generate(client, sid)["shots"][0]
    r = client.post(f"{base(sid)}/{shot['id']}/regenerate", json={"instruction": "tighter"}).json()
    assert (r["size"], r["angle"], r["movement"], r["lens"]) == ("ECU", "Low angle", "Push-in", "100mm")
    ai.repair = "fail"
    r = client.post(f"{base(sid)}/{shot['id']}/regenerate", json={}).json()
    assert (r["angle"], r["movement"], r["lens"]) == ("Eye level", "Static", "100mm")


def test_the_repair_is_part_of_the_same_metered_action(client, script, ai, monkeypatch):
    sid = script["id"]
    monkeypatch.setenv("FREE_SHOT_REGENERATIONS_PER_DAY", "1")
    shot = generate(client, sid)["shots"][1]
    assert [e.kind for e in usage_events()] == ["shot_generate_full", "shot_generate_scene", "shot_generate_scene"]
    assert client.post(f"{base(sid)}/{shot['id']}/regenerate", json={}).status_code == 200
    assert [e.kind for e in usage_events()][-1] == "shot_regenerate" and len(usage_events()) == 4
    assert any(c[0] == "repair" for c in ai.calls[-2:])  # the repair did run
    r = client.post(f"{base(sid)}/{shot['id']}/regenerate", json={})
    assert r.status_code == 429  # one regeneration used, not two


@pytest.mark.parametrize("raw, expected", [
    ("eye level", "Eye level"), ("EYE-LEVEL", "Eye level"), ("low", "Low angle"), ("High Angle", "High angle"),
    ("bird's eye", "Overhead"), ("dutch", "Dutch angle"), ("over the shoulder", "Over-the-shoulder"),
    ("OTS", "Over-the-shoulder"), ("point of view", "POV"), ("pov", "POV"), ("profile", "Profile"),
    ("Low angle, slightly canted", "Low angle"), ("worm's-eye", "Low angle"), ("eyeline match", "Eyeline match"),
    ("  hip level  ", "Hip level"), ("", ""), ("-", ""), ("N/A", ""),
])
def test_angle_normalisation(raw, expected):
    assert shotfix.normalize_angle(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("static", "Static"), ("Locked off", "Static"), ("slow push-in", "Push-in"), ("Dolly in", "Push-in"),
    ("pull back", "Pull-out"), ("PAN", "Pan"), ("tilt up", "Tilt"), ("tracking", "Tracking"),
    ("hand-held", "Handheld"), ("dolly", "Dolly"), ("crane up", "Crane"), ("rack focus", "Rack focus"),
    ("focus pull to John", "Rack focus"), ("—", ""), ("none", ""), ("Snorricam", "Snorricam"),
])
def test_movement_normalisation(raw, expected):
    assert shotfix.normalize_movement(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("35mm", "35mm"), ("35 MM", "35mm"), (" 85mm lens ", "85mm"), ("50", "50mm"), ("24-70mm zoom", "24mm"),
    ("85mm f/1.4", "85mm"), ("anamorphic 40mm", "40mm"), ("12.5mm", "12.5mm"), ("wide", ""), ("n/a", ""), ("", ""),
])
def test_lens_normalisation(raw, expected):
    assert shotfix.normalize_lens(raw) == expected


@pytest.mark.parametrize("size, lens", [("EWS", "16mm"), ("WS", "24mm"), ("MLS", "35mm"), ("MS", "50mm"),
                                        ("MCU", "65mm"), ("CU", "85mm"), ("ECU", "100mm")])
def test_fallback_lens_follows_the_size(size, lens):
    spec = ShotSpec(size=size, angle="", movement="", lens="", description="x", composition="", notes="")
    report = shotfix.finish([spec], repair=None)
    assert (spec.angle, spec.movement, spec.lens) == ("Eye level", "Static", lens) and report.fallback == 3


def test_fill_blanks_never_touches_a_set_value():
    spec = ShotSpec(size="CU", angle="Whip-pan into the door", movement="", lens="", description="x",
                    composition="", notes="")
    shotfix.fill_blanks([spec], repair=lambda inc: [type("F", (), {"index": 1, "angle": "Low angle",
                                                                    "movement": "Pan", "lens": "35mm"})()])
    assert (spec.angle, spec.movement, spec.lens) == ("Whip-pan into the door", "Pan", "35mm")


# ---------- 3. shots change only through explicit user actions ----------

def test_screenplay_edits_restores_and_ai_edits_leave_shots_alone(client, script, ai, roomy):
    sid = script["id"]
    generate(client, sid)
    before = snapshot(client, sid)

    # manual edit that even removes the second scene from the text
    shorter = SCREENPLAY.split("EXT. TRAIN PLATFORM")[0]
    assert client.put(f"/api/scripts/{sid}/content", json={"content": shorter}).status_code == 200
    assert snapshot(client, sid) == before
    # AI edit, proposed and applied
    ai.edits = [EditOp(find="You came.", replace="You're late.", reason="Tension")]
    prop = client.post(f"/api/scripts/{sid}/chat", json={"message": "Sharper", "mode": "edit"}).json()["messages"][1]["proposal"]
    assert client.post(f"/api/scripts/{sid}/proposals/{prop['id']}/apply", json={}).status_code == 200
    assert snapshot(client, sid) == before
    # restore the original
    revs = client.get(f"/api/scripts/{sid}/revisions").json()["revisions"]
    assert client.post(f"/api/scripts/{sid}/revisions/{revs[-1]['id']}/restore").status_code == 200
    assert snapshot(client, sid) == before
    # discussing the screenplay
    client.post(f"/api/scripts/{sid}/chat", json={"message": "Who is Sarah?"})
    assert snapshot(client, sid) == before


def test_loading_and_polling_never_modify_shots(client, script, ai):
    sid = script["id"]
    generate(client, sid)
    before = snapshot(client, sid)
    for _ in range(3):
        client.get(base(sid))
        client.get(f"/api/scripts/{sid}")
        client.get("/api/scripts")
        client.get(f"/api/scripts/{sid}/messages?channel=shots")
        client.get("/api/me/usage")
    assert snapshot(client, sid) == before


def test_assistant_update_changes_only_the_named_fields(client, script, ai):
    sid = script["id"]
    generate(client, sid)
    before = snapshot(client, sid)
    target = next(iter(before))
    ai.shot_ops = [ShotOp(op="update", shot_id=target, fields=ShotPatch(lens="40mm"))]
    client.post(f"{base(sid)}/chat", json={"message": "Make shot 1 a touch tighter"})
    after = snapshot(client, sid)
    changed = {k for k in before[target] if before[target][k] != after[target][k]}
    assert changed - {"updated_at"} == {"lens"} and after[target]["lens"] == "40mm"
    assert {k: v for k, v in after.items() if k != target} == {k: v for k, v in before.items() if k != target}


def test_regenerating_one_shot_leaves_every_other_shot_alone(client, script, ai):
    sid = script["id"]
    generate(client, sid)
    before = snapshot(client, sid)
    target = list(before)[1]
    client.post(f"{base(sid)}/{target}/regenerate", json={"instruction": "insert"})
    after = snapshot(client, sid)
    assert after[target] != before[target]
    assert {k: v for k, v in after.items() if k != target} == {k: v for k, v in before.items() if k != target}


def test_scene_regeneration_only_replaces_that_scene(client, script, ai, roomy):
    sid = script["id"]
    generate(client, sid)
    before = snapshot(client, sid)
    generate(client, sid, scene=1)
    after = snapshot(client, sid)
    scene2 = lambda snap: {k: v for k, v in snap.items() if v["scene_number"] == 2}  # noqa: E731
    assert scene2(after) == scene2(before)


def test_repair_and_fallback_never_touch_existing_shots(client, script, ai, roomy):
    sid = script["id"]
    generate(client, sid)
    # a hand-made shot with blank technical fields, in each scene
    for scene in (1, 2):
        client.post(base(sid), json={"scene_number": scene, "description": f"Hand-made {scene}", "size": "MS"})
    handmade = {k: v for k, v in snapshot(client, sid).items() if v["description"].startswith("Hand-made")}
    assert all(all(v[f] == "" for f in TECH) for v in handmade.values())
    assert client.get(base(sid)).json()["incomplete"] == 2

    shot_in_scene_1 = next(v["id"] for v in snapshot(client, sid).values()
                           if v["scene_number"] == 1 and not v["description"].startswith("Hand-made"))
    client.post(f"{base(sid)}/{shot_in_scene_1}/regenerate", json={})  # repair runs for the new spec only
    ai.shot_ops = [ShotOp(op="update", shot_id=shot_in_scene_1, fields=ShotPatch(notes="tighter"))]
    client.post(f"{base(sid)}/chat", json={"message": "Note on shot 1"})
    after = snapshot(client, sid)
    assert {k: after[k] for k in handmade} == handmade  # still blank, untouched
    repairs = [c for c in ai.calls if c[0] == "repair"]
    assert all("Hand-made" not in c[2] for c in repairs)


def test_resumed_job_leaves_completed_scenes_exactly_as_they_were(client, script, ai, monkeypatch):
    from planyourshot.web import settings, worker

    monkeypatch.setenv("RUN_JOBS_INLINE", "0")
    settings.reset_settings()
    sid = script["id"]
    real, armed = ai.scene_shots, {"crash": True}

    def scene_shots(title, n, *rest):
        if n == 2 and armed.pop("crash", False):
            raise RuntimeError("worker died")
        return real(title, n, *rest)

    monkeypatch.setattr(ai, "scene_shots", scene_shots)
    client.post(f"{base(sid)}/generate", json={})
    worker.run_once()
    scene1 = {k: v for k, v in snapshot(client, sid).items() if v["scene_number"] == 1}
    assert len(scene1) == 2
    worker.run_once()
    after = snapshot(client, sid)
    assert {k: v for k, v in after.items() if v["scene_number"] == 1} == scene1
    assert len([v for v in after.values() if v["scene_number"] == 2]) == 2


# ---------- 4. Fill missing details ----------

def _make_gaps(client, sid):
    """An 'old' list: some shots have blanks, some don't. Returns (partial, empty, complete) ids."""
    generate(client, sid)
    ids = [s["id"] for s in client.get(base(sid)).json()["shots"]]
    partial, empty, complete = ids[0], ids[1], ids[2]
    client.patch(f"{base(sid)}/{partial}", json={"angle": "", "movement": "Whip-pan into the door", "lens": "-"})
    client.patch(f"{base(sid)}/{empty}", json={"angle": "", "movement": "", "lens": ""})
    return partial, empty, complete


def test_fill_missing_fills_blanks_and_leaves_set_values(client, script, ai, roomy):
    sid = script["id"]
    partial, empty, complete = _make_gaps(client, sid)
    before = snapshot(client, sid)
    assert client.get(base(sid)).json()["incomplete"] == 2
    ai.calls.clear()

    r = client.post(f"{base(sid)}/fill-missing")
    assert r.status_code == 200
    body = r.json()
    assert body["incomplete"] == 0 and body["filled"] == {"shots": 2, "repaired": 5, "fallback": 0}
    after = snapshot(client, sid)
    assert (after[partial]["angle"], after[partial]["movement"], after[partial]["lens"]) == \
        ("Low angle", "Whip-pan into the door", "85mm")  # the set movement is kept, word for word
    assert (after[empty]["angle"], after[empty]["movement"], after[empty]["lens"]) == ("Low angle", "Push-in", "85mm")
    for k in before:
        if k not in (partial, empty):
            assert after[k] == before[k]  # complete shots untouched, updated_at included
    for k in (partial, empty):
        same = {f for f in before[k] if f not in TECH + ("updated_at", "missing")}
        assert before[k]["missing"] and not after[k]["missing"]
        assert {f: after[k][f] for f in same} == {f: before[k][f] for f in same}
    # one repair call, listing only the incomplete shots
    [call] = [c for c in ai.calls if c[0] == "repair"]
    assert call[2].count("\n") == 1 and "Establish scene 1" not in call[2].split("\n")[1]


def test_fill_missing_uses_the_fallback_and_counts_as_one_scene_regeneration(client, script, ai, monkeypatch):
    sid = script["id"]
    monkeypatch.setenv("FREE_REQUESTS_PER_MINUTE", "1000")
    partial, empty, _ = _make_gaps(client, sid)
    ai.repair = "fail"
    monkeypatch.setenv("FREE_SCENE_GENERATIONS_PER_DAY", "1")
    before = len(usage_events())
    r = client.post(f"{base(sid)}/fill-missing").json()
    assert r["filled"]["fallback"] == 5
    after = snapshot(client, sid)
    assert (after[empty]["angle"], after[empty]["movement"], after[empty]["lens"]) == ("Eye level", "Static", after[empty]["lens"])
    assert after[empty]["lens"] and after[partial]["movement"] == "Whip-pan into the door"
    events = usage_events()[before:]
    assert [(e.kind, e.run_id) for e in events] == [("shot_generate_scene", None)]  # one action
    r = client.post(f"{base(sid)}/generate", json={"scene_number": 1})
    assert r.status_code == 429 and "single-scene generations" in r.json()["detail"]


def test_fill_missing_with_nothing_to_fill(client, script, ai):
    sid = script["id"]
    generate(client, sid)
    before = len(usage_events())
    r = client.post(f"{base(sid)}/fill-missing")
    assert r.status_code == 409 and "already has" in r.json()["detail"]
    assert len(usage_events()) == before


def test_fill_missing_is_scoped_to_the_owner(make_client, client, script, ai):
    sid = script["id"]
    _make_gaps(client, sid)
    other = make_client("b@example.com")
    assert other.post(f"{base(sid)}/fill-missing").status_code == 404
