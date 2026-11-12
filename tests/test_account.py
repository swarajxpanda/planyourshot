"""Account deletion, data export, and terms acceptance."""
import csv
import io
import json
import zipfile

from sqlalchemy import func, select

from conftest import upload
from planyourshot.web import models
from planyourshot.web.db import new_session
from planyourshot.web.settings import get_settings

# Every table the app writes, with how to find one user's rows in it.
def _owned_counts(user_id, script_ids):
    m = models
    db = new_session()
    try:
        count = lambda q: db.scalar(select(func.count()).select_from(q.subquery()))  # noqa: E731
        sids = script_ids or ["-"]
        return {
            "users": count(select(m.User).where(m.User.id == user_id)),
            "scripts": count(select(m.Script).where(m.Script.user_id == user_id)),
            "revisions": count(select(m.Revision).where(m.Revision.script_id.in_(sids))),
            "messages": count(select(m.Message).where(m.Message.script_id.in_(sids))),
            "proposals": count(select(m.Proposal).where(m.Proposal.script_id.in_(sids))),
            "shots": count(select(m.Shot).where(m.Shot.script_id.in_(sids))),
            "jobs": count(select(m.Job).where(m.Job.user_id == user_id)),
            "usage_events": count(select(m.UsageEvent).where(m.UsageEvent.user_id == user_id)),
            "rate_limit_hits": count(select(m.RateLimitHit).where(m.RateLimitHit.key.like(f"%:{user_id}"))),
            "leases": count(select(m.Lease).where(m.Lease.name == f"user:{user_id}")),
        }
    finally:
        db.close()


def _fill_account(client):
    sid = upload(client).json()["id"]
    upload(client, "second.txt")
    client.post(f"/api/scripts/{sid}/chat", json={"message": "Who is Sarah?"})
    client.post(f"/api/scripts/{sid}/chat", json={"message": "Tighten it", "mode": "edit"})
    client.post(f"/api/scripts/{sid}/shots/generate", json={})
    client.post(f"/api/scripts/{sid}/shots/chat", json={"message": "Add a wide shot"})
    client.get("/api/me/export")
    return sid


def test_delete_account_removes_every_row_and_file(make_client, client, ai):
    other = make_client("b@example.com")
    other_sid = upload(other).json()["id"]
    sid = _fill_account(client)
    me = client.get("/api/me").json()
    sids = [s["id"] for s in client.get("/api/scripts").json()["scripts"]]
    before = _owned_counts(me["id"], sids)
    # the fixture really did create something in every table
    assert all(before[k] > 0 for k in before if k != "leases"), before
    user_dir = get_settings().storage_dir / me["id"]
    assert user_dir.is_dir() and any(user_dir.iterdir())

    assert client.request("DELETE", "/api/me", json={"confirm": "delete"}).status_code == 422
    assert client.get(f"/api/scripts/{sid}").status_code == 200  # nothing happened
    assert client.request("DELETE", "/api/me", json={"confirm": "DELETE"}).status_code == 204

    assert _owned_counts(me["id"], sids) == {k: 0 for k in before}
    assert not user_dir.exists()
    assert client.get("/api/me").status_code == 401  # session cleared
    # other accounts are untouched
    assert other.get(f"/api/scripts/{other_sid}").status_code == 200
    # signing in again starts a fresh, empty account
    client.post("/api/auth/dev-login", json={"email": "a@example.com", "name": "A"})
    assert client.get("/api/scripts").json()["scripts"] == []


def test_export_contains_text_shots_and_conversations(client, script, ai):
    sid = script["id"]
    client.post(f"/api/scripts/{sid}/chat", json={"message": "Who is Sarah?"})
    client.post(f"/api/scripts/{sid}/chat", json={"message": "Tighten it", "mode": "edit"})
    client.post(f"/api/scripts/{sid}/shots/generate", json={})
    client.post(f"/api/scripts/{sid}/shots/chat", json={"message": "Add a wide shot"})
    assert client.get(f"/api/scripts/{sid}/shots/export.csv").status_code == 403  # per-plan button: still off

    r = client.get("/api/me/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "plan-your-shot-export.zip" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = sorted(z.namelist())
    assert names == ["01 the last train/conversations.json", "01 the last train/screenplay.txt",
                     "01 the last train/shots.csv", "account.json"]

    assert z.read("01 the last train/screenplay.txt").decode() == script["revision"]["content"]
    rows = list(csv.reader(io.StringIO(z.read("01 the last train/shots.csv").decode("utf-8-sig"))))
    assert rows[0][:4] == ["Scene", "Scene heading", "Shot", "Size"] and len(rows) == 5
    assert rows[1][1] == "INT. COFFEE SHOP - NIGHT" and rows[1][7] == "Establish scene 1"

    conv = json.loads(z.read("01 the last train/conversations.json"))
    assert [m["content"] for m in conv["screenplay"]][:2] == ["Who is Sarah?", "Thoughts on the last train: Who is Sarah?"]
    assert conv["screenplay"][3]["proposal"]["edits"][0]["find"] == "I almost didn't."
    assert [m["role"] for m in conv["shot_list"]] == ["user", "assistant"]
    account = json.loads(z.read("account.json"))
    assert account["email"] == "a@example.com" and account["screenplays"] == 1 and account["terms_accepted_at"]


def test_export_of_an_empty_account(client):
    z = zipfile.ZipFile(io.BytesIO(client.get("/api/me/export").content))
    assert z.namelist() == ["account.json"]


def test_terms_accepted_on_first_sign_in_only(make_client):
    c = make_client("a@example.com")
    db = new_session()
    try:
        first = db.scalar(select(models.User.terms_accepted_at).where(models.User.email == "a@example.com"))
    finally:
        db.close()
    assert first is not None
    c.post("/api/auth/logout")
    c.post("/api/auth/dev-login", json={"email": "a@example.com", "name": "A"})
    db = new_session()
    try:
        again = db.scalar(select(models.User.terms_accepted_at).where(models.User.email == "a@example.com"))
    finally:
        db.close()
    assert again == first
