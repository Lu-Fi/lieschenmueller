"""Selbstpruefung "Auffaelligkeiten bei Anrufen" (anomaly.py): jede Regel mit Fakes, Dedupe, Buendelung der Mail, Option je Empfaenger,
nur Admins sehen Liste, Einstellungen und den Hinweis im Ablauf."""
import json
import time
from datetime import datetime

from app import anomaly, flow, main, notify
from tests.conftest import run

T0 = 1_800_000_000.0           # feste Zeit (Nov. 2027), weit weg von "jetzt"


def _call(db, started, dur, caller="05550001234", called="05550000", direction="in", status="beendet"):
    return db.x("INSERT INTO calls(uuid, started, ended, caller, called, agent_name, status, direction) VALUES(?,?,?,?,?,?,?,?)",
                (f"u{started}-{caller}", started, started + dur if dur is not None else None, caller, called, "Empfang", status, direction))


def _ev(db, cid, ts, kind, **data):
    db.x("INSERT INTO call_events(call_id, ts, kind, data) VALUES(?,?,?,?)", (cid, ts, kind, json.dumps(data)))


def _start(db):
    """Wasserstand setzen wie beim ersten Lauf (alte Anrufe werden nicht nachtraeglich geprueft)."""
    assert anomaly.check_calls(T0) == 0
    db.set_meta("anom_ring_streak", "{}")


def kinds(db):
    return [r["kind"] for r in db.q("SELECT kind FROM anomalies ORDER BY id")]


def test_first_run_ignores_old_calls(bridge):
    db = bridge.db
    cid = _call(db, T0 - 500, 60)
    _ev(db, cid, T0 - 490, "ai", agent="Empfang")
    _ev(db, cid, T0 - 440, "qos", leg="a", rxcount=2000, txcount=100, rtt=0)
    assert anomaly.check_calls(T0) == 0 and kinds(db) == []
    assert int(db.get_meta("anom_call_wm")) == cid


def test_oneway_rtt_zero_and_tx_ratio(bridge):
    db = bridge.db
    _start(db)
    a = _call(db, T0, 45)                                 # angenommen, rtt 0 bei 40 s Gespraech
    _ev(db, a, T0 + 1, "ring", targets="**610")
    _ev(db, a, T0 + 5, "answered")
    _ev(db, a, T0 + 45, "qos", leg="a", rxcount=2000, txcount=1990, rtt=0)
    b = _call(db, T0 + 100, 60)                           # KI-Gespraech, KI sendet nur 10 %
    _ev(db, b, T0 + 101, "ai", agent="Empfang")
    _ev(db, b, T0 + 159, "ai_end", reason="Gespraech beendet: Anrufer hat aufgelegt")
    _ev(db, b, T0 + 160, "qos", leg="a", rxcount=2900, txcount=290, rtt=0.02)
    c = _call(db, T0 + 200, 15)                           # zu kurz: nichts
    _ev(db, c, T0 + 201, "answered")
    _ev(db, c, T0 + 215, "qos", leg="a", rxcount=700, txcount=700, rtt=0)
    d = _call(db, T0 + 300, 60)                           # normal: tx ~ rx, rtt > 0
    _ev(db, d, T0 + 301, "ai", agent="Empfang")
    _ev(db, d, T0 + 360, "qos", leg="a", rxcount=3000, txcount=2950, rtt=0.03)
    assert anomaly.check_calls(T0 + 1000) == 2
    rows = db.q("SELECT * FROM anomalies ORDER BY id")
    assert [(r["kind"], r["call_id"]) for r in rows] == [("oneway", a), ("oneway", b)]
    assert "ohne RTCP" in rows[0]["text"] and "…1234" in rows[0]["text"] and "05550001234" not in rows[0]["text"]
    assert "gesendet 290 von 2900" in rows[1]["text"]
    assert anomaly.check_calls(T0 + 2000) == 0             # schon geprueft: keine Wiederholung


