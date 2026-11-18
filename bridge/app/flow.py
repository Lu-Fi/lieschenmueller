"""Ablauf eines Anrufs: strukturierte Ereignisse (Tabelle call_events) zusaetzlich zu den Transkript-Segmenten.

Jedes Ereignis hat Zeit, Art (kind) und Daten (JSON). Die Oberflaeche zeigt daraus im Anruf den Abschnitt "Ablauf" als Zeitleiste
in verstaendlichem Deutsch, mit der Dauer bis zum naechsten Schritt. Alte Anrufe ohne Ereignisse zeigen den Abschnitt nicht.
Schreiben ist nie kritisch: ein Fehler hier wird protokolliert und aendert nichts am Anruf.

| Art | Daten | Text (Beispiel) |
|---|---|---|
| ring | targets, ring, label, rules, record | klingelte bei **610 + **620 (Regel: Abends, 25 s) |
| wait | seconds, agent, rule | klingelte, Agent Empfang nimmt nach 20 s ab (Anrufregel Standard) |
| no_answer | status (DIALSTATUS, optional) | niemand nahm ab (keine Antwort) |
| ai | agent, rule, retry, remote | Agent Empfang übernahm (Anrufregel Standard) |
| ai_end | reason | KI-Gespräch beendet: KI hat aufgelegt |
| transfer | targets, record, screen, who | Weiterleitung an **610 / Rückfrage bei Petra (**610) |
| screen_result | who, result | Rückfrage bei Petra -> abgelehnt |
| busy | status, target | Durchstellen fehlgeschlagen (besetzt), KI nimmt wieder an |
| answered | – | jemand nahm ab |
| rec | what (start, ai, append, conversation) | Aufnahme gestartet / angehängt |
| cost_brake | reason, agent | Kostenbremse: … |
| no_agent | – | kein Agent zuständig, nicht angenommen |
| manual | action, to | manuell angenommen / manuell umgeleitet an … |
| unanswered | – | Anrufer legte vor der Annahme auf |
| hangup | – | aufgelegt |
| line | sides {Gegenseite/Mitarbeiter: dur, speech, tone, silence, tone_first}, warn | Seitenprüfung: Gegenseite 0 s Sprache, 2 s Freizeichen … |
| qos | leg (a = eingehend, b = angerufenes Telefon), Werte aus CHANNEL(rtcp,all) | Leitungswerte angerufenes Telefon: 520 Pakete empfangen … |
| leg_end | target (99610 / **610), cause (Q.850), tech (SIP-Antwort), after (s seit dem Klingeln) | **610 (über 99610): Klingeln endete nach 14 s – nicht abgenommen |
"""
import json
import logging
import time

from . import db

log = logging.getLogger("flow")

SCREEN_RESULT = {"accept": "angenommen", "decline": "abgelehnt", "voicemail": "Mailbox", "none": "keine Antwort",
                 "timeout": "keine Antwort"}
BUSY = {"BUSY": "besetzt", "NOANSWER": "keine Antwort", "CHANUNAVAIL": "nicht erreichbar", "CONGESTION": "nicht erreichbar",
        "ANSWER": "Rückfrage ohne Zusage", "abgelehnt": "abgelehnt", "Mailbox": "Mailbox"}
# Q.850-Gruende (HANGUPCAUSE) fuer das Ende des Klingelns an einem Telefon
CAUSE = {0: "Klingelzeit abgelaufen (nicht abgenommen)", 16: "beendet (Anrufer aufgelegt oder anderes Telefon nahm ab)", 17: "besetzt", 18: "Telefon antwortet nicht (nicht erreichbar)",
         19: "nicht abgenommen", 20: "Telefon nicht angemeldet", 21: "abgelehnt", 26: "ein anderes Telefon nahm ab",
         1: "Nummer unbekannt", 3: "keine Verbindung zum Ziel", 27: "Ziel gestört", 31: "beendet",
         34: "keine Leitung frei", 38: "Netz gestört", 41: "vorübergehende Störung", 42: "Fritzbox überlastet", 127: "unbekannter Grund"}
REC = {"start": "Aufnahme gestartet", "ai": "Aufnahme des KI-Gesprächs gestartet", "append": "zweiter KI-Abschnitt an die Aufnahme angehängt",
       "conversation": "Gespräch an die Aufnahme angehängt", "stop": "Aufnahme beendet und verworfen"}


def add(call_id, kind, **data):
    """Ereignis speichern (nie mit Fehler zurueck)."""
    if not call_id:
        return
    try:
        db.x("INSERT INTO call_events(call_id, ts, kind, data) VALUES(?,?,?,?)",
             (int(call_id), time.time(), kind, json.dumps(data, ensure_ascii=False, default=str)))
    except Exception:
        log.exception("Ablauf-Ereignis %s fuer Anruf %s nicht gespeichert", kind, call_id)


def add_for(uuid, kind, **data):
    """Wie add, Anruf ueber die Asterisk-UUID."""
    try:
        r = db.q("SELECT id FROM calls WHERE uuid=?", (uuid,))
    except Exception:
        return
    if r:
        add(r[0]["id"], kind, **data)


