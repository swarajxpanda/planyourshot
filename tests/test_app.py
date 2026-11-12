import io

import docx

from conftest import SCREENPLAY, upload
from planyourshot.web.ai import EditOp, ShotOp, ShotPatch


def test_requires_login(make_client):
    c = make_client()
    assert c.get("/api/me").status_code == 401
    assert c.get("/api/scripts").status_code == 401
    assert c.post("/api/scripts", files={"file": ("a.txt", b"x")}).status_code == 401
    assert c.get("/api/auth/login", follow_redirects=False).status_code == 503  # Google not configured


def test_upload_parse_and_persist(client, script):
    assert script["status"] == "ready" and script["title"] == "the last train"
    assert [s["heading"] for s in script["scenes"]] == ["INT. COFFEE SHOP - NIGHT", "EXT. TRAIN PLATFORM - LATER"]
    assert script["scenes"][0]["location"] == "COFFEE SHOP" and script["scenes"][0]["time_of_day"] == "NIGHT"
    assert "WE NEED TO TALK" in script["revision"]["content"] and script["revision"]["number"] == 1
    assert client.get(f"/api/scripts/{script['id']}/file").content == SCREENPLAY.encode()
    listing = client.get("/api/scripts").json()
    assert listing["limit"] == 2 and len(listing["scripts"]) == 1


def test_docx_and_bad_files(client):
    d = docx.Document()
    for line in SCREENPLAY.split("\n\n"):
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    r = upload(client, "script.docx", buf.getvalue())
    assert r.status_code == 201
    assert len(client.get(f"/api/scripts/{r.json()['id']}").json()["scenes"]) == 2

    assert upload(client, "notes.md").status_code == 415
    assert upload(client, "fake.pdf", b"not a pdf").status_code == 415
    assert upload(client, "empty.txt", b"").status_code == 400
    r = upload(client, "tiny.txt", b"hi")
    assert client.get(f"/api/scripts/{r.json()['id']}").json()["status"] == "failed"


def test_script_limit_is_enforced_server_side(client):
    for i in range(2):
        assert upload(client, f"s{i}.txt").status_code == 201
    r = upload(client, "s3.txt")
    assert r.status_code == 409 and "limit of 2 screenplays on the Free plan" in r.json()["detail"]
    first = client.get("/api/scripts").json()["scripts"][0]["id"]
    assert client.delete(f"/api/scripts/{first}").status_code == 204
    assert upload(client, "s3.txt").status_code == 201


def test_users_are_isolated(make_client, client, script, ai):
    other = make_client("b@example.com")
    sid = script["id"]
    assert other.get("/api/scripts").json()["scripts"] == []
    for method, path in [
        ("get", f"/api/scripts/{sid}"), ("delete", f"/api/scripts/{sid}"), ("get", f"/api/scripts/{sid}/file"),
        ("get", f"/api/scripts/{sid}/messages"), ("get", f"/api/scripts/{sid}/shots"),
        ("get", f"/api/scripts/{sid}/revisions"), ("get", f"/api/scripts/{sid}/shots/export.csv"),
    ]:
        assert getattr(other, method)(path).status_code == 404, path
    assert other.patch(f"/api/scripts/{sid}", json={"title": "x"}).status_code == 404
    assert other.post(f"/api/scripts/{sid}/chat", json={"message": "hi"}).status_code == 404
    assert other.post(f"/api/scripts/{sid}/shots/generate", json={}).status_code == 404
    assert client.get(f"/api/scripts/{sid}").status_code == 200


def test_rename_delete(client, script):
    sid = script["id"]
    assert client.patch(f"/api/scripts/{sid}", json={"title": "  Last Train  "}).json()["title"] == "Last Train"
    assert client.patch(f"/api/scripts/{sid}", json={"title": "   "}).status_code == 422
    assert client.delete(f"/api/scripts/{sid}").status_code == 204
    assert client.get(f"/api/scripts/{sid}").status_code == 404


def test_discuss_chat_keeps_history(client, script, ai):
    sid = script["id"]
    r = client.post(f"/api/scripts/{sid}/chat", json={"message": "Who is Sarah?"})
    assert r.status_code == 200 and r.json()["messages"][1]["role"] == "assistant"
    client.post(f"/api/scripts/{sid}/chat", json={"message": "And John?"})
    _, context, history, _ = ai.calls[-1]
    assert "COFFEE SHOP" in context
    assert [h[0] for h in history] == ["user", "assistant"] and history[0][1] == "Who is Sarah?"
    assert len(client.get(f"/api/scripts/{sid}/messages").json()["messages"]) == 4
    # nothing changes the screenplay in discuss mode
    assert client.get(f"/api/scripts/{sid}").json()["revision"]["number"] == 1