def test_call_not_checked_before_settled(bridge):
    db = bridge.db
    _start(db)
    a = _call(db, T0, 40)
    _ev(db, a, T0 + 1, "answered")
    _ev(db, a, T0 + 40, "qos", leg="a", rxcount=2000, txcount=2000, rtt=0)
    assert anomaly.check_calls(T0 + 70) == 0               # erst 30 s nach dem Ende: Leitungswerte evtl. noch nicht da
    assert anomaly.check_calls(T0 + 101) == 1


def test_ai_deaf(bridge):
    db = bridge.db
    _start(db)
    a = _call(db, T0, 50)
    _ev(db, a, T0 + 1, "ai", agent="Empfang")
    _ev(db, a, T0 + 48, "ai_end", reason="Gespraech beendet: KI hat nach 10 s Stille aufgelegt")
    _ev(db, a, T0 + 50, "qos", leg="a", rxcount=2300, txcount=2300, rtt=0.02)
    b = _call(db, T0 + 100, 50)                           # Anrufer hat gesprochen: nichts
    _ev(db, b, T0 + 101, "ai", agent="Empfang")
    _ev(db, b, T0 + 148, "ai_end", reason="Gespraech beendet: KI hat nach 10 s Stille aufgelegt")
    _ev(db, b, T0 + 150, "qos", leg="a", rxcount=2300, txcount=2300, rtt=0.02)
    db.x("INSERT INTO segments(call_id, ts, speaker, text) VALUES(?,?,?,?)", (b, T0 + 110, "caller", "Hallo?"))
    c = _call(db, T0 + 200, 50)                           # Anrufer legte auf: nichts
    _ev(db, c, T0 + 201, "ai", agent="Empfang")
    _ev(db, c, T0 + 248, "ai_end", reason="Gespraech beendet: Anrufer hat aufgelegt")
    _ev(db, c, T0 + 250, "qos", leg="a", rxcount=2300, txcount=2300, rtt=0.02)
    anomaly.check_calls(T0 + 1000)
    rows = db.q("SELECT kind, call_id, text FROM anomalies")
    assert [(r["kind"], r["call_id"]) for r in rows] == [("deaf", a)]
    assert "kein Wort des Anrufers" in rows[0]["text"]


def _ring_call(db, t, target="99610", tech="180 Ringing", after=20.0, cause=16, answered=False):
    cid = _call(db, t, 30)
    _ev(db, cid, t + 0.5, "ring", targets="**610", ring=20)
    if answered:
        _ev(db, cid, t + 8, "answered")
        _ev(db, cid, t + 30, "qos", leg="b", rxcount=1000, txcount=1000, rtt=0.01)
    else:
        _ev(db, cid, t + 0.5 + after, "leg_end", target=target, cause=cause, tech=tech, after=after)
        _ev(db, cid, t + 21, "ai", agent="Empfang")
    return cid


def test_ring_dead_streak_and_reset(bridge):
    db = bridge.db
    _start(db)
    _ring_call(db, T0)
    _ring_call(db, T0 + 100)
    _ring_call(db, T0 + 200, answered=True)                # abgenommen: Zaehler zurueck
    _ring_call(db, T0 + 300)
    _ring_call(db, T0 + 400)
    anomaly.check_calls(T0 + 1000)
    assert kinds(db) == []
    last = _ring_call(db, T0 + 500)                        # dritter in Folge
    _ring_call(db, T0 + 600, after=5.0)                    # Anrufer legte frueh auf: zaehlt nicht
    anomaly.check_calls(T0 + 2000)
    rows = db.q("SELECT * FROM anomalies")
    assert len(rows) == 1 and rows[0]["kind"] == "ring_dead" and rows[0]["call_id"] == last
    assert rows[0]["text"].startswith("**610 klingelte bei 3 Anrufen in Folge")
    _ring_call(db, T0 + 700)                               # vierter: gleiche Meldung zaehlt hoch
    anomaly.check_calls(T0 + 3000)
    rows = db.q("SELECT * FROM anomalies")
    assert len(rows) == 1 and rows[0]["count"] == 2 and "bei 4 Anrufen" in rows[0]["text"]


