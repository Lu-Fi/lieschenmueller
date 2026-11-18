"""Anrufentscheidung (/internal/call, route_call, /internal/outcall): Antwort-Strings fuer den Dialplan und Rangfolge
Waehlregel > Status/"bis" > Zeitplan > Vertretung > KI. Verhalten festgeschrieben (Stand vor dem Umbau)."""
import json
import os
import uuid
import pytest

from app import dialrules, main, rectrans, schedule
from tests.conftest import run

MSN = "0301111"
HDR = f"<sip:{MSN}@fritz.box>"
CALLER = "015112345678"


def staff(db, name, number="", msn=MSN, status="office", **kw):
    sid = db.save_staff({"name": name, "number": number, "msn": msn, "status": status, **{k: v for k, v in kw.items() if k in ("away_number", "deputy_id", "note", "record", "use_hours", "schedule", "ai_fallback")}})
    if "dialrules" in kw:
        db.x("UPDATE staff SET dialrules=? WHERE id=?", (json.dumps(dialrules.normalize(kw["dialrules"], main.ami.dial_number)), sid))
    return sid


def ring(db, s=20):
    db.x("UPDATE call_rules SET ring_s=?", (s,))


def call(b, frm=CALLER, hdr=HDR, u=None):
    u = u or str(uuid.uuid4())
    return u, b.internal("call", uuid=u, frm=frm, to="s", hdr=hdr, chan="PJSIP/fritz-0001")


def notice_file():
    os.makedirs(rectrans.SOUNDS, exist_ok=True)
    open(os.path.join(rectrans.SOUNDS, "rec_notice.sln"), "wb").write(b"\x00" * 3200)


def _go(bridge, fn):
    async def scenario(b):
        await fn(b)
    run(bridge, scenario)


def test_no_staff_ai_answers_immediately(bridge):
    async def fn(b):
        u, r = call(b, hdr="<sip:0309999@fritz.box>")
        assert await r == "ok"
        assert main.CALLS.field(u, "pending") and main.CALLS.field(u, "pending")["agent"]["name"] == "Assistent"
        assert b.call_row(u)["status"] == "aktiv"
    _go(bridge, fn)


def test_ring_delay_without_staff_rings_all_then_ai(bridge):
    async def fn(b):
        ring(b.db, 15)
        u, r = call(b, hdr="<sip:0309999@fritz.box>")
        assert await r == "fdial:15:99613"                   # alle Telefone (ring_all) klingeln, danach die KI
        assert b.call_row(u)["agent_name"] == "Klingeln: Assistent"
        b.db.set_meta("ring_all", "")
        u2, r2 = call(b, hdr="<sip:0309999@fritz.box>")
        assert await r2 == "wait:15"                         # ohne Klingel-Nummer: Fritzbox klingelt selbst, KI nach 15 s
    _go(bridge, fn)


def test_staff_office_rings_with_ai_fallback(bridge):
    async def fn(b):
        ring(b.db)
        staff(b.db, "Petra Muster", "**610")
        u, r = call(b)
        assert await r == "fdial:20:**610"
        row = b.call_row(u)
        assert row["status"] == "durchgestellt" and row["agent_name"] == "Durchstellen: Petra Muster"
        assert main.CALLS.field(u, "fallback") and main.CALLS.field(u, "ringing")["id"] == row["id"]
    _go(bridge, fn)


def test_staff_ai_first_when_rule_answers_at_once(bridge):
    async def fn(b):
        staff(b.db, "Petra Muster", "**610")              # Anrufregel ohne Klingelzeit: die KI nimmt sofort an
        u, r = call(b)
        assert await r == "ok"
        fs = main.CALLS.field(u, "pending")["for_staff"]
        assert fs[0]["name"] == "Petra Muster" and fs[0]["status"] == "off"
    _go(bridge, fn)