def test_ai_failure_is_reported_and_not_saved(client, script, ai):
    ai.fail = True
    r = client.post(f"/api/scripts/{script['id']}/chat", json={"message": "hi"})
    assert r.status_code == 502 and "try again" in r.json()["detail"].lower()
    assert client.get(f"/api/scripts/{script['id']}/messages").json()["messages"] == []


def test_edit_proposal_review_apply_and_history(client, script, ai):
    sid = script["id"]
    ai.edits = [
        EditOp(find="I almost didn't.", replace="I almost stayed home.", reason="Sharper line"),
        EditOp(find="Rain hammers the\nempty   platform.", replace="Rain floods the empty platform.", reason="Stronger verb"),
        EditOp(find="This text is not in the script", replace="x", reason="bad anchor"),
    ]
    msgs = client.post(f"/api/scripts/{sid}/chat", json={"message": "Tighten it", "mode": "edit"}).json()["messages"]
    prop = msgs[1]["proposal"]
    assert prop["status"] == "pending" and len(prop["edits"]) == 2  # unanchored edit dropped
    assert "couldn't be matched" in msgs[1]["content"]
    # Not applied until approved
    assert "I almost didn't." in client.get(f"/api/scripts/{sid}").json()["revision"]["content"]

    r = client.post(f"/api/scripts/{sid}/proposals/{prop['id']}/apply", json={"indices": [1]})
    assert r.status_code == 200
    body = r.json()
    assert [e["status"] for e in body["proposal"]["edits"]] == ["rejected", "applied"]
    assert "Rain floods the empty platform." in body["revision"]["content"]
    assert "I almost didn't." in body["revision"]["content"] and body["revision"]["number"] == 2
    assert client.post(f"/api/scripts/{sid}/proposals/{prop['id']}/apply", json={}).status_code == 409

    revs = client.get(f"/api/scripts/{sid}/revisions").json()["revisions"]
    assert [(r["number"], r["source"]) for r in revs] == [(2, "ai_edit"), (1, "upload")]
    original = client.get(f"/api/scripts/{sid}/revisions/{revs[1]['id']}").json()
    assert "Rain hammers" in original["content"]  # original preserved

    restored = client.post(f"/api/scripts/{sid}/revisions/{revs[1]['id']}/restore").json()
    assert restored["revision"]["number"] == 3 and "Rain hammers" in restored["revision"]["content"]

    # reject path
    ai.edits = [EditOp(find="You came.", replace="You're late.", reason="Tension")]
    p2 = client.post(f"/api/scripts/{sid}/chat", json={"message": "again", "mode": "edit"}).json()["messages"][1]["proposal"]
    assert client.post(f"/api/scripts/{sid}/proposals/{p2['id']}/reject").json()["proposal"]["status"] == "rejected"
    assert "You came." in client.get(f"/api/scripts/{sid}").json()["revision"]["content"]


def test_stale_proposal_cannot_be_applied(client, script, ai):
    sid = script["id"]
    prop = client.post(f"/api/scripts/{sid}/chat", json={"message": "x", "mode": "edit"}).json()["messages"][1]["proposal"]
    new = SCREENPLAY.replace("I almost didn't.", "No.")
    assert client.put(f"/api/scripts/{sid}/content", json={"content": new}).json()["revision"]["source"] == "manual"
    assert client.put(f"/api/scripts/{sid}/content", json={"content": new}).status_code == 409
    assert client.post(f"/api/scripts/{sid}/proposals/{prop['id']}/apply", json={}).status_code == 409