def test_ring_short_without_reason(bridge):
    db = bridge.db
    _start(db)
    a = _call(db, T0, 25)                                  # Direktruf **612 endet sofort, KI uebernimmt
    _ev(db, a, T0 + 0.5, "ring", targets="**612")
    _ev(db, a, T0 + 0.9, "leg_end", target="**612", cause=127, tech="", after=0.4)
    _ev(db, a, T0 + 1.5, "ai", agent="Empfang")
    b = _call(db, T0 + 100, 2)                             # Anrufer legte nach 1,5 s auf: kein Fehler
    _ev(db, b, T0 + 100.2, "ring", targets="**610")
    _ev(db, b, T0 + 101.7, "unanswered")
    _ev(db, b, T0 + 101.8, "leg_end", target="99610", cause=16, tech="180 Ringing", after=1.6)
    c = _call(db, T0 + 200, 20)                            # besetzt: erkennbarer Grund
    _ev(db, c, T0 + 200.2, "ring", targets="**610")
    _ev(db, c, T0 + 201, "leg_end", target="99610", cause=17, tech="486 Busy Here", after=0.8)
    anomaly.check_calls(T0 + 1000)
    rows = db.q("SELECT kind, call_id, text FROM anomalies")
    assert [(r["kind"], r["call_id"]) for r in rows] == [("ring_short", a)]
    assert rows[0]["text"].startswith("**612: Klingeln endete schon nach 0.4 s – unbekannter Grund")


def test_rules_can_be_switched_off(bridge):
    db = bridge.db
    _start(db)
    anomaly.save_cfg({"oneway": False, "ring_short": False, "oneway_s": 99999})
    assert anomaly.cfg()["oneway_s"] == 600                # auf die Grenze begrenzt
    a = _call(db, T0, 45)
    _ev(db, a, T0 + 5, "answered")
    _ev(db, a, T0 + 45, "qos", leg="a", rxcount=2000, txcount=1990, rtt=0)
    _ev(db, a, T0 + 1, "leg_end", target="**612", cause=127, after=0.4)
    anomaly.check_calls(T0 + 1000)
    assert kinds(db) == []


LOG1 = """19.11.26 12:40:05 Internetverbindung wurde erfolgreich hergestellt. IP-Adresse: 192.0.2.10
19.11.26 12:39:58 Internetverbindung wurde getrennt.
19.11.26 08:00:00 Anmeldung an der WLAN-Sicherung erfolgreich."""
LOG2 = """19.11.26 12:47:13 Internettelefonie mit 05550009876 über tel.example.invalid war nicht erfolgreich. Ursache: 480 Temporarily Unavailable
19.11.26 12:46:01 Anmeldung der Internetrufnummer 05550009876 war nicht erfolgreich. Ursache: Gegenstelle antwortet nicht.
19.11.26 12:45:00 Anmeldung der Internetrufnummer 99613 war nicht erfolgreich. Ursache: Gegenstelle antwortet nicht.
19.11.26 12:44:00 Dect-Telefon 2 angemeldet.
""" + LOG1


def test_fritz_log_only_new_lines(bridge):
    db = bridge.db
    assert anomaly.check_log(LOG1) == 0 and kinds(db) == []          # erster Lauf: nur merken
    assert anomaly.check_log(LOG2) == 3
    rows = db.q("SELECT kind, text, mail FROM anomalies ORDER BY id")
    assert [(r["kind"], r["mail"]) for r in rows] == [("ring_lines", 0), ("fritz_log", 1), ("fritz_log", 1)]
    assert "99613" in rows[0]["text"]                                # Klingel-Rufnummer: nur Anzeige (Mail macht der Waechter)
    assert "…9876" in rows[1]["text"] and "05550009876" not in rows[2]["text"] and "480" in rows[2]["text"]
    assert anomaly.check_log(LOG2) == 0                              # nichts Neues
    more = "19.11.26 13:10:00 Internetverbindung wurde getrennt.\n" + LOG2
    assert anomaly.check_log(more) == 1
    assert db.q("SELECT kind, text FROM anomalies ORDER BY id DESC LIMIT 1")[0]["text"].startswith("Fritzbox 19.11. 13:10: Internetverbindung getrennt")


