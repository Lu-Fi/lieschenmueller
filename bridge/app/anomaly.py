"""Selbstpruefung "Auffaelligkeiten bei Anrufen": erkennt Stoerungen aus dem Ablauf der Anrufe und aus der Fritzbox.

Regeln (je Regel abschaltbar, Schwellen in den Einstellungen, meta anomaly_cfg):
  oneway      Einweg-Ton: angenommener/KI-Anruf, eingehende Leitung (qos leg a) ohne RTCP-Bestaetigung (rtt 0) bei Dauer > oneway_s,
              oder die KI sendete deutlich weniger Pakete als ankamen (txcount < tx_ratio % von rxcount bei KI-Gespraech > ai_s)
  deaf        KI hoert nichts: KI-Gespraech > ai_s, viele Pakete empfangen, aber kein Anrufer-Segment und Ende durch Stille/KI
  ring_dead   Klingeln ohne Wirkung: dasselbe Telefon klingelte bei ring_streak Anrufen in Folge bis zum Ende (180 Ringing), nie abgenommen
  ring_short  Klingeln endete nach < short_s ohne erkennbaren Grund (nicht besetzt/abgelehnt/anderes Telefon/Anrufer legte auf)
  fritz_log   Ereignisprotokoll der Fritzbox (nur neue Zeilen): Internettelefonie nicht erfolgreich (gefiltert, siehe voip_class),
              Anmeldung gescheitert
  inet        dort zusaetzlich: Internetverbindung getrennt (Neueinwahl, Zwangstrennung); eigene Regel, damit abschaltbar
  voip_status eigene Rufnummern der Fritzbox nicht angemeldet (X_AVM-DE_GetVoIPStatus, zweimal in Folge)
  undelivered viele nicht zugestellte Anrufe (Anrufliste Typ 2/10 ohne Geraet) auf einer Rufnummer in kurzer Zeit (neutral, ohne Wertung)
  ring_lines  Klingel-Rufnummern 99NNN bzw. die Anmeldung von Asterisk an der Fritzbox fehlen. Das meldet ops/watchdog.py schon per Mail:
              hier nur ein Eintrag in der Oberflaeche (mail=0), keine zweite Mail

Ablage: Tabelle anomalies (ein Eintrag je Dedupe-Schluessel; tritt dasselbe wieder auf, zaehlt count hoch, der Eintrag wird wieder offen,
aber nicht noch einmal gemailt). Mail nur an Empfaenger mit der Option on_anomaly ("Stoerungs-/Auffaelligkeitsmeldungen"), gebuendelt
(hoechstens eine Sammelmail je mail_min Minuten, hoechstens mail_day am Tag). Ohne solche Empfaenger: nur die Oberflaeche.
Rufnummern stehen in Texten nur gekuerzt (letzte 4 Ziffern), Namen nicht.

Robust: alle Pruefungen laufen im Hintergrund (loop), Fehler beim Lesen der Fritzbox werden protokolliert und stoeren den Anrufbetrieb
nie; TR-064 ueber die gemeinsame Verbindung (fritz._with_conn)."""
import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime

from . import db

log = logging.getLogger("anomaly")