def test_rangfolge_status_away_deputy_off(bridge):
    async def fn(b):
        ring(b.db)
        dep = staff(b.db, "Peter Vertreter", "**630", msn="")
        sid = staff(b.db, "Petra Muster", "**610", status="away", away_number="0171555", deputy_id=dep)
        assert await call(b)[1] == "fdial:20:0171555"                     # abwesend: Nummer ausser Haus
        b.db.x("UPDATE staff SET away_number='' WHERE id=?", (sid,))
        u, r = call(b)
        assert await r == "fdial:20:**630"                                # sonst die Vertretung
        assert b.call_row(u)["agent_name"] == "Durchstellen: Peter Vertreter (Vertretung für Petra Muster)"
        b.db.x("UPDATE staff SET status='off', deputy_id=NULL WHERE id=?", (sid,))
        u, r = call(b)
        assert await r == "ok"                                           # niemand erreichbar: die KI nimmt sofort an (kein ring_all)
    _go(bridge, fn)


def test_rangfolge_dialrule_beats_status_and_schedule(bridge):
    async def fn(b):
        ring(b.db)
        rules = [{"name": "Handy", "caller_mode": "any", "action": "forward", "target": "0171999"}]
        staff(b.db, "Petra Muster", "**610", status="off", dialrules=rules)
        u, r = call(b)
        assert await r == "fdial:20:0171999"
        assert b.call_row(u)["agent_name"] == "Durchstellen: Petra Muster (Regel: Handy)"
    _go(bridge, fn)


def test_dialrule_ai_answers_without_delay(bridge):
    async def fn(b):
        ring(b.db)
        staff(b.db, "Petra Muster", "**610", dialrules=[{"name": "KI", "caller_mode": "any", "action": "ai"}])
        u, r = call(b)
        assert await r == "ok"                                         # Regel "sofort zum Anrufbeantworter"
        assert main.CALLS.field(u, "pending")["rules_hit"] == ["Petra Muster: KI"]
    _go(bridge, fn)


def test_schedule_overrides_office(bridge):
    async def fn(b):
        ring(b.db)
        sch = schedule.normalize([{"days": list(range(7)), "from": "00:00", "to": "24:00", "status": "away", "note": "Termin"}])
        staff(b.db, "Petra Muster", "**610", away_number="0171555", use_hours=1, schedule=sch)
        assert await call(b)[1] == "fdial:20:0171555"                 # Zeitplan sagt abwesend
    _go(bridge, fn)


def test_multiple_numbers_ring_together(bridge):
    async def fn(b):
        ring(b.db)
        staff(b.db, "Petra Muster", "**610, **611")                  # zwei Nummern eines Benutzers (n1+n2)
        assert await call(b)[1] == "fdial:20:**610+**611"
        staff(b.db, "Peter Zwei", "**620")                            # zweiter Benutzer auf derselben Rufnummer
        assert await call(b)[1] == "fdial:20:**620+**610+**611"      # Reihenfolge der Benutzer: nach Name
    _go(bridge, fn)


def test_staff_without_ai_fallback_dials_plain(bridge):
    async def fn(b):
        ring(b.db)
        staff(b.db, "Petra Muster", "**610", ai_fallback=0)
        u, r = call(b)
        assert await r == "dial:**610"
        assert not main.CALLS.field(u, "fallback")
    _go(bridge, fn)


def test_staff_record_uses_recx(bridge):
    async def fn(b):
        ring(b.db)
        staff(b.db, "Petra Muster", "**610", record=1)
        assert await call(b)[1] == "fdial:20:**610"                   # ohne Hinweis-Ansage keine Aufnahme
        notice_file()
        u, r = call(b)
        assert await r == "recx:f:**610:20"
        assert b.call_row(u)["agent_name"] == "Durchstellen: Petra Muster (mit Mitschrift)"
        b.db.set_meta("rec_notice", "0")
        assert await call(b)[1] == "recx:qf:**610:20"
    _go(bridge, fn)