def test_voip_status_twice(bridge):
    db = bridge.db
    anomaly._voip_bad.clear()
    bad = [("05550001111", "Registered"), ("05550002222", "unregistered")]
    assert anomaly.check_voip(bad, T0) == 0                          # einmal: kurzer Aussetzer
    assert anomaly.check_voip(bad, T0 + 300) == 1
    assert anomaly.check_voip(bad, T0 + 600) == 0
    r = db.q("SELECT kind, text FROM anomalies")
    assert r[0]["kind"] == "voip_status" and "…2222" in r[0]["text"] and "05550002222" not in r[0]["text"]
    assert anomaly.check_voip([("05550002222", "connected")], T0 + 900) == 0 and not anomaly._voip_bad


def test_read_voip_status_skips_ring_numbers(monkeypatch):
    from app import fritz

    class FC:
        def call_action(self, svc, action, **kw):
            if action == "GetMaxVoIPNumbers":
                return {"NewMaxVoIPNumbers": 3}
            if action == "X_AVM-DE_GetVoIPAccount":
                return [{"NewVoIPNumber": "05550001111"}, {"NewVoIPNumber": "99613"}, {"NewVoIPNumber": "05550002222"}][kw["NewVoIPAccountIndex"]]
            if action == "X_AVM-DE_GetVoIPStatus":
                assert kw["NewVoIPAccountIndex"] != 1
                return {"NewX_AVM-DE_VoIPStatus": "registered" if kw["NewVoIPAccountIndex"] == 0 else "not registered"}
            raise AssertionError(action)
    monkeypatch.setattr(fritz, "_shared_conn", lambda: FC())
    assert anomaly.read_voip_status() == [("05550001111", "registered"), ("05550002222", "not registered")]


def _fc_call(ts, typ="2", own="05550004321", caller="05550007777", device="", duration="0:00"):
    return {"type": typ, "own": own, "caller": caller, "device": device, "duration": duration, "ts": ts}


def test_undelivered_burst(bridge):
    db = bridge.db
    base = datetime(2027, 1, 15, 12, 46).timestamp()
    calls = [_fc_call(base + i * 12, caller="05550007777" if i % 2 else "05550008888", typ="2" if i % 3 else "10") for i in range(35)]
    calls += [_fc_call(base + 30, device="DECT 1", duration="0:05"), _fc_call(base + 40, typ="1")]      # geklingelt/angenommen: zaehlt nicht
    calls += [_fc_call(base + i * 60, own="05550005555") for i in range(5)]                             # andere Nummer, zu wenige
    assert anomaly.check_undelivered(calls, base + 600) == 1
    r = db.q("SELECT * FROM anomalies")
    assert len(r) == 1 and r[0]["kind"] == "undelivered"
    assert r[0]["text"] == "Rufnummer …4321: 35 Anrufe am 15.01. zwischen 12:46 und 12:52 von 2 Nummern, bei keinem hat ein Telefon geklingelt."
    calls.append(_fc_call(base + 500))                     # die Haeufung waechst: gleicher Eintrag, Text neu, keine neue Meldung
    anomaly.check_undelivered(calls, base + 900)
    r = db.q("SELECT * FROM anomalies")
    assert len(r) == 1 and "36 Anrufe" in r[0]["text"] and r[0]["count"] == 1
    assert anomaly.check_undelivered(calls[:9], base + 600) == 0          # unter der Schwelle


def test_ring_lines_ui_only(bridge, monkeypatch):
    db = bridge.db
    from app import astconf, outrec

    async def regs():
        return {"99610"}

    async def st(name, timeout_s=10):
        return "Rejected"
    monkeypatch.setattr(outrec, "registered", regs)
    monkeypatch.setattr(astconf, "ring_lines", lambda: ["99613", "99610"])
    monkeypatch.setattr(main.ami, "registration_status", st)
    anomaly._line_bad.clear()

    async def scenario(b):
        assert await anomaly.check_ring_lines(T0) == 0              # einmal: Neustart von Asterisk
        assert await anomaly.check_ring_lines(T0 + 300) == 2
    run(bridge, scenario)
    rows = db.q("SELECT key, mail FROM anomalies ORDER BY id")
    assert [r["mail"] for r in rows] == [0, 0] and rows[0]["key"].startswith("ring_lines:99613:")


