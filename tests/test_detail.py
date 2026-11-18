"""Ausfuehrliche Zusammenfassung (alle besprochenen Punkte): Auswertung, Aufbewahrung bei neuer Auswertung, Mail-Option."""
import json
import time
import types
import uuid

from app import analyze, notify
from tests.conftest import run

DETAIL = {"fazit": "Termin am Dienstag, Rechnung wird geprueft.",
          "punkte": [{"thema": "Termin", "inhalt": "Anrufer moechte Dienstag", "ergebnis": "Dienstag 10 Uhr vorgemerkt"},
                     {"thema": "Rechnung", "inhalt": "Rueckfrage zu Betrag", "ergebnis": "kein Ergebnis, Unterlagen fehlen"}],
          "zusagen": [{"wer": "Buero", "was": "Rueckruf morgen", "bis_wann": "morgen"}], "offen": [{"punkt": "Unterlagen schicken", "grund": "fehlen noch"}]}


class FakeClient:
    """Ersetzt genai.Client() in analyze: liefert je nach Prompt die Detail- oder die Kurzfassung."""
    def __init__(self):
        outer = self
        self.aio = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=outer._gen))
        self.prompts = []

    async def _gen(self, model, contents, config):
        self.prompts.append(contents)
        data = DETAIL if contents.startswith(analyze.DETAIL_PROMPT[:30]) else {
            "zusammenfassung": "Kurz", "anliegen": "Termin", "dringlichkeit": "niedrig", "stimmung": "ruhig", "sprache": "Deutsch",
            "ergebnis": "Nachricht", "betrugsverdacht": "nein"}
        return types.SimpleNamespace(text=json.dumps(data), usage_metadata=types.SimpleNamespace(prompt_token_count=100, candidates_token_count=50))


def make_call(db, meta=None):
    cid = db.x("INSERT INTO calls(uuid,started,ended,caller,called,agent_name,agent_chain,status,direction,meta) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (str(uuid.uuid4()), time.time() - 60, time.time(), "015112345678", "0301111", "Assistent", "Assistent", "beendet", "in",
                json.dumps(meta or {})))
    for who, text in (("agent", "Guten Tag"), ("caller", "Ich moechte einen Termin am Dienstag"), ("agent", "Gern, wegen der Rechnung?"), ("caller", "Ja")):
        db.add_segment(cid, who, text)
    return cid


def test_run_detail_stores_in_meta(bridge, monkeypatch):
    async def scenario(b):
        fake = FakeClient()
        monkeypatch.setattr(analyze.genai, "Client", lambda: fake)
        cid = make_call(b.db)
        d = await analyze.run_detail(cid)
        assert d["fazit"] == DETAIL["fazit"] and d["punkte"] == DETAIL["punkte"] and d["offen"] == DETAIL["offen"]
        assert d["zusagen"][0]["was"] == "Rueckruf morgen"
        meta = json.loads(b.db.q("SELECT meta FROM calls WHERE id=?", (cid,))[0]["meta"])
        assert meta["detail"]["punkte"] == DETAIL["punkte"]
        assert "Ich moechte einen Termin" in fake.prompts[0]
    run(bridge, scenario)


def test_wanted_detail_follows_agent_option(bridge):
    async def scenario(b):
        cid = make_call(b.db)
        assert analyze.wanted_detail(cid) is False
        b.db.x("UPDATE agents SET meta_detail=1 WHERE name='Assistent'")
        assert analyze.wanted_detail(cid) is True
    run(bridge, scenario)


def test_mail_contains_points_only_for_recipient_with_option(bridge):
    async def scenario(b):
        for k, v in (("smtp_host", "mail.invalid"), ("smtp_user", "t@example.invalid"), ("smtp_password", "x"), ("smtp_from", "t@example.invalid")):
            b.db.set_meta(k, v)
        b.db.x("INSERT INTO mail_recipients(email,on_call,enabled,inc_points) VALUES('mit@example.invalid','all',1,1)")
        b.db.x("INSERT INTO mail_recipients(email,on_call,enabled,inc_points) VALUES('ohne@example.invalid','all',1,0)")
        cid = make_call(b.db, {"zusammenfassung": "Kurz", "detail": DETAIL})
        await notify.call_finished(cid)
        by = {m["to"] if isinstance(m["to"], str) else m["to"][0]: m["text"] for m in b.mails}
        m = by["mit@example.invalid"]
        assert "BESPROCHENE PUNKTE" in m and "FAZIT" in m and "Ergebnis: Dienstag 10 Uhr vorgemerkt" in m
        assert "Rueckruf morgen" in m and "Unterlagen schicken" in m
        assert "BESPROCHENE PUNKTE" not in by["ohne@example.invalid"]
    run(bridge, scenario)


