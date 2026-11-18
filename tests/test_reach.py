"""Erreichbarkeit an einer Stelle (db.resolve): dieselbe Rangfolge beim Klingeln, beim Verbinden durch die KI und in der
Mitarbeiterliste der KI. Rangfolge: nicht stoeren > Waehlregel > Zeitplan > manueller Status, danach die Vertretung."""
import datetime
import uuid

from app import schedule
from tests.conftest import run
from tests.test_routing import call, ring, staff

SON = "015100000001"
BOSS = "01719998887"
NOW = datetime.datetime(2026, 11, 16, 10, 0)          # Montag, 10 Uhr
NIGHT_ALL = schedule.normalize([{"days": list(range(7)), "from": "00:00", "to": "24:00", "status": "closed", "note": "Nacht"}])


def row(db, sid):
    return next(s for s in db.list_staff() if s["id"] == sid)


def test_dnd_beats_dialrule_office(bridge):
    db = bridge.db
    dep = staff(db, "Peter Vertreter", "**630", msn="")
    sid = staff(db, "Petra Muster", "**610", status="dnd", deputy_id=dep,
                dialrules=[{"name": "Alle", "caller_mode": "any", "action": "office"}])
    db.x("UPDATE staff SET dnd_allow=? WHERE id=?", (SON, sid))
    r = db.resolve(row(db, sid), "030555444", "", NOW)
    assert r["blocked"] and r["rule"] is None and r["via_deputy"] and r["number"] == "**630"      # Regel "erreichbar" holt nicht durch
    assert "nicht stören" in r["reason"] and "Vertretung Peter Vertreter" in r["reason"]
    r = db.resolve(row(db, sid), SON, "", NOW)
    assert r["number"] == "**610" and not r["via_deputy"] and r["rule"]["name"] == "Alle"       # Ausnahme: Regel gilt


def test_dialrule_off_for_caller_and_deputy_rules(bridge):
    db = bridge.db
    dep = staff(db, "Peter Vertreter", "**630", msn="",
                dialrules=[{"name": "Chef nicht", "caller_mode": "list", "callers": [BOSS], "action": "off"}])
    sid = staff(db, "Petra Muster", "**610", deputy_id=dep,
                dialrules=[{"name": "Chef nicht", "caller_mode": "list", "callers": [BOSS], "action": "off"}])
    r = db.resolve(row(db, sid), BOSS, "", NOW)
    assert r["number"] == "" and r["blocked"] and r["status"] == "off"            # die Vertretung sperrt den Chef auch (ihre Regel)
    assert "Vertretung Peter Vertreter ist auch nicht erreichbar" in r["reason"]
    assert db.resolve(row(db, sid), "030555444", "", NOW)["number"] == "**610"
    assert db.reach_number(row(db, sid), BOSS) == ""


def test_schedule_applies_to_dnd_exceptions(bridge):
    db = bridge.db
    sid = staff(db, "Petra Muster", "**610", status="dnd", use_hours=1, schedule=NIGHT_ALL)
    db.x("UPDATE staff SET dnd_allow=? WHERE id=?", (SON, sid))
    r = db.resolve(row(db, sid), SON, "", NOW)
    assert r["number"] == "" and r["status"] == "off" and "Zeitplan: außerhalb der Bürozeit (Nacht)" in r["reason"]
    assert row(db, sid)["eff_status"] == "off"                                 # auch die Liste zeigt "laut Zeitplan"
    # gewollt durchlassen: Waehlregel fuer diesen Anrufer (steht ueber dem Zeitplan)
    db.x("UPDATE staff SET dialrules=? WHERE id=?",
         ('[{"id":"a","name":"Sohn","enabled":true,"caller_mode":"list","callers":["' + SON + '"],"days":[],"from":"","to":"",'
          '"called":[],"action":"office","target":"","note":""}]', sid))
    assert db.resolve(row(db, sid), SON, "", NOW)["number"] == "**610"
    assert db.resolve(row(db, sid), "030555444", "", NOW)["blocked"]          # alle anderen bleiben gesperrt