def test_dedupe_reopen_without_second_mail(bridge):
    db = bridge.db
    aid, new = anomaly.record("ring_short", "k1", "Text A", ts=T0)
    assert new and anomaly.open_count() == 1
    assert anomaly.ack(aid, "admin") == 1 and anomaly.open_count() == 0
    db.x("UPDATE anomalies SET mailed=1 WHERE id=?", (aid,))
    aid2, new2 = anomaly.record("ring_short", "k1", "Text B", ts=T0 + 60)
    r = db.q("SELECT * FROM anomalies")[0]
    assert (aid2, new2) == (aid, False) and r["count"] == 2 and r["ack"] == 0 and r["mailed"] == 1 and r["text"] == "Text B"


def _rcpt(db, email, on_anomaly, enabled=1):
    return db.x("INSERT INTO mail_recipients(email, on_call, daily, enabled, on_anomaly) VALUES(?,?,?,?,?)", (email, "all", 0, enabled, on_anomaly))


def test_mail_only_to_opted_in_bundled_and_limited(bridge, monkeypatch):
    db = bridge.db
    monkeypatch.setattr(notify, "configured", lambda: True)
    _rcpt(db, "anrufe@example.invalid", 0)
    _rcpt(db, "stoerung@example.invalid", 1)
    _rcpt(db, "aus@example.invalid", 1, enabled=0)
    anomaly.record("oneway", "a1", "Anruf von …1234: Text 1", ts=T0)
    anomaly.record("ring_lines", "a2", "nur Anzeige", mail=False, ts=T0)
    anomaly.record("deaf", "a3", "Anruf von …5678: Text 3", ts=T0 + 10)
    assert anomaly.mail_tick(T0 + 20) == 1
    assert len(bridge.mails) == 1 and bridge.mails[0]["to"] == "stoerung@example.invalid"
    m = bridge.mails[0]
    assert "2 Auffälligkeiten" in m["subject"] and "Text 1" in m["text"] and "Text 3" in m["text"] and "nur Anzeige" not in m["text"]
    anomaly.record("oneway", "a4", "Text 4", ts=T0 + 60)
    assert anomaly.mail_tick(T0 + 120) == 0 and len(bridge.mails) == 1          # Buendelung: erst nach 15 min
    anomaly.record("oneway", "a3", "Text 3 wieder", ts=T0 + 130)                 # gleiche Meldung: keine zweite Mail
    assert anomaly.mail_tick(T0 + 15 * 60 + 21) == 1 and len(bridge.mails) == 2
    assert "Text 4" in bridge.mails[1]["text"] and "Text 3" not in bridge.mails[1]["text"]
    anomaly.save_cfg({"mail_day": 2, "mail_min": 5})
    anomaly.record("oneway", "a5", "Text 5", ts=T0 + 2000)
    assert anomaly.mail_tick(T0 + 4000) == 0 and len(bridge.mails) == 2           # Tageslimit erreicht
    assert db.q("SELECT mailed FROM anomalies WHERE key='a5'")[0]["mailed"] == 0  # bleibt offen fuer morgen


def test_no_mail_without_opted_in_recipient(bridge, monkeypatch):
    db = bridge.db
    monkeypatch.setattr(notify, "configured", lambda: True)
    _rcpt(db, "anrufe@example.invalid", 0)
    anomaly.record("oneway", "b1", "Text", ts=T0)
    assert anomaly.mail_tick(T0 + 10) == 0 and bridge.mails == []
    assert db.q("SELECT mailed FROM anomalies")[0]["mailed"] == -1                # spaeter nicht nachsenden
    _rcpt(db, "neu@example.invalid", 1)
    assert anomaly.mail_tick(T0 + 3600) == 0 and bridge.mails == []


