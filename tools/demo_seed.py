"""Demo-Daten fuer Screenshots und Vorfuehrungen: fuellt eine FRISCHE Datenbank nur mit erfundenen Daten.

Alle Namen, Firmen und Rufnummern sind erfunden: Rufnummern nur aus den Bloecken 0221 555 01xx (Festnetz), 0151 0000xxxx (Mobil)
und den MSN 555 01xx; E-Mail-Adressen nur unter example.com. Nichts davon gehoert zu einer echten Person oder Firma.

Benutzt wird es von tools/demo_server.py (Wegwerf-Bridge mit Fake-Fritzbox, Fake-Asterisk, ohne Gemini). Konfiguration (Benutzer,
Agenten, Regeln, Kontakte, Einstellungen) geht ueber die normale API der Bridge (prueft die Formate), Anrufe mit Transkript und
Ablauf werden direkt in die Datenbank geschrieben (dafuer gibt es keine API).

Anmeldung an der Demo-Oberflaeche: siehe DEMO_LOGINS (nur fuer diese Wegwerf-Datenbank)."""
import json
import time
import uuid as uuidlib

# Nur fuer die Wegwerf-Datenbank der Demo (tools/demo_server.py), nirgends sonst gueltig
DEMO_LOGINS = {"admin": "demo-admin-2026", "petra": "demo-petra-2026"}

# Rufnummern der (Fake-)Fritzbox: MSN aus dem 555er-Block, Klingel-Rufnummern 99NNN
MSN_ZENTRALE, MSN_PETRA, MSN_CLEMENS, MSN_SERVICE = "5550101", "5550102", "5550103", "5550104"
FRITZ_NUMBERS = [
    {"number": MSN_ZENTRALE, "name": "Zentrale"},
    {"number": MSN_PETRA, "name": "Vertrieb"},
    {"number": MSN_CLEMENS, "name": "Technik"},
    {"number": MSN_SERVICE, "name": "Service"},
]
RING_NUMBERS = ["99610", "99611", "99613"]          # Klingel-Rufnummern 99NNN (in der Fritzbox angelegt)
PHONES = [
    {"number": "**610", "name": "Mobilteil 1", "kind": "DECT"},
    {"number": "**611", "name": "Mobilteil 2", "kind": "DECT"},
    {"number": "**612", "name": "Empfang", "kind": "DECT"},
    {"number": "**613", "name": "Mobilteil Lager", "kind": "DECT"},
    {"number": "**620", "name": "IP-Telefon Besprechung", "kind": "IP"},
    {"number": "**1", "name": "FON1: Fax", "kind": "analog"},
]

GREETING = "Guten Tag, Sie sprechen mit der Telefonassistenz der Beispiel GmbH. Wie kann ich Ihnen helfen?"
GREETING_LATE = ("Guten Abend, Sie sprechen mit der Telefonassistenz der Beispiel GmbH. Unser Büro ist gerade nicht besetzt – "
                 "ich nehme Ihr Anliegen gern auf.")
PROMPT = ("Du bist die freundliche Telefonassistenz der Beispiel GmbH. Sprich Deutsch, antworte kurz und natürlich. "
          "Frage nach Name, Rückrufnummer und Anliegen. Verbinde mit dem passenden Ansprechpartner, wenn er erreichbar ist, "
          "sonst nimm eine Nachricht auf. Wenn das Gespräch beendet ist, rufe end_call auf.")

CONTACTS = [
    ("Beispiel Handel GmbH", "0221 5550150", "Kunde seit 2024", ""),
    ("Herr Huber", "0151 00004711", "Wartungsvertrag", "Herr"),
    ("Frau Kaiser", "0151 00002233", "", "Frau"),
    ("Muster & Partner KG", "0221 5550170", "Lieferant", ""),
    ("Musterbau AG", "0221 5550180", "", ""),
]


async def _ok(r):
    assert r.status_code < 300, (r.request.method, str(r.request.url), r.status_code, r.text[:300])
    return r.json()