def test_shot_list_generate_and_curate(client, script, ai):
    sid = script["id"]
    base = f"/api/scripts/{sid}/shots"
    assert client.get(base).json() == {"shots": [], "incomplete": 0, "status": "none", "error": "", "done": 0, "total": 0}
    assert client.post(f"{base}/generate", json={}).status_code == 202
    data = client.get(base).json()
    assert data["status"] == "ready" and (data["done"], data["total"]) == (2, 2)
    shots = data["shots"]
    assert [(s["scene_number"], s["position"], s["size"]) for s in shots] == [(1, 1, "WS"), (1, 2, "CU"), (2, 1, "WS"), (2, 2, "CU")]
    assert shots[0]["scene_heading"] == "INT. COFFEE SHOP - NIGHT" and shots[0]["location"] == "COFFEE SHOP"
    assert any(c[0] == "shots" and "lens" in c[2].lower() for c in ai.calls)  # KB guidance retrieved

    a, b, c, d = (s["id"] for s in shots)
    # edit
    r = client.patch(f"{base}/{a}", json={"size": "ews", "lens": "18mm", "characters": ["Sarah", " "]})
    assert (r.json()["size"], r.json()["lens"], r.json()["characters"]) == ("EWS", "18mm", ["Sarah"])
    assert client.patch(f"{base}/{a}", json={"size": "HUGE"}).status_code == 422
    # duplicate lands right after the original
    ids = [s["id"] for s in client.post(f"{base}/{a}/duplicate").json()["shots"] if s["scene_number"] == 1]
    assert ids[0] == a and ids[2] == b and len(ids) == 3
    dup = ids[1]
    # add
    out = client.post(base, json={"scene_number": 2, "index": 0, "size": "MS", "description": "Insert"}).json()["shots"]
    s2 = [s for s in out if s["scene_number"] == 2]
    assert s2[0]["description"] == "Insert" and [s["position"] for s in s2] == [1, 2, 3]
    assert client.post(base, json={"scene_number": 9}).status_code == 422
    # reorder within a scene, then move across scenes
    out = client.put(f"{base}/{b}/move", json={"scene_number": 1, "index": 0}).json()["shots"]
    assert [s["id"] for s in out if s["scene_number"] == 1] == [b, a, dup]
    out = client.put(f"{base}/{b}/move", json={"scene_number": 2, "index": 99}).json()["shots"]
    assert [s["id"] for s in out if s["scene_number"] == 1] == [a, dup]
    moved = [s for s in out if s["id"] == b][0]
    assert (moved["scene_number"], moved["position"], moved["scene_heading"]) == (2, 4, "EXT. TRAIN PLATFORM - LATER")
    # delete resequences
    out = client.delete(f"{base}/{a}").json()["shots"]
    assert [(s["id"], s["position"]) for s in out if s["scene_number"] == 1] == [(dup, 1)]
    # regenerate one shot only
    r = client.post(f"{base}/{c}/regenerate", json={"instruction": "tighter"}).json()
    assert r["size"] == "ECU" and r["description"] == "Regenerated: tighter"
    assert [s for s in client.get(base).json()["shots"] if s["id"] == d][0]["size"] == "CU"
    # regenerate a single scene leaves the other untouched
    client.post(f"{base}/generate", json={"scene_number": 1})
    out = client.get(base).json()["shots"]
    assert len([s for s in out if s["scene_number"] == 1]) == 2 and len([s for s in out if s["scene_number"] == 2]) == 4
    # CSV export is a Pro feature (see test_limits.py for the allowed case)
    r = client.get(f"{base}/export.csv")
    assert r.status_code == 403 and "Free plan" in r.json()["detail"]


def test_shot_chat_applies_operations(client, script, ai):
    sid = script["id"]
    base = f"/api/scripts/{sid}/shots"
    client.post(f"{base}/generate", json={})
    a, b, c, d = (s["id"] for s in client.get(base).json()["shots"])
    ai.shot_ops = [
        ShotOp(op="update", shot_id=a, fields=ShotPatch(lens="35mm", movement="Slow push-in")),
        ShotOp(op="delete", shot_id=b),
        ShotOp(op="add", scene_number=2, after_shot_id=c, fields=ShotPatch(size="MCU", description="Sarah's face at the glass")),
        ShotOp(op="delete", shot_id=99999),
    ]
    r = client.post(f"{base}/chat", json={"message": "Make scene 1 more intimate"}).json()
    assert r["changes"] == {"added": 1, "updated": 1, "deleted": 1, "skipped": 1}
    shots = r["shots"]
    assert [(s["scene_number"], s["position"]) for s in shots] == [(1, 1), (2, 1), (2, 2), (2, 3)]
    assert shots[0]["lens"] == "35mm" and shots[0]["size"] == "WS"  # untouched fields kept
    assert shots[2]["description"] == "Sarah's face at the glass" and shots[3]["id"] == d
    assert "#" + str(a) in ai.calls[-1][1]
    assert len(client.get(f"/api/scripts/{sid}/messages?channel=shots").json()["messages"]) == 2
    assert client.get(f"/api/scripts/{sid}/messages").json()["messages"] == []  # channels are separate


def test_generation_failure_is_surfaced(client, script, ai):
    ai.fail = True
    base = f"/api/scripts/{script['id']}/shots"
    client.post(f"{base}/generate", json={})
    data = client.get(base).json()
    assert data["status"] == "failed" and data["error"] and data["shots"] == []
    ai.fail = False
    client.post(f"{base}/generate", json={})
    assert client.get(base).json()["status"] == "ready"


def test_logout_and_session_persistence(make_client, script):
    c = make_client("a@example.com")  # same account, new browser
    assert [s["id"] for s in c.get("/api/scripts").json()["scripts"]] == [script["id"]]
    c.post("/api/auth/logout")
    assert c.get("/api/me").status_code == 401