def test_mail_generates_points_when_missing(bridge, monkeypatch):
    async def scenario(b):
        for k, v in (("smtp_host", "mail.invalid"), ("smtp_user", "t@example.invalid"), ("smtp_password", "x"), ("smtp_from", "t@example.invalid")):
            b.db.set_meta(k, v)
        b.db.x("INSERT INTO mail_recipients(email,on_call,enabled,inc_points) VALUES('mit@example.invalid','all',1,1)")
        monkeypatch.setattr(analyze.genai, "Client", lambda: FakeClient())
        cid = make_call(b.db, {"zusammenfassung": "Kurz"})
        await notify.call_finished(cid)
        assert len(b.mails) == 1 and "BESPROCHENE PUNKTE" in b.mails[0]["text"] and "Rueckruf morgen" in b.mails[0]["text"]
    run(bridge, scenario)


def test_detail_lines_understands_legacy_lists():
    """Aeltere gespeicherte Form (Listen aus Texten) wird weiter angezeigt."""
    lines = dict(analyze.detail_lines({"punkte": ["Termin: Dienstag"], "zusagen": ["Rueckruf"], "offen": ["Unterlagen"]}))
    assert lines["Besprochene Punkte"] == ["Termin: Dienstag"] and lines["Zusagen und Vereinbarungen"] == ["Rueckruf"] and lines["Offen"] == ["Unterlagen"]
    assert "Fazit" not in lines


def _smtp(db):
    for k, v in (("smtp_host", "mail.invalid"), ("smtp_user", "t@example.invalid"), ("smtp_password", "x"), ("smtp_from", "t@example.invalid")):
        db.set_meta(k, v)


class _Req:
    def __init__(self, d):
        self.d = d

    async def json(self):
        return self.d


def test_call_mail_with_points_contains_summary(bridge):
    """Der vorhandene Dialog 'Per E-Mail senden' kann die ausfuehrliche Zusammenfassung mitschicken."""
    from app import main

    async def scenario(b):
        _smtp(b.db)
        cid = make_call(b.db, {"zusammenfassung": "Kurz", "detail": DETAIL})
        r = await main.call_mail(cid, _Req({"to": "a@example.invalid", "points": True, "transcript": False, "audio": False}))
        assert r["ok"] and len(b.mails) == 1
        t = b.mails[0]["text"]
        assert "FAZIT" in t and "Ergebnis: Dienstag 10 Uhr vorgemerkt" in t and "Unterlagen schicken" in t
        b.mails.clear()
        await main.call_mail(cid, _Req({"to": "a@example.invalid", "points": False, "transcript": False, "audio": False}))
        assert "BESPROCHENE PUNKTE" not in b.mails[0]["text"]
    run(bridge, scenario)


def test_call_mail_with_points_generates_missing(bridge, monkeypatch):
    from app import main

    async def scenario(b):
        _smtp(b.db)
        monkeypatch.setattr(analyze.genai, "Client", lambda: FakeClient())
        cid = make_call(b.db, {"zusammenfassung": "Kurz"})
        await main.call_mail(cid, _Req({"to": "a@example.invalid", "points": True, "transcript": False, "audio": False}))
        assert "Rueckruf morgen" in b.mails[0]["text"]
    run(bridge, scenario)


def test_mail_html_structure_for_points(bridge):
    """HTML der Mail: Fazit statt Kurzfassung, Karten je Punkt, Ergebnis hervorgehoben, Zusagen und Offenes."""
    async def scenario(b):
        cid = make_call(b.db, {"zusammenfassung": "KURZFASSUNG-X", "detail": DETAIL})
        call, meta, notes = notify._load(cid)
        subject, text, html = notify.build_call_mail(call, meta, notes, "short", True, True)
        assert "KURZFASSUNG-X" not in html and "KURZFASSUNG-X" not in text       # das Fazit ersetzt die Kurzfassung
        assert "Fazit" in html and "Besprochene Punkte" in html and "Zusagen und Vereinbarungen" in html and "Offen" in html
        assert "Ergebnis:</b> Dienstag 10 Uhr vorgemerkt" in html and "#fffbeb" in html           # zweites Ergebnis (\"kein Ergebnis\") in Warnfarbe
        assert "bis morgen" in html and "fehlen noch" in html
        # ohne Option: unveraendert die Kurzfassung
        _, t2, h2 = notify.build_call_mail(call, meta, notes, "short", True, False)
        assert "KURZFASSUNG-X" in h2 and "Besprochene Punkte" not in h2
    run(bridge, scenario)


def test_long_transcript_is_not_cut_in_mail(bridge):
    async def scenario(b):
        cid = make_call(b.db, {"zusammenfassung": "Kurz"})
        for i in range(300):
            b.db.add_segment(cid, "local" if i < 240 else "remote", f"Satz {i} " + "wort " * 12)
        t = notify._transcript(cid)
        assert "Satz 0 " in t and "Satz 299 " in t and len(t) > 15000      # frueher bei 2500 Zeichen abgeschnitten
        assert t.count("Benutzer:") == 1 and t.count("015112345678:") == 2 + 1  # gleiche Sprecher zusammengefasst (2 Anrufer-Saetze + 1 Block)
        assert "gekürzt" not in t
    run(bridge, scenario)