async def seed_config(http, db):
    """Benutzer, Status, Rufnummern, Agenten, Regeln, Kontakte und Einstellungen ueber die API (Admin-Rechte im Prozess)."""
    tomorrow = time.strftime("%Y-%m-%dT17:00", time.localtime(time.time() + 86400))
    week = [{"days": [0, 1, 2, 3, 4], "from": "08:00", "to": "17:00", "status": "office", "note": ""}]
    await _ok(await http.put("/api/settings", json={"schedule": week, "budget_eur": 25}))
    petra = (await _ok(await http.post("/api/staff", json={
        "name": "Petra Muster", "role": "Vertrieb", "number": "**610", "away_number": "0151 00001234",
        "status": "office", "nicknames": "Petra", "schedule": week, "use_hours": 1})))["id"]
    clemens = (await _ok(await http.post("/api/staff", json={
        "name": "Clemens Beispiel", "role": "Technik", "number": "**611", "away_number": "0151 00005678",
        "status": "away", "note": "Kundentermin", "until": tomorrow})))["id"]
    anna = (await _ok(await http.post("/api/staff", json={
        "name": "Anna Probe", "role": "Buchhaltung", "number": "**612", "status": "off", "note": "Urlaub"})))["id"]
    jonas = (await _ok(await http.post("/api/staff", json={
        "name": "Jonas Vorlage", "role": "Service", "number": "**620", "status": "dnd", "note": "Besprechung",
        "dnd_allow": "0151 00004711"})))["id"]
    zentrale = (await _ok(await http.post("/api/staff", json={
        "name": "Sammelziel Zentrale", "kind": "group", "number": "**610, **612"})))["id"]
    # Vertretungen
    db.x("UPDATE staff SET deputy_id=? WHERE id=?", (petra, clemens))
    db.x("UPDATE staff SET deputy_id=? WHERE id=?", (petra, anna))
    db.x("UPDATE staff SET see_staff=? WHERE id=?", (str(zentrale), petra))
    # Ausnahmen (Waehlregeln) fuer Petra
    await _ok(await http.put(f"/api/staff/{petra}/rules", json={"rules": [
        {"name": "Stammkunde Huber", "caller_mode": "list", "callers": ["0151 00004711"], "action": "office",
         "note": "immer durchstellen"},
        {"name": "Mittagspause", "caller_mode": "any", "days": [0, 1, 2, 3, 4], "from": "12:30", "to": "13:15", "action": "ai"},
        {"name": "Freitag nachmittag", "caller_mode": "external", "days": [4], "from": "14:00", "to": "17:00", "action": "away",
         "enabled": False},
    ]}))
    await _ok(await http.put(f"/api/staff/{clemens}/rules", json={"rules": [
        {"name": "Notfall Wartung", "caller_mode": "contacts", "action": "forward", "target": "0151 00005678"}]}))
    # Rufnummern zuweisen (Seite "Rufnummern"): Zentrale geteilt, Vertrieb/Technik exklusiv
    for num, ids in ((MSN_ZENTRALE, [zentrale]), (MSN_PETRA, [petra]), (MSN_CLEMENS, [clemens]), (MSN_SERVICE, [jonas, anna])):
        await _ok(await http.put(f"/api/numbers/{num}", json={"staff_ids": ids}))
    # Anmeldekonten
    await _ok(await http.post("/api/users", json={"username": "petra", "password": DEMO_LOGINS["petra"], "role": "user",
                                                  "staff_id": petra}))
    await _ok(await http.post("/api/users", json={"username": "clemens", "password": "demo-clemens-2026", "role": "user",
                                                  "staff_id": clemens}))
    # Agenten: Standard-Agent umbenennen, zweiten anlegen
    a1 = db.list_agents()[0]
    a1.update(name="Empfang", greeting=GREETING, prompt=PROMPT, voice="Kore", questions="Name\nRückrufnummer\nAnliegen",
              meta_on=1, mood_on=1)
    await _ok(await http.put(f"/api/agents/{a1['id']}", json=a1))
    a2 = (await _ok(await http.post("/api/agents", json={
        "name": "Nach Feierabend", "mode": "assist", "voice": "Aoede", "greeting": GREETING_LATE, "prompt": PROMPT,
        "enabled": 1, "priority": 200, "notice_on": 1, "notice": "Sie sprechen mit einer KI. Das Gespräch wird transkribiert.",
        "allow_connect": 0, "meta_on": 1})))["id"]
    # Anrufregeln: vorhandene (aus der Migration) entfernen, eigene anlegen
    for r in db.list_call_rules():
        await _ok(await http.delete(f"/api/rules/call/{r['id']}"))
    for r in db.list_rec_rules():
        if r.get("direction") != "out":
            await _ok(await http.delete(f"/api/rules/rec/{r['id']}"))
    await _ok(await http.post("/api/rules/call", json={"name": "Mehrfach angerufen", "priority": 10, "agent_id": a1["id"], "ring_s": 0,
                                                      "repeat_n": 2, "repeat_min": 30, "repeat_kind": "hangup"}))
    await _ok(await http.post("/api/rules/call", json={"name": "Geschäftszeiten", "priority": 50, "agent_id": a1["id"], "ring_s": 20,
                                                      "schedule": "open"}))
    await _ok(await http.post("/api/rules/call", json={"name": "Außerhalb der Geschäftszeiten", "priority": 60, "agent_id": a2,
                                                      "ring_s": 0, "schedule": "closed"}))
    await _ok(await http.post("/api/rules/call", json={"name": "Service-Hotline", "priority": 70, "agent_id": a1["id"], "ring_s": 30,
                                                      "match_called": MSN_SERVICE, "enabled": False}))
    await _ok(await http.post("/api/rules/rec", json={"name": "Transkript aller KI-Gespräche", "agent_id": 0, "transcript": 1,
                                                     "audio": 0, "direction": "in"}))
    await _ok(await http.post("/api/rules/rec", json={"name": "Aufnahme Service-Hotline", "agent_id": 0, "transcript": 1, "audio": 1,
                                                     "match_called": MSN_SERVICE, "direction": "in", "enabled": False}))
    for name, number, note, sal in CONTACTS:
        await _ok(await http.post("/api/contacts", json={"name": name, "number": number, "note": note, "salutation": sal}))
    # Mail-Empfaenger (nur example.com; in der Demo wird nie gesendet)
    await _ok(await http.post("/api/mail/recipients", json={"email": "buero@example.com", "label": "Büro", "on_call": "all",
                                                           "detail": "short", "daily": 1}))
    # Klingel-Rufnummern (Anruferanzeige): 99610 und 99611 eingerichtet, 99613 = alle Telefone
    db.set_meta("ring_lines", "99610,99611")
    db.set_meta("ring_ok", "99610,99611")
    db.set_meta("ring_all", "99613")
    # Petra: ausgehende Gespraeche ohne Vorwahl mitschreiben, ihr Telefon **610 ist (laut Fake-Fritzbox) auf 99610 umgestellt
    db.x("UPDATE staff SET rec_out='transcript' WHERE id=?", (petra,))
    db.set_meta("out_orig:**610", json.dumps({"orig": MSN_PETRA, "sid": petra, "kind": "DECT"}))
    return {"petra": petra, "clemens": clemens, "anna": anna, "jonas": jonas, "zentrale": zentrale,
            "empfang": a1["id"], "feierabend": a2}