# Regeln fuer die Oberflaeche: Schluessel, Titel, Erklaerung, Schwellen (Schluessel, Text, Standard, min, max, Erklaerung)
RULES = [
    ("oneway", "Einweg-Ton / keine RTCP-Bestätigung",
     "Angenommener oder von der KI geführter Anruf, bei dem die eingehende Leitung keine RTCP-Rückmeldung der Gegenseite hatte "
     "(Laufzeit 0) oder die KI deutlich weniger Pakete sendete als ankamen. Hinweis auf Ton nur in eine Richtung.",
     [("oneway_s", "Mindestdauer ohne RTCP (s)", 20, 5, 600, "Erst ab dieser Gesprächsdauer gilt fehlendes RTCP als auffällig."),
      ("ai_s", "Mindestdauer KI-Gespräch (s)", 30, 5, 600, "Für den Vergleich gesendet/empfangen und für „KI hört nichts“."),
      ("tx_ratio", "Gesendet unter (% der empfangenen)", 50, 5, 95, "Normal sind gesendete ≈ empfangene Pakete.")]),
    ("deaf", "KI hört nichts",
     "KI-Gespräch mit vielen empfangenen Paketen, aber ohne ein einziges erkanntes Wort des Anrufers, und die KI beendete es "
     "(Stille oder KI legte auf).",
     [("rx_min", "Mindestens empfangene Pakete", 1000, 50, 100000, "50 Pakete ≈ 1 s Ton.")]),
    ("ring_dead", "Klingeln ohne Wirkung",
     "Dasselbe Telefon klingelte bei mehreren Anrufen in Folge bis zum Ende („180 Ringing“), ohne dass es je abgenommen wurde. "
     "Klingelt es wirklich?",
     [("ring_streak", "Anrufe in Folge", 3, 2, 20, "So viele Anrufe hintereinander ohne Abnehmen."),
      ("ring_min", "Klingeln mindestens (s)", 15, 3, 120, "Kürzeres Klingeln (Anrufer legte früh auf) zählt nicht.")]),
    ("ring_short", "Klingeln endet sofort",
     "Das Klingeln an einem Telefon endete nach wenigen Sekunden ohne erkennbaren Grund (nicht besetzt, nicht abgelehnt, kein anderes "
     "Telefon nahm ab, der Anrufer legte nicht auf).",
     [("short_s", "Kürzer als (s)", 3, 1, 15, "")]),
    ("fritz_log", "Ereignisprotokoll der Fritzbox",
     "Liest regelmäßig neue Zeilen im Ereignisprotokoll. Gemeldet werden Störungen beim Anbieter (z. B. 500, 503, 408, 480 über den "
     "Anbieter) erst, wenn sie sich häufen, abgewiesene Anmeldung beim Anbieter (401, 403, 407) sofort, gescheiterte Anmeldung einer "
     "Rufnummer, Internetverbindung getrennt (eigene Regel „Neueinwahl“). Nicht gemeldet: normale Ausgänge eines Anrufs (besetzt, "
     "abgelehnt, falsche Nummer, Anrufer legte auf) und Telefone im Haus (über eine lokale Adresse, auch Asterisk: Telefon aus, "
     "„Nicht stören“).",
     [("log_min", "Abfrage alle (min)", 5, 1, 60, "Gilt auch für Rufnummern-Status, Anrufliste und Klingel-Rufnummern."),
      ("voip_n", "Störungen beim Anbieter: melden ab", 3, 1, 50, "So viele nicht erfolgreiche Verbindungen über den Anbieter …"),
      ("voip_min", "… innerhalb (min)", 30, 5, 1440, "Einzelne Aussetzer beim Anbieter sind normal.")]),
    ("inet", "Neueinwahl der Internetverbindung",
     "Zeile im Ereignisprotokoll der Fritzbox: Internetverbindung getrennt, Zwangstrennung, DSL antwortet nicht. Bei Anschlüssen mit "
     "täglicher Zwangstrennung ist das normal und lässt sich hier abschalten. Braucht die Regel „Ereignisprotokoll der Fritzbox“.", []),
    ("voip_status", "Rufnummer nicht angemeldet",
     "Fragt bei der Fritzbox den Status aller eigenen Rufnummern ab (zweimal in Folge nicht „registered“/„connected“).", []),
    ("undelivered", "Viele nicht zugestellte Anrufe",
     "Anrufliste der Fritzbox: viele verpasste oder abgewiesene Anrufe ohne klingelndes Telefon auf derselben Rufnummer in kurzer Zeit. "
     "Neutrale Meldung, ohne Wertung.",
     [("und_count", "Anzahl mindestens", 10, 3, 500, ""), ("und_min", "innerhalb (min)", 10, 1, 120, "")]),
    ("ring_lines", "Klingel-Rufnummern / Asterisk-Anmeldung",
     "Klingel-Rufnummern 99NNN nicht bei Asterisk angemeldet oder Asterisk nicht bei der Fritzbox angemeldet. Nur Eintrag hier: "
     "die Mail dazu verschickt der Wächter (ops/watchdog.py), die ausgehende Mitschrift schaltet selbst in den Notbetrieb.", []),
]
MAIL_OPTS = [("mail_min", "Höchstens eine Sammelmail je (min)", 15, 5, 1440, ""),
             ("mail_day", "Höchstens Mails am Tag", 6, 1, 50, "")]
KIND_LABEL = {r[0]: r[1] for r in RULES}


def defaults():
    d = {r[0]: True for r in RULES}
    for r in RULES:
        d.update({t[0]: t[2] for t in r[3]})
    d.update({t[0]: t[2] for t in MAIL_OPTS})
    return d


def _limits():
    out = {}
    for r in RULES:
        out.update({t[0]: (t[3], t[4]) for t in r[3]})
    out.update({t[0]: (t[3], t[4]) for t in MAIL_OPTS})
    return out


def cfg():
    d = defaults()
    try:
        stored = json.loads(db.get_meta("anomaly_cfg", "") or "{}")
    except ValueError:
        stored = {}
    for k, v in (stored if isinstance(stored, dict) else {}).items():
        if k in d:
            d[k] = v
    return d


def save_cfg(new):
    """Nur bekannte Schluessel; Zahlen auf die Grenzen begrenzt. ValueError bei ungueltigen Werten."""
    cur, lim = cfg(), _limits()
    for k, v in (new or {}).items():
        if k not in cur:
            continue
        if k in lim:
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k}: Zahl erwartet") from None
            lo, hi = lim[k]
            cur[k] = max(lo, min(hi, n))
        else:
            cur[k] = bool(v)
    db.set_meta("anomaly_cfg", json.dumps(cur))
    return cur


def mask(n):
    """Rufnummer fuer Mails/Oberflaeche: nur die letzten 4 Ziffern (…1234); interne Nummern (**610, 99610) bleiben."""
    d = re.sub(r"\D", "", str(n or ""))
    if len(d) <= 5:
        return str(n or "") or "?"
    return "…" + d[-4:]


def mask_text(s):
    return re.sub(r"\+?\d[\d ]{4,}\d", lambda m: mask(m.group(0)) if len(re.sub(r"\D", "", m.group(0))) >= 6 else m.group(0), str(s or ""))


# --- Ablage ---
ON_NEW = []          # Rueckrufe (z. B. Live-Hinweis an die Oberflaeche)