def test_transcript_uses_names_numbers_and_fallback(bridge):
    async def scenario(b):
        cid = make_call(b.db, {"zusammenfassung": "Kurz"})
        b.db.add_segment(cid, "remote", "Hallo")
        b.db.add_segment(cid, "local", "Guten Tag")
        t = notify._transcript(cid)
        assert "Gegenseite" not in t and "015112345678: Hallo" in t and "Benutzer: Guten Tag" in t     # Nummer statt 'Gegenseite'
        b.db.x("UPDATE calls SET caller='' WHERE id=?", (cid,))
        assert "Anrufer: Hallo" in notify._transcript(cid)                                              # ohne Nummer einfach Anrufer
        b.db.x("INSERT INTO contacts(name,number) VALUES('Frau Test','0123456789')")
        b.db.x("UPDATE calls SET caller='0123456789' WHERE id=?", (cid,))
        assert "Frau Test: Hallo" in notify._transcript(cid)                                            # bekannter Kontakt: Name
    run(bridge, scenario)


def test_local_label_staff_phone_or_user(bridge):
    async def scenario(b):
        cid = make_call(b.db, {"zusammenfassung": "Kurz"})
        b.db.add_segment(cid, "local", "Hallo")
        assert "Benutzer: Hallo" in notify._transcript(cid)                                    # Nummer keinem zugeordnet
        b.db.x("UPDATE calls SET notes=? WHERE id=?", (json.dumps({"Angenommen von": "Mobil -1-"}), cid))
        b.db.x("INSERT INTO staff(name,msn) VALUES('Petra Muster','0301111')")
        assert "Petra Muster: Hallo" in notify._transcript(cid)                               # exklusiv: Name des Mitarbeiters
        b.db.x("INSERT INTO staff(name,msn) VALUES('Ludwig Maier','0301111')")
        assert "Mobil -1-: Hallo" in notify._transcript(cid)                                   # geteilt: Name des Telefons
        b.db.x("UPDATE calls SET notes='{}' WHERE id=?", (cid,))
        assert "Benutzer: Hallo" in notify._transcript(cid)                                    # geteilt, Telefon unbekannt
    run(bridge, scenario)


def test_local_label_uses_person_who_was_asked_or_connected(bridge):
    async def scenario(b):
        cid = make_call(b.db, {"zusammenfassung": "Kurz"})
        b.db.x("INSERT INTO staff(name,msn) VALUES('Doris Maier','0301111')")
        b.db.add_segment(cid, "local", "Hallo")
        assert "Doris Maier: Hallo" in notify._transcript(cid)                       # Nummer gehoert Doris
        b.db.add_segment(cid, "system", "Rückfrage bei Ludwig Maier vor dem Verbinden")
        b.db.add_segment(cid, "system", "Verbinde mit Ludwig Maier")
        assert "Ludwig Maier: Hallo" in notify._transcript(cid)                          # tatsaechlich gefragt/verbunden (Vertretung)
    run(bridge, scenario)


def test_mail_only_for_selected_call_results(bridge):
    """Je Empfaenger waehlbar, bei welchem Ergebnis des Anrufs (Status) eine Mail kommt; leer = jedes Ergebnis."""
    async def scenario(b):
        for k, v in (("smtp_host", "mail.invalid"), ("smtp_user", "t@example.invalid"), ("smtp_password", "x"), ("smtp_from", "t@example.invalid")):
            b.db.set_meta(k, v)
        for mail, st in (("ki@example.invalid", "beendet"), ("person@example.invalid", "angenommen"), ("beide@example.invalid", "beendet,angenommen"), ("alle@example.invalid", "")):
            b.db.x("INSERT INTO mail_recipients(email,on_call,enabled,on_status) VALUES(?,?,?,?)", (mail + "", "all", 1, st))
        cid = make_call(b.db, {"zusammenfassung": "Kurz"})                      # Status 'beendet' (von der KI bearbeitet)
        await notify.call_finished(cid)
        got = {m["to"] if isinstance(m["to"], str) else m["to"][0] for m in b.mails}
        assert got == {"ki@example.invalid", "beide@example.invalid", "alle@example.invalid"}
        b.mails.clear()
        cid2 = make_call(b.db, {"zusammenfassung": "Kurz"})
        b.db.x("UPDATE calls SET status='angenommen' WHERE id=?", (cid2,))
        await notify.call_finished(cid2)
        got = {m["to"] if isinstance(m["to"], str) else m["to"][0] for m in b.mails}
        assert got == {"person@example.invalid", "beide@example.invalid", "alle@example.invalid"}
    run(bridge, scenario)


def test_clean_recipient_normalizes_status_selection():
    from app import main
    base = {"email": "x@example.invalid"}
    assert main.clean_recipient({**base, "on_status": ["angenommen"]})["on_status"] == "angenommen"
    assert main.clean_recipient({**base, "on_status": ["angenommen", "gibtsnicht"]})["on_status"] == "angenommen"
    assert main.clean_recipient({**base, "on_status": [k for k, _ in notify.NOTIFY_STATUSES]})["on_status"] == ""      # alle = jedes Ergebnis
    assert main.clean_recipient({**base, "on_status": []})["on_status"] == ""
    assert main.clean_recipient({**base, "on_status": "beendet,durchgestellt"})["on_status"] == "beendet,durchgestellt"