def test_recipient_option_saved_separately(bridge, monkeypatch):
    async def scenario(b):
        monkeypatch.setattr(main, "current_user", lambda request: main.PSEUDO_ADMIN)
        r = await b.http.post("/api/mail/recipients", json={"email": "x@example.invalid", "on_call": "off", "on_anomaly": 1})
        rid = r.json()["id"]
        row = b.db.q("SELECT on_call, on_anomaly FROM mail_recipients WHERE id=?", (rid,))[0]
        assert row == {"on_call": "off", "on_anomaly": 1}
        await b.http.put(f"/api/mail/recipients/{rid}", json={"email": "x@example.invalid", "on_call": "all"})
        assert b.db.q("SELECT on_call, on_anomaly FROM mail_recipients WHERE id=?", (rid,))[0] == {"on_call": "all", "on_anomaly": 0}
    run(bridge, scenario)


def test_api_admin_only_and_flow_hint(bridge, monkeypatch):
    db = bridge.db
    cid = _call(db, time.time() - 100, 50)
    flow.add(cid, "ai", agent="Empfang")
    aid, _ = anomaly.record("oneway", "c1", "Anruf von …1234: Test", call_id=cid)
    user = {"id": 9, "username": "petra", "role": "user", "staff_id": None, "disabled": 0}

    async def scenario(b):
        monkeypatch.setattr(main, "current_user", lambda request: user)
        for path in ("/api/anomalies", "/api/anomalies/config"):
            assert (await b.http.get(path)).status_code == 403
        assert (await b.http.post(f"/api/anomalies/{aid}/ack")).status_code == 403
        assert (await b.http.put("/api/anomalies/config", json={"oneway": False})).status_code == 403
        assert "anomalies" not in (await b.http.get("/api/status")).json()
        monkeypatch.setattr(main.users, "level_for", lambda u, c: "full")
        ev = (await b.http.get(f"/api/calls/{cid}")).json()["events"]
        assert [e["kind"] for e in ev] == ["ai"]                                  # Benutzer sehen den Hinweis nicht
        monkeypatch.setattr(main, "current_user", lambda request: main.PSEUDO_ADMIN)
        ev = (await b.http.get(f"/api/calls/{cid}")).json()["events"]
        assert [e["kind"] for e in ev] == ["ai", "anomaly"] and "Auffälligkeit" in ev[1]["text"]
        j = (await b.http.get("/api/anomalies")).json()
        assert j["open"] == 1 and j["items"][0]["label"] == "Einweg-Ton / keine RTCP-Bestätigung"
        assert (await b.http.post(f"/api/anomalies/{aid}/ack")).json()["open"] == 0
        assert (await b.http.post(f"/api/anomalies/{aid}/ack")).status_code == 404
        r = await b.http.put("/api/anomalies/config", json={"ring_streak": 5, "deaf": False, "unbekannt": 1})
        assert r.status_code == 200 and r.json()["cfg"]["ring_streak"] == 5 and r.json()["cfg"]["deaf"] is False
        assert "unbekannt" not in r.json()["cfg"]
        assert (await b.http.put("/api/anomalies/config", json={"ring_streak": "viele"})).status_code == 400
        c = (await b.http.get("/api/anomalies/config")).json()
        assert {r["id"] for r in c["rules"]} >= {"oneway", "deaf", "ring_dead", "ring_short", "fritz_log", "voip_status", "undelivered", "ring_lines"}
    run(bridge, scenario)


def test_fritz_errors_never_raise(bridge, monkeypatch):
    """Fritzbox nicht erreichbar: fritz_tick protokolliert nur."""
    monkeypatch.setenv("FRITZ_TR064_USER", "u")
    monkeypatch.setenv("FRITZ_TR064_PASSWORD", "p")

    def boom():
        raise ConnectionError("weg")
    monkeypatch.setattr(anomaly, "read_log", boom)
    monkeypatch.setattr(anomaly, "read_voip_status", boom)
    monkeypatch.setattr(anomaly, "read_calls", boom)

    async def scenario(b):
        await anomaly.fritz_tick(T0)
    run(bridge, scenario)
    assert bridge.db.q("SELECT COUNT(*) n FROM anomalies")[0]["n"] == 0