def record(kind, key, text, call_id=None, mail=True, ts=None, bump=True):
    """Auffaelligkeit merken (Dedupe ueber key). Rueckgabe: (id, neu). Gibt es den Schluessel schon: count+1 (bump) und wieder offen,
    aber keine zweite Mail."""
    ts = ts or time.time()
    old = db.q("SELECT id, ack, text FROM anomalies WHERE key=?", (key,))
    if old:
        o = old[0]
        if bump:
            db.x("UPDATE anomalies SET count=count+1, last_ts=?, text=?, ack=0 WHERE id=?", (ts, text, o["id"]))
        elif o["text"] != text:
            db.x("UPDATE anomalies SET last_ts=?, text=?, ack=0 WHERE id=?", (ts, text, o["id"]))
        else:
            return o["id"], False
        _notify()
        return o["id"], False
    aid = db.x("INSERT INTO anomalies(ts, last_ts, kind, key, text, call_id, count, ack, mail, mailed) VALUES(?,?,?,?,?,?,1,0,?,0)",
               (ts, ts, kind, key, text, call_id, 1 if mail else 0))
    log.warning("Auffaelligkeit (%s): %s", kind, mask_text(text)[:200])
    _notify()
    return aid, True


def _notify():
    for fn in ON_NEW:
        try:
            fn()
        except Exception:
            pass


def open_count():
    try:
        return db.q("SELECT COUNT(*) n FROM anomalies WHERE ack=0")[0]["n"]
    except Exception:
        return 0


def items(only_open=False, limit=300):
    rows = db.q("SELECT * FROM anomalies" + (" WHERE ack=0" if only_open else "") + " ORDER BY last_ts DESC LIMIT ?", (limit,))
    for r in rows:
        r["label"] = KIND_LABEL.get(r["kind"], r["kind"])
    return rows


def ack(aid=None, by=""):
    if aid is None:
        n = db.x_count("UPDATE anomalies SET ack=1, ack_by=?, ack_ts=? WHERE ack=0", (by, time.time()))
    else:
        n = db.x_count("UPDATE anomalies SET ack=1, ack_by=?, ack_ts=? WHERE id=? AND ack=0", (by, time.time(), aid))
    _notify()
    return n


def for_call(call_id):
    """Auffaelligkeiten eines Anrufs als Ablauf-Eintraege (nur fuer Admins anzeigen)."""
    try:
        rows = db.q("SELECT id, ts, kind, text, ack FROM anomalies WHERE call_id=? ORDER BY ts", (call_id,))
    except Exception:
        return []
    return [{"ts": r["ts"], "kind": "anomaly", "data": {"id": r["id"], "rule": r["kind"], "ack": r["ack"]},
             "text": f"⚠ Auffälligkeit: {r['text']}", "dur": None} for r in rows]


# --- Regeln aus dem Ablauf eines Anrufs ---
def _ring_target(t):
    """99610 -> **610 (Klingel-Rufnummer = internes Telefon), sonst unveraendert."""
    t = str(t or "")
    return "**" + t[2:] if re.fullmatch(r"99\d{1,4}", t) else t


