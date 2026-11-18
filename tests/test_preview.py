"""Vorschau im Benutzer-Editor und in Meine Einstellungen: "Ein Anruf von ... ginge jetzt an ..., weil ..." - dieselbe Entscheidung
wie beim echten Anruf (db.resolve und Anrufregel ohne Klingelzeit)."""
from app import main
from tests.conftest import run
from tests.test_routing import call, ring, staff

SON = "015100000001"


def test_preview_endpoint_uses_same_decision(bridge):
    async def scenario(b):
        ring(b.db)
        dep = staff(b.db, "Peter Vertreter", "**630", msn="")
        sid = staff(b.db, "Petra Muster", "**610", status="dnd", deputy_id=dep)
        b.db.x("UPDATE staff SET dnd_allow=? WHERE id=?", (SON, sid))
        b.db.save_contact({"name": "Sohn Muster", "number": SON})
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        try:
            r = (await b.http.post(f"/api/staff/{sid}/preview", json={"caller": SON})).json()
            assert r["text"].startswith("Ein Anruf von Sohn Muster (" + SON + ") ginge jetzt an Petra Muster (**610), weil:"), r
            assert "Ausnahme" in r["text"] and r["called"] == "0301111"
            r = (await b.http.post(f"/api/staff/{sid}/preview", json={"caller": "030555444"})).json()
            assert "an die Vertretung Peter Vertreter (**630)" in r["text"] and r["via_deputy"]
            assert await call(b, frm="030555444")[1] == "fdial:20:**630"              # der echte Anruf geht genauso
            r = (await b.http.post(f"/api/staff/{sid}/preview", json={"caller": ""})).json()
            assert r["text"].startswith("Ein Anruf von unterdrückter Nummer")
            ring(b.db, 0)                     # Anrufregel ohne Klingelzeit: sofort die KI (wie /internal/call)
            r = (await b.http.post(f"/api/staff/{sid}/preview", json={"caller": SON})).json()
            assert r["ai"] and "direkt an den Anrufbeantworter" in r["text"]
            assert (await b.http.post("/api/staff/999/preview", json={"caller": SON})).status_code == 404
        finally:
            main.app.dependency_overrides.pop(main.auth, None)
        uid = b.db.x("INSERT INTO users(username,pw_hash,role,staff_id) VALUES('petra','x','user',?)", (sid,))
        main.app.dependency_overrides[main.user_auth] = lambda: {"id": uid, "username": "petra", "role": "user", "staff_id": sid, "disabled": 0}
        try:
            ring(b.db)
            r = await b.http.post("/api/me/preview", json={"caller": "030555444", "called": "0301111"})
            assert r.status_code == 200 and "Vertretung" in r.json()["text"]
        finally:
            main.app.dependency_overrides.pop(main.user_auth, None)
    run(bridge, scenario)


def test_preview_ui_present():
    import os
    from tests.conftest import ROOT
    html = open(os.path.join(ROOT, "bridge", "app", "static", "index.html"), encoding="utf-8").read()
    assert 'previewBox("pv_st"' in html and 'previewBox("pv_my"' in html and "/api/me/preview" in html
    assert 'initTag("pv_st_caller","caller",true)' in html