def _ago(days, hh, mm, ss=0):
    """Zeitpunkt vor `days` Tagen um hh:mm:ss Ortszeit (Anrufe der letzten Tage, relativ zu heute)."""
    t = time.localtime(time.time() - days * 86400)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hh, mm, ss, 0, 0, -1))


def _call(db, started, dur, caller, called, status, agent="", meta=None, notes=None, cost=0.0, direction="in", staff=None,
          user_id=0, task=""):
    cid = db.x("INSERT INTO calls(uuid, started, ended, caller, called, agent_name, status, direction, meta, notes, cost_usd, "
               "owner_staff_id, user_id, task, tok_in_audio, tok_out_audio) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (str(uuidlib.uuid4()), started, started + dur, caller, called, agent, status, direction,
                json.dumps(meta, ensure_ascii=False) if meta else "", json.dumps(notes or {}, ensure_ascii=False), cost, staff,
                user_id, task, int(dur * 25) if agent else 0, int(dur * 12) if agent else 0))
    return cid


def _events(db, cid, t0, evs):
    for off, kind, data in evs:
        db.x("INSERT INTO call_events(call_id, ts, kind, data) VALUES(?,?,?,?)", (cid, t0 + off, kind, json.dumps(data, ensure_ascii=False)))


def _segments(db, cid, t0, segs, agent="Empfang"):
    for off, speaker, text in segs:
        db.x("INSERT INTO segments(call_id, ts, speaker, text, agent) VALUES(?,?,?,?,?)",
             (cid, t0 + off, speaker, text, agent if speaker == "agent" else ""))