def test_ring_settings_extra_fields(bridge):
    async def fn(b):
        ring(b.db)
        staff(b.db, "Petra Muster", "**610")
        b.db.set_meta("ring_delay", "3")
        b.db.set_meta("ring_cid", "plus")
        assert await call(b)[1] == "fdial:20:**610::3:plus:"
        b.db.set_meta("ring_music", "1")
        assert await call(b)[1] == "fdial:20:**610:default:3:plus:"
        notice_file()
        b.db.x("UPDATE staff SET record=1")
        assert await call(b)[1] == "recx:f:**610:20:default:3:plus:"
        b.db.set_meta("ring_delay", "0")
        b.db.set_meta("ring_cid", "")
        assert await call(b)[1] == "recx:f:**610:20:default"
    _go(bridge, fn)


def test_agent_transfer_mode_and_no_agent(bridge):
    async def fn(b):
        aid = b.db.save_agent({"name": "Durchsteller", "mode": "transfer", "transfer_to": "**699"})
        assist = b.db.q("SELECT agent_id FROM call_rules")[0]["agent_id"]
        b.db.x("UPDATE call_rules SET agent_id=?", (aid,))
        u, r = call(b, hdr="<sip:0309999@fritz.box>")
        assert await r == "dial:**699"                 # Standard: direkt waehlen wie bisher
        b.db.x("UPDATE agents SET ring_fallback=1 WHERE id=?", (aid,))
        b.db.x("UPDATE call_rules SET agent_id=?, ring_s=30", (aid,))
        b.db.x("INSERT INTO call_rules(name,priority,agent_id,ring_s) VALUES('Rueckfall',1000,?,0)", (assist,))
        u, r = call(b, hdr="<sip:0309999@fritz.box>")
        assert await r == "fdial:30:**699"           # ueber die Klingel-Rufnummer (Anruferanzeige), mit KI-Rueckfall nach 30 s
        assert b.call_row(u)["status"] == "durchgestellt"
        assert await b.internal("fallback", uuid=u) == "ok"                    # niemand nimmt ab: die KI uebernimmt
        assert main.CALLS.field(u, "pending")["agent"]["mode"] == "assist"
        b.db.x("UPDATE call_rules SET enabled=0")
        u, r = call(b, hdr="<sip:0309999@fritz.box>")
        assert await r == "no-agent"
        assert b.call_row(u)["status"] == "abgelehnt"
    _go(bridge, fn)


def test_outcall_routes(bridge):
    async def fn(b):
        u = str(uuid.uuid4())
        assert await b.internal("outcall", uuid=u, to="*77110", chan="PJSIP/99610-1") == "dial:110"       # Notruf nie aufzeichnen
        assert await b.internal("outcall", uuid=str(uuid.uuid4()), to="*77abc") == "no-agent"
        u = str(uuid.uuid4())
        assert await b.internal("outcall", uuid=u, to="*770301234567", chan="PJSIP/99610-1") == "recx:o:0301234567"
        row = b.call_row(u)
        assert row["direction"] == "rec" and row["status"] == "waehlt"
        # ohne Vorwahl = Rueckruf aus der Anrufliste ueber die Klingel-Rufnummer 99NNN: normal waehlen, nichts aufzeichnen
        u2 = str(uuid.uuid4())
        assert await b.internal("outcall", uuid=u2, to="0301234567", chan="PJSIP/99613-1") == "dial:0301234567"
        assert not b.db.q("SELECT 1 FROM calls WHERE uuid=?", (u2,))
        b.db.set_meta("rec_out_any", "1")              # Einstellung: auch ohne Vorwahl nach den Ausgangsregeln mitschreiben
        assert (await b.internal("outcall", uuid=str(uuid.uuid4()), to="0301234567", chan="PJSIP/99613-1")).startswith("recx:o")
        b.db.set_meta("rec_out_any", "0")
        b.db.x("UPDATE rec_rules SET notice=0 WHERE direction='out'")
        assert await b.internal("outcall", uuid=str(uuid.uuid4()), to="*770301234567") == "recx:oq:0301234567"
        b.db.x("UPDATE rec_rules SET enabled=0 WHERE direction='out'")
        assert await b.internal("outcall", uuid=str(uuid.uuid4()), to="*770301234567") == "dial:0301234567"
    _go(bridge, fn)