def since(uuid, kind):
    """Sekunden seit dem juengsten Ereignis dieser Art(en) im Anruf (kind: Name oder Liste, z. B. ("ring", "transfer") = Beginn des
    letzten Klingelns, auch beim Durchstellen/bei der Rueckfrage), sonst None."""
    kinds = [kind] if isinstance(kind, str) else list(kind)
    try:
        r = db.q("SELECT e.ts FROM call_events e JOIN calls c ON c.id=e.call_id WHERE c.uuid=? AND e.kind IN (%s) "
                 "ORDER BY e.ts DESC LIMIT 1" % ",".join("?" * len(kinds)), (uuid, *kinds))
    except Exception:
        return None
    return round(time.time() - r[0]["ts"], 1) if r else None


def _targets(t):
    return " + ".join(x for x in str(t or "").split("+") if x) or "?"


def describe(kind, d):
    """Verstaendlicher deutscher Text zu einem Ereignis."""
    if kind == "ring":
        extra = [x for x in (("Regel: " + ", ".join(d["rules"])) if d.get("rules") else "",
                             f"{d['ring']} s" if d.get("ring") else "", "mit Aufnahme" if d.get("record") else "") if x]
        who = f" ({d['label']})" if d.get("label") else ""
        return f"klingelte bei {_targets(d.get('targets'))}{who}" + (f" – {', '.join(extra)}" if extra else "")
    if kind == "wait":
        return (f"klingelte, Agent {d.get('agent') or '?'} nimmt nach {d.get('seconds')} s ab"
                + (f" (Anrufregel {d['rule']})" if d.get("rule") else ""))
    if kind == "no_answer":
        st = BUSY.get(d.get("status") or "", "")
        return "niemand nahm ab" + (f" ({st})" if st else "")
    if kind == "leg_end":
        t = str(d.get("target") or "")
        who = f"**{t[2:]} (über {t})" if t.startswith("99") and len(t) > 2 else (t or "Telefon")
        why = CAUSE.get(d.get("cause"), f"Grund {d.get('cause')}")
        tech = f", {d['tech']}" if d.get("tech") else ""
        aft = f" nach {d['after']:g} s" if isinstance(d.get("after"), (int, float)) else ""
        return f"{who}: Klingeln endete{aft} – {why}{tech}"
    if kind == "ai":
        if d.get("retry"):
            return f"KI nahm den Anrufer wieder an (Agent {d.get('agent') or '?'})"
        return (f"Agent {d.get('agent') or '?'} übernahm" + (f" (Anrufregel {d['rule']})" if d.get("rule") else "")
                + (" – Fernabfrage möglich" if d.get("remote") else ""))
    if kind == "ai_end":
        return "KI-Gespräch beendet: " + str(d.get("reason") or "").replace("Gespraech beendet: ", "")
    if kind == "transfer":
        if d.get("screen"):
            return f"Rückfrage bei {d.get('who') or _targets(d.get('targets'))} ({_targets(d.get('targets'))})"
        return f"Weiterleitung an {_targets(d.get('targets'))}" + (" mit Mitschrift" if d.get("record") else "")
    if kind == "screen_result":
        return f"Rückfrage bei {d.get('who') or 'der Person'} → {SCREEN_RESULT.get(d.get('result'), d.get('result') or '?')}"
    if kind == "busy":
        return f"Durchstellen fehlgeschlagen ({BUSY.get(d.get('status'), d.get('status') or 'keine Antwort')}), die KI nimmt wieder an"
    if kind == "answered":
        return "jemand nahm ab"
    if kind == "rec":
        return REC.get(d.get("what"), "Aufnahme")
    if kind == "cost_brake":
        return f"Kostenbremse: {d.get('reason') or ''}".strip()
    if kind == "no_agent":
        return "kein Agent zuständig – nicht angenommen"
    if kind == "manual":
        return "manuell angenommen (KI)" if d.get("action") == "accept" else f"manuell umgeleitet an {_targets(d.get('to'))}"
    if kind == "unanswered":
        return "Anrufer legte vor der Annahme auf (oder ein anderes Gerät nahm ab)"
    if kind == "hangup":
        return "aufgelegt"
    if kind == "line":
        from . import linediag
        parts = [linediag.side_text(n, a) for n, a in (d.get("sides") or {}).items()]
        return "Seitenprüfung: " + "; ".join(parts) + "".join(f" ⚠ {w}" for w in d.get("warn") or [])
    if kind == "qos":
        from . import linediag
        return linediag.qos_text(d.get("leg"), d)
    return kind


def list_for(call_id, ended=None):
    """Ereignisse eines Anrufs fuer die Oberflaeche: ts, kind, data, text, dur (Sekunden bis zum naechsten Ereignis bzw. Ende)."""
    try:
        rows = db.q("SELECT ts, kind, data FROM call_events WHERE call_id=? ORDER BY ts, id", (call_id,))
    except Exception:
        log.exception("Ablauf von Anruf %s nicht lesbar", call_id)
        return []
    out = []
    for i, r in enumerate(rows):
        try:
            d = json.loads(r["data"] or "{}")
        except ValueError:
            d = {}
        nxt = rows[i + 1]["ts"] if i + 1 < len(rows) else ended
        out.append({"ts": r["ts"], "kind": r["kind"], "data": d, "text": describe(r["kind"], d),
                    "dur": round(max(0.0, nxt - r["ts"]), 1) if nxt else None})
    return out