def test_dialrule_office_without_number_uses_deputy(bridge):
    """Verhaltensaenderung: Regel "erreichbar" ohne Nummer im Buero -> Vertretung (bisher sofort die KI)."""
    db = bridge.db
    dep = staff(db, "Peter Vertreter", "**630", msn="")
    sid = staff(db, "Petra Muster", "", deputy_id=dep, dialrules=[{"name": "Alle", "caller_mode": "any", "action": "office"}])
    r = db.resolve(row(db, sid), "030555444", "", NOW)
    assert r["via_deputy"] and r["number"] == "**630"


def test_dialrule_ai_never_uses_deputy(bridge):
    db = bridge.db
    dep = staff(db, "Peter Vertreter", "**630", msn="")
    sid = staff(db, "Petra Muster", "**610", deputy_id=dep, dialrules=[{"name": "KI", "caller_mode": "any", "action": "ai"}])
    r = db.resolve(row(db, sid), "030555444", "", NOW)
    assert r["ai"] and r["number"] == "" and not r["via_deputy"]


def test_ring_dnd_with_rule_office_rings_deputy(bridge):
    async def scenario(b):
        ring(b.db)
        dep = staff(b.db, "Peter Vertreter", "**630", msn="")
        staff(b.db, "Petra Muster", "**610", status="dnd", deputy_id=dep, dialrules=[{"name": "Alle", "caller_mode": "any", "action": "office"}])
        u, r = call(b)
        assert await r == "fdial:20:**630"                                     # bisher: **610 (Regel ueberging "nicht stoeren")
        assert "Vertretung für Petra Muster" in b.call_row(u)["agent_name"]
    run(bridge, scenario)


def test_ai_connect_respects_dialrule_off(bridge):
    """Bisher verband die KI trotz Waehlregel "Anrufer X -> nicht erreichbar"; jetzt gilt dieselbe Entscheidung wie beim Klingeln."""
    u1, u2 = str(uuid.uuid4()), str(uuid.uuid4())
    steps = [("say", "Guten Tag."), ("hear", "Petra bitte."), ("tool", "connect_employee", {"name": "Petra"}), ("say", "Moment.")]
    bridge.genai.script(steps + [("tool", "end_call")])
    bridge.genai.script(steps)

    async def scenario(b):
        staff(b.db, "Petra Muster", "**610", msn="", note="",
              dialrules=[{"name": "Chef nicht", "caller_mode": "list", "callers": [BOSS], "action": "off", "note": "in Klausur"}])
        assert await b.internal("call", uuid=u1, frm=BOSS, to="s", hdr="<sip:0309999@fritz.box>", chan="PJSIP/fritz-1") == "ok"
        sock = await b.audiosocket(u1)
        await b.wait_ended(sock)
        s1 = b.genai.sessions[0]
        assert "nicht erreichbar" in s1.tool_results[0][1]["fehler"] and "in Klausur" in s1.tool_results[0][1]["fehler"]
        assert "- Petra Muster: nicht erreichbar – in Klausur" in s1.prompt          # die Liste der KI zeigt dasselbe
        assert any("Wählregel „Chef nicht“" in t for t in b.segments(b.call_row(u1)["id"], "system"))
        assert await b.internal("call", uuid=u2, frm="030555444", to="s", hdr="<sip:0309999@fritz.box>", chan="PJSIP/fritz-2") == "ok"
        sock = await b.audiosocket(u2)
        await b.wait_ended(sock)
        s2 = b.genai.sessions[1]
        assert s2.tool_results[0][1]["ok"] is True and "- Petra Muster: erreichbar" in s2.prompt
    run(bridge, scenario)


def test_answer_now_rule_also_skips_deputy_under_dnd(bridge):
    """Anrufregel ohne Klingelzeit: die KI nimmt sofort an - auch bei "nicht stoeren" klingelt dann keine Vertretung."""
    db = bridge.db
    dep = staff(db, "Peter Vertreter", "**630", msn="")
    sid = staff(db, "Petra Muster", "**610", status="dnd", deputy_id=dep)
    r = db.resolve(row(db, sid), "030555444", "", NOW, answer_now="Anrufbeantworter")
    assert r["ai"] and not r["via_deputy"] and r["number"] == "" and r["status"] == "dnd"
    r = db.resolve(row(db, sid), "030555444", "", NOW)
    assert r["via_deputy"]