def _day(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _hm(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def analyse_call(call, events, caller_segments, c=None):
    """Regeln oneway, deaf, ring_short fuer einen beendeten Anruf. events: [{ts, kind, data}]. Rueckgabe: [(kind, key, text)]."""
    c = c or cfg()
    out = []
    cid = call["id"]
    who = f"Anruf {'von' if call.get('direction', 'in') == 'in' else 'an'} {mask(call.get('caller') if call.get('direction', 'in') == 'in' else call.get('called'))}"
    when = f"{datetime.fromtimestamp(call.get('started') or 0).strftime('%d.%m. %H:%M')}"
    end = call.get("ended") or (events[-1]["ts"] if events else 0)
    first = lambda k: next((e for e in events if e["kind"] == k), None)
    ai, ai_end, ans = first("ai"), first("ai_end"), first("answered")
    qa = [e["data"] for e in events if e["kind"] == "qos" and e["data"].get("leg") == "a"]
    qa = qa[-1] if qa else None
    talk_start = min([e["ts"] for e in (ai, ans) if e] or [0])
    talk = (end - talk_start) if talk_start and end else 0
    ai_dur = ((ai_end["ts"] if ai_end else end) - ai["ts"]) if ai and end else 0
    if c.get("oneway") and qa and talk_start:
        why = []
        if "rtt" in qa and not qa.get("rtt") and talk > c["oneway_s"]:
            why.append(f"eingehende Leitung ohne RTCP-Bestätigung der Gegenseite (Laufzeit 0) bei {round(talk)} s Gespräch")
        rx, tx = qa.get("rxcount") or 0, qa.get("txcount")
        if ai and ai_dur > c["ai_s"] and rx > 0 and isinstance(tx, (int, float)) and tx < rx * c["tx_ratio"] / 100:
            why.append(f"gesendet {tx} von {rx} empfangenen Paketen ({round(100 * tx / rx)} %) im KI-Gespräch")
        if why:
            out.append(("oneway", f"oneway:{cid}", f"{who} ({when}): {'; '.join(why)} – möglicherweise Ton nur in eine Richtung."))
    if c.get("deaf") and ai and ai_dur > c["ai_s"] and qa and (qa.get("rxcount") or 0) >= c["rx_min"] and not caller_segments:
        reason = str((ai_end or {}).get("data", {}).get("reason") or "")
        if "Stille" in reason or "KI hat aufgelegt" in reason:
            out.append(("deaf", f"deaf:{cid}", f"{who} ({when}): KI-Gespräch {round(ai_dur)} s, {qa.get('rxcount')} Pakete empfangen, "
                        f"aber kein Wort des Anrufers erkannt ({reason.replace('Gespraech beendet: ', '')}) – hörte die KI den Anrufer?"))
    if c.get("ring_short"):
        from .flow import CAUSE
        for e in events:
            if e["kind"] != "leg_end":
                continue
            d = e["data"]
            after, cause = d.get("after"), d.get("cause")
            if not isinstance(after, (int, float)) or after >= c["short_s"] or cause in (17, 21, 26):
                continue
            # Anrufer legte auf / jemand nahm ab: das Klingeln endet mit dem Anruf (Grund 16) - kein Fehler
            near = [x for x in events if x["kind"] in ("answered", "unanswered", "hangup") and abs(x["ts"] - e["ts"]) <= 2]
            if cause in (16, None) and (near or (call.get("ended") and abs(call["ended"] - e["ts"]) <= 2)):
                continue
            t = _ring_target(d.get("target"))
            why = CAUSE.get(cause, f"Grund {cause}")
            out.append(("ring_short", f"ring_short:{t}:{_day(e['ts'])}",
                        f"{t or 'Telefon'}: Klingeln endete schon nach {after:g} s – {why}{', ' + d['tech'] if d.get('tech') else ''} "
                        f"({who}, {when})."))
    return out


def ring_outcomes(events, c=None):
    """Je Telefon dieses Anrufs: 'dead' (klingelte bis zum Ende, 180 Ringing, nicht abgenommen), 'ok' (abgenommen) oder fehlt (neutral)."""
    c = c or cfg()
    res, rung = {}, set()
    for e in events:
        if e["kind"] == "ring":
            rung |= {_ring_target(t) for t in str(e["data"].get("targets") or "").split("+") if t}
        if e["kind"] == "leg_end":
            d = e["data"]
            t = _ring_target(d.get("target"))
            if not t:
                continue
            after = d.get("after")
            if d.get("cause") in (0, 16, 19) and "180" in str(d.get("tech") or "") and isinstance(after, (int, float)) and after >= c["ring_min"]:
                res.setdefault(t, "dead")
            else:
                res[t] = res.get(t) if res.get(t) == "dead" else "neutral"
    if any(e["kind"] == "answered" for e in events):
        for t in rung - set(res):               # klingelte, kein Ende-Grund gemeldet, und jemand nahm ab: dieses Telefon
            if len(rung - set(res)) == 1:
                res[t] = "ok"
    return {t: v for t, v in res.items() if v in ("dead", "ok")}


def _streak():
    try:
        v = json.loads(db.get_meta("anom_ring_streak", "") or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def check_calls(now=None, c=None):
    """Beendete Anrufe seit dem letzten Stand pruefen (ab 60 s nach dem Ende, damit die Leitungswerte da sind)."""
    now, c = now or time.time(), c or cfg()
    wm = db.get_meta("anom_call_wm", "")
    if wm == "":                              # erster Lauf: alte Anrufe nicht nachtraeglich pruefen
        mx = db.q("SELECT MAX(id) m FROM calls")[0]["m"] or 0
        db.set_meta("anom_call_wm", mx)
        return 0
    wm, n = int(wm), 0
    streak = _streak()
    for call in db.q("SELECT id, uuid, started, ended, caller, called, direction, status FROM calls WHERE id>? ORDER BY id LIMIT 200", (wm,)):
        if not call["ended"] or call["ended"] > now - 60:
            if call["ended"] or (call["started"] or 0) > now - 3 * 3600:
                break                             # laeuft noch: spaeter (Reihenfolge bleibt erhalten)
        wm = call["id"]
        if call["direction"] == "rec" or not call["ended"]:
            continue
        try:
            evs = []
            for r in db.q("SELECT ts, kind, data FROM call_events WHERE call_id=? ORDER BY ts, id", (call["id"],)):
                try:
                    evs.append({"ts": r["ts"], "kind": r["kind"], "data": json.loads(r["data"] or "{}")})
                except ValueError:
                    pass
            if not evs:
                continue
            segs = db.q("SELECT COUNT(*) n FROM segments WHERE call_id=? AND speaker='caller'", (call["id"],))[0]["n"]
            for kind, key, text in analyse_call(call, evs, segs, c):
                record(kind, key, text, call_id=call["id"])
                n += 1
            if c.get("ring_dead"):
                for t, res in ring_outcomes(evs, c).items():
                    if res == "ok":
                        streak.pop(t, None)
                        continue
                    s = streak.get(t) or {"n": 0, "first": call["started"]}
                    s["n"] += 1
                    streak[t] = s
                    if s["n"] >= c["ring_streak"]:
                        record("ring_dead", f"ring_dead:{t}:{_day(s['first'] or now)}",
                               f"{t} klingelte bei {s['n']} Anrufen in Folge bis zum Ende („180 Ringing“), ohne dass abgenommen wurde "
                               f"(seit {datetime.fromtimestamp(s['first'] or now).strftime('%d.%m. %H:%M')}) – klingelt das Telefon wirklich? "
                               f"Lautstärke, Ruhezeit, Akku prüfen.", call_id=call["id"])
                        n += 1
        except Exception:
            log.exception("Auffaelligkeiten: Anruf %s nicht geprueft", call["id"])
    db.set_meta("anom_call_wm", wm)
    db.set_meta("anom_ring_streak", json.dumps(streak))
    return n


# --- Fritzbox: Ereignisprotokoll ---
LOG_LINE = re.compile(r"^\s*(\d\d\.\d\d\.\d\d) (\d\d:\d\d:\d\d) (.+?)\s*$")
P_VOIP = re.compile(r"Internettelefonie.*nicht erfolgreich", re.I)
P_INET = re.compile(r"Internetverbindung wurde getrennt|Zwangstrennung|DSL antwortet nicht|Internetverbindung.*unterbrochen", re.I)
P_REG = re.compile(r"(Anmeldung|Registrierung).*(nicht erfolgreich|fehlgeschlagen|gescheitert)|nicht registriert", re.I)


def parse_log(txt):
    """Ereignisprotokoll (neueste zuerst) -> [(ts, text)] in zeitlicher Reihenfolge."""
    out = []
    for line in str(txt or "").splitlines():
        m = LOG_LINE.match(line)
        if not m:
            continue
        try:
            ts = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%d.%m.%y %H:%M:%S").timestamp()
        except ValueError:
            continue
        out.append((ts, m.group(3)))
    out.sort(key=lambda x: x[0])
    return out


# normale Ausgaenge eines Anrufs, keine Stoerung: falsche Nummer, besetzt, Anrufer legte auf, abgelehnt, gibt es nicht, nicht annehmbar
VOIP_NORMAL = {"404", "410", "484", "486", "487", "600", "603", "604", "606"}
VOIP_AUTH = {"401", "403", "407"}                 # Anbieter weist die Anmeldung/den Anruf ab: Zugangsdaten oder Vertrag
P_ROUTE = re.compile(r"über\s+(\S+?)(?::\d+)?\s+war\b", re.I)


def _local_host(h):
    import ipaddress
    h = str(h or "").strip("[]").lower()
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return "." not in h or h.endswith((".box", ".local", ".lan", ".home", ".internal", ".localdomain"))


def voip_class(text):
    """Internettelefonie nicht erfolgreich -> (Art, Code): "normal" (Ausgang eines Anrufs), "local" (Telefon im Haus bzw. Asterisk:
    Telefon aus, "Nicht stoeren" - nichts fuer den Anbieter), "auth" (Anbieter weist ab: sofort melden), "provider" (Stoerung beim
    Anbieter: erst melden, wenn sie sich haeufen)."""
    tail = text.split("Ursache", 1)[-1]
    m = re.search(r"\b([4-6]\d\d)\b", tail) or re.search(r"\b([4-6]\d\d)\b", text)
    code = m.group(1) if m else "?"
    route = P_ROUTE.search(text)
    if code in VOIP_NORMAL:
        return "normal", code
    if route and _local_host(route.group(1)):
        return "local", code
    if code in VOIP_AUTH:
        return "auth", code
    return "provider", code


def classify_log(text):
    """Zeile -> (kind, key-Teil, mail) oder None. Internettelefonie: ("voip", Art, Code) - check_log entscheidet (voip_class)."""
    if P_VOIP.search(text):
        return ("voip",) + voip_class(text)
    if P_INET.search(text):
        return "inet", "inet", True
    if P_REG.search(text):
        ring = re.search(r"(?<!\d)99\d{3}(?!\d)", text)
        if ring:                                  # Klingel-Rufnummer an Asterisk: das meldet der Waechter
            return "ring_lines", f"reg:{ring.group(0)}", False
        num = re.search(r"\d[\d ]{4,}\d", text)
        return "fritz_log", f"reg:{mask(num.group(0)) if num else '?'}", True
    return None


def check_log(txt, now=None):
    """Neue Zeilen seit dem letzten Stand (meta anom_log_last: Zeit + Pruefsummen der Zeilen dieser Sekunde)."""
    lines = parse_log(txt)
    if not lines:
        return 0
    try:
        last = json.loads(db.get_meta("anom_log_last", "") or "null")
    except ValueError:
        last = None
    h = lambda ts, t: hashlib.sha1(f"{ts}|{t}".encode()).hexdigest()[:12]
    top = lines[-1][0]
    newest = {"ts": top, "seen": [h(ts, t) for ts, t in lines if ts == top]}
    if not isinstance(last, dict):                # erster Lauf: nur merken, alte Zeilen nicht melden
        db.set_meta("anom_log_last", json.dumps(newest))
        return 0
    n, c = 0, cfg()
    try:
        recent = [x for x in json.loads(db.get_meta("anom_voip_recent", "") or "[]") if isinstance(x, list) and len(x) == 3]
    except ValueError:
        recent = []
    for ts, t in lines:
        if ts < last["ts"] or (ts == last["ts"] and h(ts, t) in last.get("seen", [])):
            continue
        cl = classify_log(t)
        if not cl:
            continue
        stamp = datetime.fromtimestamp(ts).strftime("%d.%m. %H:%M")
        if cl[0] == "voip":
            _, art, code = cl
            if art in ("normal", "local"):
                continue
            if art == "auth":
                record("fritz_log", f"fritzlog:voip:{code}:{_day(ts)}", f"Fritzbox {stamp}: Anbieter weist ab ({code}) – {mask_text(t)}", ts=ts)
                n += 1
                continue
            route = P_ROUTE.search(t)
            recent = [x for x in recent if ts - x[0] <= c["voip_min"] * 60] + [[ts, code, route.group(1) if route else "?"]]
            if len(recent) >= c["voip_n"]:
                codes = ", ".join(sorted({x[1] for x in recent}))
                hosts = ", ".join(sorted({x[2] for x in recent}))
                record("fritz_log", f"fritzlog:voipburst:{_day(ts)}",
                       f"Fritzbox {stamp}: {len(recent)} nicht erfolgreiche Verbindungen über den Anbieter in {c['voip_min']} min "
                       f"(Ursache {codes}; über {hosts}) – Störung beim Anbieter? Letzte: {mask_text(t)}", ts=ts)
                recent = []
                n += 1
            continue
        kind, part, mail = cl
        if kind == "inet" and not c.get("inet"):
            continue
        what = {"inet": "Internetverbindung getrennt (Neueinwahl)"}.get(part, "")
        record(kind, f"fritzlog:{part}:{_day(ts)}", f"Fritzbox {stamp}: {what + ' – ' if what else ''}{mask_text(t)}", mail=mail, ts=ts)
        n += 1
    db.set_meta("anom_voip_recent", json.dumps(recent[-50:]))
    if top > last["ts"]:
        db.set_meta("anom_log_last", json.dumps(newest))
    elif top == last["ts"]:
        db.set_meta("anom_log_last", json.dumps({"ts": top, "seen": sorted(set(last.get("seen", [])) | set(newest["seen"]))}))
    return n


def read_log():
    from . import fritz
    return fritz._with_conn(lambda fc: fc.call_action("DeviceInfo1", "GetDeviceLog").get("NewDeviceLog", ""))


# --- Fritzbox: Status der eigenen Rufnummern ---
_voip_bad = {}             # Rufnummer -> Anzahl Abfragen in Folge nicht angemeldet (nur im Speicher)
OK_STATUS = ("registered", "connected")


def read_voip_status():
    """[(nummer, status)] aller eigenen Rufnummern ausser den Klingel-Rufnummern 99NNN."""
    from . import fritz

    def go(fc):
        accounts, _ = fritz._voip_accounts(fc)
        out = []
        for a in accounts:
            if not a["number"] or re.fullmatch(r"99\d{1,4}", a["number"]):
                continue
            try:
                r = fc.call_action("X_VoIP1", "X_AVM-DE_GetVoIPStatus", NewVoIPAccountIndex=a["index"])
            except Exception as e:
                if fritz._conn_error(e):
                    raise
                continue                           # Aktion fehlt (aeltere Fritzbox): diese Nummer nicht bewerten
            st = next((str(v) for k, v in r.items() if "status" in k.lower()), "")
            out.append((a["number"], st))
        return out
    return fritz._with_conn(go)


def check_voip(status, now=None):
    now = now or time.time()
    n = 0
    seen = set()
    for num, st in status:
        seen.add(num)
        if not st or st.strip().lower() in OK_STATUS:
            _voip_bad.pop(num, None)
            continue
        _voip_bad[num] = _voip_bad.get(num, 0) + 1
        if _voip_bad[num] == 2:
            record("voip_status", f"voip_status:{mask(num)}:{_day(now)}",
                   f"Rufnummer {mask(num)} ist in der Fritzbox nicht angemeldet (Status „{st}“) – Anrufe auf diese Nummer kommen evtl. nicht an.", ts=now)
            n += 1
    for k in set(_voip_bad) - seen:
        _voip_bad.pop(k, None)
    return n


# --- Fritzbox: Anrufliste (nicht zugestellte Anrufe) ---
def _g(c, *names):
    for n in names:
        v = getattr(c, n, None) if not isinstance(c, dict) else c.get(n)
        if v not in (None, ""):
            return v
    return ""


def read_calls():
    """Anrufliste der Fritzbox (letzter Tag) als [{type, own, caller, device, duration, ts}]."""
    from . import fritz
    from fritzconnection.lib.fritzcall import FritzCall
    raw = fritz._with_conn(lambda fc: FritzCall(fc).get_calls(days=1))
    out = []
    for c in raw:
        try:
            ts = datetime.strptime(str(_g(c, "Date", "date")), "%d.%m.%y %H:%M").timestamp()
        except ValueError:
            dt = _g(c, "date")
            ts = dt.timestamp() if hasattr(dt, "timestamp") else None
        if ts is None:
            continue
        out.append({"type": str(_g(c, "Type", "type")), "own": str(_g(c, "CalledNumber", "Called", "called")),
                    "caller": str(_g(c, "Caller", "CallerNumber", "caller")), "device": str(_g(c, "Device", "device")),
                    "duration": str(_g(c, "Duration", "duration")), "ts": ts})
    return out


def check_undelivered(calls, now=None, c=None):
    """Haeufungen: >= und_count Eintraege Typ 2/10 ohne Geraet (Dauer 0) auf derselben eigenen Nummer innerhalb und_min Minuten."""
    now, c = now or time.time(), c or cfg()
    win = c["und_min"] * 60
    by = {}
    for x in calls:
        if x["type"] not in ("2", "10"):
            continue
        if x["device"].strip().lower() not in ("", "none") or x["duration"].strip() not in ("", "0", "0:00", "00:00"):
            continue
        own = re.sub(r"\D", "", x["own"])
        if own:
            by.setdefault(own, []).append(x)
    n = 0
    for own, xs in by.items():
        xs.sort(key=lambda x: x["ts"])
        groups, cur = [], []
        for x in xs:                               # Ketten mit Luecken <= Fenster
            if cur and x["ts"] - cur[-1]["ts"] > win:
                groups.append(cur)
                cur = []
            cur.append(x)
        if cur:
            groups.append(cur)
        for g in groups:
            if g[-1]["ts"] < now - 3 * 3600:      # alte Haeufungen nicht (wieder) melden
                continue
            j, best = 0, 0
            for i in range(len(g)):               # dichtestes Fenster
                while g[i]["ts"] - g[j]["ts"] > win:
                    j += 1
                best = max(best, i - j + 1)
            if best < c["und_count"]:
                continue
            callers = {re.sub(r"\D", "", x["caller"]) or "?" for x in g}
            day = datetime.fromtimestamp(g[0]["ts"]).strftime("%d.%m.")
            text = (f"Rufnummer {mask(own)}: {len(g)} Anrufe am {day} zwischen {_hm(g[0]['ts'])} und {_hm(g[-1]['ts'])} von "
                    f"{len(callers)} {'Nummer' if len(callers) == 1 else 'Nummern'}, bei keinem hat ein Telefon geklingelt.")
            record("undelivered", f"undelivered:{own}:{int(g[0]['ts'])}", text, ts=g[-1]["ts"], bump=False)
            n += 1
    return n


# --- Klingel-Rufnummern / Asterisk (nur Oberflaeche, Mail macht der Waechter) ---
_line_bad = {}


async def check_ring_lines(now=None):
    from . import ami, astconf, outrec
    now = now or time.time()
    found = {}
    regs = await outrec.registered()
    if regs is None:
        found["asterisk"] = "Asterisk ist für die Bridge nicht erreichbar (AMI) – Klingeln und KI-Annahme gehen evtl. nicht."
    else:
        for n in astconf.ring_lines():
            if n not in regs:
                found[n] = (f"Klingel-Rufnummer {n} ist nicht bei Asterisk angemeldet – Telefone über {n} klingeln nicht. "
                            "Die Mail dazu verschickt der Wächter (ops/watchdog.py), falls eingerichtet.")
        try:
            st = await ami.registration_status("fritz-reg")
        except Exception:
            st = ""
        if st and st != "Registered":
            found["fritz-reg"] = f"Asterisk ist nicht bei der Fritzbox angemeldet (Status „{st}“) – die KI nimmt keine Anrufe an."
    n = 0
    for k in list(_line_bad):
        if k not in found:
            _line_bad.pop(k)
    for k, text in found.items():
        _line_bad[k] = _line_bad.get(k, 0) + 1
        if _line_bad[k] == 2:                      # zweimal in Folge (Neustart von Asterisk: Anmeldung dauert)
            record("ring_lines", f"ring_lines:{k}:{_day(now)}", text, mail=False, ts=now)
            n += 1
    return n


# --- Mail (gebuendelt) ---
def mail_recipients():
    return [r for r in db.q("SELECT * FROM mail_recipients WHERE enabled=1 AND on_anomaly=1") if r.get("email")]


def build_mail(rows):
    from . import notify
    base = notify.config()["base_url"]
    subj = f"LieschenMüller: {len(rows)} {'Auffälligkeit' if len(rows) == 1 else 'Auffälligkeiten'} bei Anrufen"
    lines, html_rows = [], ""
    for r in rows:
        stamp = datetime.fromtimestamp(r["ts"]).strftime("%d.%m. %H:%M")
        link = f"{base}/#/calls/{r['call_id']}" if base and r.get("call_id") else ""
        lines.append(f"- {stamp} [{KIND_LABEL.get(r['kind'], r['kind'])}] {r['text']}" + (f"\n  {link}" if link else ""))
        html_rows += (f"<tr><td style=\"padding:6px 8px;border-bottom:1px solid #e5e7eb;white-space:nowrap;color:#6b7280\">{notify._e(stamp)}</td>"
                      f"<td style=\"padding:6px 8px;border-bottom:1px solid #e5e7eb\"><b>{notify._e(KIND_LABEL.get(r['kind'], r['kind']))}</b><br>"
                      f"{notify._e(r['text'])}{f' <a href=\"{notify._e(link)}\">Anruf öffnen</a>' if link else ''}</td></tr>")
    more = f"\n\nAlle Auffälligkeiten: {base}/#/anomalies" if base else ""
    text = ("Die Selbstprüfung von Lieschen Müller hat Folgendes bemerkt:\n\n" + "\n".join(lines) + more +
            "\n\nDiese Mail kommt, weil bei diesem Empfänger „Störungs-/Auffälligkeitsmeldungen“ eingeschaltet ist (Benachrichtigungen).")
    foot = f" &middot; <a href=\"{notify._e(base)}/#/anomalies\" style=\"color:#6b7280\">Auffälligkeiten</a>" if base else ""
    page = notify._layout("Selbstprüfung", "Auffälligkeiten bei Anrufen", f"{len(rows)} neue Meldung(en)",
                          f"<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"font-size:14px\">{html_rows}</table>"
                          "<p style=\"color:#6b7280;font-size:12px\">Diese Mail kommt, weil bei diesem Empfänger „Störungs-/Auffälligkeitsmeldungen“ "
                          "eingeschaltet ist.</p>", "#d97706", foot)
    return subj, text, page


def mail_tick(now=None, c=None):
    """Sammelmail mit allen noch nicht gemailten Meldungen; hoechstens alle mail_min Minuten und mail_day am Tag (blockierend)."""
    from . import notify
    now, c = now or time.time(), c or cfg()
    pend = db.q("SELECT * FROM anomalies WHERE mail=1 AND mailed=0 ORDER BY ts")
    if not pend:
        return 0
    rcpts = mail_recipients()
    if not rcpts or not notify.configured():
        db.x("UPDATE anomalies SET mailed=-1 WHERE mail=1 AND mailed=0")      # nur Oberflaeche (spaeter keine alten Meldungen nachsenden)
        return 0
    stale = [r["id"] for r in pend if r["ts"] < now - 86400]
    if stale:
        db.x(f"UPDATE anomalies SET mailed=-1 WHERE id IN ({','.join('?' * len(stale))})", stale)
        pend = [r for r in pend if r["id"] not in stale]
        if not pend:
            return 0
    try:
        last = float(db.get_meta("anom_mail_last", "0") or 0)
    except ValueError:
        last = 0.0
    if now - last < c["mail_min"] * 60:
        return 0
    day, _, cnt = (db.get_meta("anom_mail_day", "") or "").partition(":")
    cnt = int(cnt) if day == _day(now) and cnt.isdigit() else 0
    if cnt >= c["mail_day"]:
        return 0
    db.set_meta("anom_mail_last", now)            # zuerst merken: bei Fehlern nicht im Minutentakt neu versuchen
    db.set_meta("anom_mail_day", f"{_day(now)}:{cnt + 1}")
    subj, text, page = build_mail(pend)
    ok = 0
    for r in rcpts:                                # einzeln: Empfaenger sehen sich nicht gegenseitig
        try:
            notify.send_mail(r["email"], subj, text, page)
            ok += 1
        except Exception as e:
            log.warning("Auffaelligkeiten: Mail nicht gesendet (%s)", type(e).__name__)
    if ok:
        ids = [r["id"] for r in pend]
        db.x(f"UPDATE anomalies SET mailed=1 WHERE id IN ({','.join('?' * len(ids))})", ids)
    return ok


# --- Hintergrund ---
def _fritz_ready():
    import os
    return bool(os.environ.get("FRITZ_TR064_USER") and os.environ.get("FRITZ_TR064_PASSWORD"))


async def fritz_tick(now=None, c=None):
    """Fritzbox-Regeln (jede einzeln abgesichert: ein Fehler beim Lesen stoert weder die anderen Regeln noch den Anrufbetrieb)."""
    c = c or cfg()
    if not _fritz_ready():
        return
    for on, read, check in (("fritz_log", read_log, check_log), ("voip_status", read_voip_status, check_voip),
                            ("undelivered", read_calls, check_undelivered)):
        if not c.get(on):
            continue
        try:
            data = await asyncio.wait_for(asyncio.to_thread(read), 60)
        except Exception as e:
            log.info("Auffaelligkeiten: %s nicht lesbar (%s)", on, type(e).__name__)
            continue
        try:
            check(data, now) if check is not check_undelivered else check(data, now, c)
        except Exception:
            log.exception("Auffaelligkeiten: Regel %s fehlgeschlagen", on)


async def loop():
    """Jede Minute Anrufe pruefen und ggf. Sammelmail senden; alle log_min Minuten Fritzbox und Klingel-Rufnummern."""
    last_fritz = 0.0
    last_purge = 0.0
    await asyncio.sleep(30)
    while True:
        try:
            c = cfg()
            now = time.time()
            await asyncio.to_thread(check_calls, now, c)
            if now - last_fritz >= c["log_min"] * 60:
                last_fritz = now
                await fritz_tick(now, c)
                if c.get("ring_lines"):
                    try:
                        await check_ring_lines(now)
                    except Exception:
                        log.exception("Auffaelligkeiten: Klingel-Rufnummern nicht geprueft")
            await asyncio.to_thread(mail_tick, now, c)
            if now - last_purge > 3600:
                last_purge = now
                db.x("DELETE FROM anomalies WHERE last_ts<?", (now - 90 * 86400,))
        except Exception:
            log.exception("Auffaelligkeiten: Pruefung fehlgeschlagen")
        await asyncio.sleep(60)