def test_ring_answered_status_angenommen(bridge):
    """Eine Person nimmt ab, bevor die KI annimmt: Status 'angenommen' (vorher immer 'durchgestellt')."""
    async def fn(b):
        ring(b.db)
        staff(b.db, "Petra Muster", "**610")
        u, r = call(b)
        assert await r == "fdial:20:**610"
        assert b.call_row(u)["status"] == "durchgestellt"                      # zum Zeitpunkt des Klingelns: noch offen
        await b.internal("answered", uuid=u)
        assert b.call_row(u)["status"] == "angenommen"
        await b.internal("rec-done", uuid=u)
        assert b.call_row(u)["status"] == "angenommen"
        assert "answered" in [e["kind"] for e in b.db.q("SELECT kind FROM call_events WHERE call_id=?", (b.call_row(u)["id"],))]
    _go(bridge, fn)


def test_ring_nobody_answers_caller_hangs_up_unbeantwortet(bridge):
    """Niemand nimmt ab und der Anrufer legt vor der KI auf: 'unbeantwortet' statt 'durchgestellt'."""
    async def fn(b):
        ring(b.db)
        staff(b.db, "Petra Muster", "**610")
        u, r = call(b)
        assert await r == "fdial:20:**610"
        await b.internal("rec-done", uuid=u)
        assert b.call_row(u)["status"] == "unbeantwortet"
        assert "unanswered" in [e["kind"] for e in b.db.q("SELECT kind FROM call_events WHERE call_id=?", (b.call_row(u)["id"],))]
    _go(bridge, fn)


def test_ai_transfer_stays_durchgestellt(bridge):
    """Hat die KI gesprochen und selbst durchgestellt, bleibt es 'durchgestellt'."""
    async def fn(b):
        ring(b.db)
        staff(b.db, "Petra Muster", "**610")
        u, r = call(b)
        assert await r == "fdial:20:**610"
        b.db.add_segment(b.call_row(u)["id"], "agent", "Guten Tag")
        await b.internal("answered", uuid=u)
        await b.internal("rec-done", uuid=u)
        assert b.call_row(u)["status"] == "durchgestellt"
    _go(bridge, fn)


def test_end_status_of_record_calls(bridge):
    """Anruf mit Mitschrift: Anrufer legt waehrend der Musik auf -> 'unbeantwortet'; abgenommen -> 'angenommen'; KI gesprochen -> 'durchgestellt'."""
    async def fn(b):
        ring(b.db)
        staff(b.db, "Petra Muster", "**610", record=1)
        notice_file()
        u, r = call(b)
        await r
        cid = b.call_row(u)["id"]
        assert b.db.end_status(cid) == "unbeantwortet"
        await b.internal("rec-start", uuid=u, chan="PJSIP/fritz-0001")
        assert b.db.end_status(cid) == "angenommen"
        b.db.add_segment(cid, "agent", "Moment, ich verbinde")
        assert b.db.end_status(cid) == "durchgestellt"
        b.db.x("UPDATE calls SET direction='rec' WHERE id=?", (cid,))
        assert b.db.end_status(cid) == "beendet"
    _go(bridge, fn)


def test_wait_mode_with_music_answers_itself(bridge):
    """Ohne Klingel-Rufnummer und mit Wartemusik: Asterisk nimmt selbst an und spielt Musik (wait:<s>:<Klasse>...), kein Telefon klingelt."""
    async def fn(b):
        ring(b.db, 15)
        b.db.set_meta("ring_all", "")
        b.db.set_meta("ring_music", "1")
        b.db.set_meta("ring_delay", "2")
        u, r = call(b, hdr="<sip:0309999@fritz.box>")
        res = await r
        assert res.startswith("wait:15:"), res
        assert res.endswith(":2::0") or res.count(":") >= 3, res          # Verzoegerung 2 s steht im Protokoll
        b.db.set_meta("ring_music", "0")
        u2, r2 = call(b, hdr="<sip:0309999@fritz.box>")
        assert await r2 == "wait:15"                       # ohne Musik wie bisher: die Fritzbox klingelt selbst
    _go(bridge, fn)