def seed_calls(db, ids):
    """Anrufe der letzten Tage: Gruppierung (wiederholte Anrufe derselben Nummer), ein Anruf mit Transkript, Zusammenfassung und
    Ablauf, Durchstellen, nicht angenommene Anrufe, ein ausgehendes KI-Gespraech und eine Mitschrift."""
    huber, kaiser, handel, partner = "015100004711", "015100002233", "02215550150", "02215550170"
    unknown1, unknown2 = "02215550199", "015100009876"
    # vor 2 Tagen
    t = _ago(2, 9, 12)
    c = _call(db, t, 74, handel, MSN_ZENTRALE, "durchgestellt", "Empfang", cost=0.012,
              meta={"zusammenfassung": "Rückfrage zu einer Lieferung, an Petra Muster durchgestellt.", "anliegen": "Lieferung",
                    "dringlichkeit": "mittel", "stimmung": "neutral", "sprache": "Deutsch", "betrugsverdacht": "nein"})
    _events(db, c, t, [(0, "ring", {"targets": "**610+**612", "label": "Sammelziel Zentrale", "rules": ["Geschäftszeiten"], "ring": 20}),
                       (20, "ai", {"agent": "Empfang", "rule": "Geschäftszeiten"}),
                       (52, "transfer", {"targets": "**610", "screen": True, "who": "Petra Muster"}),
                       (61, "screen_result", {"who": "Petra Muster", "result": "accept"}), (62, "answered", {})])
    t = _ago(2, 11, 40)
    _call(db, t, 0, unknown1, MSN_PETRA, "unbeantwortet")
    t = _ago(2, 15, 5)
    c = _call(db, t, 132, partner, MSN_CLEMENS, "beendet", "Empfang", cost=0.021,
              meta={"zusammenfassung": "Lieferant meldet Terminverschiebung, bittet Clemens Beispiel um Rückruf.",
                    "anliegen": "Termin", "dringlichkeit": "niedrig", "stimmung": "freundlich", "sprache": "Deutsch",
                    "betrugsverdacht": "nein", "naechster_schritt": "Rückruf durch Clemens Beispiel"},
              notes={"Name": "Frau Vogel (Muster & Partner KG)", "Rückrufnummer": "0221 5550170", "Anliegen": "Liefertermin verschoben"})
    _events(db, c, t, [(0, "ai", {"agent": "Empfang", "rule": "Geschäftszeiten"}), (131, "ai_end", {"reason": "KI hat aufgelegt"})])
    t = _ago(2, 19, 22)
    c = _call(db, t, 58, unknown2, MSN_ZENTRALE, "beendet", "Nach Feierabend", cost=0.009,
              meta={"zusammenfassung": "Anfrage nach den Öffnungszeiten, keine Nachricht.", "anliegen": "Information",
                    "dringlichkeit": "niedrig", "stimmung": "neutral", "sprache": "Deutsch", "betrugsverdacht": "nein"})
    _events(db, c, t, [(0, "ai", {"agent": "Nach Feierabend", "rule": "Außerhalb der Geschäftszeiten"}),
                       (57, "ai_end", {"reason": "Anrufer hat aufgelegt"})])
    # gestern
    t = _ago(1, 8, 47)
    c = _call(db, t, 205, "**610", "0221 5550180", "beendet", "", direction="rec", user_id=2, staff=ids["petra"])
    _segments(db, c, t, [(3, "local", "Guten Morgen, Petra Muster von der Beispiel GmbH. Ich rufe wegen Ihres Angebots an."),
                         (9, "remote", "Guten Morgen! Ja, das Angebot ist gestern rausgegangen."),
                         (16, "local", "Wir würden gern die Variante mit Wartung nehmen, Lieferung bis Ende des Monats."),
                         (24, "remote", "Das passt. Ich schicke Ihnen heute noch die Auftragsbestätigung.")])
    t = _ago(1, 10, 3)
    c = _call(db, t, 96, kaiser, MSN_PETRA, "beendet", "Empfang", cost=0.015,
              meta={"zusammenfassung": "Frau Kaiser bittet um Rückruf wegen einer Rechnung.", "anliegen": "Rechnung",
                    "dringlichkeit": "mittel", "stimmung": "freundlich", "sprache": "Deutsch", "betrugsverdacht": "nein",
                    "naechster_schritt": "Rückruf heute Nachmittag"},
              notes={"Name": "Frau Kaiser", "Rückrufnummer": "0151 00002233", "Anliegen": "Frage zur Rechnung"})
    _events(db, c, t, [(0, "ring", {"targets": "**610", "label": "Petra Muster", "rules": ["Geschäftszeiten"], "ring": 20}),
                       (20, "leg_end", {"target": "99610", "cause": 0, "after": 20}),
                       (20, "ai", {"agent": "Empfang", "rule": "Geschäftszeiten"}), (95, "ai_end", {"reason": "KI hat aufgelegt"})])
    t = _ago(1, 14, 30)
    _call(db, t, 0, handel, MSN_ZENTRALE, "abgebrochen")
    t = _ago(1, 16, 12)
    c = _call(db, t, 140, "**610", "0221 5550150", "beendet", "Empfang", direction="out", cost=0.02, user_id=2,
              task="Termin für die Wartung am Donnerstag bestätigen",
              meta={"zusammenfassung": "Wartungstermin am Donnerstag um 10 Uhr bestätigt.", "anliegen": "Termin",
                    "dringlichkeit": "niedrig", "stimmung": "freundlich", "sprache": "Deutsch", "betrugsverdacht": "nein"})
    _events(db, c, t, [(0, "ai", {"agent": "Empfang"}), (139, "ai_end", {"reason": "KI hat aufgelegt"})])
    # heute: Herr Huber ruft dreimal an (Gruppierung), der dritte Anruf mit Transkript, Zusammenfassung und Ablauf
    t = _ago(0, 8, 2)
    c = _call(db, t, 18, huber, MSN_CLEMENS, "unbeantwortet")
    _events(db, c, t, [(0, "ring", {"targets": "**611", "label": "Clemens Beispiel", "ring": 20}),
                       (18, "unanswered", {})])
    t = _ago(0, 8, 9)
    c = _call(db, t, 25, huber, MSN_CLEMENS, "nicht erreicht")
    _events(db, c, t, [(0, "ring", {"targets": "**611", "label": "Clemens Beispiel", "rules": ["Geschäftszeiten"], "ring": 20}),
                       (20, "leg_end", {"target": "99611", "cause": 19, "tech": "SIP 480", "after": 20}),
                       (20, "no_answer", {"status": "NOANSWER"}), (25, "hangup", {})])
    t = _ago(0, 8, 14)
    big = _call(db, t, 171, huber, MSN_CLEMENS, "durchgestellt", "Empfang", cost=0.034,
                meta={"zusammenfassung": "Herr Huber meldet eine Störung an der Heizungssteuerung im Lager und bittet heute noch um "
                                         "einen Techniker. Clemens Beispiel ist außer Haus; die KI hat an Petra Muster durchgestellt.",
                      "anliegen": "Störung", "dringlichkeit": "hoch", "stimmung": "besorgt", "sprache": "Deutsch",
                      "betrugsverdacht": "nein", "ergebnis": "An Petra Muster durchgestellt (Vertretung).",
                      "naechster_schritt": "Techniker-Termin heute bis 14 Uhr vereinbaren", "genannte_namen": "Herr Huber, Clemens Beispiel",
                      "detail": {"fazit": "Dringende Störungsmeldung eines Wartungskunden; Weitergabe an die Vertretung hat geklappt.",
                                 "punkte": [{"thema": "Störung", "inhalt": "Heizungssteuerung im Lager zeigt seit dem Morgen Fehler E4.",
                                             "ergebnis": "Techniker soll heute kommen"},
                                            {"thema": "Erreichbarkeit", "inhalt": "Clemens Beispiel ist bis morgen außer Haus.",
                                             "ergebnis": "Vertretung Petra Muster übernimmt"}],
                                 "zusagen": [{"wer": "Petra Muster", "was": "Techniker-Termin vereinbaren", "bis_wann": "heute 14 Uhr"}],
                                 "offen": []}},
                notes={"Name": "Herr Huber", "Rückrufnummer": "0151 00004711", "Anliegen": "Störung Heizungssteuerung, Fehler E4"})
    _events(db, big, t, [
        (0, "ring", {"targets": "**611", "label": "Clemens Beispiel", "rules": ["Mehrfach angerufen"], "ring": 0}),
        (0.4, "ai", {"agent": "Empfang", "rule": "Mehrfach angerufen"}),
        (1.2, "rec", {"what": "ai"}),
        (78, "transfer", {"targets": "**610", "screen": True, "who": "Petra Muster"}),
        (86, "screen_result", {"who": "Petra Muster", "result": "accept"}),
        (87, "leg_end", {"target": "99610", "cause": 16, "after": 9}),
        (87.5, "answered", {}),
        (171, "hangup", {})])
    _segments(db, big, t, [
        (1, "agent", GREETING),
        (6, "caller", "Ja, guten Morgen, Huber hier. Ich versuche schon die ganze Zeit, Herrn Beispiel zu erreichen."),
        (12, "agent", "Das tut mir leid, Herr Huber. Herr Beispiel ist heute außer Haus. Worum geht es denn?"),
        (18, "caller", "Bei uns im Lager zeigt die Heizungssteuerung seit heute früh den Fehler E4. Da müsste heute noch jemand kommen."),
        (28, "agent", "Verstehe, eine Störung an der Heizungssteuerung mit Fehler E4. Unter welcher Nummer erreichen wir Sie?"),
        (35, "caller", "Unter dieser Handynummer, null eins fünf eins, null null null null, vier sieben eins eins."),
        (44, "agent", "Danke. Ich frage kurz bei Frau Muster nach, sie vertritt Herrn Beispiel. Einen Moment bitte."),
        (78, "system", "Rückfrage bei Petra Muster (**610)"),
        (86, "system", "Petra Muster hat angenommen – Gespräch durchgestellt"),
        (90, "agent", "Frau Muster übernimmt jetzt. Auf Wiederhören, Herr Huber!"),
    ])
    t = _ago(0, 9, 31)
    c = _call(db, t, 47, unknown1, MSN_ZENTRALE, "beendet", "Empfang", cost=0.008,
              meta={"zusammenfassung": "Werbeanruf, abgelehnt.", "anliegen": "Werbung", "dringlichkeit": "niedrig",
                    "stimmung": "neutral", "sprache": "Deutsch", "betrugsverdacht": "moeglich"})
    _events(db, c, t, [(0, "ring", {"targets": "**610+**612", "label": "Sammelziel Zentrale", "ring": 20}),
                       (20, "ai", {"agent": "Empfang", "rule": "Geschäftszeiten"}), (46, "ai_end", {"reason": "KI hat aufgelegt"})])
    t = _ago(0, 10, 5)
    c = _call(db, t, 63, handel, MSN_ZENTRALE, "angenommen")
    _events(db, c, t, [(0, "ring", {"targets": "**610+**612", "label": "Sammelziel Zentrale", "ring": 20}),
                       (6, "answered", {})])
    return big


async def seed_all(http, db):
    ids = await seed_config(http, db)
    big = seed_calls(db, ids)
    db.set_meta("setup_done", "1")
    return dict(ids, big_call=big)
