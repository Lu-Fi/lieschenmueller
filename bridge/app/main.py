import asyncio
import glob
import base64
import hashlib
import hmac
import json
import logging
import re
import os
import secrets
import time
from uuid import uuid4

import uvicorn
from urllib.parse import urlparse
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse

from . import hold, labels as lbls, agents, ami, analyze, astconf, config, costguard, db, flow, gclient, logmask, fritzconf, gcal, linediag, mp3, outrec, presence, notify, numinfo, plugins, protocol, remote, dialrules as drules, fritz, fritzweb, models, schedule, rate, rectrans, users, voices
from . import audio as aiaudio
from . import call as call_mod
from .call import Call
from .events import hub

logging.basicConfig(level=logging.INFO)


logging.getLogger("uvicorn.access").addFilter(logmask.AccessFilter())     # ohne /internal/*, /api/calls ohne Suchbegriffe
log = logging.getLogger("bridge")

app = FastAPI()
# Zustand je Anruf: EIN CallRecord je Anruf-UUID in der Registry CALLS (callstate.py: Felder, Phasen, Ablaufzeiten, Aufraeumen).
from .callstate import CALLS, Phase
SPY = {}       # Nummer -> Kanalname fuer die Ansage im Gespraech (Dialplan [recspy] fragt sie ab); kein Anrufzustand (eigener Schluessel)


def active_calls():
    """Laufende KI-Sitzungen (call.Call), auch Rueckfrage-Sitzungen."""
    return [c for _, c in CALLS.having("active")]


def phones_busy():
    """Laufen gerade Telefonate (KI-Sitzung oder klingelnder Anruf)?"""
    return bool(CALLS.having("active") or CALLS.having("ringing"))


BG = set()   # Referenzen auf Hintergrund-Tasks (sonst kann der GC sie einsammeln)


def track(task):
    BG.add(task)
    task.add_done_callback(BG.discard)
    return task


FAILS = {}   # ip -> (Fehlversuche, erster Versuch)


SESSION_COOKIE = "lm_session"
def _internal_key():
    s = os.environ.get("AMI_SECRET", "")
    return hashlib.sha256(("internal:" + s).encode()).hexdigest()[:24] if s else ""


INTERNAL_KEY = _internal_key()


def internal_ok(request):
    """Aufrufe des Dialplans (/internal/*): von localhost UND mit dem abgeleiteten Schluessel (schuetzt auch hinter einem Reverse-Proxy auf demselben Rechner)."""
    if not request.client or request.client.host not in ("127.0.0.1", "::1"):
        return False
    return bool(INTERNAL_KEY) and hmac.compare_digest(request.query_params.get("k", ""), INTERNAL_KEY)


ALLOW_BASIC = os.environ.get("ALLOW_BASIC", "0") == "1"      # Reserve fuer Skripte (admin + UI_PASSWORD); seit 2026-10 nur mit ALLOW_BASIC=1
PSEUDO_ADMIN = {"id": 0, "username": "admin", "role": "admin", "staff_id": None, "disabled": 0}


def check_basic(user, pw):
    want = os.environ.get("UI_PASSWORD", "")
    # Leeres Passwort = gesperrt; bytes-Vergleich (auch bei Umlauten ohne Fehler)
    return bool(want) and user == "admin" and secrets.compare_digest(pw.encode(), want.encode())


def throttle(key, ok, limit=10):
    """Bremse gegen Passwort-Raten: `limit` Fehlversuche je 5 Minuten und Schluessel (IP oder Benutzername+IP)."""
    now = time.time()
    if len(FAILS) > 2000:                                  # abgelaufene Eintraege entfernen (Speicher begrenzen)
        for k in [k for k, (_, t) in FAILS.items() if now - t > 300]:
            FAILS.pop(k, None)
    n, t0 = FAILS.get(key, (0, now))
    if now - t0 > 300:
        n, t0 = 0, now
    if ok:
        return False
    FAILS[key] = (n + 1, t0)
    return n + 1 > limit


def blocked(key, limit=10):
    return FAILS.get(key, (0, 0))[0] > limit and time.time() - FAILS[key][1] <= 300


def login_keys(request, username):
    """Fehlversuche zaehlen je Benutzername+IP (ein Nutzer sperrt nicht alle hinter demselben Proxy) und grob je IP (gegen Durchprobieren vieler Namen)."""
    ip = request.client.host if request.client else "?"
    return f"{ip}|{(username or '').strip().lower()[:60]}", f"ip:{ip}"


def current_user(request, basic=True):
    """Angemeldeter Benutzer: Sitzungs-Cookie (Formular-Anmeldung) oder, nur fuer die API, Basic-Auth als Admin-Reserve."""
    ip = request.client.host if request.client else "?"
    tok = request.cookies.get(SESSION_COOKIE)
    u = users.session_user(tok) if tok else None
    if u:
        return u
    h = request.headers.get("authorization", "")
    if basic and ALLOW_BASIC and h.lower().startswith("basic "):
        try:
            usr, _, pw = base64.b64decode(h[6:]).decode().partition(":")
        except Exception:
            return None
        if check_basic(usr, pw):
            return PSEUDO_ADMIN
        throttle(ip, False)
    return None


def user_auth(request: Request):
    """Jeder angemeldete Benutzer. Kein WWW-Authenticate: der Browser soll kein Passwortfenster zeigen."""
    ip = request.client.host if request.client else "?"
    if blocked(ip):
        raise HTTPException(429, "Zu viele Fehlversuche, bitte spaeter erneut")
    u = current_user(request)
    if not u:
        raise HTTPException(401, "Nicht angemeldet")
    return u


def auth(request: Request):
    """Nur Admins."""
    u = user_auth(request)
    if u["role"] != "admin":
        raise HTTPException(403, "Nur für Administratoren")
    return u


@app.exception_handler(json.JSONDecodeError)
@app.exception_handler(UnicodeDecodeError)
async def bad_json(request: Request, exc: ValueError):
    """Ungueltiger JSON-Body (await request.json()): 400 statt 500."""
    return JSONResponse({"detail": "Ungültiger JSON-Inhalt"}, status_code=400)


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # Bewusst keine script-src/style-src/connect-src: die Ein-Datei-Oberflaeche nutzt Inline-Skript, Inline-Styles und
    # onclick-Handler (braeuchte 'unsafe-inline', das die Wirkung gegen XSS aufhebt) sowie WebSocket/SSE/Blob-Audio.
    # Diese Direktiven schraenken nichts davon ein, verhindern aber Einbetten (Clickjacking), Plugins/<object> und <base>-Tricks.
    "Content-Security-Policy": "frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'",
}


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    """Schreibende Aufrufe nur vom eigenen UI: Origin muss zum Host passen, Body muss JSON sein
    (blockt Formular-Posts fremder Webseiten, die gespeicherte Basic-Anmeldedaten mitschicken wuerden)."""
    if request.method in ("POST", "PUT", "DELETE", "PATCH") and request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host", ""):
            return PlainTextResponse("Cross-Origin nicht erlaubt", status_code=403)
        if (request.method in ("POST", "PUT") and int(request.headers.get("content-length") or 0) > 0
                and not request.headers.get("content-type", "").startswith("application/json")):
            return PlainTextResponse("Content-Type application/json erforderlich", status_code=415)
    return await call_next(request)


@app.middleware("http")          # zuletzt registriert = aeusserste Schicht: gilt auch fuer Antworten von csrf_guard
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        resp.headers.setdefault(k, v)
    return resp


def validate_agent(d):
    """Aktionen und Ziele beim Speichern pruefen (eindeutige Namen, erlaubte Ziele)."""
    from .call import _slug
    if (d.get("transfer_to") or "").strip():
        try:      # mehrere Ziele (Komma oder +) klingeln gleichzeitig
            d["transfer_to"] = "+".join(ami.dial_list(str(d["transfer_to"]).replace("+", ",")))
        except ValueError as e:
            raise HTTPException(400, f"Durchstellen an: {e}")
    elif d.get("mode") == "transfer":
        raise HTTPException(400, "Modus Durchstellen braucht eine Zielnummer")
    if d.get("schedule") not in (None, "", "open", "closed"):
        raise HTTPException(400, "Zeitfenster: erlaubt sind immer, nur in oder nur außerhalb der Geschäftszeiten")
    try:
        d["answer_delay"] = max(0, min(60, int(d.get("answer_delay") or 0)))
    except (TypeError, ValueError):
        raise HTTPException(400, "Annahme-Verzögerung: Zahl zwischen 0 und 60 Sekunden")
    # Kostenbremse/Hoechstdauer: nur pruefen, wenn mitgeschickt (Speichern schreibt nur mitgesendete Felder)
    for k, lo, hi, label in (("limit_calls_h", 0, 1000, "Anrufe je Nummer und Stunde"), ("limit_min_day", 0, 1440, "Minuten je Nummer und Tag"),
                             ("max_call_s", 0, 14400, "Höchstdauer (Sekunden)")):
        if k in d:
            try:
                v = int(d[k] or 0)
            except (TypeError, ValueError):
                raise HTTPException(400, f"{label}: ganze Zahl (0 = Standard)")
            if not lo <= v <= hi or (k == "max_call_s" and 0 < v < 60):
                raise HTTPException(400, f"{label}: 0 (Standard) bis {hi}" + (", mindestens 60" if k == "max_call_s" else ""))
            d[k] = v
    if "speech_pace" in d and d["speech_pace"] not in ("", "slow", "fast"):
        raise HTTPException(400, "Sprechtempo: normal, langsam oder zügig")
    if "speech_style" in d:
        d["speech_style"] = str(d["speech_style"] or "").strip()[:160]
    tools = d.get("tools") or []
    if isinstance(tools, str):
        try:
            tools = json.loads(tools or "[]")
        except ValueError:
            raise HTTPException(400, "Aktionen: ungueltiges JSON")
    seen = set()
    reserved = set(call_mod.CORE_TOOLS) | {n for p in plugins.PLUGINS.values() for n in p.tools}
    for t in tools:
        name = _slug(t.get("name"))
        if not re.match(r"^[A-Za-z_]", name) or name in reserved or name in seen:
            raise HTTPException(400, f"Aktion '{t.get('name')}': Name ungueltig, reserviert oder doppelt "
                                     "(muss mit Buchstabe beginnen)")
        seen.add(name)
        target = (t.get("target") or "").strip()
        if t.get("action") == "transfer":
            try:
                t["target"] = ami.dial_number(target)
            except ValueError as e:
                raise HTTPException(400, f"Aktion '{t.get('name')}': {e}")
        elif t.get("action") == "webhook" and not target.lower().startswith(("http://", "https://")):
            raise HTTPException(400, f"Aktion '{t.get('name')}': Webhook-URL muss mit http:// oder https:// beginnen")
    d["tools"] = json.dumps(tools, ensure_ascii=False)
    return d


def agent_rec(agent):
    """(aufnehmen, Audio behalten, Transkript) laut Agent: 'Aufzeichnen' (Audio speichern) und 'Transkript' sind getrennte Optionen."""
    rec, tr = bool(agent and agent.get("record")), bool(agent and agent.get("transcribe"))
    return rec or tr, (True if rec else False if tr else None), tr


def route_call(uuid, frm, dialed, number, label, record, fallback=False, quiet=None, ring=None, keep=None, transcribe=True, voicestop=True, rules=None):
    """Anruf ohne KI durchstellen (optional mit Mitschrift). Antwort fuer den Dialplan (protocol.py): Dial, FDial (mit KI-Rueckfall)
    oder bei Aufnahme RecX (q = ohne Hinweis-Ansage, f = KI-Rueckfall, wenn niemand abnimmt)."""
    number = "+".join(ami.dial_number(n) for n in number.split("+"))     # mehrere Ziele: n1+n2 (klingeln gleichzeitig)
    # Klingeln ueber Klingel-Rufnummern 99NNN: Verzoegerung vor dem Wahlen und Schreibweise der Anrufernummer (Fritzbox-Eigenheiten, siehe Einstellungen)
    delay = max(0, min(int(db.get_meta("ring_delay", "0") or 0), 10)) if fallback else 0
    rcid = db.get_meta("ring_cid", "") if fallback and db.get_meta("ring_cid", "") in ("plus", "zz") else ""
    music = hold.pick_class("ring") if fallback and db.get_meta("ring_music", "0") == "1" else ""      # Wartemusik statt Freizeichen, waehrend die Telefone klingeln
    tones = hold.ring_after() if music.startswith("r_") else 0       # Zahl der Freizeichen-Toene vor der Musik (fuer die Verzoegerung im Dialplan)
    prep = protocol.RingPrep(delay, rcid, tones)
    if quiet is None:
        quiet = db.get_meta("rec_notice", "1") == "0"          # Einstellung: Hinweis-Ansage bei Aufzeichnung
    if record and not quiet and not os.path.exists(os.path.join(rectrans.SOUNDS, "rec_notice.sln")):
        record = False                      # ohne Hinweis-Ansage wird nicht aufgenommen
        log.warning("Mitschrift angefordert, aber Hinweis-Ansage fehlt - Anruf wird ohne Aufnahme durchgestellt")
    status = "aktiv" if record else "durchgestellt"
    cid = db.x("INSERT INTO calls(uuid,started,caller,called,agent_name,status,ended,rec_keep,rec_transcribe,rec_voicestop) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (uuid, time.time(), frm, dialed, label + (" (mit Mitschrift)" if record and transcribe else " (aufgezeichnet)" if record else ""), status,
                None if record else time.time(), -1 if keep is None else int(keep), int(bool(transcribe)), int(bool(voicestop))))
    hub.publish({"type": "call", "call_id": cid, "caller": frm, "called": dialed, "agent": label, "status": status})
    flow.add(cid, "ring", targets=number, ring=ring if fallback else None, label=label, rules=[r for r in (rules or []) if r], record=bool(record))
    CALLS.put(uuid, "ringing", {"id": cid, "caller": frm, "called": dialed, "t": time.time(), "info": label})
    CALLS.phase(uuid, Phase.RINGING)
    if record:
        return protocol.RecX(number, quiet=bool(quiet), fallback=fallback, ring=ring if fallback else None, moh=music, prep=prep).encode()
    if fallback:
        return protocol.FDial(number, ring, music, prep).encode()
    return protocol.Dial(number).encode()


def _answer_now(first):
    """Name der Anrufregel, deren Agent sofort ohne Klingeln annimmt ("Anruf erst annehmen nach: 0"), sonst ''."""
    if first and first["mode"] == "assist" and int(first.get("answer_delay") or 0) == 0:
        return first.get("rule_name") or first["name"]
    return ""


def reach_preview(st, caller, called="", mask=False):
    """Vorschau "Ein Anruf von ... ginge jetzt an ..., weil ..." - dieselbe Entscheidung wie /internal/call (db.resolve + Anrufregel).
    mask: Nummern anderer Mitarbeiter (Vertretung) ausblenden - fuer Benutzer, die nur ihren eigenen Eintrag sehen."""
    caller = re.sub(r"[^0-9*+]", "", caller or "")[:30]
    called = db._digits(called)[:30] or next((m for m in (st.get("msn") or "").split(",") if m), "")
    c = db.find_contact(caller) if caller else None
    who = next((s for s in db.list_staff() if caller and any(db.same_number(caller, n) for n in re.split(r"[;,]\s*", s.get("number") or "") if n)), None)
    label = (f"{c['name']} ({caller})" if c else f"{who['name']} ({caller})" if who else caller) if caller else "unterdrückter Nummer"
    r = db.resolve(st, caller, called, staff=db.list_staff(),
                   answer_now=_answer_now(agents.pick_agent(caller, [called], db.business_open())) if called else "")
    text = db.route_text(r, st, label)
    if r["number"] and st.get("ai_fallback", 1):
        text += " Nimmt niemand ab, übernimmt die KI."
    number, steps = r["number"], list(r["steps"])
    if mask and r["via_deputy"]:
        # Benutzer (Meine Einstellungen): Nummern anderer Mitarbeiter (Vertretung, oft privat ausser Haus) nur als Name
        dep = r["target"]
        secret = {n.strip() for n in [number, *number.split("+"), dep.get("number"), dep.get("away_number"),
                                      *(x.get("target") for x in db.dialrules_of(dep))] if n and n.strip()}
        text = text.replace(f" ({number})", "")
        for n in sorted(secret, key=len, reverse=True):
            text = text.replace(n, "…")
            steps = [x.replace(n, "…") for x in steps]
        number = ""
    return {"text": text, "number": number, "target": r["target"]["name"], "via_deputy": r["via_deputy"], "ai": r["ai"],
            "rule": (r["rule"] or {}).get("name", ""), "reason": "; ".join(steps), "steps": steps, "called": called}


@app.get("/internal/call", response_class=PlainTextResponse)
async def internal_call(request: Request, uuid: str, to: str = "", hdr: str = ""):
    if not internal_ok(request):
        raise HTTPException(403)
    frm = request.query_params.get("from", "")
    CALLS.put(uuid, "chan", {"chan": request.query_params.get("chan", ""), "to": to, "t": time.time()})
    # Fritzbox: Request-URI ist immer "s"; die gewaehlte Nummer steht in P-Called-Party-ID.
    # "**9" ist bei internen Anrufen immer gesetzt (kein Ziel); dann gilt die Leitung (Request-URI).
    sip_users = [u for u in re.findall(r"sip:([^@>;\s]+)@", hdr) if u not in ("s", "**9")]
    dialed = sip_users[-1] if sip_users else to
    dialed = db.canonical_msn(dialed) or dialed
    called = [dialed, to]
    # 0) Ruft ein Mitarbeiter von einer seiner Fernabfrage-Nummern an, geht der Anruf direkt an die KI (persoenliche Anrede);
    #    die Fernabfrage selbst wird erst durch die Codewoerter freigeschaltet (Rufnummer UND Codewoerter noetig)
    rs = remote.staff_by_caller(frm)
    # 1) Rufnummer der Fritzbox Mitarbeitern zugewiesen? Dann je nach Status weiterleiten; bei einer geteilten
    #    Nummer klingeln alle erreichbaren Zugewiesenen gleichzeitig
    hits = [] if rs else db.staffs_for_number([dialed, hdr])
    for_staff = []
    now_ai = False
    if hits:
        staff_all = db.list_staff()
        targets, labels, record = [], [], False
        rules_hit = []
        fb_staff = []        # fuer den KI-Rueckfall: rung = bei dieser Person klingelte es selbst (nicht ueber die Vertretung)
        # Agent mit "Anruf erst annehmen nach: 0" = Anrufbeantworter ohne Klingeln: kein Durchstellen an die Mitarbeiter
        # (Waehlregeln des Mitarbeiters gelten weiter)
        first = agents.pick_agent(frm, called, db.business_open(), uuid=uuid)
        answer_now = _answer_now(first)
        for st in hits:
            # Erreichbarkeit an einer Stelle (db.resolve, Rangfolge db.RESOLVE_RULES): nicht stoeren > Waehlregel > Zeitplan > Status,
            # danach die Vertretung; "Anruf erst annehmen nach: 0" gilt nur, wenn keine Waehlregel passt
            r = db.resolve(st, frm, dialed, staff=staff_all, answer_now=answer_now)
            rule = r["rule"]
            if rule:
                rules_hit.append(f"{st['name']}: {rule['name']}")
                if rule["action"] == "ai":
                    now_ai = True      # Regel 'sofort zum Anrufbeantworter': keine Annahme-Verzoegerung
            if r["number"]:
                number, target, via_deputy = r["number"], r["target"], r["via_deputy"]
                try:
                    number = ami.dial_numbers(number)          # mehrere Nummern eines Benutzers klingeln gleichzeitig (n1+n2)
                except ValueError:
                    pass
                for n in number.split("+"):       # pro Einzelnummer (geteilte Nummer + Zentrale: **610 nicht doppelt klingeln)
                    if n and n not in targets:
                        targets.append(n)
                labels.append(target["name"] + (f" (Vertretung für {st['name']})" if via_deputy else "")
                              + (f" (Regel: {rule['name']})" if rule else ""))
                record = record or bool(st["record"])
                fb_staff.append(dict(st, rung=True) if not via_deputy else dict(st, status=r["status"], note=r["note"]))
            else:
                for_staff.append(dict(st, status=r["status"], note=r["note"]))
                fb_staff.append(for_staff[-1])
        if targets:
            # nimmt niemand ab, uebernimmt die KI - sofern bei einem der Zugewiesenen eingeschaltet (Standard)
            fb = any(st.get("ai_fallback", 1) for st in hits)
            ring = None
            if fb:       # Klingelzeit = "Anruf erst annehmen nach" des Agenten, der danach uebernimmt (0 = Standard)
                fa = agents.pick_agent(frm, [dialed, to], db.business_open(), modes=("assist",), uuid=uuid)
                d = int((fa or {}).get("answer_delay") or 0)
                ring = max(5, min(d, 60)) if d > 0 else None
            if fb:
                CALLS.put(uuid, "fallback", {"t": time.time(), "from": frm, "dialed": dialed, "to": to, "hits": hits, "staff": fb_staff})
            lead = first["id"] if first and first["mode"] == "assist" else 0
            rf = agents.rec_flags(frm, called, db.business_open(), lead)
            a_rec, a_keep, a_tr = agent_rec(rf)
            st_rec = record                                # Option "Mitschrift" beim Mitarbeiter: mit Transkript
            return route_call(uuid, frm, dialed, "+".join(targets),
                              "Durchstellen: " + ", ".join(dict.fromkeys(labels)), st_rec or a_rec, fallback=fb, ring=ring,
                              keep=a_keep if a_rec else None, transcribe=st_rec or a_tr,
                              voicestop=bool(rf["stop_cmd"]), quiet=(not rf["notice"]) if (rf["rules"] and a_rec and not st_rec) else None,
                              rules=[r.split(": ", 1)[-1] for r in rules_hit] or ([(fa or {}).get("rule_name")] if fb else []))      # fuer den Ablauf
        # niemand erreichbar (keine Vertretung): die KI nimmt den Anruf an - sofort, ohne Klingeln aller Telefone (ring_all),
        # sonst klingelte auch das Telefon der Person auf "nicht stoeren" bzw. ausserhalb ihres Zeitplans.
        # Ein Durchstell-Agent (Anrufregel, festes Ziel) wirkt weiter (unten).
        now_ai = True
    agent = agents.pick_agent(frm, called, db.business_open(), uuid=uuid)
    cid = db.x("INSERT INTO calls(uuid,started,caller,called,agent_name,status) VALUES(?,?,?,?,?,?)",
               (uuid, time.time(), frm, dialed, agent["name"] if agent else None,
                "aktiv" if agent else "abgelehnt"))
    if not agent:
        flow.add(cid, "no_agent")
        return protocol.NO_AGENT
    if agent["mode"] == "transfer" and agent["transfer_to"].strip():
        db.x("DELETE FROM calls WHERE id=?", (cid,))       # Zeile legt route_call an
        try:
            a_rec, a_keep, a_tr = agent_rec(agent)
            # Option "KI-Rueckfall" (nur interne Ziele **NNN): wie das Klingeln bei Mitarbeitern (fdial/recx) ueber angemeldete
            # Klingel-Rufnummern 99NNN, damit die Telefone die Nummer des Anrufers zeigen; Asterisk nimmt dafuer an, und nimmt niemand ab,
            # uebernimmt nach der Klingelzeit der Regel (Standard 25 s) die KI. Sonst wie bisher: direkt waehlen (dial:, 60 s).
            fb = bool(agent.get("ring_fallback")) and all(t.strip().startswith("**") for t in agent["transfer_to"].split("+"))
            ring = max(5, min(int(agent.get("answer_delay") or 0), 60)) if fb and int(agent.get("answer_delay") or 0) > 0 else None
            if fb:
                CALLS.put(uuid, "fallback", {"t": time.time(), "from": frm, "dialed": dialed, "to": to, "hits": []})
            return route_call(uuid, frm, dialed, agent["transfer_to"], agent["name"], a_rec, fallback=fb, ring=ring,
                              quiet=not agent.get("notice_on", 1) or (bool(agent.get("rec_rules")) and not agent.get("rec_notice_h", 1)), keep=a_keep, transcribe=a_tr, voicestop=bool(agent.get("stop_cmd", 1)),
                              rules=[agent.get("rule_name")])
        except ValueError:
            return protocol.NO_AGENT
    delay = 0 if (now_ai or rs) else int(agent.get("answer_delay") or 0)
    ring_to = astconf.ring_all()
    if agent["mode"] == "assist" and delay > 0 and ring_to and not rs:
        # Asterisk nimmt selbst an und laesst alle Telefone klingeln (Rufnummer ring_to, bei der Fritzbox allen Telefonen zugewiesen;
        # nur fuer Rufnummern ohne zugewiesene Mitarbeiter: sind welche zugewiesen, aber keiner erreichbar, nimmt die KI sofort an);
        # nimmt jemand ab, wird (je nach Agent) aufgezeichnet und transkribiert, sonst uebernimmt nach 'delay' Sekunden die KI
        db.x("DELETE FROM calls WHERE id=?", (cid,))       # Zeile legt route_call an
        CALLS.put(uuid, "fallback", {"t": time.time(), "from": frm, "dialed": dialed, "to": to, "hits": hits})
        try:
            a_rec, a_keep, a_tr = agent_rec(agent)
            return route_call(uuid, frm, dialed, ring_to, f"Klingeln: {agent['name']}", a_rec,
                              fallback=True, ring=max(5, min(delay, 60)), keep=a_keep, transcribe=a_tr, voicestop=bool(agent.get("stop_cmd", 1)),
                              quiet=(not agent.get("rec_notice_h", 1)) if (agent.get("rec_rules") and a_rec) else None,
                              rules=[agent.get("rule_name")])
        except ValueError:
            CALLS.take(uuid, "fallback")
            cid = db.x("INSERT INTO calls(uuid,started,caller,called,agent_name,status) VALUES(?,?,?,?,?,?)",
                       (uuid, time.time(), frm, dialed, agent["name"], "aktiv"))
    # Kostenbremse (Klingeln ueber ring_to prueft erst beim Rueckfall, start_fallback): wie "kein Agent" - der Dialplan nimmt
    # nicht an, die Fritzbox laesst andere Telefone weiter klingeln
    brake = cost_brake(agent, frm) if not rs else None        # Fernabfrage (eigene Nummer des Benutzers) nie bremsen
    if brake:
        db.x("UPDATE calls SET status='abgelehnt', agent_name=? WHERE id=?", (f"Kostenbremse ({agent['name']})", cid))
        db.add_segment(cid, "system", f"Kostenbremse: nicht an die KI ({brake})")
        flow.add(cid, "cost_brake", reason=f"nicht an die KI ({brake})", agent=agent["name"])
        log.info("Kostenbremse: Anruf %s nicht an die KI (%s)", cid, brake)
        return protocol.NO_AGENT
    CALLS.put(uuid, "pending", {"created": time.time(), "uuid": uuid, "from": frm, "to": to, "hdr": hdr, "call_id": cid,
                                "agent": agent, "for_staff": for_staff, "rules_hit": rules_hit if hits else [], "remote_cand": rs})
    hub.publish({"type": "call", "call_id": cid, "caller": frm, "called": dialed,
                 "agent": agent["name"], "status": "aktiv"})
    if delay > 0:
        flow.add(cid, "wait", seconds=delay, agent=agent["name"], rule=agent.get("rule_name"))
        CALLS.put(uuid, "ringing", {"id": cid, "caller": frm, "called": dialed, "t": time.time(), "info": f"klingelt, Agent {agent['name']} nimmt nach {delay} s ab"})
    CALLS.phase(uuid, Phase.RINGING if delay > 0 else Phase.AI)
    # Anrufbeantworter-Verhalten: erst klingeln lassen; nimmt in der Zwischenzeit jemand anders ab, entfaellt der Anruf hier
    if delay <= 0:
        return protocol.OK
    # Mit eingeschalteter Wartemusik nimmt Asterisk selbst an und spielt die Musik (kein Telefon klingelt); sonst klingelt die Fritzbox selbst
    music = hold.pick_class("ring") if db.get_meta("ring_music", "0") == "1" else ""
    tones = hold.ring_after() if music.startswith("r_") else 0
    prep = protocol.RingPrep(max(0, min(int(db.get_meta("ring_delay", "0") or 0), 10)) if music else 0, "", tones)
    return protocol.Wait(delay, music, prep).encode()


@app.get("/internal/fallback", response_class=PlainTextResponse)
async def internal_fallback(request: Request, uuid: str, status: str = ""):
    """Das Ziel hat nicht abgenommen (oder war besetzt/nicht erreichbar): die KI uebernimmt den Anruf. status = DIALSTATUS."""
    if not internal_ok(request):
        raise HTTPException(403)
    flow.add_for(uuid, "no_answer", **({"status": status[:20]} if re.fullmatch(r"[A-Z]{2,20}", status or "") else {}))
    return protocol.OK if start_fallback(uuid) else protocol.NO_AGENT


def cost_brake(agent, caller):
    """Kostenbremse (costguard): Grund, warum die KI diesen Anruf nicht annehmen soll, sonst None. Fehler beim Zaehlen bremsen nie."""
    try:
        return costguard.check(agent, caller, [c.meta["call_id"] for c in active_calls()])
    except Exception:
        log.exception("Kostenbremse: Zaehlen fehlgeschlagen - Anruf geht normal an die KI")
        return None


def start_fallback(uuid, note="Nicht abgenommen: der Anrufbeantworter (KI) übernimmt", staff_note="hat den Anruf nicht angenommen", brake=True):
    ctx = CALLS.take(uuid, "fallback")
    row = db.q("SELECT id FROM calls WHERE uuid=?", (uuid,))
    if not ctx or not row:
        return False
    agent = agents.pick_agent(ctx["from"], [ctx["dialed"], ctx["to"]], db.business_open(), modes=("assist",), uuid=uuid)
    if not agent:
        return False
    cid = row[0]["id"]
    brake = cost_brake(agent, ctx["from"]) if brake else None
    if brake:                # niemand hat abgenommen, die KI uebernimmt aber nicht (wie ohne Agent: Auflegen)
        db.x("UPDATE calls SET status='abgelehnt', ended=? WHERE id=?", (time.time(), cid))
        db.add_segment(cid, "system", f"Kostenbremse: die KI übernimmt nicht ({brake})")
        flow.add(cid, "cost_brake", reason=f"die KI übernimmt nicht ({brake})", agent=agent["name"])
        log.info("Kostenbremse: Anruf %s nicht an die KI (%s)", cid, brake)
        return False
    # staff_note nur fuer die Zugewiesenen, bei denen es selbst klingelte; die uebrigen (nicht stoeren, Vertretung, ...) mit ihrem
    # echten Status. Gerufene bleiben fuer connect_employee frei (kein screen_tried): auf Wunsch darf die KI es nochmal versuchen.
    fs = ctx.get("staff")
    for_staff = [dict(x, status="off", note=staff_note) if x.get("rung") else x for x in fs] if fs is not None else \
        [dict(st, status="off", note=staff_note) for st in ctx["hits"]]
    db.x("UPDATE calls SET agent_name=?, agent_chain='', status='aktiv', ended=NULL WHERE id=?", (agent["name"], cid))
    db.add_segment(cid, "system", note)
    CALLS.put(uuid, "pending", {"created": time.time(), "uuid": uuid, "from": ctx["from"], "to": ctx["to"], "hdr": "", "call_id": cid,
                                "agent": agent, "for_staff": for_staff, "rules_hit": []})
    CALLS.phase(uuid, Phase.AI)
    hub.publish({"type": "call", "call_id": cid, "caller": ctx["from"], "called": ctx["dialed"], "agent": agent["name"], "status": "aktiv"})
    return True


@app.get("/internal/busy", response_class=PlainTextResponse)
async def internal_busy(request: Request, uuid: str, status: str = ""):
    """Durchstellen fehlgeschlagen (besetzt/keine Antwort): die KI nimmt den Anrufer wieder entgegen."""
    if not internal_ok(request):
        raise HTTPException(403)
    ctx = CALLS.take(uuid, "retarget")
    row = db.q("SELECT * FROM calls WHERE uuid=?", (uuid,))
    if not ctx or not row:
        return protocol.NO_AGENT
    row = row[0]
    out = ctx.get("out")
    if out:
        agent = out["agent"]
    else:
        agent = next((a for a in db.list_agents(enabled_only=True) if a["name"] == row["agent_name"] and a["mode"] == "assist"), None) \
            or agents.pick_agent(row["caller"], [row["called"]], db.business_open(), modes=("assist",), uuid=uuid)
        if not agent:
            return protocol.NO_AGENT
        agent = agents.with_flags(agent, row["caller"], [row["called"]], db.business_open())
    tgt = db._digits(ctx["target"].split("+")[0])
    staff = db.list_staff()
    st = next((s for s in staff if ctx.get("staff_id") and s["id"] == ctx["staff_id"]), None)
    if not st:
        same = [s for s in staff if tgt and db._digits(s.get("number") or "") == tgt]
        st = same[0] if len(same) == 1 else None     # mehrere mit derselben Nummer (z. B. Zentrale): keinen Namen raten
    sc = CALLS.take(uuid, "screen")
    if sc:      # Rueckfrage vor dem Verbinden: Ablehnung, Mailbox und keine Antwort klingen fuer den Anrufer gleich ("nicht erreichbar")
        res = (sc.get("screen") or {}).get("result") or sc.get("final")
        if res:
            status = {"decline": "abgelehnt", "voicemail": "Mailbox", "pass": "weitergegeben", "none": "NOANSWER", "timeout": "NOANSWER"}.get(res, status)
    why = {"BUSY": "ist gerade besetzt", "NOANSWER": "nimmt gerade nicht ab", "weitergegeben": "gibt den Anruf weiter"}.get(status, "ist gerade nicht erreichbar")
    passed = (sc.get("screen") or {}) if sc and status == "weitergegeben" else {}
    # Namen nur, wenn der Anrufer nach dieser Person gefragt hat (nicht nach manuellem Umleiten aus der Oberflaeche)
    who = dict(st, status="off", note=why) if st and ctx.get("asked") else \
        {"name": "der gewuenschte Gespraechspartner", "role": "", "status": "off", "note": why}
    cid = row["id"]
    db.x("UPDATE calls SET status='aktiv', ended=NULL WHERE id=?", (cid,))
    db.add_segment(cid, "system", f"Durchstellen fehlgeschlagen ({status or 'keine Antwort'}): die KI nimmt den Anrufer wieder entgegen")
    flow.add(cid, "busy", status=status, target=ctx["target"])
    tried = CALLS.field(uuid, "screen_tried") or set()
    if sc and tgt and status != "weitergegeben":      # weitergegeben: das Ziel (z. B. Zentrale) darf dieselbe Nummer haben
        tried.add(tgt)
    CALLS.put(uuid, "screen_tried", tried)
    CALLS.put(uuid, "pending", {"created": time.time(), "uuid": uuid, "from": row["caller"], "to": row["called"], "hdr": "", "call_id": cid,
                                "agent": agent, "for_staff": [who], "rules_hit": [], "retry": True, "screen_tried": sorted(tried),
                                "pass_to": passed.get("pass_to", ""), "pass_reason": passed.get("reason", ""), **(out or {})})
    CALLS.phase(uuid, Phase.AI)
    hub.publish({"type": "call", "call_id": cid, "caller": row["caller"], "called": row["called"], "agent": agent["name"], "status": "aktiv"})
    return protocol.OK


@app.get("/internal/screen-start", response_class=PlainTextResponse)
async def screen_start(request: Request, uuid: str, suuid: str = "", chan: str = ""):
    """Die angerufene Person hat abgenommen (Dial-Option U): zweite KI-Sitzung, die fragt, ob sie den wartenden Anrufer sprechen moechte."""
    if not internal_ok(request):
        raise HTTPException(403)
    ctx = CALLS.field(uuid, "screen")
    if not ctx or not re.fullmatch(r"[0-9a-fA-F-]{36}", suuid or ""):
        return protocol.SKIP                            # keine Rueckfrage vorgesehen: ohne Frage verbinden
    sc = {"who": ctx.get("who", ""), "reason": ctx.get("reason", ""), "caller_label": ctx.get("caller_label", ""), "result": None,
          "rec_screen": bool(ctx.get("rec_screen"))}
    ctx["screen"] = sc
    ag = dict(ctx["agent"], name="Rückfrage", mode="assist", notice_on=0, use_base=0, silence_ask=8, silence_hangup=6, greeting="",
              prompt="", record=0, transcribe=0, record_ai=0, rec_notice=1, rec_notice_h=1, stop_cmd=1, mood_on=0, mood_text=0, meta_on=0,
              tools="[]", ask_before=0, allow_connect=0, keywords="", mood_trigger="", dial_callers="")
    number = ctx.get("number", "")
    cid = ctx["call_id"]            # die Rueckfrage ist Teil des Anrufs des Anrufers (kein eigener Eintrag in der Anrufliste)
    CALLS.ensure(suuid, kind="screen")
    CALLS.put(suuid, "pending", {"created": time.time(), "uuid": suuid, "from": number, "to": number, "hdr": "", "call_id": cid, "agent": ag,
                                 "direction": "out", "task": sc["reason"], "contact": sc["who"], "owner": "", "screen": sc,
                                 "max_s": max(20, min(int(db.get_meta("screen_max", "45") or 45), 120)), "no_vm_hint": True})
    CALLS.phase(suuid, Phase.AI)
    db.add_segment(ctx["call_id"], "system", f"Rückfrage: {sc['who'] or number} hat abgenommen, die KI fragt, ob der Anruf angenommen wird")
    return protocol.OK


@app.get("/internal/screen-result", response_class=PlainTextResponse)
async def screen_result(request: Request, uuid: str):
    """Ergebnis der Rueckfrage fuer den Dialplan: accept (verbinden) oder etwas anderes (zurueck zur KI)."""
    if not internal_ok(request):
        raise HTTPException(403)
    ctx = CALLS.field(uuid, "screen")
    if not ctx:
        return protocol.screen_result(None)
    res = (ctx.get("screen") or {}).get("result") or "none"
    ctx["final"] = res
    if res == "none":               # die anderen Ergebnisse stehen schon als Ereignis der Rueckfrage-Sitzung im Transkript
        db.add_segment(ctx["call_id"], "system", f"Rückfrage: {ctx.get('who') or 'die Person'} hat nicht geantwortet")
    flow.add(ctx["call_id"], "screen_result", who=ctx.get("who") or "", result=res)
    if res == "accept":
        CALLS.phase(uuid, Phase.CONNECTED)
    return protocol.screen_result(res)


@app.get("/internal/after", response_class=PlainTextResponse)
async def internal_after(request: Request, uuid: str):
    if not internal_ok(request):
        raise HTTPException(403)
    for _ in range(20):   # Anruf noch im Abbau? kurz auf das Ziel warten
        if CALLS.field(uuid, "after") or not CALLS.field(uuid, "active"):
            break
        await asyncio.sleep(0.1)
    return CALLS.take(uuid, "after", (protocol.HANGUP, 0))[0]


async def post_call(cid):
    """Nach dem Anruf: ggf. Zusatzinfos auswerten, dann per E-Mail benachrichtigen (mit Zusammenfassung)."""
    if analyze.wanted(cid):
        await analyze.run_safe(cid)
    if analyze.wanted_detail(cid):
        await analyze.run_detail_safe(cid)
    await notify.call_finished(cid)


async def transcribe_then_notify(cid, uuid, direction, notify_end=None):
    """Mitschrift erst fertig transkribieren, dann benachrichtigen (die Mail enthaelt sonst weder Transkript noch Zusammenfassung).
    notify_end: z. B. post_call, wenn vorher die KI gesprochen hat (Auswertung und Mail fuer das ganze Gespraech)."""
    ok = False
    try:
        await rectrans.transcribe(cid, uuid)
        ok = True
    finally:
        if direction == "in" or (direction == "rec" and ok):     # ohne Transkript keine leere Mail (der Nutzer kann neu transkribieren)
            await (notify_end or notify.call_finished)(cid)


async def late_notify(cid):
    """Anrufe ohne KI (durchgestellt/unbeantwortet): kurz warten, ob die KI-Auswertung den Anruf schon uebernommen hat."""
    await asyncio.sleep(4)
    await notify.call_finished(cid)


async def audiosocket(reader, writer):
    try:
        hdr = await reader.readexactly(3)
        uid = (await reader.readexactly(int.from_bytes(hdr[1:], "big"))).hex()
        uid = f"{uid[:8]}-{uid[8:12]}-{uid[12:16]}-{uid[16:20]}-{uid[20:]}"
    except Exception:
        writer.close()
        return
    meta = CALLS.take(uid, "pending")
    if not meta:
        log.warning("AudioSocket ohne Anmeldung: %s", uid)
        writer.close()
        return
    CALLS.take(uid, "ringing")
    CALLS.take(uid, "after")      # neue Sitzung (z. B. KI nach abgelehnter Rueckfrage): ein Rest vom vorigen Durchstellen darf nicht noch einmal greifen
    hub.publish({"type": "ring"})
    call = Call(meta, writer)
    def _after(v):
        CALLS.put(uid, "after", (v, time.time()))
        rt = {"t": time.time(), "target": protocol.targets_of(v), "asked": bool(call.meta.get("transfer_asked")),
              "staff_id": call.meta.get("transfer_staff")}      # fuer /internal/busy
        if meta.get("direction") == "out" and not meta.get("screen"):
            # ausgehender KI-Anruf: nach einem fehlgeschlagenen Durchstellen macht dieselbe KI mit demselben Auftrag weiter
            rt["out"] = {k: meta[k] for k in ("from", "to", "agent", "direction", "task", "contact", "owner") if k in meta}
        CALLS.put(uid, "retarget", rt)
        if not meta.get("screen"):
            flow.add(meta["call_id"], "transfer", targets=protocol.targets_of(v), record=v.startswith("rec"), screen=protocol.is_screen(v),
                     who=(call.meta.get("screen_ctx") or {}).get("who", "") if protocol.is_screen(v) else "")
        if protocol.is_screen(v) and call.meta.get("screen_ctx"):
            CALLS.put(uid, "screen", dict(call.meta["screen_ctx"], t=time.time()))
    call.on_after = _after
    CALLS.ensure(uid, kind="screen" if meta.get("screen") else "call")
    CALLS.put(uid, "active", call)
    CALLS.phase(uid, Phase.AI)
    if not meta.get("screen"):
        flow.add(meta["call_id"], "ai", agent=meta["agent"].get("name"), rule=meta["agent"].get("rule_name"), retry=bool(meta.get("retry")),
                 remote=bool(meta.get("remote_cand")))
    if meta.get("direction") == "out" and not meta.get("screen"):
        db.x("UPDATE calls SET status='aktiv' WHERE id=?", (meta["call_id"],))
        hub.publish({"type": "call", "call_id": meta["call_id"], "status": "aktiv"})
    try:
        await call.run(reader)
    finally:
        rec = CALLS.get(uid)
        hung_up = lambda: bool(rec and rec.phase == Phase.ENDED)       # Asterisk hat schon aufgelegt (rec-done): kein Nachlauf mehr
        if CALLS.field(uid, "active") not in (None, call):
            # Inzwischen laeuft eine neue KI-Sitzung desselben Anrufs (z. B. Ziel sofort besetzt -> /internal/busy): deren Zustand,
            # Status und Mail gehoeren ihr; hier nur das Ende dieser Sitzung vermerken
            if not meta.get("screen"):
                flow.add(meta["call_id"], "ai_end", reason=call.end_reason or "")
                await plugins.emit("call_end", call, meta)
            return
        CALLS.take(uid, "active")
        if not meta.get("screen"):       # Rueckfrage-Sitzung: der Anruf des Anrufers laeuft weiter, nichts an dessen Zeile aendern
            if call.after and not hung_up():
                CALLS.put(uid, "after", (call.after, time.time()))
                CALLS.phase(uid, Phase.SCREENING if protocol.is_screen(call.after) else Phase.RINGING)
            db.x("UPDATE calls SET ended=?, status=CASE WHEN status='durchgestellt' THEN status ELSE 'beendet' END WHERE id=?",
                 (time.time(), meta["call_id"]))
            hub.publish({"type": "end", "call_id": meta["call_id"]})
            flow.add(meta["call_id"], "ai_end", reason=call.end_reason or "")
            await plugins.emit("call_end", call, meta)
            if call.after and not hung_up():
                # Rueckfrage oder Durchstellen laeuft: Auswertung/Mail erst, wenn das ganze Gespraech zu Ende ist (sonst fehlen eine spaetere
                # Nachricht nach "besetzt" oder die Mitschrift des verbundenen Gespraechs)
                CALLS.put(uid, "defer_post", True)
            else:
                CALLS.take(uid, "defer_post")
                track(asyncio.create_task(post_call(meta["call_id"])))

# --- API ---
@app.get("/api/calls")
def calls(page: int = 1, size: int = 25, q: str = "", status: str = "", direction: str = "",
          agent: str = "", date_from: str = "", date_to: str = "", archived: int = 0, user: dict = Depends(user_auth)):
    """Anrufliste mit Paging und Filtern (alles in der Datenbank). Normale Benutzer sehen nur eigene und freigegebene Anrufe."""
    admin = user["role"] == "admin"
    size = min(max(size, 5), 200)
    where, args = [], []
    if q.strip():
        like = f"%{q.strip()}%"
        base = "caller LIKE ? OR called LIKE ? OR agent_name LIKE ?"
        content = ("task LIKE ? OR notes LIKE ? OR memo LIKE ? OR EXISTS (SELECT 1 FROM segments s WHERE s.call_id=calls.id AND s.text LIKE ?)")
        where.append(f"({base} OR {content})")
        args += [like] * 7
    asql, aargs = ("1=1", []) if admin else users.sql_scope(user)
    where.append(asql)
    args += aargs
    if status:
        where.append("status=?")
        args.append(status)
    if direction in ("in", "out"):
        where.append("direction=?")
        args.append(direction)
    if archived:
        where.append("archived=1")
    if agent:
        where.append("(agent_name=? OR agent_chain LIKE ?)")
        args += [agent, f"%{agent}%"]
    day = "date(started,'unixepoch','localtime')"
    if date_from:
        where.append(f"{day} >= ?")
        args.append(date_from)
    if date_to:
        where.append(f"{day} <= ?")
        args.append(date_to)
    w = " WHERE " + " AND ".join(where)
    total = db.q(f"SELECT COUNT(*) AS n FROM calls{w}", args)[0]["n"]
    pages = max(1, -(-total // size))
    page = min(max(page, 1), pages)
    items = db.q(f"SELECT * FROM calls{w} ORDER BY id DESC LIMIT ? OFFSET ?", args + [size, (page - 1) * size])
    owners = db.number_owners()
    nth = db.day_positions([it["id"] for it in items], asql, aargs)        # der wievielte Anruf der Nummer an diesem Tag
    out = []
    names = {}
    for it in items:
        c = it if admin else users.mask(it)
        c["access"] = "full"
        c["day_n"], c["day_total"] = nth.get(it["id"], (0, 0))
        c["num_key"] = db.number_key(it.get("caller")) if it.get("direction") == "in" else ""      # fuer die Gruppierung in der Liste
        if it.get("caller") not in names:
            ct = db.find_contact(it.get("caller"))
            names[it.get("caller")] = (ct["name"] if ct else "", numinfo.label(it.get("caller")))
        c["caller_name"], c["caller_info"] = names[it.get("caller")]
        who = owners.get(db._digits(it.get("called"))) or []
        c["owner_name"] = ", ".join(x["name"] for x in who)       # Mitarbeiter, denen die angerufene Nummer zugewiesen ist
        c["owner_shared"] = len(who) > 1
        out.append(c)
    return {"items": out, "total": total, "page": page, "pages": pages, "size": size,
            "agents": [r["agent_name"] for r in db.q(
                f"SELECT DISTINCT agent_name FROM calls WHERE agent_name IS NOT NULL AND {asql} ORDER BY agent_name", aargs)]}


@app.get("/api/calls/{cid}")
def call_detail(cid: int, user: dict = Depends(user_auth)):
    call = (db.q("SELECT * FROM calls WHERE id=?", (cid,)) or [None])[0]
    if call is None or users.level_for(user, call) is None:
        raise HTTPException(404, "Anruf nicht gefunden")      # auch ohne Recht: nicht verraten, ob es ihn gibt
    admin = user["role"] == "admin"
    who = db.number_owners().get(db._digits(call.get("called"))) or []
    live = next((c.rec_state() for c in active_calls() if c.meta["call_id"] == cid), None) if admin else None
    c = call if admin else users.mask(call)
    c["access"] = "full"
    c["owner_name"] = ", ".join(x["name"] for x in who)
    c["owner_shared"] = len(who) > 1
    ct = db.find_contact(call.get("caller"))
    c["caller_name"], c["caller_info"] = (ct["name"] if ct else ""), numinfo.label(call.get("caller"))
    shared = {r["user_id"] for r in db.q("SELECT user_id FROM call_shares WHERE call_id=?", (cid,))}
    share_users = []
    for u in db.q("SELECT * FROM users WHERE role='user' AND disabled=0 ORDER BY username") if users.may_share(user, call) else []:   # ohne Recht: kein Knopf
        if u["id"] == user["id"] or (u["id"] not in shared and users.level_for(u, call) is not None):
            continue                           # man selbst und wer den Anruf ohnehin ueber seine Rufnummer sieht
        st = db.q("SELECT name FROM staff WHERE id=?", (u["staff_id"],)) if u["staff_id"] else []
        share_users.append({"id": u["id"], "label": (st[0]["name"] + " (" + u["username"] + ")") if st else u["username"], "shared": u["id"] in shared})
    return {"call": c, "live": live, "share_users": share_users, "party": notify.party_label(call), "local_name": notify.local_label(call),
            "agents": [{"id": a["id"], "name": a["name"]} for a in db.list_agents(enabled_only=True) if a["mode"] == "assist"] if live else [],
            "segments": db.q("SELECT * FROM segments WHERE call_id=? ORDER BY id", (cid,)),
            "events": flow.list_for(cid, call.get("ended") or (None if call.get("status") in ("aktiv", "waehlt") else call.get("started")))}


@app.put("/api/calls/{cid}/memo")
async def call_memo(cid: int, request: Request, user: dict = Depends(user_auth)):
    """Notiz zum Anruf (frei, fuer alle, die den Anruf sehen)."""
    call = (db.q("SELECT * FROM calls WHERE id=?", (cid,)) or [None])[0]
    if not call or users.level_for(user, call) is None:
        raise HTTPException(404)
    db.x("UPDATE calls SET memo=? WHERE id=?", (str((await request.json()).get("memo") or "")[:4000], cid))
    return {"ok": True}


@app.put("/api/calls/{cid}/shares")
async def call_shares(cid: int, request: Request, user: dict = Depends(user_auth)):
    """Anruf einzelnen Benutzern freigeben (sehen Transkript, Notiz und Aufnahme). Freigeben darf nur, wer den Anruf aus eigenem Recht sieht
    (nicht weitergeben, was man selbst nur freigegeben bekam); Benutzer nehmen nur eigene Freigaben zurueck, Administratoren alle."""
    call = (db.q("SELECT * FROM calls WHERE id=?", (cid,)) or [None])[0]
    if not call or users.level_for(user, call) is None:
        raise HTTPException(404)
    if not users.may_share(user, call):
        raise HTTPException(403, "Diesen Anruf hast du selbst nur freigegeben bekommen; weitergeben kann ihn nur, wem die Rufnummer gehört")
    want = {int(i) for i in ((await request.json()).get("user_ids") or []) if str(i).isdigit()}
    ok = {u["id"] for u in db.q("SELECT id FROM users WHERE role='user' AND disabled=0") if u["id"] != user["id"]}
    mine = "" if user["role"] == "admin" else f" AND by_user={int(user['id'])}"
    db.x("DELETE FROM call_shares WHERE call_id=? AND user_id NOT IN (%s)%s" % (",".join(str(i) for i in (want & ok) or [0]), mine), (cid,))
    for i in want & ok:
        db.x("INSERT OR IGNORE INTO call_shares(call_id,user_id,by_user,ts) VALUES(?,?,?,?)", (cid, i, user["id"], time.time()))
    return {"ok": True, "shared": sorted(want & ok)}


# --- Rufnummern: Zuweisung an Mitarbeiter (nur Administratoren) ---
# --- Google Kalender (Dienstkonto), Einstellungen (nur Administratoren) ---
@app.get("/api/gcal", dependencies=[Depends(auth)])
def gcal_get():
    return {"configured": gcal.configured(), "email": gcal.service_email()}


@app.put("/api/gcal", dependencies=[Depends(auth)])
async def gcal_put(request: Request):
    d = await request.json()
    try:
        if d.get("clear_key"):
            gcal.set_key("")
        elif (d.get("key") or "").strip():
            gcal.set_key(d["key"])
    except ValueError as e:
        raise HTTPException(400, str(e) if "Dienstkonto" in str(e) else "Der Schlüssel ist kein gültiges JSON")
    return gcal_get()


@app.post("/api/gcal/test", dependencies=[Depends(auth)])
async def gcal_test(request: Request):
    cal = ((await request.json()).get("calendar_id") or "").strip()
    if not cal:
        raise HTTPException(400, "Kalender-ID angeben")
    try:
        title = await asyncio.to_thread(gcal.calendar_title, cal)
        evs = await asyncio.to_thread(gcal.events, cal, time.time(), time.time() + 30 * 86400)
    except gcal.CalendarError as e:
        raise HTTPException(502, str(e))
    return {"ok": True, "title": title, "events_30d": len(evs)}


# --- Benachrichtigungen per E-Mail (nur Administratoren) ---
MAIL_SETTINGS = ("smtp_host", "smtp_port", "smtp_security", "smtp_user", "smtp_from", "base_url", "report_time", "mail_max_mb")


@app.get("/api/mail", dependencies=[Depends(auth)])
def mail_get():
    c = notify.config()
    return {"settings": {k: db.get_meta(k, "") for k in MAIL_SETTINGS}, "effective": {**{k: c[k] for k in ("host", "port", "security", "user", "from", "report_time")}, "max_mb": notify.max_attach_mb()},
            "password_set": bool(c["password"]), "password_source": c["password_source"], "configured": notify.configured(),
            "recipients": db.q("SELECT * FROM mail_recipients ORDER BY id"), "statuses": [{"id": k, "label": l} for k, l in notify.NOTIFY_STATUSES]}


@app.put("/api/mail/settings", dependencies=[Depends(auth)])
async def mail_settings(request: Request):
    d = await request.json()
    if d.get("report_time") and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", d["report_time"]):
        raise HTTPException(400, "Berichtszeit im Format HH:MM")
    if d.get("smtp_port") and not str(d["smtp_port"]).isdigit():
        raise HTTPException(400, "Port: Zahl erwartet")
    if d.get("mail_max_mb") not in (None, "") and not (str(d["mail_max_mb"]).isdigit() and 1 <= int(d["mail_max_mb"]) <= 50):
        raise HTTPException(400, "Maximale Mailgröße: 1 bis 50 MB")
    if d.get("smtp_security") and d["smtp_security"] not in ("starttls", "ssl", "none"):
        raise HTTPException(400, "Verschluesselung: starttls, ssl oder none")
    for k in MAIL_SETTINGS:
        if k in d:
            db.set_meta(k, str(d[k] or "").strip()[:200])
    if d.get("clear_password"):
        db.x("DELETE FROM meta WHERE key='smtp_password'")
    elif d.get("smtp_password"):                 # nur schreiben, nie ausgeben
        db.set_meta("smtp_password", str(d["smtp_password"])[:300])
    return {"ok": True}


@app.post("/api/mail/test", dependencies=[Depends(auth)])
async def mail_test(request: Request):
    to = ((await request.json()).get("to") or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", to):
        raise HTTPException(400, "Gueltige E-Mail-Adresse angeben")
    try:
        await asyncio.to_thread(notify.send_mail, to, "Lieschen Müller: Testnachricht",
                                "Das ist eine Testnachricht. Der Mailversand funktioniert.")
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return {"ok": True}


def clean_recipient(d):
    email = (d.get("email") or "").strip()
    if not re.fullmatch(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+", email):
        raise HTTPException(400, "Gueltige E-Mail-Adresse angeben")
    nums = ",".join(n for n in (db._digits(x) for x in str(d.get("numbers") or "").split(",")) if n)
    st = d.get("on_status") or []
    if isinstance(st, str):
        st = st.split(",")
    valid = [k for k, _ in notify.NOTIFY_STATUSES]
    st = [k for k in valid if k in st]
    on_status = "" if len(st) in (0, len(valid)) else ",".join(st)         # alle oder keine Auswahl = jedes Ergebnis
    return {"email": email, "label": (d.get("label") or "").strip()[:80], "numbers": nums, "on_status": on_status,
            "on_call": d.get("on_call") if d.get("on_call") in ("off", "all", "important") else "off",
            "detail": "full" if d.get("inc_transcript", d.get("detail") == "full") else "short",
            "inc_details": 0 if d.get("inc_details") in (False, 0) else 1, "inc_transcript": 1 if d.get("inc_transcript", d.get("detail") == "full") else 0,
            "inc_audio": 1 if d.get("inc_audio") else 0, "inc_points": 1 if d.get("inc_points") else 0, "daily": 1 if d.get("daily") else 0, "on_rec": 1 if d.get("on_rec") else 0,
            "enabled": 0 if d.get("enabled") is False or d.get("enabled") == 0 else 1}


@app.post("/api/mail/recipients", dependencies=[Depends(auth)])
async def recipient_create(request: Request):
    r = clean_recipient(await request.json())
    return {"id": db.x(f"INSERT INTO mail_recipients({','.join(r)}) VALUES({','.join('?' * len(r))})", list(r.values()))}


@app.put("/api/mail/recipients/{rid}", dependencies=[Depends(auth)])
async def recipient_update(rid: int, request: Request):
    r = clean_recipient(await request.json())
    db.x(f"UPDATE mail_recipients SET {','.join(k + '=?' for k in r)} WHERE id=?", [*r.values(), rid])
    return {"ok": True}


@app.delete("/api/mail/recipients/{rid}", dependencies=[Depends(auth)])
def recipient_delete(rid: int):
    db.x("DELETE FROM mail_recipients WHERE id=?", (rid,))
    return {"ok": True}


@app.post("/api/mail/recipients/{rid}/report", dependencies=[Depends(auth)])
async def recipient_report(rid: int):
    """Tagesbericht (bis jetzt) sofort an diesen Empfaenger senden - zum Ausprobieren."""
    r = (db.q("SELECT * FROM mail_recipients WHERE id=?", (rid,)) or [None])[0]
    if not r:
        raise HTTPException(404)
    try:
        await notify.send_report(r)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return {"ok": True}


@app.get("/api/fritz/phones", dependencies=[Depends(auth)])
async def fritz_phones():
    """Interne Anschluesse der Fritzbox (DECT, analog, IP-Telefone) fuer die Auswahl bei 'Ich telefoniere'."""
    try:
        return fritz.cached_phones()           # nie auf die Fritzbox warten: der Stand wird im Hintergrund erneuert
    except Exception as e:
        log.warning("Fritzbox-Anschluesse nicht lesbar: %s", type(e).__name__)
        return []


@app.get("/api/fritz/hosts", dependencies=[Depends(auth)])
async def fritz_hosts():
    """Geraete im Netz der Fritzbox (fuer "Anwesenheit ueber WLAN-Geraet"): MAC, Name, IP, gerade aktiv, WLAN."""
    try:
        return await asyncio.to_thread(presence.hosts)
    except Exception as e:
        raise HTTPException(502, f"Geräteliste der Fritzbox nicht lesbar ({type(e).__name__}). Hat der TR-064-Benutzer das Recht "
                                 "„FRITZ!Box Einstellungen“?")


@app.get("/api/fritz/host-status", dependencies=[Depends(auth)])
async def fritz_host_status(mac: str):
    """Sind diese Geraete (Komma) gerade im Netz der Fritzbox? (live, fuer die Anzeige beim Einstellen)"""
    macs = [m for m in presence.clean_macs(mac).split(",") if m]
    if not macs:
        raise HTTPException(400, "Ungültige MAC-Adresse")
    res = await asyncio.to_thread(lambda: {m: presence.fritz_active(m) for m in macs})
    return {"devices": res, "active": True if any(res.values()) else (None if None in res.values() else False)}


@app.get("/api/numbers", dependencies=[Depends(auth)])
async def get_numbers():
    """Fritzbox-Rufnummern mit Zuweisung. Eine Nummer gehoert einem Mitarbeiter (exklusiv) oder mehreren (geteilt)."""
    owners = db.number_owners()
    labels, hint = {}, ""
    try:
        for n in await asyncio.to_thread(fritz.list_numbers):
            labels[n["number"]] = n["name"]
    except Exception as e:
        hint = str(e) if isinstance(e, RuntimeError) else f"Rufnummern der Fritzbox nicht lesbar ({type(e).__name__})"
    nums = list(labels)
    for d, sts in owners.items():                     # zugewiesene Nummern auch dann zeigen, wenn die Fritzbox sie nicht (mehr) liefert
        for st in sts:
            for m in (st["msn"] or "").split(","):
                if db._digits(m) == d and m not in nums:
                    nums.append(m)
    counts = {r["d"]: r["n"] for r in db.q("SELECT digits(called) d, COUNT(*) n FROM calls WHERE direction='in' GROUP BY d")}
    out = []
    for n in nums:
        who = owners.get(db._digits(n)) or []
        out.append({"number": n, "label": labels.get(n, ""), "calls": counts.get(db._digits(n), 0),
                    "staff": [{"id": x["id"], "name": x["name"]} for x in who],
                    "mode": "shared" if len(who) > 1 else "exclusive" if who else "free"})
    return {"numbers": out, "hint": hint}


@app.put("/api/numbers/{number}", dependencies=[Depends(auth)])
async def assign_number(number: str, request: Request):
    """Zuweisung einer Nummer festlegen: staff_ids = alle Mitarbeiter, die sie bekommen (leer = frei).
    Ein Mitarbeiter = exklusiv, mehrere = geteilt (alle sehen die Anrufe, bei Anrufen klingeln alle Erreichbaren)."""
    if not number.isdigit() or len(number) < 3:
        raise HTTPException(400, "Rufnummer: nur Ziffern (mindestens 3)")
    d = await request.json()
    ids = d.get("staff_ids")
    if ids is None:
        ids = [d["staff_id"]] if d.get("staff_id") else []
    try:
        ids = sorted({int(i) for i in ids})
    except (TypeError, ValueError):
        raise HTTPException(400, "staff_ids: Liste von Mitarbeiter-IDs")
    known = {r["id"] for r in db.q("SELECT id FROM staff")}
    if any(i not in known for i in ids):
        raise HTTPException(400, "Mitarbeiter nicht gefunden")
    for st in db.q("SELECT id, msn FROM staff"):
        had = number in (st["msn"] or "").split(",")
        parts = [m for m in (st["msn"] or "").split(",") if m and m != number]
        if st["id"] in ids:
            parts.append(number)
        new = ",".join(parts)
        if new != (st["msn"] or ""):
            db.x("UPDATE staff SET msn=? WHERE id=?", (new, st["id"]))
        if st["id"] in ids and not had:          # neu zugewiesen: aeltere Anrufe auf dieser Nummer bleiben dem Vorbesitzer/Admin
            db.set_meta(db.msn_since_key(st["id"], number), time.time())
        elif had and st["id"] not in ids:
            db.x("DELETE FROM meta WHERE key=?", (db.msn_since_key(st["id"], number),))
    return {"ok": True}


# --- Anmeldung, eigenes Konto, Benutzerverwaltung ---
def _staff_name(sid):
    r = db.q("SELECT name FROM staff WHERE id=?", (sid,)) if sid else []
    return r[0]["name"] if r else ""


def _me(u):
    return {"id": u["id"], "username": u["username"], "role": u["role"], "staff_id": u["staff_id"],
            "staff_name": _staff_name(u["staff_id"]), "basic": u is PSEUDO_ADMIN, "device": _own_device(u)}


def is_internal(n):
    """Interne Fritzbox-Nummer (**NNN): kostet nichts, darf jeder Benutzer verwenden."""
    return bool(re.fullmatch(r"\*\*\d{1,4}", n or ""))


def _own_device(u):
    """Eigene interne Nummer (Anschluss), von der aus ein Benutzer Gespraeche mit Mitschrift starten darf.
    Eingeschraenkte Konten: nur eine interne Nummer (**NNN) oder die vom Administrator eingetragene Nummer
    (sonst koennte ein Benutzer zwei externe Gespraeche auf Kosten des Betreibers fuehren)."""
    r = db.q("SELECT number, number_admin FROM staff WHERE id=?", (u.get("staff_id"),)) if u.get("staff_id") else []
    raw = (r[0]["number"] if r else "").strip()
    try:
        nums = ami.dial_list(raw) if raw else []
    except ValueError:
        return ""
    adm = {x.strip() for x in re.split(r"[;,]", (r[0]["number_admin"] if r else "") or "") if x.strip()}
    for n in nums:                        # das erste zulaessige Geraet (bei mehreren Nummern)
        if u.get("role") == "admin" or is_internal(n) or n in adm:
            return n
    return ""


def resolve_target(to):
    """Verweis "staff:<id>:away" (Nummer ausser Haus, die Benutzer nicht angezeigt bekommen) durch die Nummer ersetzen."""
    def one(p):
        m = re.fullmatch(r"staff:(\d+):away", p.strip())
        if not m:
            return p
        r = db.q("SELECT away_number FROM staff WHERE id=?", (int(m.group(1)),))
        num = re.split(r"[;,]", r[0]["away_number"] or "")[0].strip() if r else ""
        if not num:
            raise HTTPException(400, "Für diesen Benutzer ist keine Nummer außer Haus eingetragen")
        return num
    return "+".join(one(p) for p in (to or "").split("+"))


def user_target_ok(n):
    """Ziel fuer Durchstellen/Umleiten durch eingeschraenkte Konten: interne Nummer, Nummer eines Benutzers
    (Buero/ausser Haus) oder eines Kontakts (pflegt der Administrator). Beliebige externe Nummern nur Administratoren."""
    if is_internal(n):
        return True
    def norm(v):
        try:
            return ami.dial_number(v) if (v or "").strip() else ""
        except ValueError:
            return ""
    known = {norm(p) for s in db.q("SELECT number, away_number FROM staff") for k in ("number", "away_number") for p in re.split(r"[;,]", s[k] or "")}
    known |= {norm(c["number"]) for c in db.list_contacts()}
    return n in known - {""}


@app.post("/api/login")
async def login(request: Request):
    d = await request.json()
    if not isinstance(d, dict) or not isinstance(d.get("username"), str) or not isinstance(d.get("password"), str) \
            or len(d["username"]) > 200 or len(d["password"]) > 1024:
        raise HTTPException(400, "Benutzername und Passwort als Text angeben")
    k_user, k_ip = login_keys(request, d.get("username"))
    if blocked(k_user) or blocked(k_ip, 60):
        raise HTTPException(429, "Zu viele Fehlversuche, bitte später erneut")
    u = await asyncio.to_thread(users.authenticate, d.get("username"), d.get("password"))
    if not u:
        throttle(k_user, False)
        throttle(k_ip, False, 60)
        await asyncio.sleep(0.5)
        raise HTTPException(401, "Benutzername oder Passwort falsch")
    FAILS.pop(k_user, None)
    remember = d.get("remember") is True
    tok = users.new_session(u["id"], remember)
    resp = JSONResponse(_me(u))
    # ohne "Angemeldet bleiben" ein Sitzungs-Cookie: endet mit dem Schliessen des Browsers (spaetestens nach SESSION_IDLE ohne Aktivitaet)
    resp.set_cookie(SESSION_COOKIE, tok, max_age=users.SESSION_MAX_REMEMBER if remember else None, httponly=True, samesite="strict", path="/",
                    secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")
    return resp


@app.post("/api/logout")
async def logout(request: Request):
    users.drop_session(request.cookies.get(SESSION_COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@app.get("/api/labels")
def get_labels(user: dict = Depends(user_auth)):
    """Alle Statustexte (Benutzer-Status, Herkunft, Zeitplan, Waehlregeln) aus labels.py - die Oberflaeche definiert sie nicht selbst."""
    return lbls.all_labels()


@app.get("/api/me")
def me(user: dict = Depends(user_auth)):
    return _me(user)


@app.put("/api/me/password")
async def me_password(request: Request, user: dict = Depends(user_auth)):
    if user is PSEUDO_ADMIN:
        raise HTTPException(400, "Bei der Basic-Anmeldung nicht möglich - bitte mit dem Formular anmelden")
    d = await request.json()
    full = users.get(user["id"])
    if not users.verify_pw(d.get("old") or "", full["pw_hash"]):
        raise HTTPException(400, "Das bisherige Passwort stimmt nicht")
    try:
        users.check_new_password(d.get("new"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.x("UPDATE users SET pw_hash=? WHERE id=?", (users.hash_pw(d["new"]), user["id"]))
    users.drop_user_sessions(user["id"], request.cookies.get(SESSION_COOKIE))   # andere Sitzungen beenden
    return {"ok": True}


def _my_staff(user):
    sid = user.get("staff_id")
    return next((x for x in db.list_staff(effective=False) if x["id"] == sid), None) if sid else None


@app.get("/api/me/suggest")
def my_suggest(user: dict = Depends(user_auth)):
    """Vorschlaege fuer Nummernfelder (Tag-Felder) ohne Administrator-APIs: der eigene Eintrag (alle Nummern), von Kollegen und
    Sammelzielen nur interne Nummern (**NNN, keine Nummern ausser Haus), interne Telefone der Fritzbox; das Telefonbuch nur, wenn
    unter Benutzer freigegeben (users_phonebook) oder fuer Administratoren. Gruppen/Felder wie in der Oberflaeche (caller, called, fwd)."""
    admin = user["role"] == "admin"
    caller, called, fwd = [], [], []
    for s in db.list_staff(effective=False):
        own = s["id"] == user.get("staff_id")
        for n in [x.strip() for x in re.split(r"[;,]", s.get("number") or "") if x.strip()]:
            if own or admin or is_internal(n):
                caller.append({"v": n, "l": s["name"], "s": n + (" (intern)" if is_internal(n) else "")})
                fwd.append({"g": "Intern" if is_internal(n) else "Eigene Nummern", "v": n, "l": s["name"], "s": n})
        if (own or admin) and (s.get("away_number") or "").strip():
            caller.append({"v": s["away_number"], "l": s["name"] + " (außer Haus)", "s": s["away_number"]})
            fwd.append({"g": "Eigene Nummern" if own else "Außer Haus", "v": s["away_number"], "l": s["name"] + " (außer Haus)", "s": s["away_number"]})
        if own or admin:
            called += [{"v": m, "l": s["name"], "s": m + " (Rufnummer)"} for m in (s.get("msn") or "").split(",") if m]
    try:
        phones = fritz.cached_phones() or []
    except Exception:
        phones = []
    for p in phones:
        if p.get("number") and is_internal(p["number"]) and not any(f["v"] == p["number"] for f in fwd):
            fwd.append({"g": "Intern", "v": p["number"], "l": p.get("name") or p["number"], "s": p["number"]})
    if admin or db.get_meta("users_phonebook", "0") == "1":
        for c in db.list_contacts():
            caller.append({"v": c["number"], "l": c["name"], "s": c["number"]})
            fwd.append({"g": "Kontakte", "v": c["number"], "l": c["name"], "s": c["number"]})
    return {"caller": caller, "called": called, "fwd": fwd, "phonebook": admin or db.get_meta("users_phonebook", "0") == "1"}


@app.get("/api/me/this-device")
async def me_this_device(request: Request, user: dict = Depends(user_auth)):
    """Das Geraet, mit dem die Seite gerade geoeffnet ist (IP -> Fritzbox), fuer die WLAN-Anwesenheit der Person selbst."""
    if not _my_staff(user):
        raise HTTPException(400, "Ihr Konto ist mit keinem Mitarbeiter verknüpft")
    ip = request.client.host if request.client else ""
    dev = await asyncio.to_thread(presence.device_by_ip, ip) if ip and not ip.startswith("127.") else None
    if not dev:
        raise HTTPException(404, "Dieses Gerät ist der Fritzbox nicht bekannt – die Seite bitte auf dem Handy im WLAN öffnen.")
    return dev


@app.get("/api/me/staff")
def my_staff(user: dict = Depends(user_auth)):
    return _my_staff(user) or {}


@app.put("/api/me/staff")
async def put_my_staff(request: Request, user: dict = Depends(user_auth)):
    """Eigener Status und eigene Weiterleitung (nur diese Felder)."""
    row = _my_staff(user)
    if not row:
        raise HTTPException(400, "Ihr Konto ist mit keinem Mitarbeiter verknüpft")
    d = await request.json()
    merged = dict(row)
    for k in ("status", "note", "until", "number", "away_number", "dnd_allow", "presence_on", "presence_mac",
              "rec_out", "rec_out_numbers", "rec_out_notice"):
        if k in d:
            merged[k] = d[k]
    if merged["status"] not in db.STAFF_STATUS:
        raise HTTPException(400, "Ungültiger Status")
    _check_rec_out(merged)
    for k, label in (("number", "Nummer im Büro"), ("away_number", "Nummer außer Haus")):
        if (merged.get(k) or "").strip():
            try:
                merged[k] = ", ".join(ami.dial_list(merged[k]))
            except ValueError as e:
                raise HTTPException(400, f"{label}: {e}")
    admin = user["role"] == "admin"
    old = {x.strip() for x in re.split(r"[;,]", (row.get("number") or "") + "," + (row.get("number_admin") or "")) if x.strip()}
    num = next((x.strip() for x in re.split(r"[;,]", merged.get("number") or "") if x.strip() and not is_internal(x.strip()) and x.strip() not in old), "")
    if not admin and num:
        # "Nummer im Buero" ist das eigene Geraet fuer Gespraeche mit Mitschrift: selbst nur interne Nummern
        raise HTTPException(403, "Nummer im Büro: selbst eintragen lassen sich nur interne Nummern (**NNN). "
                                 "Eine externe Nummer trägt ein Administrator ein.")
    if merged["status"] == "office" and row["status"] != "office":      # beim Wechsel zurueck ins Buero: Abwesenheitsangaben loeschen
        merged["note"], merged["until"] = "", ""
    out = await _apply_rec_out(row, merged, explicit="rec_out" in d)
    db.save_staff(merged, row["id"], presence_self=True)       # WLAN-Anwesenheit einschalten: nur hier (die Person selbst)
    if admin:
        db.x("UPDATE staff SET number_admin=number WHERE id=?", (row["id"],))
    return {"ok": True, "out_rec": out}


def _check_rec_out(m):
    """Einstellung "Ausgehende Gespraeche mitschreiben" pruefen (staff-Felder rec_out, rec_out_numbers, rec_out_notice)."""
    if (m.get("rec_out") or "off") not in db.REC_OUT:
        raise HTTPException(400, "Ausgehende Gespräche: ungültige Auswahl")
    if m.get("rec_out") == "numbers" and not db.clean_dnd_allow(m.get("rec_out_numbers")):
        raise HTTPException(400, "Ausgehende Gespräche „nur an bestimmte Nummern“: bitte mindestens eine Nummer angeben")


async def _apply_rec_out(old, new, explicit=False):
    """Telefone umstellen, wenn sich "Ausgehende Gespraeche mitschreiben" (an/aus) oder die Telefone geaendert haben (explicit: der
    Benutzer hat die Einstellung selbst gespeichert - dann auch erneut versuchen). 409 bei einem Fehler (dann nichts speichern)."""
    was_on, on = (old.get("rec_out") or "off") != "off", (new.get("rec_out") or "off") != "off"
    phones_changed = outrec.staff_phones(old) != outrec.staff_phones(new)
    if not (was_on != on or ((was_on or on) and phones_changed) or (on and explicit)):
        return None
    try:
        return await outrec.apply(dict(new, id=old["id"]))
    except outrec.OutRecError as e:
        raise HTTPException(409, str(e))


@app.get("/api/me/out-rec")
async def my_out_rec(user: dict = Depends(user_auth)):
    """Stand der eigenen Telefone fuer "Ausgehende Gespraeche mitschreiben" (umgestellt, Notbetrieb, Grund)."""
    row = _my_staff(user)
    if not row:
        raise HTTPException(400, "Ihr Konto ist mit keinem Mitarbeiter verknüpft")
    return {"phones": await outrec.status(row), "rec_out": row.get("rec_out") or "off"}


@app.get("/api/staff/{sid}/out-rec", dependencies=[Depends(auth)])
async def staff_out_rec(sid: int):
    r = db.q("SELECT * FROM staff WHERE id=?", (sid,))
    if not r:
        raise HTTPException(404)
    return {"phones": await outrec.status(r[0]), "rec_out": r[0].get("rec_out") or "off"}


# --- Waehlregeln (Mitarbeiter pflegen ihre eigenen, Administratoren die aller Mitarbeiter) ---
def _rules_get(sid):
    st = db.q("SELECT * FROM staff WHERE id=?", (sid,))
    if not st:
        raise HTTPException(404, "Mitarbeiter nicht gefunden")
    nums = [m for m in (st[0]["msn"] or "").split(",") if m]
    return {"rules": db.dialrules_of(st[0]), "numbers": nums, "actions": drules.ACTIONS, "modes": drules.MODES}


def _rules_put(sid, rules):
    try:
        clean = drules.normalize(rules, ami.dial_number)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.x("UPDATE staff SET dialrules=? WHERE id=?", (json.dumps(clean, ensure_ascii=False), sid))
    return {"rules": clean}


def _rules_toggle(sid, rid, enabled):
    st = db.q("SELECT * FROM staff WHERE id=?", (sid,))
    rules = db.dialrules_of(st[0]) if st else []
    hit = next((r for r in rules if r["id"] == rid), None)
    if not hit:
        raise HTTPException(404, "Regel nicht gefunden")
    hit["enabled"] = bool(enabled)
    db.x("UPDATE staff SET dialrules=? WHERE id=?", (json.dumps(rules, ensure_ascii=False), sid))
    return {"ok": True, "enabled": hit["enabled"]}


def _my_sid(user):
    if not user.get("staff_id"):
        raise HTTPException(400, "Ihr Konto ist mit keinem Mitarbeiter verknüpft")
    return user["staff_id"]


@app.get("/api/me/rules")
def my_rules(user: dict = Depends(user_auth)):
    return _rules_get(_my_sid(user))


@app.put("/api/me/rules")
async def put_my_rules(request: Request, user: dict = Depends(user_auth)):
    return _rules_put(_my_sid(user), (await request.json()).get("rules") or [])


@app.put("/api/me/rules/{rid}/toggle")
async def toggle_my_rule(rid: str, request: Request, user: dict = Depends(user_auth)):
    return _rules_toggle(_my_sid(user), rid, (await request.json()).get("enabled"))


@app.post("/api/me/preview")
async def my_preview(request: Request, user: dict = Depends(user_auth)):
    """Vorschau fuer den eigenen Eintrag: wohin ginge ein Anruf von dieser Nummer jetzt?"""
    st = next((x for x in db.list_staff() if x["id"] == _my_sid(user)), None)
    if not st:
        raise HTTPException(404, "Mitarbeiter nicht gefunden")
    d = await request.json()
    return reach_preview(st, str(d.get("caller") or ""), str(d.get("called") or ""), mask=user["role"] != "admin")


@app.post("/api/staff/{sid}/preview", dependencies=[Depends(auth)])
async def staff_preview(sid: int, request: Request):
    """Vorschau im Benutzer-Editor: "Ein Anruf von <Nummer/Kontakt> ginge jetzt an ..., weil ..." (gespeicherter Stand)."""
    st = next((x for x in db.list_staff() if x["id"] == sid), None)
    if not st:
        raise HTTPException(404, "Mitarbeiter nicht gefunden")
    d = await request.json()
    return reach_preview(st, str(d.get("caller") or ""), str(d.get("called") or ""))


@app.get("/api/staff/{sid}/rules", dependencies=[Depends(auth)])
def staff_rules_get(sid: int):
    return _rules_get(sid)


@app.put("/api/staff/{sid}/rules", dependencies=[Depends(auth)])
async def staff_rules_put(sid: int, request: Request):
    return _rules_put(sid, (await request.json()).get("rules") or [])


@app.put("/api/staff/{sid}/rules/{rid}/toggle", dependencies=[Depends(auth)])
async def staff_rule_toggle(sid: int, rid: str, request: Request):
    return _rules_toggle(sid, rid, (await request.json()).get("enabled"))


def _last_admin(uid):
    return not db.q("SELECT 1 FROM users WHERE role='admin' AND disabled=0 AND id!=?", (uid,))


@app.get("/api/users", dependencies=[Depends(auth)])
def get_users():
    return [dict(users.public(u), staff_name=_staff_name(u["staff_id"])) for u in db.q("SELECT * FROM users ORDER BY username")]


@app.post("/api/users", dependencies=[Depends(auth)])
async def new_user(request: Request):
    d = await request.json()
    _no_group_account(d.get("staff_id") or None)
    try:
        return {"id": users.create(d.get("username"), d.get("password"), d.get("role", "user"), d.get("staff_id") or None)}
    except ValueError as e:
        raise HTTPException(400, str(e))


def _no_group_account(staff_id):
    """Sammelziele (z. B. Zentrale) haben kein Anmeldekonto."""
    if staff_id and db.q("SELECT 1 FROM staff WHERE id=? AND kind='group'", (staff_id,)):
        raise HTTPException(400, "Ein Sammelziel hat kein Anmeldekonto")


@app.put("/api/users/{uid}", dependencies=[Depends(auth)])
async def put_user(uid: int, request: Request):
    u = users.get(uid)
    if not u:
        raise HTTPException(404, "Benutzer nicht gefunden")
    d = await request.json()
    role = d.get("role", u["role"])
    disabled = 1 if d.get("disabled") else 0
    if role not in ("admin", "user"):
        raise HTTPException(400, "Rolle: admin oder user")
    if (role != "admin" or disabled) and u["role"] == "admin" and _last_admin(uid):
        raise HTTPException(400, "Der letzte aktive Administrator kann nicht herabgestuft oder gesperrt werden")
    staff_id = d.get("staff_id") or None
    if staff_id and not db.q("SELECT 1 FROM staff WHERE id=?", (staff_id,)):
        raise HTTPException(400, "Mitarbeiter nicht gefunden")
    _no_group_account(staff_id)
    db.x("UPDATE users SET role=?, staff_id=?, disabled=? WHERE id=?", (role, staff_id, disabled, uid))
    if disabled:
        users.drop_user_sessions(uid)
    return {"ok": True}


@app.post("/api/users/{uid}/password", dependencies=[Depends(auth)])
async def reset_password(uid: int, request: Request):
    if not users.get(uid):
        raise HTTPException(404, "Benutzer nicht gefunden")
    d = await request.json()
    try:
        users.check_new_password(d.get("password"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.x("UPDATE users SET pw_hash=? WHERE id=?", (users.hash_pw(d["password"]), uid))
    users.drop_user_sessions(uid)
    return {"ok": True}


@app.delete("/api/users/{uid}", dependencies=[Depends(auth)])
def del_user(uid: int):
    u = users.get(uid)
    if not u:
        raise HTTPException(404, "Benutzer nicht gefunden")
    if u["role"] == "admin" and _last_admin(uid):
        raise HTTPException(400, "Der letzte aktive Administrator kann nicht gelöscht werden")
    users.drop_user_sessions(uid)
    db.x("DELETE FROM users WHERE id=?", (uid,))
    return {"ok": True}


@app.get("/api/agents", dependencies=[Depends(auth)])
def get_agents():
    return db.list_agents()


@app.post("/api/agents", dependencies=[Depends(auth)])
async def new_agent(request: Request):
    aid = db.save_agent(validate_agent(await request.json()))
    track(asyncio.create_task(warm_agent(next(a for a in db.list_agents() if a["id"] == aid))))
    return {"id": aid}


@app.put("/api/agents/{aid}/enabled", dependencies=[Depends(auth)])
async def agent_enabled(aid: int, request: Request):
    """Schnellschalter in der Agenten-Uebersicht."""
    on = 1 if (await request.json()).get("enabled") else 0
    if not db.q("SELECT id FROM agents WHERE id=?", (aid,)):
        raise HTTPException(404)
    db.x("UPDATE agents SET enabled=? WHERE id=?", (on, aid))
    return {"ok": True, "enabled": on}


def _clean_mail_targets(d):
    """Empfaenger-IDs (Liste) und freie Adressen (Text) zu 'id,id,adresse' zusammenfuehren."""
    ids = {r["id"] for r in db.q("SELECT id FROM mail_recipients")}
    out = [str(i) for i in sorted({int(x) for x in (d.get("mail") or []) if str(x).isdigit()} & ids)]
    for a in re.split(r"[,;\s]+", str(d.get("mail_free") or "")):
        if a:
            if not re.fullmatch(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+", a):
                raise HTTPException(400, f"Ungültige E-Mail-Adresse: {a[:60]}")
            out.append(a.lower())
    return ",".join(dict.fromkeys(out))


def _clean_rule(d, rec):
    out = {"name": str(d.get("name") or "").strip()[:80], "enabled": 0 if d.get("enabled") in (False, 0) else 1,
           "match_called": str(d.get("match_called") or "").strip()[:300], "match_caller": str(d.get("match_caller") or "").strip()[:300]}
    if d.get("schedule") not in (None, "", "open", "closed"):
        raise HTTPException(400, "Zeitfenster: immer, nur während oder nur außerhalb der Geschäftszeiten")
    out["schedule"] = d.get("schedule") or ""
    aid = int(d.get("agent_id") or 0)
    if rec:
        if aid and not db.get_agent(aid):
            raise HTTPException(400, "Agent nicht gefunden")
        dr = d.get("direction") or "in"
        if dr not in ("in", "out"):
            raise HTTPException(400, "Richtung: eingehend oder ausgehend")
        out.update(agent_id=aid, audio=1 if d.get("audio") else 0, transcript=1 if d.get("transcript") else 0,
                   ai_audio=1 if d.get("ai_audio") else 0, stop_cmd=0 if d.get("stop_cmd") in (False, 0) else 1,
                   direction=dr, notice=0 if d.get("notice") in (False, 0) else 1, rec_screen=1 if d.get("rec_screen") else 0)
        if dr == "out":                              # ausgehend: kein Agent, keine Anrufernummer (der Anrufer ist man selbst), kein KI-Audio
            out.update(agent_id=0, ai_audio=0, match_caller="", mail=_clean_mail_targets(d))
        else:
            out["mail"] = ""
        out["name"] = out["name"] or "Aufzeichnung"
    else:
        ag = db.get_agent(aid)
        if not ag:
            raise HTTPException(400, "Bitte einen Agenten wählen")
        try:
            ring = max(0, min(60, int(d.get("ring_s") or 0)))
        except (TypeError, ValueError):
            raise HTTPException(400, "Klingelzeit: Zahl zwischen 0 und 60 Sekunden")
        out.update(agent_id=aid, ring_s=ring, priority=int(d.get("priority") if d.get("priority") not in (None, "") else 100))
        try:
            rn, rm = int(d.get("repeat_n") or 0), int(d.get("repeat_min") or 0)
        except (TypeError, ValueError):
            raise HTTPException(400, "Mehrfach angerufen: ganze Zahlen")
        if rn and not (1 <= rn <= 50 and 1 <= rm <= 1440):
            raise HTTPException(400, "Mehrfach angerufen: 1 bis 50 frühere Anrufe in 1 bis 1440 Minuten (0 = egal)")
        kind = d.get("repeat_kind") or ""
        if kind not in db.REPEAT_KINDS:
            raise HTTPException(400, "Mehrfach angerufen: unbekannte Auswahl, welche Anrufe zählen")
        out.update(repeat_n=rn, repeat_min=rm if rn else 0, repeat_kind=kind if rn else "")
        out["name"] = out["name"] or ag["name"]
    return out


def _num_names(csv):
    """Zu einer Liste von Rufnummern (Komma) die bekannten Namen aus Kontakten und Mitarbeitern: [{n, name}]."""
    digits = lambda s: "".join(c for c in (s or "") if c.isdigit())
    staff = db.q("SELECT name, number, away_number FROM staff")
    out = []
    for n in [p.strip() for p in (csv or "").split(",") if p.strip()]:
        d, name = digits(n), ""
        if d:
            c = db.find_contact(n)
            name = c["name"] if c else ""
            if not name:
                for s in staff:
                    for sn in (s["number"], s["away_number"]):
                        sd = digits(sn)
                        if sd and (sd == d or (min(len(sd), len(d)) >= 7 and sd[-9:] == d[-9:])):
                            name = s["name"]
        out.append({"n": n, "name": name})
    return out


@app.get("/api/rules", dependencies=[Depends(auth)])
def rules_get():
    ags = {a["id"]: a for a in db.list_agents()}
    nm = lambda r: {"caller_names": _num_names(r["match_caller"]), "called_names": _num_names(r["match_called"])}
    name = lambda i: (ags.get(i) or {}).get("name", "– (gelöscht)")
    return {"call": [dict(r, agent_name=name(r["agent_id"]), agent_mode=(ags.get(r["agent_id"]) or {}).get("mode", ""),
                          agent_enabled=bool((ags.get(r["agent_id"]) or {}).get("enabled")), **nm(r)) for r in db.list_call_rules()],
            "rec": [dict(r, agent_name=name(r["agent_id"]) if r["agent_id"] else "", **nm(r)) for r in db.list_rec_rules()],
            "agents": [{"id": a["id"], "name": a["name"], "mode": a["mode"], "enabled": bool(a["enabled"])} for a in db.list_agents()]}


@app.post("/api/rules/{kind}", dependencies=[Depends(auth)])
async def rule_create(kind: str, request: Request):
    if kind not in ("call", "rec"):
        raise HTTPException(404)
    r = _clean_rule(await request.json(), kind == "rec")
    return {"id": db.x(f"INSERT INTO {kind}_rules({','.join(r)}) VALUES({','.join('?' * len(r))})", list(r.values()))}


@app.put("/api/rules/{kind}/{rid}", dependencies=[Depends(auth)])
async def rule_update(kind: str, rid: int, request: Request):
    if kind not in ("call", "rec"):
        raise HTTPException(404)
    r = _clean_rule(await request.json(), kind == "rec")
    if not db.q(f"SELECT 1 FROM {kind}_rules WHERE id=?", (rid,)):
        raise HTTPException(404)
    db.x(f"UPDATE {kind}_rules SET {','.join(k + '=?' for k in r)} WHERE id=?", [*r.values(), rid])
    return {"ok": True}


@app.delete("/api/rules/{kind}/{rid}", dependencies=[Depends(auth)])
def rule_delete(kind: str, rid: int):
    if kind not in ("call", "rec"):
        raise HTTPException(404)
    db.x(f"DELETE FROM {kind}_rules WHERE id=?", (rid,))
    return {"ok": True}


@app.put("/api/rules/call/{rid}/enabled", dependencies=[Depends(auth)])
async def rule_call_enabled(rid: int, request: Request, kind: str = "call"):
    db.x("UPDATE call_rules SET enabled=? WHERE id=?", (1 if (await request.json()).get("enabled") else 0, rid))
    return {"ok": True}


@app.put("/api/rules/rec/{rid}/enabled", dependencies=[Depends(auth)])
async def rule_rec_enabled(rid: int, request: Request):
    db.x("UPDATE rec_rules SET enabled=? WHERE id=?", (1 if (await request.json()).get("enabled") else 0, rid))
    return {"ok": True}


@app.put("/api/rules/call/{rid}/move", dependencies=[Depends(auth)])
async def rule_call_move(rid: int, request: Request):
    """Reihenfolge der Anrufregeln: eine Stelle nach oben oder unten (die erste passende Regel gilt)."""
    up = (await request.json()).get("dir") == "up"
    rows = db.list_call_rules()
    ids = [r["id"] for r in rows]
    if rid not in ids:
        raise HTTPException(404)
    i = ids.index(rid)
    j = i - 1 if up else i + 1
    if 0 <= j < len(ids):
        ids[i], ids[j] = ids[j], ids[i]
    for n, k in enumerate(ids):
        db.x("UPDATE call_rules SET priority=? WHERE id=?", ((n + 1) * 10, k))
    return {"ok": True}


@app.put("/api/agents/{aid}", dependencies=[Depends(auth)])
async def put_agent(aid: int, request: Request):
    db.save_agent(validate_agent(await request.json()), aid)
    track(asyncio.create_task(warm_agent(next(a for a in db.list_agents() if a["id"] == aid))))
    return {"id": aid}


@app.delete("/api/agents/{aid}", dependencies=[Depends(auth)])
def del_agent(aid: int):
    db.x("DELETE FROM agents WHERE id=?", (aid,))
    db.x("DELETE FROM call_rules WHERE agent_id=?", (aid,))
    db.x("DELETE FROM rec_rules WHERE agent_id=?", (aid,))
    return {"ok": True}


@app.get("/api/voices", dependencies=[Depends(auth)])
def get_voices():
    return [{"name": k, "style": v} for k, v in voices.VOICES.items()]


@app.get("/api/models", dependencies=[Depends(auth)])
def get_models():
    return {"default": models.DEFAULT, "models": [{**m, "eu": m["id"] in models.EU_LIVE} for m in models.MODELS], "default_notice": db.DEFAULT_NOTICE,
            "vertex_live": gclient.use_vertex("live")}


async def warm_agent(a):
    """Ansagen vorab erzeugen, damit der erste echte Anruf nicht auf die Sprachausgabe warten muss."""
    from .call import REC_NOTICE
    notice = ((a.get("notice") or "").strip() or db.DEFAULT_NOTICE) if a.get("notice_on", 1) else ""
    from .call import Call as _C
    handover = {db.get_meta("handover_text", "") or "Einen Moment bitte, ich verbinde Sie jetzt.",
                db.get_meta("handover_agent_text", "") or "Einen Moment bitte, ich verbinde Sie mit einem Kollegen."}
    # Klammern: der leere Text (kein Hinweis/keine Aufzeichnungsregel) muss aus der GANZEN Menge heraus - sonst wartet die
    # Sprachausgabe bei jedem Start vergeblich auf Ton zu einem leeren Text (Warnung "Ansage nicht vorab erzeugt: TimeoutError")
    for text in ({notice, REC_NOTICE if any(r["ai_audio"] and r["enabled"] and r["agent_id"] in (0, a["id"]) for r in db.list_rec_rules()) else ""}
                 | handover) - {""}:
        try:
            await voices.sample(a["voice"] or "Kore", text, a.get("model"), compact=True, hint=voices.speech_hint(a.get("speech_pace") or "", a.get("speech_style") or ""))
        except Exception as e:
            log.warning("Ansage nicht vorab erzeugt: %s", type(e).__name__)


@app.post("/api/staff-nick-sample", dependencies=[Depends(auth)])
async def staff_nick_sample(request: Request):
    """Test fuer die Anreden eines Benutzers: spricht eine Beispiel-Begruessung der Rueckfrage mit einer zufaellig gewaehlten Anrede
    (Stimme, Tempo, Stil und Name der KI aus den Einstellungen)."""
    import random
    from urllib.parse import quote
    d = await request.json()
    opts = [p for p in db.clean_nicknames(d.get("nicknames")).split(", ") if p]
    if not opts:
        raise HTTPException(400, "Bitte erst Anreden eintragen")
    pick = random.choice(opts)
    nm = db.get_meta("screen_ai_name", "")
    text = f"Hallo {pick}, hier ist {nm or 'dein Telefonassistent'}. Frau Meier ist in der Leitung und möchte wegen der Rechnung mit dir sprechen. Hast du kurz Zeit?"
    hint = voices.speech_hint(db.get_meta("screen_pace", ""), db.get_meta("screen_style", ""))
    try:
        wav = await voices.sample(db.get_meta("screen_voice", "") or "Kore", text, None, hint=hint)
    except Exception as e:
        call_mod.flag_api_error(e)
        log.exception("Anreden-Test fehlgeschlagen")
        raise HTTPException(502, "Test fehlgeschlagen: " + type(e).__name__)
    return Response(wav, media_type="audio/wav", headers={"X-Anrede": quote(pick), "Access-Control-Expose-Headers": "X-Anrede"})


@app.post("/api/voice-sample", dependencies=[Depends(auth)])
async def voice_sample(request: Request):
    d = await request.json()
    text = (d.get("text") or "Hallo, hier ist dein Telefonassistent. Wie kann ich dir helfen?")[:300]
    try:
        wav = await voices.sample(d.get("voice", ""), text, d.get("model"), hint=voices.speech_hint(d.get("pace", ""), d.get("style", "")))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        call_mod.flag_api_error(e)
        log.exception("Stimmprobe fehlgeschlagen")
        raise HTTPException(502, "Stimmprobe fehlgeschlagen: " + type(e).__name__)
    return Response(wav, media_type="audio/wav")


def _started(cid):
    """Beginn eines laufenden Anrufs (calls.started, sonst jetzt): users.level_for vergleicht ihn mit der Zuweisung der Rufnummer
    (msn_since); ohne Angabe saehen Benutzer laufende Anrufe auf eine neu zugewiesene Nummer nicht."""
    row = db.q("SELECT started FROM calls WHERE id=?", (cid,)) if cid else []
    return (row[0]["started"] if row else None) or time.time()


def active_call(cid, user=None):
    for c in active_calls():
        if c.meta["call_id"] == cid:
            if user is not None:     # Benutzer: nur Anrufe auf eigene Rufnummern
                row = db.q("SELECT called, started FROM calls WHERE id=?", (cid,))
                if not row or users.level_for(user, {"called": row[0]["called"], "started": row[0]["started"] or time.time()}) is None:
                    raise HTTPException(404, "Anruf nicht aktiv")
            return c
    raise HTTPException(404, "Anruf nicht aktiv")


@app.get("/api/live/stream")
async def live_stream(request: Request, user: dict = Depends(user_auth)):
    """Server-Sent Events: schickt den Stand der laufenden Anrufe sofort bei jeder Aenderung."""
    q = hub.subscribe()

    async def gen():
        try:
            last = None
            while not await request.is_disconnected():
                snap = json.dumps(live_snapshot(user), separators=(",", ":"))
                if snap != last:
                    last = snap
                    yield f"data: {snap}\n\n"
                else:
                    yield ": ping\n\n"
                try:
                    ev = await asyncio.wait_for(q.get(), 10)     # nach spaetestens 10 s neu pruefen (Ablauf klingelnder Anrufe)
                    if ev.get("type") not in ("call", "end", "ring", "agent", "note"):      # note: Nachricht aufgenommen (Banner)
                        continue
                    await asyncio.sleep(0.15)                  # Ereignisse bündeln
                    while not q.empty():
                        q.get_nowait()
                except asyncio.TimeoutError:
                    pass
        finally:
            hub.unsubscribe(q)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/calls/{cid}/ring")
async def call_ring(cid: int, request: Request, user: dict = Depends(user_auth)):
    """Klingelnden Anruf umleiten (Asterisk-Kanal per AMI): sofort die KI abnehmen lassen oder an eine Nummer durchstellen."""
    d = await request.json()
    uuid = next((u for u, r in CALLS.having("ringing") if r["id"] == cid), None)
    r = CALLS.field(uuid, "ringing") if uuid else None
    if not r or users.level_for(user, {"called": r["called"], "started": _started(r["id"])}) is None:
        raise HTTPException(404, "Anruf klingelt nicht mehr")
    ch = CALLS.field(uuid, "chan")
    if not ch or not ch["chan"]:
        raise HTTPException(409, "Kanal des Anrufs unbekannt")
    action = d.get("action")
    if action == "accept":
        if CALLS.field(uuid, "fallback"):      # Nummer mit Mitarbeiter: KI-Sitzung jetzt vorbereiten
            if not start_fallback(uuid, "Manuell angenommen: die KI übernimmt", "ist gerade nicht am Telefon", brake=False):
                raise HTTPException(409, "Kein passender Agent aktiv")
        elif not CALLS.field(uuid, "pending"):
            raise HTTPException(409, "Anruf kann nicht mehr angenommen werden")
        ok, why = await ami.redirect(ch["chan"], ch["to"], "answer")
        if not ok:
            raise HTTPException(409, why)
        flow.add(cid, "manual", action="accept")
        CALLS.take(uuid, "ringing")
        hub.publish({"type": "ring"})
        return {"ok": True}
    if action == "route":
        try:
            nums = "+".join(ami.dial_number(n) for n in resolve_target((d.get("to") or "").strip()).split("+"))
        except ValueError as e:
            raise HTTPException(400, str(e))
        if user["role"] != "admin" and not all(user_target_ok(n) for n in nums.split("+")):
            raise HTTPException(403, "Umleiten an diese Nummer dürfen nur Administratoren (erlaubt: interne Nummern, Benutzer, Kontakte)")
        rec = bool(d.get("record"))
        if rec and not os.path.exists(os.path.join(rectrans.SOUNDS, "rec_notice.sln")):
            raise HTTPException(409, "Hinweis-Ansage fuer die Mitschrift fehlt noch (wird beim Start erzeugt, bitte kurz warten)")
        ok, why = await ami.redirect(ch["chan"], ch["to"], "recdial" if rec else "direct",
                                     {"RES": (protocol.Rec(nums) if rec else protocol.Dial(nums)).encode()})
        if not ok:
            raise HTTPException(409, why)
        CALLS.take(uuid, "pending")
        CALLS.take(uuid, "fallback")
        CALLS.put(uuid, "retarget", {"t": time.time(), "target": nums})     # besetzt/keine Antwort -> zurueck zur KI
        CALLS.take(uuid, "ringing")
        CALLS.phase(uuid, Phase.RINGING)
        db.x("UPDATE calls SET agent_name=?, status=?, ended=? WHERE id=?",
             (f"Manuell durchgestellt: {nums}" + (" (mit Mitschrift)" if rec else ""), "aktiv" if rec else "durchgestellt",
              None if rec else time.time(), cid))
        db.add_segment(cid, "system", f"Manuell durchgestellt an {nums} (Anruf klingelte noch)")
        flow.add(cid, "manual", action="route", to=nums)
        hub.publish({"type": "ring"})
        return {"ok": True}
    raise HTTPException(400, "action: accept oder route")


@app.post("/api/calls/{cid}/analyze")
async def call_analyze(cid: int, user: dict = Depends(user_auth)):
    """Zusatzinfos (Zusammenfassung, Anliegen, ...) aus dem Transkript auswerten, auch nachtraeglich."""
    call = (db.q("SELECT * FROM calls WHERE id=?", (cid,)) or [None])[0]
    if call is None or users.level_for(user, call) is None:
        raise HTTPException(404, "Anruf nicht gefunden")
    if any(c.meta["call_id"] == cid for c in active_calls()):
        raise HTTPException(409, "Das Gespraech laeuft noch")
    try:
        data = await analyze.run(cid)
    except Exception as e:
        call_mod.flag_api_error(e)
        raise HTTPException(502, "Auswertung fehlgeschlagen: " + type(e).__name__)
    if data is None:
        raise HTTPException(409, "Zu wenig Gespraech zum Auswerten")
    return data


@app.post("/api/calls/{cid}/analyze-detail")
async def call_analyze_detail(cid: int, user: dict = Depends(user_auth)):
    """Ausfuehrliche Zusammenfassung (alle besprochenen Punkte, Zusagen, offene Punkte), auch nachtraeglich."""
    call = (db.q("SELECT * FROM calls WHERE id=?", (cid,)) or [None])[0]
    if call is None or users.level_for(user, call) is None:
        raise HTTPException(404, "Anruf nicht gefunden")
    if any(c.meta["call_id"] == cid for c in active_calls()):
        raise HTTPException(409, "Das Gespraech laeuft noch")
    try:
        data = await analyze.run_detail(cid)
    except Exception as e:
        call_mod.flag_api_error(e)
        raise HTTPException(502, "Zusammenfassung fehlgeschlagen: " + type(e).__name__)
    if data is None:
        raise HTTPException(409, "Zu wenig Gespraech zum Zusammenfassen")
    return data


@app.post("/api/calls/{cid}/record", dependencies=[Depends(auth)])
async def call_record(cid: int):
    try:
        return {"started": await active_call(cid).start_recording()}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/calls/{cid}/record/stop", dependencies=[Depends(auth)])
async def call_record_stop(cid: int, request: Request):
    discard = bool((await request.json()).get("discard")) if int(request.headers.get("content-length") or 0) else False
    return {"stopped": await active_call(cid).stop_recording(discard=discard)}


@app.get("/api/live")
async def api_live(user: dict = Depends(user_auth)):
    return live_snapshot(user)


def live_snapshot(user):
    """Laufende Anrufe mit den Zielen fuer das Umleiten per Klick."""
    now = time.time()
    nm = lambda n: (db.find_contact(n) or {}).get("name", "")
    # mit Beginn des Anrufs (sonst gilt 0 und Anrufe auf eine neu zugewiesene Nummer blieben unsichtbar)
    see = lambda called, started: users.level_for(user, {"called": called, "started": started or now}) is not None
    ring = [{"id": r["id"], "caller": r["caller"], "name": nm(r["caller"]), "where": numinfo.label(r["caller"]), "called": r["called"], "info": r["info"]}
            for _, r in CALLS.having("ringing") if now - r["t"] < 90 and see(r["called"], _started(r["id"]))]
    calls = []
    for c in active_calls():
        if not c.alive or c.meta.get("screen"):          # Rueckfrage-Sitzungen (zweite KI) haben keine Banner-Aktionen
            continue
        row = (db.q("SELECT started, called FROM calls WHERE id=?", (c.meta["call_id"],)) or [None])[0]
        if not row or not see(row["called"], row["started"]):
            continue
        calls.append({"id": c.meta["call_id"], "name": nm(c.meta.get("from", "")), "where": numinfo.label(c.meta.get("from", "")), "caller": c.meta.get("from", ""),
                      "called": row["called"], "agent": c.agent["name"], "started": row["started"],
                      "out": c.meta.get("direction") == "out",
                      # fuer die Animation im Banner: die KI stellt gerade durch / hat eine Nachricht aufgenommen (note_message)
                      "handover": bool(c.after),
                      "msg": c.messages_left()})
    # Nummer ausser Haus (oft privat): Benutzer sehen nur die eigene; fremde stehen als Verweis "staff:<id>:away" da (Durchstellen geht weiter)
    hide = lambda s: user["role"] != "admin" and s["id"] != user.get("staff_id")
    staff = [{"id": s["id"], "name": s["name"], "number": s["number"], "status": s["status"],
              "away": (f"staff:{s['id']}:away" if hide(s) else s["away_number"]) if s.get("away_number") else "", "away_hidden": hide(s)}
             for s in db.list_staff(effective=True) if s.get("number")]
    ags = [{"id": a["id"], "name": a["name"]} for a in db.list_agents(enabled_only=True)]
    humans = [{"id": h["id"], "caller": h["caller"], "name": nm(h["caller"]), "called": h["called"], "rec": h["rec"], "since": h["since"],
               "out": h["direction"] == "rec"} for _, h in CALLS.having("human")
              if users.level_for(user, {"called": h["called"], "id": h["id"], "user_id": h.get("user_id"), "started": _started(h["id"])}) is not None]
    return {"calls": calls, "ringing": ring, "staff": staff, "agents": ags, "humans": humans}


@app.post("/api/calls/{cid}/transfer")
async def call_transfer(cid: int, request: Request, user: dict = Depends(user_auth)):
    d = await request.json()
    try:
        to = ami.dial_number(resolve_target((d.get("to") or "").strip()))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if user["role"] != "admin" and not user_target_ok(to):
        raise HTTPException(403, "Durchstellen an diese Nummer dürfen nur Administratoren (erlaubt: interne Nummern, Benutzer, Kontakte)")
    rec = bool(d.get("record"))
    if rec and not os.path.exists(os.path.join(rectrans.SOUNDS, "rec_notice.sln")):
        raise HTTPException(409, "Hinweis-Ansage fuer die Mitschrift fehlt noch (wird beim Start erzeugt, bitte kurz warten)")
    call = active_call(cid, user if user['role'] != 'admin' else None)
    call.transfer(to, record=rec, say=call.handover_text())
    return {"ok": True}


@app.post("/api/calls/{cid}/switch")
async def call_switch(cid: int, request: Request, user: dict = Depends(user_auth)):
    call = active_call(cid, user if user["role"] != "admin" else None)
    aid = int((await request.json()).get("agent_id") or 0)
    agent = next((a for a in db.list_agents(enabled_only=True) if a["id"] == aid), None)
    if not agent:
        raise HTTPException(400, "Agent nicht gefunden oder nicht aktiv")
    if agent["name"] == call.agent["name"]:
        raise HTTPException(400, "Dieser Agent fuehrt das Gespraech bereits")
    call.switch_to, call.switch_reason = agent, "manuell im Webinterface"
    return {"ok": True}


@app.post("/api/calls/{cid}/hangup")
async def call_hangup(cid: int, user: dict = Depends(user_auth)):
    uuid = human_uuid(cid)
    if uuid:                           # Gespraech zwischen Menschen (von Asterisk gefuehrt): Kanal auflegen
        row = (db.q("SELECT id, called, user_id, started FROM calls WHERE id=?", (cid,)) or [None])[0]
        if not row or users.level_for(user, row) is None:
            raise HTTPException(404, "Anruf nicht aktiv")
        chan = CALLS.field(uuid, "human").get("chan", "")
        if not chan or not re.fullmatch(r"[A-Za-z0-9/_@.:+-]+", chan):
            raise HTTPException(409, "Der Kanal dieses Gesprächs ist unbekannt")
        resp, _ = await ami.action("Hangup", {"Channel": chan})          # AMI-Aktion (Recht call) statt CLI-Befehl
        if resp.get("Response") != "Success":
            log.warning("Auflegen von Anruf %s: %s", cid, resp.get("Message", "Fehler"))
        return {"ok": True}
    active_call(cid, user if user["role"] != "admin" else None).begin_hangup()
    return {"ok": True}


@app.delete("/api/calls/{cid}", dependencies=[Depends(auth)])
def call_delete(cid: int):
    if any(c.meta["call_id"] == cid for c in active_calls()) or db.q(
            "SELECT 1 FROM calls WHERE id=? AND status IN ('waehlt','aktiv','transkribiere')", (cid,)):
        raise HTTPException(409, "Anruf laeuft noch")
    if db.q("SELECT 1 FROM calls WHERE id=? AND archived=1", (cid,)):
        raise HTTPException(409, "Der Anruf ist archiviert. Erst die Archivierung aufheben, dann löschen.")
    remove_call(cid)
    return {"ok": True}


@app.post("/api/calls/{cid}/mail", dependencies=[Depends(auth)])
async def call_mail(cid: int, request: Request):
    """Anruf (Zusammenfassung, optional Transkript und Aufnahme als Anhang) per E-Mail an eine Adresse schicken. Nur Administratoren."""
    d = await request.json()
    to = [a.strip() for a in re.split(r"[,;\s]+", str(d.get("to") or "")) if a.strip()]
    if not to or len(to) > 5 or any(not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", a) for a in to):
        raise HTTPException(400, "Bitte 1 bis 5 gültige E-Mail-Adressen angeben")
    if not notify.configured():
        raise HTTPException(400, "Der E-Mail-Versand ist nicht eingerichtet (Seite Benachrichtigungen)")
    call, meta, notes = notify._load(cid)
    if not call:
        raise HTTPException(404)
    note = str(d.get("note") or "").strip()[:1000]
    if call.get("memo"):
        notes = {**notes, "Notiz zum Anruf": call["memo"]}
    if note:
        notes = {**notes, "Hinweis": note}
    pts = bool(d.get("points"))
    if pts and not (meta.get("detail") or {}).get("punkte"):
        await notify.ensure_detail(cid)                 # fehlt die ausfuehrliche Zusammenfassung, wird sie vor dem Versand erzeugt
        call, meta, notes = notify._load(cid)
        if call.get("memo"):
            notes = {**notes, "Notiz zum Anruf": call["memo"]}
        if note:
            notes = {**notes, "Hinweis": note}
    subject, text, html = notify.build_call_mail(call, meta, notes, "full" if d.get("transcript", True) else "short", bool(d.get("details", True)), pts)
    att = []
    rec = call.get("recording") or ""
    if d.get("audio", True) and rec and os.path.exists(rec):
        if os.path.getsize(rec) > 20 * 1024 * 1024:
            raise HTTPException(400, "Die Aufnahme ist zu groß für eine E-Mail (über 20 MB)")
        mp3f = rec.lower().endswith(".mp3")
        with open(rec, "rb") as f:
            att.append((f"anruf-{cid}.{'mp3' if mp3f else 'wav'}", f.read(), "audio", "mpeg" if mp3f else "wav"))
        subject = "Aufnahme: " + subject
    elif d.get("audio", True) and not rec:
        att = []
    try:
        await asyncio.to_thread(notify.send_mail, to, subject, text, html, att)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return {"ok": True, "attached": bool(att), "to": to}


@app.put("/api/calls/{cid}/archive")
async def call_archive(cid: int, request: Request, user: dict = Depends(user_auth)):
    """Anruf fuer das Archiv markieren: wird von der automatischen Loeschung (Aufbewahrungsfrist) ausgenommen."""
    call = (db.q("SELECT * FROM calls WHERE id=?", (cid,)) or [None])[0]
    if not call or users.level_for(user, call) is None:
        raise HTTPException(404)
    on = 1 if (await request.json()).get("on") else 0
    db.x("UPDATE calls SET archived=? WHERE id=?", (on, cid))
    return {"ok": True, "archived": bool(on)}


@app.post("/api/calls/{cid}/record-start")
async def call_record_start(cid: int, request: Request, user: dict = Depends(user_auth)):
    """Aufzeichnung eines laufenden Gespraechs zwischen Menschen nachtraeglich einschalten (optional mit Hinweis-Ansage im Gespraech)."""
    d = await request.json()
    uuid = human_uuid(cid)
    call = (db.q("SELECT * FROM calls WHERE id=?", (cid,)) or [None])[0]
    if not uuid or not call or users.level_for(user, call) is None:
        raise HTTPException(404, "Dieses Gespräch läuft nicht mehr")
    h = CALLS.field(uuid, "human")
    if h["rec"]:
        raise HTTPException(409, "Das Gespräch wird bereits aufgezeichnet")
    audio, trans, announce = bool(d.get("audio")), bool(d.get("transcript")), bool(d.get("announce", True))
    if user.get("role") != "admin":
        announce = True              # wie bei /api/record-call: ohne Hinweis-Ansage aufzeichnen darf nur ein Admin
    if not (audio or trans):
        raise HTTPException(400, "Bitte Audio und/oder Transkript wählen")
    chan = h["chan"]
    if not chan or not re.fullmatch(r"[A-Za-z0-9/_@.:+-]+", chan):
        raise HTTPException(409, "Der Kanal dieses Gesprächs ist unbekannt")
    base = os.path.join(rectrans.REC_IN, "")          # Host: data/rec-in, in Asterisk: /recordings
    notice_s = 0.0
    if announce:
        try:
            await rectrans.ensure_notice()
            peer = ""
            try:
                peer = await ami.bridged_peer(chan)
            except Exception:
                log.warning("Gegenseite fuer die Hinweis-Ansage nicht ermittelt (AMI CoreShowChannels)")
            if not peer:
                out = await ami.command(f"core show channel {chan}")          # aeltere Asterisk-Versionen
                peer = (re.search(r"Bridged ?Channel:\s*(\S+)", out) or [None, ""])[1]
            for ch in [chan] + ([peer] if peer and peer != "(null)" else []):
                key = str(uuid4().int % 10**8)
                SPY[key] = ch
                ok, why = await ami.play_into(key, "/sounds/rec_notice")
                if not ok:
                    log.warning("Ansage im Gespraech nicht abgespielt: %s", why)
            try:
                notice_s = os.path.getsize(os.path.join(rectrans.SOUNDS, "rec_notice.sln")) / 16000.0
            except OSError:
                notice_s = 6.0
        except Exception:
            log.exception("Ansage im Gespraech fehlgeschlagen - Aufzeichnung wird nicht gestartet")
            raise HTTPException(502, "Die Hinweis-Ansage konnte nicht abgespielt werden. Die Aufzeichnung wurde nicht gestartet.")
        await asyncio.sleep(notice_s + 1.0)           # die Ansage selbst soll nicht mit aufgenommen werden
    # Spuren: ausgehend (000) ist der Anrufer der Mitarbeiter (me), bei eingehenden Anrufen die Gegenseite (peer)
    out_dir = h["direction"] == "rec"
    rside, tside = ("me", "peer") if out_dir else ("peer", "me")
    opts = f"r(/recordings/{uuid}-{rside}.wav)t(/recordings/{uuid}-{tside}.wav)"
    try:       # CLI-Befehl: die AMI-Aktion MixMonitor verlangt das Recht "system" (kann Befehle ausfuehren), das hat die Bridge nicht
        res = await ami.command(f"mixmonitor start {chan} /recordings/{uuid}-mix.wav,{opts}")
    except Exception as e:
        raise HTTPException(502, f"Die Aufnahme konnte nicht gestartet werden ({type(e).__name__})")
    if "rror" in res or "No such" in res or "No channel" in res or "nable" in res:     # Asterisk 23: "No channel matching ... found."
        raise HTTPException(502, "Asterisk konnte die Aufnahme nicht starten: " + res.strip()[:120])
    now = time.time()
    m = re.search(r"\(([^)]*)\)", call["called"] or "")
    rf = agents.rec_flags(call["caller"] or "", [call["called"] or "", m.group(1) if m else ""], db.business_open(), 0,
                          "out" if call["direction"] == "rec" else "in")          # Sprachbefehl: wie die passenden Regeln es sagen
    db.x("UPDATE calls SET rec_stopped=0, rec_keep=?, rec_transcribe=?, rec_voicestop=?, agent_name=CASE WHEN agent_name LIKE '%aufgezeichnet%' OR agent_name LIKE '%Mitschrift%' THEN agent_name ELSE agent_name || ' (aufgezeichnet)' END WHERE id=?",
         (1 if audio else 0, 1 if trans else 0, int(bool(rf["stop_cmd"])), cid))
    db.set_meta(f"recbase:{uuid}", now)
    db.add_segment(cid, "system", "Aufzeichnung vom " + ("Administrator" if user["role"] == "admin" else "Benutzer") + " im laufenden Gespräch gestartet"
                   + (" (Hinweis-Ansage abgespielt)" if announce else " (ohne Hinweis-Ansage)") + f": {'Audio' if audio else ''}{' + ' if audio and trans else ''}{'Transkript' if trans else ''}")
    h["rec"] = True
    rectrans.start_live(cid, uuid, now, store=trans)
    hub.publish({"type": "ring"})
    return {"ok": True}


@app.post("/api/calls/{cid}/record-stop")
async def call_record_stop_human(cid: int, user: dict = Depends(user_auth)):
    """Aufzeichnung eines laufenden Gespraechs zwischen Menschen beenden; das bisher Aufgezeichnete wird verworfen."""
    uuid = human_uuid(cid)
    call = (db.q("SELECT * FROM calls WHERE id=?", (cid,)) or [None])[0]
    if not uuid or not call or users.level_for(user, call) is None:
        raise HTTPException(404, "Dieses Gespräch läuft nicht mehr")
    if not CALLS.field(uuid, "human")["rec"]:
        raise HTTPException(409, "Das Gespräch wird nicht aufgezeichnet")
    await rectrans.stop_live(uuid)
    await voice_stop(cid, uuid, "", "", note="Aufzeichnung vom " + ("Administrator" if user["role"] == "admin" else "Benutzer") + " im laufenden Gespräch beendet und verworfen")
    return {"ok": True}


@app.get("/api/calls/{cid}/recording")
def call_recording(cid: int, user: dict = Depends(user_auth)):
    call = (db.q("SELECT * FROM calls WHERE id=?", (cid,)) or [None])[0]
    if not call or users.level_for(user, call) is None or not call["recording"] or not os.path.exists(call["recording"]):
        raise HTTPException(404)
    return FileResponse(call["recording"], media_type="audio/mpeg" if call["recording"].lower().endswith(".mp3") else "audio/wav")


def locked(cid):
    r = db.q("SELECT source FROM contacts WHERE id=?", (cid,))
    if r and r[0]["source"] == "fritz":
        raise HTTPException(409, "Importierter Kontakt: bitte im Fritzbox-Telefonbuch aendern")


@app.get("/api/contacts", dependencies=[Depends(auth)])
def get_contacts():
    return db.list_contacts()


@app.get("/api/contacts/sync-info", dependencies=[Depends(auth)])
def sync_info():
    return {"last": float(db.get_meta("fritz_sync", "0") or 0),
            "configured": bool(os.environ.get("FRITZ_TR064_USER") and os.environ.get("FRITZ_TR064_PASSWORD"))}


@app.post("/api/contacts/sync-fritz", dependencies=[Depends(auth)])
async def sync_fritz():
    try:
        return await asyncio.to_thread(fritz.sync_phonebooks)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        log.exception("Fritzbox-Import fehlgeschlagen")
        raise HTTPException(502, f"Fritzbox-Import fehlgeschlagen ({type(e).__name__}): {e}"[:300])


@app.post("/api/contacts", dependencies=[Depends(auth)])
async def new_contact(request: Request):
    return {"id": db.save_contact(await request.json())}


@app.put("/api/contacts/{cid}", dependencies=[Depends(auth)])
async def put_contact(cid: int, request: Request):
    locked(cid)
    return {"id": db.save_contact(await request.json(), cid)}


@app.put("/api/contacts/{cid}/salutation", dependencies=[Depends(auth)])
async def put_contact_salutation(cid: int, request: Request):
    """Anrede setzen - auch bei Kontakten aus der Fritzbox (das Telefonbuch kennt keine Anrede)."""
    sal = (await request.json()).get("salutation") or ""
    if sal not in db.SALUTATIONS:
        raise HTTPException(400, "Anrede: Herr, Frau oder leer")
    if not db.x_count("UPDATE contacts SET salutation=? WHERE id=?", (sal, cid)):
        raise HTTPException(404)
    return {"ok": True}


@app.delete("/api/contacts/{cid}", dependencies=[Depends(auth)])
def del_contact(cid: int):
    locked(cid)
    db.x("DELETE FROM contacts WHERE id=?", (cid,))
    return {"ok": True}


async def run_originate(cid, uuid, number):
    try:
        ok, reason = await ami.originate(number, uuid)
    except Exception as e:
        log.exception("AMI-Fehler")
        ok, reason = False, f"Asterisk nicht erreichbar ({type(e).__name__})"
    if not ok:
        meta = CALLS.take(uuid, "pending") or {}
        await plugins.emit("call_unreached", meta, reason)
        db.x("UPDATE calls SET status='nicht erreicht', ended=? WHERE id=?", (time.time(), cid))
        db.add_segment(cid, "system", f"Anruf nicht zustande gekommen: {reason}")
        hub.publish({"type": "segment", "call_id": cid, "speaker": "system", "ts": time.time(),
                     "text": f"Anruf nicht zustande gekommen: {reason}"})
        hub.publish({"type": "end", "call_id": cid})


async def start_outbound(number, name, task, agent, owner="", extra=None, label="Ausgehend"):
    """Startet einen ausgehenden KI-Anruf (fuer die Oberflaeche und fuer Plugins). Gibt die Anruf-ID zurueck."""
    # Ausgehende KI-Anrufe (Assistent ruft an, Kampagnen) nutzen keine Aufzeichnungsregeln: kein KI-Audio, Transkript immer
    agent = dict(agent, record=0, transcribe=0, record_ai=0, stop_cmd=1, rec_rules=[], rec_notice=1, rec_notice_h=1)
    uuid = str(uuid4())
    cid = db.x("INSERT INTO calls(uuid,started,caller,called,agent_name,status,direction,task) VALUES(?,?,?,?,?,?,?,?)",
               (uuid, time.time(), label, f"{name} ({number})" if name else number, agent["name"], "waehlt", "out", task))
    CALLS.put(uuid, "pending", {**(extra or {}), "created": time.time(), "uuid": uuid, "from": number, "to": number, "hdr": "", "call_id": cid,
                                "agent": agent, "direction": "out", "task": task, "contact": name, "owner": (owner or "").strip()})
    hub.publish({"type": "call", "call_id": cid, "caller": label, "called": name or number, "agent": agent["name"], "status": "waehlt"})
    track(asyncio.create_task(run_originate(cid, uuid, number)))
    return cid


@app.post("/api/outbound", dependencies=[Depends(auth)])
async def outbound(request: Request, user: dict = Depends(auth)):
    """Loest einen ausgehenden Anruf mit Auftrag aus."""
    d = await request.json()
    try:
        number = ami.dial_number(d.get("number", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    task = (d.get("task") or "").strip()
    if not task:
        raise HTTPException(400, "Auftrag fehlt")
    agent = next((a for a in db.list_agents(enabled_only=True)
                  if a["id"] == int(d.get("agent_id") or 0) and a["mode"] == "assist"), None)
    if not agent:
        raise HTTPException(400, "Agent nicht gefunden (nur aktive Assistenten)")
    cid = await start_outbound(number, (d.get("name") or "").strip(), task, agent, d.get("owner") or "")
    return {"call_id": cid}


@app.get("/api/costs", dependencies=[Depends(auth)])
def costs():
    """Monats- und Agentenuebersicht (Kosten in USD, Umrechnung im Browser)."""
    month = "strftime('%Y-%m', started, 'unixepoch', 'localtime')"
    tok = "tok_in_text+tok_in_audio+tok_out_text+tok_out_audio+tok_think"
    months = db.q(f"SELECT {month} AS month, COUNT(*) AS calls, SUM(cost_usd) AS cost_usd, "
                  f"SUM({tok}) AS tokens, SUM(CASE WHEN ended>started THEN ended-started ELSE 0 END) AS seconds "
                  f"FROM calls GROUP BY month ORDER BY month DESC")
    agents_m = db.q(f"SELECT COALESCE(agent_name,'–') AS agent, COUNT(*) AS calls, SUM(cost_usd) AS cost_usd, "
                    f"SUM({tok}) AS tokens FROM calls WHERE {month}=strftime('%Y-%m','now','localtime') "
                    f"GROUP BY agent ORDER BY cost_usd DESC")
    # Kosten ohne Anruf (Ansagen, Stimmproben) dazurechnen
    xm = "strftime('%Y-%m', ts, 'unixepoch', 'localtime')"
    extra = {r["month"]: r for r in db.q(f"SELECT {xm} AS month, COUNT(*) AS n, SUM(cost_usd) AS cost, "
                                         f"SUM(in_text+in_audio+out_text+out_audio+think) AS tokens FROM usage_extra GROUP BY month")}
    months = [dict(r) for r in months]
    for r in months:
        e = extra.pop(r["month"], None)
        if e:
            r["cost_usd"] = (r["cost_usd"] or 0) + (e["cost"] or 0)
            r["tokens"] = (r["tokens"] or 0) + (e["tokens"] or 0)
    for mo, e in extra.items():
        months.append({"month": mo, "calls": 0, "cost_usd": e["cost"], "tokens": e["tokens"], "seconds": 0})
    months.sort(key=lambda r: r["month"], reverse=True)
    agents_m = [dict(r) for r in agents_m]
    cur = db.q(f"SELECT COUNT(*) AS n, SUM(cost_usd) AS cost, SUM(in_text+in_audio+out_text+out_audio+think) AS tokens "
               f"FROM usage_extra WHERE {xm}=strftime('%Y-%m','now','localtime')")[0]
    if cur["n"]:
        agents_m.append({"agent": "Ansagen und Stimmproben (ohne Anruf)", "calls": 0, "cost_usd": cur["cost"], "tokens": cur["tokens"]})
        agents_m.sort(key=lambda r: r["cost_usd"] or 0, reverse=True)
    return {"months": months, "agents": agents_m}


def clean_staff(d, sid=None):
    if not (d.get("name") or "").strip():
        raise HTTPException(400, "Name erforderlich")
    if d.get("kind") not in (None, "person", "group"):
        raise HTTPException(400, "Art: Person oder Sammelziel")
    if d.get("kind") == "group" and sid and db.q("SELECT 1 FROM users WHERE staff_id=?", (sid,)):
        raise HTTPException(400, "Ein Sammelziel hat kein Anmeldekonto: bitte das Konto zuerst lösen oder löschen (Tab „Anmeldung“).")
    if db.presence_on_denied(d, sid):
        raise HTTPException(400, "Die Anwesenheit über WLAN kann nur die Person selbst einschalten (Meine Einstellungen). "
                                 "Geräte vorbelegen und ausschalten geht hier.")
    d.pop("msn", None)         # Rufnummern weist nur die Seite "Rufnummern" zu
    if d.get("remote_on") and not [c for c in str(d.get("remote_codes") or "").split(",") if c.strip()]:
        raise HTTPException(400, "Fernabfrage: bitte mindestens ein Codewort angeben (Rufnummer und Codewort sind beide nötig)")
    if isinstance(d.get("schedule"), list):
        try:
            d["schedule"] = schedule.normalize(d["schedule"])
        except ValueError as e:
            raise HTTPException(400, f"Zeitplan: {e}")
    for key, label in (("number", "Nummer"), ("away_number", "Nummer außer Haus")):
        if (d.get(key) or "").strip():
            try:
                d[key] = ", ".join(ami.dial_list(d[key]))       # mehrere Nummern (Komma) klingeln gleichzeitig
            except ValueError as e:
                raise HTTPException(400, f"{label}: {e}")
    return d


SIP_FILE = astconf.SIP_FILE        # data/asterisk/fritz.conf: Ausgabe der Einstellungen (astconf.py)


async def fritz_reg_status(timeout_s=10):
    """Status der Anmeldung von Asterisk an der Fritzbox ('Registered', 'Rejected', ... oder ''). AMI-Aktion statt CLI-Befehl;
    fehlt in einer noch nicht neu geladenen manager.conf das Recht 'reporting', wie bisher ueber 'pjsip show registrations'."""
    try:
        return await ami.registration_status("fritz-reg", timeout_s)
    except RuntimeError as e:
        if "nicht erlaubt" not in str(e):
            raise
    out = await ami.command("pjsip show registrations", timeout_s)
    line = next((l for l in out.splitlines() if "fritz-reg" in l), "")
    return next((w for w in ("Unregistered", "Registered", "Rejected", "Stopped") if w in line), "Unbekannt" if line else "")


async def sip_apply():
    """Zugangsdaten der Fritzbox aus den Einstellungen nach fritz.conf schreiben (astconf), PJSIP neu laden und pruefen, ob die Anmeldung
    klappt. Wirft RuntimeError."""
    await asyncio.to_thread(astconf.write_fritz)
    await ami.command("module reload res_pjsip.so")
    await asyncio.sleep(2)
    # Neu anmelden erzwingen: sonst gilt die alte Anmeldung noch bis zur naechsten Erneuerung und falsche Zugangsdaten fielen nicht auf
    await ami.command("pjsip send unregister fritz-reg")
    await asyncio.sleep(1)
    await ami.command("pjsip send register fritz-reg")
    await asyncio.sleep(3)
    for _ in range(9):
        st = await fritz_reg_status()
        if st == "Registered":
            return
        if st == "Rejected":
            raise RuntimeError("Die Fritzbox hat die Zugangsdaten abgelehnt (Benutzername oder Kennwort des IP-Telefons falsch)")
        await asyncio.sleep(3)
    raise RuntimeError("Asterisk konnte sich mit diesen Zugangsdaten nicht bei der Fritzbox anmelden")


def sip_adopt():
    """Einmalige Migration Datei -> Datenbank (nur wenn Benutzer und Kennwort in den Einstellungen leer sind), siehe astconf.adopt."""
    return astconf.adopt(SIP_FILE)


async def sip_upgrade():
    """Einmalige Migration: aeltere fritz.conf ins aktuelle Format bringen. Laeuft jetzt als Teil des Abgleichs beim Start
    (astconf.sync_fritz: Datenbank gewinnt; nur bei unvollstaendigen Einstellungen wird die Datei mit ihren eigenen Daten umgestellt)."""
    res, task = await astconf.startup()
    if task:
        await task
    return res


@app.get("/api/config", dependencies=[Depends(auth)])
def config_get():
    return config.public()


@app.put("/api/config", dependencies=[Depends(auth)])
async def config_put(request: Request):
    """Teilweise aendern. Geheimnisse: leerer Wert = unveraendert, {"clear": true} = Oberflaechenwert entfernen (.env gilt wieder)."""
    d = await request.json()
    sip_keys = {"fritz_user", "fritz_password", "fritz_host"}
    prev = {k: config.get(k) for k in sip_keys}
    prev_ui = {k: config.ui_value(k) for k in sip_keys}
    sip_touched = False
    if sip_keys & set(d) and phones_busy() and not d.get("force"):
        raise HTTPException(409, "Es laufen gerade Telefonate. Zum Ändern trotzdem bestätigen.")
    d = {k: v for k, v in d.items() if k != "force"}
    for k, v in d.items():
        if k not in config.KEYS:
            raise HTTPException(400, f"Unbekannte Einstellung: {k}")
        secret = config.KEYS[k][1]
        if isinstance(v, dict) and v.get("clear"):
            config.set_value(k, "")
            continue
        v = str(v if v is not None else "").strip()
        if secret and not v:
            continue
        if re.search(r"[\n\r;]", v):
            raise HTTPException(400, "Zeilenumbrüche und Semikolon sind nicht erlaubt")
        if k == "fritz_host" and v and not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", v):
            raise HTTPException(400, "Fritzbox-Adresse: IP-Adresse oder Name, z. B. 192.168.178.1")
        if k == "retention_days" and v:
            if not v.isdigit():
                raise HTTPException(400, "Aufbewahrung: Anzahl Tage (0 = nie löschen)")
            v = str(int(v))                      # "0" wird gespeichert und gilt ausdruecklich (auch wenn die .env etwas anderes sagt)
        config.set_value(k, v)
        if k in sip_keys:
            sip_touched = True
    now_sip = {k: config.get(k) for k in sip_keys}
    if sip_touched and now_sip != prev and not (now_sip["fritz_user"] and now_sip["fritz_password"]):
        # Anmeldung von Asterisk noch nicht eingerichtet (z. B. nur Adresse und TR-064 gespeichert): nichts anmelden, die Adresse wird
        # beim Speichern der Zugangsdaten des IP-Telefons mit in fritz.conf geschrieben
        log.info("Fritzbox-Adresse gespeichert; Anmeldung von Asterisk folgt, sobald Benutzer und Kennwort des IP-Telefons eingetragen sind")
    elif sip_touched and now_sip != prev:
        try:
            await sip_apply()
        except Exception as e:
            if not (prev["fritz_user"] and prev["fritz_password"]):
                # Ersteinrichtung: die Eingaben bleiben gespeichert (Asterisk versucht die Anmeldung weiter, z. B. bis das IP-Telefon in der Fritzbox angelegt ist)
                log.warning("Fritzbox-Anmeldung noch nicht erfolgreich (%s) - Zugangsdaten bleiben gespeichert, Asterisk versucht es weiter", e)
                raise HTTPException(502, f"{e}. Die Zugangsdaten sind gespeichert; Asterisk versucht die Anmeldung weiter.")
            log.warning("Fritzbox-Anmeldung mit neuen Zugangsdaten fehlgeschlagen (%s) - alte Werte werden wiederhergestellt", e)
            for k, v in prev_ui.items():
                config.set_value(k, v)
            try:
                if prev["fritz_user"] and prev["fritz_password"]:          # bei der Ersteinrichtung gibt es nichts wiederherzustellen
                    await sip_apply()                                       # Einstellungen sind schon zurueckgesetzt
            except Exception:
                log.exception("Wiederherstellen der Fritzbox-Zugangsdaten fehlgeschlagen")
            raise HTTPException(502, f"{e}. Die bisherigen Zugangsdaten wurden wiederhergestellt.")
        log.info("Fritzbox-Zugangsdaten fuer Asterisk geaendert, Anmeldung erfolgreich")
    return config.public()


@app.post("/api/config/test-gemini", dependencies=[Depends(auth)])
async def config_test_gemini():
    """Kurzer Test des Gemini-API-Schluessels selbst (winzige Anfrage mit dem Textmodell), auch wenn gerade Vertex AI genutzt wird
    (der Vertex-Zugang hat einen eigenen Test). "genutzt" sagt, wofuer der Schluessel bei der aktuellen Auswahl dient."""
    if not os.environ.get("GEMINI_API_KEY"):
        raise HTTPException(400, "Es ist kein Gemini-Schlüssel hinterlegt")
    used = {"": "Telefon-KI und Texte", "text": "nur Telefon-KI (Texte über Vertex AI)", "all": "nicht genutzt (alles über Vertex AI)"}
    try:
        from google import genai
        r = await genai.Client().aio.models.generate_content(model=models.TRANSCRIBE["id"], contents="Antworte nur mit dem Wort: ok")
        return {"ok": True, "antwort": (r.text or "").strip()[:40], "genutzt": used[gclient.settings()["scope"]]}
    except Exception as e:
        raise HTTPException(502, f"Gemini hat den Schlüssel nicht akzeptiert oder ist nicht erreichbar ({type(e).__name__}: {str(e)[:150]})")


@app.post("/api/config/test-vertex", dependencies=[Depends(auth)])
async def config_test_vertex(kind: str = "text"):
    """Test der Vertex-Verbindung: je Textmodell der App eine winzige Anfrage in der gewaehlten Region (unabhaengig davon, ob Vertex gerade eingeschaltet ist)."""
    s = gclient.settings()
    if not s["project"] or not gclient.key_file_found():
        raise HTTPException(400, "Projekt-ID oder Schlüsseldatei (data/vertex.json) fehlt")
    try:
        from google import genai
        cli = genai.Client(vertexai=True, project=s["project"], location=s["location"], credentials=gclient._credentials())
    except Exception as e:
        raise HTTPException(502, f"Vertex-Zugang nicht möglich ({type(e).__name__}: {str(e)[:200]})")
    ids = list(dict.fromkeys([i for i, _, _ in models.TRANSCRIBE_CHOICES] + [i for i, _ in models.DETAIL_CHOICES]))
    result = {}
    for mid in ids:
        try:
            r = await cli.aio.models.generate_content(model=mid, contents="Antworte nur mit dem Wort: ok")
            result[mid] = "ok"
        except Exception as e:
            msg = str(e)
            if " 404" in msg or "NOT_FOUND" in msg:
                result[mid] = "in dieser Region nicht verfügbar"
            elif "403" in msg or "PERMISSION_DENIED" in msg or "401" in msg or "UNAUTHENTICATED" in msg:
                raise HTTPException(502, f"Keine Berechtigung ({type(e).__name__}): Rolle des Dienstkontos oder API prüfen. {msg[:160]}")
            else:
                result[mid] = f"Fehler: {type(e).__name__}"
    return {"ok": any(v == "ok" for v in result.values()), "region": s["location"], "modelle": result}


@app.post("/api/config/test-fritz", dependencies=[Depends(auth)])
async def config_test_fritz():
    """Test der TR-064-Zugangsdaten (nur lesend)."""
    def run():
        fc = fritz._conn()
        n = fc.call_action("X_VoIP1", "X_AVM-DE_GetNumberOfClients")["NewX_AVM-DE_NumberOfClients"]
        nums = len(fritz._items(fc.call_action("X_VoIP1", "X_AVM-DE_GetNumbers")["NewNumberList"]))
        return {"ok": True, "telefone": n, "rufnummern": nums}
    try:
        return await asyncio.to_thread(run)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Fritzbox nicht erreichbar oder Zugangsdaten falsch ({type(e).__name__}: {str(e)[:150]})")


SIP_PW_RE = re.compile(r"[A-Za-z0-9._-]{8,64}")     # Kennwort des IP-Telefons, das die Fritzbox auch ueber TR-064 annimmt (fritz.write_block)


@app.get("/api/setup", dependencies=[Depends(auth)])
async def setup_get(check: int = 0):
    """Stand der Ersteinrichtung fuer den Einrichtungsassistenten (nur Admins, nur Ja/Nein und Status, nie Zugangsdaten).
    Schritte: Gemini-Zugang, Fritzbox (TR-064), IP-Telefon (Zugangsdaten + Anmeldung von Asterisk). Der Assistent erscheint von selbst,
    solange etwas fehlt, die Einrichtung noch nie vollstaendig war (meta setup_done) und er nicht mit "Später" ausgeblendet ist
    (meta setup_dismissed). check=1: zusaetzlich ueber TR-064 pruefen, ob die Fritzbox das Kennwort des IP-Telefons zum Umstellen
    der Rufnummern annimmt (fritz.write_block)."""
    gem = gclient.ready("text") and gclient.ready("live")
    tr = bool(config.get("fritz_host") and config.get("fritz_tr064_user") and config.get("fritz_tr064_password"))
    sip = bool(astconf.db_creds())
    reg = ""
    if sip:
        try:
            reg = await fritz_reg_status(4)
        except Exception:
            reg = ""
    registered = reg == "Registered"
    complete = gem and tr and sip and registered
    if complete and db.get_meta("setup_done", "") != "1":
        db.set_meta("setup_done", "1")
        log.info("Ersteinrichtung vollstaendig (Gemini, Fritzbox, Anmeldung von Asterisk)")
    done_once = db.get_meta("setup_done", "") == "1"
    dismissed = db.get_meta("setup_dismissed", "") == "1"
    pw = config.get("fritz_password")
    res = {"steps": {"gemini": gem, "fritz": tr, "sip": sip, "registered": registered}, "reg": reg, "complete": complete,
           "done_once": done_once, "dismissed": dismissed, "show": not complete and not done_once and not dismissed,
           "access": gclient.settings()["scope"] or "key", "fritz_host": config.get("fritz_host") or "192.168.178.1",
           "fritz_user": config.get("fritz_user"), "sip_pw_ok": bool(SIP_PW_RE.fullmatch(pw)) if pw else None,
           "rec_prefix": astconf.rec_prefix()}
    if check and sip and tr:
        def block():
            fc = fritz._conn()
            return fritz.write_block(fc, config.get("fritz_user"))
        try:
            res["pw_block"] = await asyncio.to_thread(block)
        except Exception as e:
            res["pw_block_error"] = f"Fritzbox nicht erreichbar ({type(e).__name__})"
    return res


@app.put("/api/setup", dependencies=[Depends(auth)])
async def setup_put(request: Request):
    """Einrichtungsassistent ausblenden ("Später", dismissed=true) oder wieder automatisch zeigen (dismissed=false)."""
    d = await request.json()
    if "dismissed" in d:
        db.set_meta("setup_dismissed", "1" if d["dismissed"] else "0")
    return {"dismissed": db.get_meta("setup_dismissed", "") == "1"}


@app.get("/api/fritz/asterisk", dependencies=[Depends(auth)])
async def fritz_asterisk_get():
    """Auf welche Rufnummern antwortet Asterisk (laut Fritzbox)? Nur lesend."""
    try:
        return await asyncio.to_thread(fritz.asterisk_numbers)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Fritzbox nicht lesbar ({type(e).__name__})")


@app.put("/api/fritz/asterisk", dependencies=[Depends(auth)])
async def fritz_asterisk_put(request: Request):
    """Eingehende Rufnummern des Asterisk-Telefons in der Fritzbox aendern (nur mit confirm=true; bei laufenden Anrufen zusaetzlich force=true).
    Geschrieben wird ueber die Weboberflaeche der Fritzbox, nur wenn die nicht geht ueber TR-064 (fritz.set_asterisk_numbers)."""
    d = await request.json()
    if not d.get("confirm"):
        raise HTTPException(400, "Bestaetigung fehlt")
    if phones_busy() and not d.get("force"):
        raise HTTPException(409, "Es laufen gerade Telefonate. Zum Ändern trotzdem bestätigen.")
    sel = [str(n).strip() for n in (d.get("numbers") or [])]
    try:
        res = await asyncio.to_thread(fritz.set_asterisk_numbers, sel)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        log.warning("Fritzbox: Rufnummern des Asterisk-Telefons setzen fehlgeschlagen: %s: %s", type(e).__name__, str(e)[:200])
        raise HTTPException(502, f"Die Fritzbox hat die Änderung nicht übernommen ({type(e).__name__}: {str(e)[:150]}). Eventuell an der Box bestätigen oder in der Fritzbox einstellen.")
    log.info("Fritzbox: Asterisk antwortet jetzt auf %s", ", ".join(map(logmask.number, sel)))
    return res


@app.put("/api/fritz/asterisk/out", dependencies=[Depends(auth)])
async def fritz_asterisk_out_put(request: Request):
    """Ausgehende Rufnummer des Asterisk-Telefons (LieschenMueller) setzen - die sieht der Angerufene bei Gespraechen ueber Asterisk
    (Mitschrift ausgehend, Gespraeche der KI). Nur mit confirm=true; ueber die Weboberflaeche mit Gegenlesen."""
    d = await request.json()
    if not d.get("confirm"):
        raise HTTPException(400, "Bestaetigung fehlt")
    try:
        res = await asyncio.to_thread(fritz.set_asterisk_out, str(d.get("number") or ""))
    except fritzweb.FritzWebError as e:
        raise HTTPException(409, str(e))
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Fritzbox nicht erreichbar ({type(e).__name__})")
    log.info("Fritzbox: Asterisk waehlt jetzt ausgehend mit %s", logmask.number(res.get("outgoing", "")))
    return res


@app.get("/api/fritz-numbers", dependencies=[Depends(auth)])
async def fritz_numbers():
    try:
        return await asyncio.to_thread(fritz.list_numbers)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Fritzbox-Rufnummern nicht lesbar ({type(e).__name__})")


@app.get("/api/fritz/ring-numbers", dependencies=[Depends(auth)])
async def fritz_ring_numbers():
    """Klingel-Rufnummern 99NNN aus der Fritzbox und ob sie gerade bei Asterisk angemeldet sind (Auswahl in den Einstellungen)."""
    try:
        nums = await asyncio.to_thread(fritz.ring_numbers)
    except Exception as e:
        raise HTTPException(502, f"Fritzbox-Rufnummern nicht lesbar ({type(e).__name__})")
    try:
        contacts = await ami.command("pjsip show contacts")
    except Exception:
        contacts = ""
    for n in nums:
        n["registered"] = f"Contact:  {n['number']}/" in contacts or f" {n['number']}/sip:" in contacts
    return nums


RING_LOCK = asyncio.Lock()          # Haken bei den Klingel-Rufnummern: Lesen, Fritzbox, Schreiben ohne verlorene Aenderungen


def _ring_set(key, ring, on):
    """meta-Liste (ring_lines / ring_ok) um eine Nummer ergaenzen oder kuerzen, frisch gelesen (die gespeicherte Liste, ohne die
    automatisch wirksamen Nummern fuer "alle Telefone klingeln")."""
    cur = astconf.stored_ring_lines() if key == "ring_lines" else astconf.stored_ring_ok()
    cur = [n for n in cur if n != ring] + ([ring] if on else [])
    db.set_meta(key, ",".join(cur))


async def _contacts():
    try:
        return await ami.command("pjsip show contacts")
    except Exception:
        return ""


async def _dect_state(rows):
    """DECT-Telefone aus der Weboberflaeche der Fritzbox ({'**NNN': Telefon}); None, wenn sie nicht lesbar ist."""
    if not any(r.get("kind") == "DECT" for r in rows):
        return {}
    try:
        return {p["intern"]: p for p in await asyncio.to_thread(fritzweb.dect_phones)}
    except Exception as e:
        log.info("Fritzbox-Weboberflaeche: Rufnummern der DECT-Telefone nicht lesbar (%s)", type(e).__name__)
        return None


def _dect_fill(r, dect):
    """assigned (True/False/None), ring_all_phone und out_num einer Zeile: DECT aus dem Stand der Weboberflaeche, IP-Telefone aus
    TR-064 (ring_overview: ip_all = "alle Rufnummern", ip_out)."""
    r["ring_all_phone"], r["out_num"] = False, ""
    if r.get("kind") == "IP" and "client_idx" in r:
        r["ring_all_phone"], r["out_num"] = bool(r.get("ip_all")), r.get("ip_out") or ""
        if r["ring_all_phone"]:
            r["assigned"] = True                          # "alle": klingelt auch auf dieser 99NNN
        return
    if r.get("kind") != "DECT":
        return
    p = (dect or {}).get(r["phone"])
    if p is None:
        r["assigned"] = None                              # nicht lesbar: manuelles Haekchen bleibt
        return
    r["ring_all_phone"], r["out_num"] = p["ring_all"], p["out_num"]
    r["assigned"] = True if p["ring_all"] else r["ring"] in p["incoming"]     # "alle": klingelt auch auf dieser 99NNN


def _web_err(e):
    return str(e) if isinstance(e, fritzweb.FritzWebError) else f"Fritzbox nicht erreichbar ({type(e).__name__})"


# Rufnummern eines Telefons ueber die Weboberflaeche: DECT (edit_dect_num, nach **NNN) oder IP-Telefon/FRITZ!App (edit_ipfon_num,
# nach TR-064-Client-Index, ermittelt ueber die interne Nummer **NNN = GetClient3 NewX_AVM-DE_InternalNumber). Synchron (to_thread).
def _ip_client(phone, row=None):
    if row and "client_idx" in row:
        return row["client_idx"]
    c = fritz.ip_clients().get(phone)
    if not c:
        raise fritzweb.FritzWebError(f"In der Fritzbox gibt es kein IP-Telefon {phone}")
    return c["idx"]


def _w_assign(phone, kind, ring, on, row=None):
    if kind == "IP":
        return fritzweb.ip_assign(_ip_client(phone, row), ring, on)
    return fritzweb.dect_assign(phone, ring, on)


def _w_set_all(phone, kind):
    if kind == "IP":
        return fritzweb.ip_set_numbers(_ip_client(phone), [], ring_all=True)
    return fritzweb.dect_set_all(phone)


def _w_select(phone, kind, nums):
    if kind == "IP":
        return fritzweb.ip_set_numbers(_ip_client(phone), nums)
    return fritzweb.dect_select_numbers(phone, nums)


def _w_box_numbers(phone, kind):
    if kind == "IP":
        return fritzweb.ip_form(_ip_client(phone))["numbers"]
    return fritzweb.box_numbers(phone)


@app.get("/api/fritz/ring-lines", dependencies=[Depends(auth)])
async def fritz_ring_lines():
    """Interne Telefone mit Klingel-Rufnummer 99NNN: angelegt (Fritzbox), eingetragen (Asterisk), zugewiesen (bei IP-Telefonen ueber
    TR-064, bei DECT ueber die Weboberflaeche gelesen; None = nicht lesbar, dann gilt das Haekchen ok), angemeldet.
    DECT und IP zusaetzlich: ring_all_phone (das Telefon klingelt bei allen Rufnummern) und out_num (seine ausgehende Rufnummer)."""
    # TR-064 (Konten, IP-Telefone) und Weboberflaeche (DECT) gleichzeitig lesen
    dect_t = asyncio.create_task(_dect_state([{"kind": "DECT"}]))
    try:
        rows = await asyncio.to_thread(fritz.ring_overview)
    except Exception as e:
        dect_t.cancel()
        raise HTTPException(502, f"Fritzbox nicht lesbar ({type(e).__name__})")
    if any(r.get("kind") == "DECT" for r in rows):
        dect = await dect_t
    else:
        dect_t.cancel()
        dect = {}
    on, ok, contacts = set(astconf.ring_lines()), set(astconf.ring_ok()), await _contacts()
    ra = set(astconf.ring_all_lines())
    orec = outrec.overview()
    for r in rows:
        _dect_fill(r, dect)
        r["out_rec"] = orec.get(r["ring"])          # ausgehend auf 99NNN umgestellt ("Ausgehende Gespraeche mitschreiben"): Stand, Benutzer
        r["on"], r["ok"] = r["ring"] in on, r["ring"] in ok
        r["was_all"] = db.get_meta(f"ring_was_all:{r['ring']}", "") == "1"
        # Nummer (oder Teil) der Einstellung "alle Telefone klingeln" (meta ring_all): immer eingetragen, hier nicht abschaltbar
        r["ring_all"] = r["ring"] in ra
        if r["ring_all"] and dect:
            # welche DECT-Telefone klingeln bei dieser Nummer ("alle" oder angehakt) - IP-/Analog-Telefone sind hier nicht lesbar
            r["rings_on"] = [p["name"] for p in dect.values() if p["ring_all"] or r["ring"] in p["incoming"]]
            r["rings_not"] = [p["name"] for p in dect.values() if not (p["ring_all"] or r["ring"] in p["incoming"])]
        r["registered"] = f" {r['ring']}/sip:" in contacts
        # Telefon steht in der Fritzbox (wieder) auf "alle", wird aber noch ueber seine 99NNN gewaehlt (dann klingelten alle solchen
        # Telefone mit): nur melden - aufgeraeumt wird mit POST .../cleanup (die Oberflaeche ruft es dann auf), GET aendert nichts.
        # Die Nummer fuer "alle Telefone klingeln" ist dafuer gedacht und zaehlt nicht.
        r["stale"] = bool(r.get("ring_all_phone") and not r["ring_all"] and r["ok"])
    return rows


@app.post("/api/fritz/ring-lines/cleanup", dependencies=[Depends(auth)])
async def fritz_ring_lines_cleanup():
    """DECT- und IP-Telefone, die in der Fritzbox (wieder) bei allen Rufnummern klingeln, nicht mehr ueber ihre 99NNN waehlen (ring_ok
    aus). Unter RING_LOCK und nach frischem Nachlesen: ein veralteter Stand (Cache, gleichzeitiges Umstellen) schaltet nichts ab."""
    async with RING_LOCK:
        ra = astconf.ring_all_lines()                  # die Nummer(n) fuer "alle Telefone klingeln" bleiben immer
        cand = [r for r in astconf.stored_ring_ok() if r not in ra]
        if not cand:
            return {"ok": True, "cleaned": []}
        ip_cand = [r for r in cand if _phone_kind("**" + r[2:]) == "IP"]
        dect, ip = {}, {}
        try:
            if len(ip_cand) < len(cand):
                dect = {p["intern"]: p for p in await asyncio.to_thread(fritzweb.dect_phones, True)}
            if ip_cand:
                ip = await asyncio.to_thread(fritz.ip_clients)
        except Exception as e:
            raise HTTPException(502, _web_err(e))
        stale = [r for r in cand if (dect.get("**" + r[2:]) or {}).get("ring_all") or (ip.get("**" + r[2:]) or {}).get("all")]
        for ring in stale:
            _ring_set("ring_ok", ring, False)
        if stale:
            await ring_apply()
            log.info("Anruferanzeige: %s klingeln bei allen Nummern - Asterisk ruft diese Telefone direkt an", ", ".join(stale))
    return {"ok": True, "cleaned": stale}


def _phone_kind(phone):
    return next((p["kind"] for p in fritz.cached_phones() if p["number"] == phone), None)


@app.put("/api/fritz/ring-lines/{ring}", dependencies=[Depends(auth)])
async def fritz_ring_line_put(ring: str, request: Request):
    """{"on": true}: 99NNN in der Fritzbox anlegen (nur wenn frei), in Asterisk eintragen und dem Telefon als ankommende Nummer
    zuweisen (DECT- und IP-Telefone ueber die Weboberflaeche) - dann ist sie gleich nutzbar (ring_ok).
    Klappt das Zuweisen nicht, bleibt es beim Haekchen: assign_error nennt den Grund. Vergeben fuer einen anderen Anschluss = nichts.
    {"on": false}: Zuweisung beim Telefon entfernen, dann in Asterisk austragen (die Nummer bleibt in der Fritzbox).
    {"assigned": true/false}: Rueckfall von Hand - in der Fritzbox zugewiesen, erst dann waehlt Asterisk ueber diese Nummer.
    {"select_numbers": [...]}: Telefon von "alle Rufnummern" bewusst auf genau diese Auswahl umstellen (muss ring enthalten)."""
    d = await request.json()
    if not re.fullmatch(r"99\d{1,4}", ring):
        raise HTTPException(400, "Diese Klingel-Rufnummer lässt sich hier nicht ändern")
    off = ("assigned" in d and not d["assigned"]) or (not any(k in d for k in ("assigned", "ring_all_phone", "select_numbers"))
                                                       and not d.get("on"))
    if off and ring in astconf.ring_all_lines():
        raise HTTPException(409, f"{ring} wird für „alle Telefone klingeln“ genutzt – zuerst unter Einstellungen → Klingeln der Telefone ändern")
    phone = "**" + ring[2:]
    created, res, err = False, None, ""
    async with RING_LOCK:
        if "assigned" in d:
            _ring_set("ring_ok", ring, bool(d["assigned"]) and ring in astconf.ring_lines())
        elif d.get("ring_all_phone") is True:
            # zurueck auf "alle Rufnummern"; dann nicht mehr ueber die 99NNN waehlen (sonst klingelten alle solchen Telefone mit)
            try:
                await asyncio.to_thread(_w_set_all, phone, _phone_kind(phone))
            except fritzweb.FritzWebError as e:
                raise HTTPException(409, str(e))
            except Exception as e:
                raise HTTPException(502, _web_err(e))
            log.info("Fritzbox: %s klingelt wieder bei allen Rufnummern", phone)
            _ring_set("ring_ok", ring, False)
            db.x("DELETE FROM meta WHERE key=?", (f"ring_was_all:{ring}",))
            res = "all"
        elif "select_numbers" in d:
            nums = d["select_numbers"]
            ra = astconf.ring_all_lines()
            note, owners = "", []
            if ring not in astconf.ring_lines():
                raise HTTPException(400, f"Die Auswahl muss die eingerichtete Klingel-Rufnummer {ring} enthalten")
            if nums == "auto":
                # wie "alle", nur ohne die Klingel-Rufnummern ANDERER Telefone: alle echten Rufnummern der Box, die Nummer fuer
                # "alle Telefone klingeln" (ring_all, z. B. 99613) und die eigene 99NNN
                try:
                    box = await asyncio.to_thread(_w_box_numbers, phone, _phone_kind(phone))
                except fritzweb.FritzWebError as e:
                    raise HTTPException(409, str(e))
                except Exception as e:
                    raise HTTPException(502, _web_err(e))
                # Gehoert das Telefon Benutzern ("Klingelt im Buero an"/"ausser Haus" enthaelt **NNN), nur deren Rufnummern;
                # sonst alle echten Rufnummern der Box. Dazu die Nummer fuer "alle Telefone klingeln" und die eigene 99NNN.
                owners = [s for s in db.list_staff() if phone in {x.strip() for x in
                          f"{s.get('number') or ''},{s.get('away_number') or ''}".replace("+", ",").split(",")}]
                real = [n for n in box if not re.fullmatch(r"99\d{1,4}", n)]
                # Rufnummern der Benutzer normalisiert vergleichen (+49 30 … = 030 …) und in der Schreibweise der Box uebernehmen
                own = list(dict.fromkeys(n for s in owners for m in (s.get("msn") or "").split(",") if m.strip()
                                         for n in real if db.same_number(n, m.strip())))
                if owners and not own:
                    note = (f"Die Rufnummern von {', '.join(s['name'] for s in owners)} stehen nicht in der Fritzbox (oder sind leer) – "
                            "das Telefon klingelt deshalb weiter bei allen echten Rufnummern.")
                nums = list(dict.fromkeys((own or real) + [n for n in ra + [ring] if n in box or n == ring]))
            if not isinstance(nums, list) or not all(isinstance(n, str) for n in nums):
                raise HTTPException(400, "select_numbers: Liste von Rufnummern erwartet")
            nums = list(dict.fromkeys(n.strip() for n in nums if n.strip()))
            # Pruefungen auch fuer den Vorschlag (dry): der Dialog zeigt nur, was sich wirklich speichern laesst
            if ring not in nums:
                raise HTTPException(400, f"Die Auswahl muss die eingerichtete Klingel-Rufnummer {ring} enthalten")
            if any(re.fullmatch(r"99\d{1,4}", n) and n != ring and n not in ra for n in nums):
                raise HTTPException(400, "Klingel-Rufnummern anderer Telefone sind hier nicht wählbar")
            if len(nums) > fritzweb.MAX_NUMS:
                raise HTTPException(400, f"Höchstens {fritzweb.MAX_NUMS} ankommende Rufnummern je Telefon")
            if d.get("dry"):
                return {"ok": True, "numbers": nums, "owners": [s["name"] for s in owners], "note": note}
            try:
                await asyncio.to_thread(_w_select, phone, _phone_kind(phone), nums)
            except fritzweb.FritzWebError as e:
                raise HTTPException(409, str(e))
            except Exception as e:
                raise HTTPException(502, _web_err(e))
            log.info("Fritzbox: %s klingelt jetzt nur noch bei %d festen Rufnummern", phone, len(nums))
            _ring_set("ring_ok", ring, True)
            db.set_meta(f"ring_was_all:{ring}", "1")          # "auf alle zurueck" nur fuer Telefone anbieten, die die App umgestellt hat
            res = "ok"
        elif d.get("on"):
            try:
                rows = await asyncio.to_thread(fritz.ring_overview)
            except Exception as e:
                raise HTTPException(502, f"Fritzbox nicht lesbar ({type(e).__name__})")
            row = next((r for r in rows if r["ring"] == ring), None)
            if not row:
                raise HTTPException(400, f"Zu {ring} gibt es kein internes Telefon {phone}")
            if row["exists"] and not row["ours"]:
                raise HTTPException(409, f"Die Rufnummer {ring} ist in der Fritzbox schon für einen anderen Anschluss vergeben "
                                         f"(Registrar {row['registrar']}) – es wurde nichts angelegt.")
            if not row["exists"]:
                try:
                    await asyncio.to_thread(fritz.create_ring_account, ring)
                except RuntimeError as e:
                    raise HTTPException(409, str(e))
                except Exception as e:
                    raise HTTPException(502, f"Die Fritzbox hat die Rufnummer nicht angelegt ({type(e).__name__}: {str(e)[:120]})")
                created = True
                log.info("Fritzbox: Klingel-Rufnummer %s angelegt", ring)
            _ring_set("ring_lines", ring, True)
            ok = row.get("assigned") is True and not row.get("ip_all")   # IP-Telefon (TR-064) mit der Nummer: gleich nutzbar
            # DECT immer; IP-Telefone, deren Client-Index TR-064 geliefert hat (sonst bleibt es beim Lesen)
            if row.get("kind") == "DECT" or (row.get("kind") == "IP" and "client_idx" in row):
                try:
                    res = await asyncio.to_thread(_w_assign, phone, row["kind"], ring, True, row)
                    # 'all': das Telefon klingelt bei jeder Nummer - ueber die 99NNN klingelten dann ALLE Telefone mit "alle" mit.
                    # Erst nach "Umstellen" (feste Auswahl) waehlt Asterisk ueber die 99NNN; bis dahin direkt **NNN.
                    ok = res in ("ok", "same")
                    log.info("Fritzbox: %s dem Telefon %s zugewiesen (%s)", ring, phone, res)
                except Exception as e:
                    if not ok:                               # IP-Telefon laut TR-064 schon zugewiesen: kein Fehler
                        err = _web_err(e)
                    log.warning("Fritzbox: %s nicht automatisch zugewiesen (%s)", ring, type(e).__name__)
            _ring_set("ring_ok", ring, ok)
        else:
            kind = _phone_kind(phone)
            oerr = await outrec.restore(phone)        # ausgehend auf 99NNN (Mitschrift): zuerst zurueck, sonst waehlt es ins Leere
            if oerr:
                err = f"Ausgehende Nummer von {phone} nicht zurückgestellt ({oerr}) – bitte in der Fritzbox prüfen. "
            if db.get_meta(f"ring_was_all:{ring}", "") == "1":
                # die App hatte das Telefon von "alle" auf eine feste Auswahl umgestellt: beim Ausschalten zuerst dorthin zurueck,
                # sonst bliebe es ohne Anruferanzeige auf der festen Auswahl. Klappt das nicht, Meta behalten (Knopf "auf alle zurueck")
                try:
                    await asyncio.to_thread(_w_set_all, phone, kind)
                    db.x("DELETE FROM meta WHERE key=?", (f"ring_was_all:{ring}",))
                    res = "all"
                    log.info("Fritzbox: %s klingelt wieder bei allen Rufnummern (Anruferanzeige aus)", phone)
                except Exception as e:
                    err += _web_err(e)
                    log.warning("Fritzbox: %s nicht auf alle Rufnummern zurueckgestellt (%s)", phone, type(e).__name__)
            elif kind in ("DECT", "IP", None):               # unbekannt (Liste noch leer): DECT versuchen, Fehler nur bei DECT/IP melden
                try:
                    res = await asyncio.to_thread(_w_assign, phone, kind, ring, False)
                except Exception as e:
                    if kind in ("DECT", "IP"):
                        err += _web_err(e)
                        log.warning("Fritzbox: Zuweisung von %s nicht entfernt (%s)", ring, type(e).__name__)
            _ring_set("ring_lines", ring, False)
            _ring_set("ring_ok", ring, False)
        warn = await ring_apply()
    return {"ok": True, "created": created, "warn": warn, "assigned": res, "ring_all_phone": res == "all", "assign_error": err,
            "was_all": db.get_meta(f"ring_was_all:{ring}", "") == "1"}


@app.get("/api/staff", dependencies=[Depends(auth)])
def get_staff():
    out = db.list_staff(effective=False)
    for s in out:
        s["dialrules"] = db.dialrules_of(s)
    return out


@app.post("/api/staff", dependencies=[Depends(auth)])
async def new_staff(request: Request):
    d = clean_staff(await request.json())
    note = ""
    if (d.get("rec_out") or "off") != "off":
        _check_rec_out(d)
        d["rec_out"] = "off"      # Telefone stellt erst das Speichern eines vorhandenen Benutzers um (put_staff)
        note = "„Ausgehende Gespräche mitschreiben“ ist noch aus: bitte den Benutzer öffnen und erneut speichern (dann werden die Telefone umgestellt)."
    sid = db.save_staff(d)
    db.x("UPDATE staff SET number_admin=number WHERE id=?", (sid,))     # vom Administrator eingetragen (siehe _own_device)
    return {"id": sid, "out_rec_note": note}


@app.put("/api/staff/{sid}", dependencies=[Depends(auth)])
async def put_staff(sid: int, request: Request):
    d = clean_staff(await request.json(), sid)
    old = db.q("SELECT * FROM staff WHERE id=?", (sid,))
    out = None
    if old:
        new = dict(old[0], **d)
        if d.get("kind") == "group":
            new.update(db.GROUP_CLEAR)
        _check_rec_out(new)
        out = await _apply_rec_out(old[0], new)
    db.save_staff(d, sid)
    db.x("UPDATE staff SET number_admin=number WHERE id=?", (sid,))
    return {"id": sid, "out_rec": out}


@app.put("/api/staff/{sid}/status", dependencies=[Depends(auth)])
async def put_staff_status(sid: int, request: Request):
    d = await request.json()
    if d.get("status") not in db.STAFF_STATUS:
        raise HTTPException(400, "Ungueltiger Status")
    if db.q("SELECT 1 FROM staff WHERE id=? AND kind='group'", (sid,)):
        raise HTTPException(400, "Ein Sammelziel hat keinen Status")
    if d["status"] == "office":
        db.x("UPDATE staff SET status='office', status_src='manual', note='', until='' WHERE id=?", (sid,))
    else:
        db.x("UPDATE staff SET status=?, status_src='manual' WHERE id=?", (d["status"], sid))
    return {"ok": True}


@app.delete("/api/staff/{sid}", dependencies=[Depends(auth)])
async def del_staff(sid: int):
    row = db.q("SELECT * FROM staff WHERE id=?", (sid,))
    if row and (row[0].get("rec_out") or "off") != "off":       # umgestellte Telefone zuerst zurueck (sonst erledigt es der Waechter)
        try:
            await outrec.apply(dict(row[0], rec_out="off"))
        except outrec.OutRecError:
            pass
    db.x("UPDATE users SET staff_id=NULL WHERE staff_id=?", (sid,))      # Konto bleibt, nur ohne Mitarbeiter
    db.x("DELETE FROM staff WHERE id=?", (sid,))
    return {"ok": True}


async def run_rec_originate(cid, uuid, device, number, notice=True):
    try:
        ok, reason = await ami.originate(device, uuid, timeout_s=40, context="rec-bridge", variables={"TARGET": number, "NOTICE": "1" if notice else "0"})
    except Exception as e:
        log.exception("AMI-Fehler")
        ok, reason = False, f"Asterisk nicht erreichbar ({type(e).__name__})"
    if not ok:
        db.x("UPDATE calls SET status='nicht erreicht', ended=? WHERE id=?", (time.time(), cid))
        db.add_segment(cid, "system", f"Dein Telefon wurde nicht erreicht: {reason}")
        hub.publish({"type": "end", "call_id": cid})


@app.post("/api/record-call")
async def record_call(request: Request, user: dict = Depends(user_auth)):
    """Gespraech mit Mitschrift: erst klingelt das eigene Geraet, nach dem Abheben waehlt Asterisk das Ziel.
    Benutzer (keine Administratoren) duerfen nur von ihrem eigenen Anschluss aus starten und koennen die Hinweis-Ansage nicht abschalten."""
    d = await request.json()
    if user["role"] != "admin":
        own = _own_device(user)
        if not own:
            raise HTTPException(403, "Dein Konto hat keinen eigenen Anschluss (Benutzer-Eintrag mit Nummer)")
        d["device"], d["notice"], d["mail"], d["mail_free"] = own, True, [], ""        # kein Mailversand an frei waehlbare Adressen
    try:
        device = ami.dial_number(d.get("device", ""))
        number = ami.dial_number(d.get("number", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    audio, trans, notice = bool(d.get("audio", True)), bool(d.get("transcript", True)), bool(d.get("notice", True))
    if not (audio or trans):
        raise HTTPException(400, "Bitte Audio und/oder Transkript wählen")
    ids = {r["id"] for r in db.q("SELECT id FROM mail_recipients WHERE enabled=1")}
    mail = [str(i) for i in sorted({int(x) for x in (d.get("mail") or []) if str(x).isdigit()} & ids)]
    for a in re.split(r"[,;\s]+", str(d.get("mail_free") or "")):        # freie Adressen
        if a:
            if not re.fullmatch(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+", a):
                raise HTTPException(400, f"Ungültige E-Mail-Adresse: {a[:60]}")
            mail.append(a.lower())
    mail = ",".join(dict.fromkeys(mail))
    if notice:
        try:
            await rectrans.ensure_notice()
        except Exception as e:
            log.exception("Hinweis-Ansage fuer Mitschrift nicht erzeugt")
            raise HTTPException(502, f"Hinweis-Ansage konnte nicht erzeugt werden ({type(e).__name__})")
    name = (d.get("name") or "").strip()
    uuid = str(uuid4())
    cid = db.x("INSERT INTO calls(uuid,started,caller,called,agent_name,status,direction,rec_keep,rec_transcribe,rec_mail,user_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
               (uuid, time.time(), f"Mein Telefon ({device})", f"{name} ({number})" if name else number,
                "Mitschrift", "waehlt", "rec", 1 if audio else 0, 1 if trans else 0, mail, user["id"] if user.get("id") else 0))
    hub.publish({"type": "call", "call_id": cid, "status": "waehlt"})
    track(asyncio.create_task(run_rec_originate(cid, uuid, device, number, notice)))
    return {"call_id": cid}


def _out_user_id(sid):
    """Konto des Benutzers (staff.id) fuer calls.user_id: dann sieht er seine ausgehenden Mitschriften (users.level_for)."""
    r = db.q("SELECT id FROM users WHERE staff_id=? AND disabled=0 ORDER BY id LIMIT 1", (sid,)) if sid else []
    return r[0]["id"] if r else 0


def _out_phone_owner(frm):
    """Telefon **NNN zum Absender 99NNN und sein Benutzer (nur wenn genau einer "Klingelt im Buero an" damit hat)."""
    if not re.fullmatch(r"99\d{3}", frm or ""):
        return "", None
    phone = "**" + frm[2:]
    own = [s for s in db.q("SELECT * FROM staff WHERE kind!='group'") if phone in outrec.staff_phones(s)]
    return phone, (own[0] if len(own) == 1 else None)


@app.get("/internal/outcall", response_class=PlainTextResponse)
async def internal_outcall(request: Request, uuid: str, to: str = "", chan: str = ""):
    """Ausgehendes Gespraech eines Telefons ueber Asterisk (Absender 99NNN: Fritzbox-Wahlregel Vorwahl -> 99NNN oder Telefon mit
    ausgehender Nummer 99NNN): Vorwahl entfernen, Ziel pruefen (DIAL_BLOCK), mitschreiben oder normal waehlen. Notrufnummern nie.
    Mit Vorwahl: Ausgangsregeln. Ohne Vorwahl: hat der Benutzer des Telefons "Ausgehende Gespraeche mitschreiben" an und hat die App
    das Telefon dafuer umgestellt (outrec), nach seiner Einstellung; sonst (z. B. Rueckruf aus der Anrufliste) nicht (ausser rec_out_any)."""
    if not internal_ok(request):
        raise HTTPException(403)
    frm = request.query_params.get("from", "")
    if not frm:                                     # aeltere Dialplaene ohne from: Endpoint-Name des Kanals (PJSIP/99NNN-...)
        m = re.match(r"PJSIP/(99\d{3})-", chan or "")
        frm = m.group(1) if m else ""
    CALLS.put(uuid, "chan", {"chan": chan, "to": to, "t": time.time()})
    prefix = astconf.rec_prefix()
    with_prefix = bool(prefix) and to.startswith(prefix)
    num = to[len(prefix):] if with_prefix else to
    try:
        number = ami.dial_number(num)
    except ValueError as e:
        log.warning("Ausgehend: ungueltiges Ziel %s (%s)", logmask.number(to), logmask.text(e))
        return protocol.NO_AGENT
    if number in ("110", "112"):
        return protocol.Dial(number).encode()
    ct = db.find_contact(number)
    phone, owner = _out_phone_owner(frm)
    user = None if with_prefix else outrec.active_owner(frm)
    if user:
        mode = user.get("rec_out") or "off"
        if mode == "numbers" and not any(drules._same(number, n) for n in (user.get("rec_out_numbers") or "").split(",") if n):
            return protocol.Dial(number).encode()      # nur an bestimmte Nummern: diese nicht
        owner = user
        rf = {"record": mode != "transcript", "transcribe": True, "notice": bool(user.get("rec_out_notice", 1)), "stop_cmd": True, "mail": ""}
    elif prefix and not with_prefix and db.get_meta("rec_out_any", "0") != "1":
        # ohne Vorwahl: Rueckruf aus der Anrufliste eines Telefons ueber seine Klingel-Rufnummer 99NNN (der Anruf kam ueber
        # Asterisk herein) oder ein Telefon mit 99NNN als ausgehender Nummer - normal waehlen, nicht aufzeichnen; mitgeschrieben
        # wird nur, wer bewusst die Vorwahl waehlt (Einstellung rec_out_any = 1: auch ohne Vorwahl nach den Ausgangsregeln) oder
        # wessen Benutzer "Ausgehende Gespraeche mitschreiben" eingeschaltet hat (outrec)
        return protocol.Dial(number).encode()
    else:
        rf = agents.rec_flags("", [number, (ct or {}).get("name", "")], db.business_open(), 0, "out")     # Aufzeichnungsregeln fuer ausgehende Gespraeche
    if not (rf["record"] or rf["transcribe"]):
        return protocol.Dial(number).encode()        # keine Regel passt: normal waehlen, nichts aufzeichnen
    quiet = not rf["notice"]
    if not quiet:
        try:
            await rectrans.ensure_notice()
        except Exception:
            log.exception("Hinweis-Ansage nicht erzeugt - ausgehendes Gespraech ohne Aufnahme")
            return protocol.Dial(number).encode()
    caller = f"{owner['name']} ({phone}, ausgehend)" if owner else (f"Telefon {phone} (ausgehend)" if phone else "Telefon (ausgehend)")
    cid = db.x("INSERT INTO calls(uuid,started,caller,called,agent_name,status,direction,rec_keep,rec_transcribe,rec_voicestop,rec_mail,user_id,owner_staff_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (uuid, time.time(), caller, f"{ct['name']} ({number})" if ct else number,
                "Mitschrift", "waehlt", "rec", 1 if rf["record"] else 0, int(bool(rf["transcribe"])), int(bool(rf["stop_cmd"])), rf["mail"],
                _out_user_id(owner["id"]) if owner else 0, owner["id"] if owner else None))
    hub.publish({"type": "call", "call_id": cid, "status": "waehlt"})
    CALLS.phase(uuid, Phase.RINGING)
    flow.add(cid, "ring", targets=number, label="ausgehendes Gespräch" + (f" ({MODE_LABEL.get(user.get('rec_out'), '')})" if user else ""), record=True)
    return protocol.RecX(number, quiet=quiet, outgoing=True).encode()


MODE_LABEL = {"record": "Mitschrift des Benutzers", "transcript": "nur Transkript", "numbers": "Mitschrift an diese Nummer"}


async def voice_stop(cid, uuid, who, text, note=None):
    """Aufzeichnung eines Gespraechs zwischen Menschen beenden: Aufnahme in Asterisk stoppen und verwerfen (Sprachbefehl oder Knopf)."""
    chan = CALLS.field(uuid, "chan", {}).get("chan", "")
    if chan and re.fullmatch(r"[A-Za-z0-9/_@.:+-]+", chan):
        try:
            res, _ = await ami.action("StopMixMonitor", {"Channel": chan})       # AMI-Aktion (Recht call)
            if res.get("Response") != "Success":
                log.warning("Aufzeichnung per Sprachbefehl: MixMonitor nicht gestoppt (%s)", res.get("Message", ""))
        except Exception:
            log.exception("Aufzeichnung per Sprachbefehl: MixMonitor konnte nicht gestoppt werden")
    db.x("UPDATE calls SET rec_stopped=1, rec_keep=0, rec_transcribe=0 WHERE id=?", (cid,))
    db.x("DELETE FROM segments WHERE call_id=? AND speaker IN ('local','remote','mix')", (cid,))
    db.x("DELETE FROM segments WHERE call_id=? AND speaker='system' AND text='Mitschrift des vermittelten Gesprächs'", (cid,))
    side = {"local": "Mitarbeiter", "remote": "Gegenseite"}.get(who, who)
    db.add_segment(cid, "system", note or f"Aufzeichnung auf Wunsch beendet und verworfen (Sprachbefehl der Seite: {side})")
    flow.add(cid, "rec", what="stop")
    if CALLS.field(uuid, "human"):
        CALLS.field(uuid, "human")["rec"] = False
    hub.publish({"type": "ring"})
    hub.publish({"type": "end", "call_id": cid})
    log.info("Aufzeichnung von Anruf %s beendet (%s)", cid, (note or f"Sprachbefehl {side}")[:80])


rectrans.VOICE_STOP = voice_stop


def human_uuid(cid):
    """Anruf-UUID des laufenden Gespraechs zwischen Menschen mit dieser Anruf-ID, sonst None."""
    return next((u for u, h in CALLS.having("human") if h["id"] == cid), None)


def human_add(uuid, rec):
    row = db.q("SELECT id, caller, called, direction, user_id FROM calls WHERE uuid=?", (uuid,))
    if not row:
        return
    CALLS.put(uuid, "human", {"user_id": row[0]["user_id"], "id": row[0]["id"], "chan": CALLS.field(uuid, "chan", {}).get("chan", ""),
                              "caller": row[0]["caller"], "called": row[0]["called"], "direction": row[0]["direction"], "since": time.time(), "rec": rec})
    CALLS.phase(uuid, Phase.CONNECTED)
    hub.publish({"type": "ring"})


async def _moh_reset(chan):
    """Musik war per VOLUME(TX) leiser: Pegel des Anrufers fuer das Gespraech zuruecksetzen (nach der Annahme)."""
    if chan and re.fullmatch(r"[A-Za-z0-9/_@.:+;-]+", chan) and hold.moh_live_db():
        try:
            await ami.action("Setvar", {"Channel": chan, "Variable": "VOLUME(TX)", "Value": "0"})
        except Exception:
            log.exception("Pegel nach Annahme nicht zurueckgesetzt")


@app.get("/internal/moh-db", response_class=PlainTextResponse)
async def internal_moh_db(request: Request):
    """Dialplan: zusaetzliche Daempfung der Wartemusik beim Klingeln in dB (negativ, 0 = aus)."""
    if not internal_ok(request):
        raise HTTPException(403)
    return str(hold.moh_live_divisor())


@app.get("/internal/qos", response_class=PlainTextResponse)
async def internal_qos(request: Request, uuid: str, leg: str = "", q: str = "", cause: str = "", tech: str = "", chan: str = "",
                       to: str = ""):
    """Leitungswerte eines Kanals beim Auflegen (Dialplan [lm-qos], CHANNEL(rtcp,all)) als Ereignis im Ablauf (Fehlersuche).
    Angerufenes Telefon ohne Gespraech (keine RTCP-Werte): Grund, warum sein Klingeln endete (leg_end)."""
    if not internal_ok(request):
        raise HTTPException(403)
    d = linediag.parse_qos(q)
    if d and leg in ("a", "b"):
        flow.add_for(uuid, "qos", leg=leg, **d)
    elif leg == "b" and cause.isdigit():
        # Ziel: to (DIALEDPEERNUMBER ohne @endpoint, z. B. **610 bei Direktwahl ueber PJSIP/fritz-…), sonst aus dem Kanalnamen (99610)
        m = re.match(r"PJSIP/([0-9*]{2,20})-", chan or "")
        tgt = to if re.fullmatch(r"[0-9*#+]{2,24}", to or "") else (m.group(1) if m else "")
        flow.add_for(uuid, "leg_end", target=tgt, cause=int(cause),
                     tech=re.sub(r"[^\w .:/()-]", "", tech or "")[:60], after=flow.since(uuid, ("ring", "transfer")))
    return protocol.OK


@app.get("/internal/answered", response_class=PlainTextResponse)
async def internal_answered(request: Request, uuid: str, chan: str = ""):
    """Jemand hat das klingelnde Telefon abgenommen: Anzeige "klingelt" beenden."""
    if not internal_ok(request):
        raise HTTPException(403)
    if CALLS.take(uuid, "ringing"):
        hub.publish({"type": "ring"})
    flow.add_for(uuid, "answered")
    human_add(uuid, False)
    await _moh_reset(chan)
    # Anruf, der nur an Telefone durchgestellt wurde (die KI hat nicht gesprochen): eine Person hat abgenommen = "angenommen" (nicht "durchgestellt")
    db.x("UPDATE calls SET status='angenommen' WHERE uuid=? AND direction='in' AND status='durchgestellt' "
         "AND NOT EXISTS (SELECT 1 FROM segments WHERE call_id=calls.id AND speaker='agent')", (uuid,))
    return "ok"


@app.get("/internal/spy-target", response_class=PlainTextResponse)
async def internal_spy_target(request: Request, id: str = ""):
    """Dialplan [recspy]: Kanal, in den die Hinweis-Ansage gesprochen werden soll."""
    if not internal_ok(request):
        raise HTTPException(403)
    return SPY.pop(id, "")


@app.get("/internal/rec-start", response_class=PlainTextResponse)
async def rec_start(request: Request, uuid: str, chan: str = "", vchan: str = ""):
    if not internal_ok(request):
        raise HTTPException(403)
    await _moh_reset(vchan)
    now = time.time()
    if chan and re.fullmatch(r"[A-Za-z0-9/_@.:+-]+", chan):      # Kanal fuer "Aufnahme beenden" und "Auflegen" (auch bei "Ich telefoniere")
        ch = CALLS.field(uuid, "chan") or {"to": "", "t": now}
        ch["chan"] = chan
        CALLS.put(uuid, "chan", ch)
    if CALLS.take(uuid, "ringing"):        # abgenommen: Anzeige "klingelt" beenden
        hub.publish({"type": "ring"})
    flow.add_for(uuid, "rec", what="start")
    if not db.get_meta(f"recbase:{uuid}", ""):
        db.set_meta(f"recbase:{uuid}", now)        # Zeitbasis der Spuren = Abnehmen (nicht Anrufbeginn, sonst sind Klingeln und KI-Teil eingerechnet)
    if not (CALLS.field(uuid, "human") or {}).get("rec"):
        human_add(uuid, True)
    db.x("UPDATE calls SET status='aktiv', started=? WHERE uuid=? AND direction='rec'", (now, uuid))
    c = db.q("SELECT id, direction FROM calls WHERE uuid=?", (uuid,))
    if c:
        if c[0]["direction"] == "rec":
            hub.publish({"type": "call", "call_id": c[0]["id"], "status": "aktiv"})
        # Live-Mitschrift (alle ~10 s) fuer die gerade begonnene Aufnahme (nur wenn ein Transkript gewuenscht ist)
        tr = bool(db.q("SELECT 1 FROM calls WHERE id=? AND rec_transcribe=1", (c[0]["id"],)))
        vs = bool(db.q("SELECT 1 FROM calls WHERE id=? AND rec_voicestop=1", (c[0]["id"],)))
        if tr or vs:         # ohne Transkript nur zum Erkennen des Sprachbefehls "keine Aufzeichnung" (nichts wird gespeichert)
            rectrans.start_live(c[0]["id"], uuid, now, store=tr)
    return "ok"


@app.get("/internal/rec-done", response_class=PlainTextResponse)
async def rec_done(request: Request, uuid: str):
    if not internal_ok(request):
        raise HTTPException(403)
    rec = CALLS.get(uuid)
    await rectrans.stop_live(uuid)            # Live-Mitschrift endet mit dem Gespraech (sonst fragt sie bis zu 3 h weiter)
    deferred = bool(rec and rec.defer_post)   # Rueckfrage/Durchstellen nach der KI lief: Auswertung und Mail jetzt, fuer das ganze Gespraech
    was_human, was_ringing, pending = bool(rec and rec.human), bool(rec and rec.ringing), rec.pending if rec else None
    CALLS.finish(uuid)                        # Asterisk hat aufgelegt: aller Zustand dieses Anrufs endet hier (ausser einer noch laufenden KI-Sitzung)
    notify_end = post_call if deferred else late_notify
    db.x("UPDATE calls SET ended=? WHERE uuid=?", (time.time(), uuid))      # Auflegen = Ende des Gespraechs (nicht das Ende der Transkription)
    if was_human:
        hub.publish({"type": "ring"})
    if was_ringing:
        hub.publish({"type": "ring"})
    c = db.q("SELECT id, direction FROM calls WHERE uuid=?", (uuid,))
    if c and pending is None and not was_human and c[0]["direction"] == "in":
        # nur an Telefone durchgestellt, niemand hat abgenommen und die KI hat nicht gesprochen: "nicht angenommen" statt "durchgestellt"
        if db.x_count("UPDATE calls SET status='unbeantwortet' WHERE id=? AND status='durchgestellt' "
                "AND NOT EXISTS (SELECT 1 FROM segments WHERE call_id=calls.id AND speaker='agent')", (c[0]["id"],)):
            flow.add(c[0]["id"], "unanswered")
    if c and pending is not None:
        flow.add(c[0]["id"], "unanswered")
    if c:
        flow.add(c[0]["id"], "hangup")
    if c and pending is not None:             # AudioSocket kam nie zustande: nicht angenommen
        db.x("UPDATE calls SET status='unbeantwortet', ended=? WHERE id=?", (time.time(), c[0]["id"]))
        db.add_segment(c[0]["id"], "system", "Nicht angenommen: der Anrufer hat vorher aufgelegt oder ein anderes Gerät hat abgenommen")
        hub.publish({"type": "end", "call_id": c[0]["id"]})
        track(asyncio.create_task(notify_end(c[0]["id"])))
        return "ok"
    if c:
        f = rectrans.files(uuid)
        # Mitschrift-Anruf (immer) oder normaler Anruf, bei dem nach der Weiterleitung aufgenommen wurde
        if db.q("SELECT 1 FROM calls WHERE id=? AND rec_stopped=1", (c[0]["id"],)):
            # Aufzeichnung auf Wunsch beendet: Audio loeschen, kein Transkript
            await asyncio.to_thread(rectrans.finish_audio, c[0]["id"], uuid)
            db.x("UPDATE calls SET status=?, ended=? WHERE id=?", (db.end_status(c[0]["id"]), time.time(), c[0]["id"]))
            hub.publish({"type": "end", "call_id": c[0]["id"]})
            track(asyncio.create_task(notify_end(c[0]["id"])))
        elif c[0]["direction"] != "rec" and db.q("SELECT 1 FROM segments WHERE call_id=? AND speaker='agent' LIMIT 1", (c[0]["id"],)):
            if any(os.path.exists(f[k]) for k in ("local", "remote")) and db.q("SELECT 1 FROM calls WHERE id=? AND rec_transcribe=1", (c[0]["id"],)):
                # Die KI hat gesprochen und danach ein aufgezeichnetes Gespraech vermittelt: dessen Spuren transkribieren
                # (das Audio wird dabei an die KI-Aufnahme angehaengt), dann Auswertung und Mail
                track(asyncio.create_task(transcribe_then_notify(c[0]["id"], uuid, "in", notify_end=notify_end)))
            else:
                # Die KI hat (nach dem Klingeln) selbst gesprochen: das Gespraech ist schon als Transkript da, nur das Audio ablegen
                track(asyncio.create_task(_finish_audio_then(c[0]["id"], uuid, notify_end=notify_end)))
        elif db.q("SELECT 1 FROM calls WHERE id=? AND rec_transcribe=0", (c[0]["id"],)):
            # nur Audio gewuenscht, kein Transkript
            cid = c[0]["id"]

            def _after_audio_only(cid=cid):
                db.add_segment(cid, "system", "Aufgezeichnet (ohne Transkript)")
                db.x("UPDATE calls SET status=?, ended=? WHERE id=?", (db.end_status(cid), time.time(), cid))
                hub.publish({"type": "end", "call_id": cid})
            track(asyncio.create_task(_finish_audio_then(cid, uuid, _after_audio_only, notify_end=notify_end)))
        elif c[0]["direction"] == "rec" or any(os.path.exists(f[k]) for k in ("local", "remote")):
            track(asyncio.create_task(transcribe_then_notify(c[0]["id"], uuid, c[0]["direction"])))
        elif c[0]["direction"] == "in" and not CALLS.field(uuid, "active"):
            track(asyncio.create_task(notify_end(c[0]["id"])))
    return "ok"


async def _finish_audio_then(cid, uuid, after=None, notify_end=None):
    """Aufnahme ablegen (und ggf. an die KI-Aufnahme anhaengen), dann benachrichtigen. Laeuft im Hintergrund: Asterisk wartet beim Auflegen
    auf die Antwort von /internal/rec-done und schliesst die Mix-Datei erst danach, sie ist waehrend der Anfrage also noch nicht lesbar.
    notify_end: post_call (nach einer Rueckfrage: Auswertung und Mail fuer das ganze Gespraech) oder late_notify (Standard)."""
    notify_end = notify_end or late_notify
    try:
        mix = rectrans.files(uuid)["mix"]
        if os.path.exists(mix):
            await asyncio.to_thread(mp3.wait_wav, mix, 60, 1.0)
        await asyncio.to_thread(rectrans.finish_audio, cid, uuid)
    except Exception:
        log.exception("Aufnahme von Anruf %s nicht abgelegt", cid)
    db.x("DELETE FROM meta WHERE key=?", (f"recbase:{uuid}",))
    if after:
        try:
            after()
        except Exception:
            log.exception("Nachbearbeitung von Anruf %s fehlgeschlagen", cid)
    await notify_end(cid)


@app.post("/api/calls/{cid}/retranscribe", dependencies=[Depends(auth)])
async def retranscribe(cid: int):
    c = db.q("SELECT uuid, direction, status FROM calls WHERE id=? AND direction IN ('rec','in')", (cid,))
    if not c:
        raise HTTPException(404, "Kein Anruf mit Mitschrift")
    if c[0]["status"] in ("aktiv", "waehlt", "transkribiere"):
        raise HTTPException(409, "Das Gespräch läuft noch oder wird gerade transkribiert")
    f = rectrans.files(c[0]["uuid"])
    if not any(os.path.exists(f[k]) for k in ("local", "remote")):
        # nach einer erfolgreichen Transkription sind die Spuren geloescht: nicht das vorhandene Transkript durch "keine Sprache" ersetzen
        raise HTTPException(409, "Die Rohaufnahme ist nicht mehr vorhanden")
    track(asyncio.create_task(transcribe_then_notify(cid, c[0]["uuid"], c[0]["direction"])))
    return {"ok": True}


@app.get("/api/templates", dependencies=[Depends(auth)])
def get_templates():
    return db.list_templates()


@app.post("/api/templates", dependencies=[Depends(auth)])
async def new_template(request: Request):
    d = await request.json()
    if not (d.get("name") or "").strip() or not (d.get("task") or "").strip():
        raise HTTPException(400, "Name und Auftrag erforderlich")
    return {"id": db.save_template(d)}


@app.put("/api/templates/{tid}", dependencies=[Depends(auth)])
async def put_template(tid: int, request: Request):
    d = await request.json()
    if not (d.get("name") or "").strip() or not (d.get("task") or "").strip():
        raise HTTPException(400, "Name und Auftrag erforderlich")
    return {"id": db.save_template(d, tid)}


@app.delete("/api/templates/{tid}", dependencies=[Depends(auth)])
def del_template(tid: int):
    db.x("DELETE FROM templates WHERE id=?", (tid,))
    return {"ok": True}


@app.get("/api/settings", dependencies=[Depends(auth)])
def get_settings():
    rules = db.global_rules()
    state = db.business_state()
    return {"budget_eur": float(db.get_meta("budget_eur", "0") or 0),
            "base_prompt": db.get_meta("base_prompt", ""),
            "schedule": rules, "schedule_desc": schedule.describe(rules) if rules else "",
            "open_now": state["open"] if state["configured"] else None, "state": state,
            "closed_text": db.get_meta("closed_text", ""),
            "screen_ring": int(db.get_meta("screen_ring", "25") or 25), "screen_max": int(db.get_meta("screen_max", "45") or 45),
            "screen_wait_text": db.get_meta("screen_wait_text", ""), "screen_ai_name": db.get_meta("screen_ai_name", ""), "screen_voice": db.get_meta("screen_voice", ""), "detail_model": db.get_meta("detail_model", "") or models.DETAIL["id"], "vertex": {**gclient.settings(), "key_file": gclient.key_file_found()}, "transcribe_model": models.transcribe_model(db.get_meta("transcribe_model", ""))[0], "transcribe_models": [{"id": i, "label": l} for i, l, _ in models.TRANSCRIBE_CHOICES], "detail_models": [{"id": i, "label": l} for i, l in models.DETAIL_CHOICES], "screen_pace": db.get_meta("screen_pace", ""), "screen_extra": db.get_meta("screen_extra", ""), "screen_style": db.get_meta("screen_style", ""),
            "ring_music": db.get_meta("ring_music", "0") == "1", "ring_music_after": hold.ring_after(),
            "screen_music": db.get_meta("screen_music", "1") != "0", "ring_delay": int(db.get_meta("ring_delay", "0") or 0), "moh_volume": hold.moh_volume(), "moh_live_db": hold.moh_live_db(), "ai_volume": aiaudio.ai_volume(), "ring_cid": db.get_meta("ring_cid", ""),
            "ring_track": db.get_meta("ring_track", ""), "screen_track": db.get_meta("screen_track", ""),
            **costguard.settings(),
            "users_phonebook": db.get_meta("users_phonebook", "0") == "1",
            "keep_audio": db.get_meta("keep_audio", "0") == "1", "mp3_kbps": mp3.bitrate(), "ring_all": astconf.ring_all(), "ring_lines": db.get_meta("ring_lines", ""), "rec_prefix": db.get_meta("rec_prefix", "*77"), "rec_out_any": db.get_meta("rec_out_any", "0") == "1", "rec_notice": db.get_meta("rec_notice", "1") != "0"}


@app.put("/api/settings", dependencies=[Depends(auth)])
async def put_settings(request: Request):
    """Teilweise Aktualisierung: nur mitgeschickte Felder werden geaendert."""
    d = await request.json()
    ring_all_changed, ring_all_warn = False, ""
    if "budget_eur" in d:
        try:
            db.set_meta("budget_eur", max(0.0, float(d["budget_eur"])))
        except (TypeError, ValueError):
            raise HTTPException(400, "Ungueltiger Betrag")
    if "base_prompt" in d:
        db.set_meta("base_prompt", str(d["base_prompt"] or "").strip()[:4000])
    if "schedule" in d:
        try:
            rules = schedule.normalize(d["schedule"] or [])
        except ValueError as e:
            raise HTTPException(400, f"Zeitplan: {e}")
        db.set_meta("schedule", schedule.dumps(rules))
        db.set_meta("hours", "")                 # alter Text ist damit abgeloest
    if "closed_text" in d:
        db.set_meta("closed_text", str(d["closed_text"] or "").strip()[:1000])
    if "keep_audio" in d:
        db.set_meta("keep_audio", "1" if d["keep_audio"] else "0")
    for key, hi, label in (("limit_calls_h", 1000, "Anrufe je Nummer und Stunde"), ("limit_min_day", 1440, "Minuten je Nummer und Tag"),
                           ("ai_min_day", 100000, "KI-Minuten pro Tag")):          # Kostenbremse, 0 = aus
        if key in d:
            try:
                v = int(d[key] or 0)
            except (TypeError, ValueError):
                v = -1
            if not 0 <= v <= hi:
                raise HTTPException(400, f"{label}: 0 (aus) bis {hi}")
            db.set_meta(key, str(v))
    if "ring_all" in d:
        r = str(d["ring_all"] or "").strip()
        if r and not re.fullmatch(r"(\*\*)?\d{1,12}(\+(\*\*)?\d{1,12})*", r):
            raise HTTPException(400, "Klingel-Rufnummer: z. B. 99613 (mehrere mit +), leer = Fritzbox klingelt selbst")
        prev_ra = astconf.ring_all_lines()
        db.set_meta("ring_all", r)
        ring_all_changed = astconf.ring_all_lines() != prev_ra
        new_ra = [n for n in astconf.ring_all_lines() if n not in prev_ra]
        if new_ra:
            ring_all_warn = await _ring_all_missing(new_ra)
    ring_changed = ring_all_changed
    if "ring_lines" in d:
        try:
            rl = ",".join(fritzconf.clean_ring_lines(d["ring_lines"]))
        except ValueError as e:
            raise HTTPException(400, f"Klingel-Rufnummern je Telefon: {e}")
        ring_changed = ring_changed or rl != db.get_meta("ring_lines", "")
        db.set_meta("ring_lines", rl)
    if "rec_prefix" in d:
        p = str(d["rec_prefix"] or "").strip()
        if p and not re.fullmatch(r"[0-9*#]{1,8}", p):
            raise HTTPException(400, "Vorwahl: 1 bis 8 Zeichen aus Ziffern, * und #")
        changed = p != astconf.rec_prefix()
        db.set_meta("rec_prefix", p)
        if changed:
            await astconf.apply_globals()           # der Dialplan braucht die Vorwahl auch, wenn die Bridge ausfaellt (globals.conf)
    if "rec_out_any" in d:
        db.set_meta("rec_out_any", "1" if d["rec_out_any"] else "0")
    if "mp3_kbps" in d:
        try:
            k = int(d["mp3_kbps"])
        except (TypeError, ValueError):
            k = 0
        if k not in mp3.BITRATES:
            raise HTTPException(400, "Bitrate nicht unterstützt")
        db.set_meta("mp3_kbps", str(k))
    if "rec_notice" in d:
        db.set_meta("rec_notice", "1" if d["rec_notice"] else "0")
    if "ring_music" in d:
        db.set_meta("ring_music", "1" if d["ring_music"] else "0")
    if "users_phonebook" in d:          # Vorschlaege aus dem Telefonbuch auch fuer eingeschraenkte Konten (Nummernfelder)
        db.set_meta("users_phonebook", "1" if d["users_phonebook"] else "0")
    if "ai_volume" in d:
        try:
            v = int(d["ai_volume"])
        except (TypeError, ValueError):
            v = -1
        if not 30 <= v <= 100:
            raise HTTPException(400, "Lautstärke der KI-Stimme: 30 bis 100 Prozent")
        db.set_meta("ai_volume", str(v))
    if "moh_volume" in d:
        try:
            v = int(d["moh_volume"])
        except (TypeError, ValueError):
            v = -1
        if not 10 <= v <= 100:
            raise HTTPException(400, "Lautstärke: 10 bis 100 Prozent")
        db.set_meta("moh_volume", str(v))
        await asyncio.to_thread(hold.rebuild_classes)
        await hold.reload_moh()
    if "moh_live_db" in d:
        try:
            v = int(d["moh_live_db"])
        except (TypeError, ValueError):
            v = -1
        if not 0 <= v <= 30:
            raise HTTPException(400, "Zusatzdämpfung: 0 bis 30 dB")
        db.set_meta("moh_live_db", str(v))
    if "ring_delay" in d:
        try:
            v = int(d["ring_delay"])
        except (TypeError, ValueError):
            v = -1
        if not 0 <= v <= 10:
            raise HTTPException(400, "Verzögerung: 0 bis 10 Sekunden")
        db.set_meta("ring_delay", str(v))
    if "ring_cid" in d:
        if d["ring_cid"] not in ("", "plus", "zz"):
            raise HTTPException(400, "Schreibweise: Original, +49 oder 0049")
        db.set_meta("ring_cid", d["ring_cid"])
    if "screen_music" in d:
        db.set_meta("screen_music", "1" if d["screen_music"] else "0")
    for key in ("ring_track", "screen_track"):                   # "" = alle eingeschalteten zufaellig, sonst ein bestimmtes Stueck
        if key in d:
            v = str(d[key] or "")
            if v and v not in {t["id"] for t in hold.list_tracks()}:
                raise HTTPException(400, "Unbekanntes Stück")
            db.set_meta(key, v)
    if "ring_music_after" in d:
        try:
            n = int(d["ring_music_after"])
        except (TypeError, ValueError):
            n = -1
        if not 0 <= n <= 8:
            raise HTTPException(400, "Musik nach: 0 bis 8 Freizeichen-Tönen")
        db.set_meta("ring_music_after", str(n))
        await asyncio.to_thread(hold.rebuild_classes)
        await hold.reload_moh()
    for key, lo, hi, label in (("screen_ring", 10, 60, "Klingelzeit"), ("screen_max", 20, 120, "Dauer der Rückfrage")):
        if key in d:
            try:
                v = int(d[key])
            except (TypeError, ValueError):
                v = 0
            if not lo <= v <= hi:
                raise HTTPException(400, f"{label}: {lo} bis {hi} Sekunden")
            db.set_meta(key, str(v))
    if "screen_extra" in d:
        db.set_meta("screen_extra", re.sub(r"[\x00-\x08\x0b-\x1f]", " ", str(d["screen_extra"] or "")).strip()[:600])
    if "screen_pace" in d:
        if d["screen_pace"] not in ("", "slow", "fast"):
            raise HTTPException(400, "Sprechtempo: normal, langsam oder zügig")
        db.set_meta("screen_pace", d["screen_pace"])
    if "screen_style" in d:
        db.set_meta("screen_style", str(d["screen_style"] or "").strip()[:160])
    if "vertex_scope" in d or "vertex_project" in d or "vertex_location" in d:
        sc = str(d.get("vertex_scope", gclient.settings()["scope"]) or "")
        pr = str(d.get("vertex_project", gclient.settings()["project"]) or "").strip()
        lo = str(d.get("vertex_location", gclient.LOCATION) or "").strip()
        if lo != gclient.LOCATION:
            raise HTTPException(400, "Nur der Standort eu ist erlaubt (Datenverarbeitung in der EU)")
        if sc not in gclient.SCOPES:
            raise HTTPException(400, "Unbekannte Auswahl")
        if pr and not gclient.PROJECT_RE.match(pr):
            raise HTTPException(400, "Projekt-ID: Kleinbuchstaben, Ziffern und Bindestriche (6 bis 30 Zeichen)")
        if sc and not (pr and gclient.key_file_found()):
            raise HTTPException(400, "Vertex AI braucht eine Projekt-ID und die Schlüsseldatei auf dem Server (data/vertex.json)")
        db.set_meta("vertex_project", pr)
        db.set_meta("vertex_scope", sc)
    if "transcribe_model" in d:
        if d["transcribe_model"] not in [i for i, _, _ in models.TRANSCRIBE_CHOICES]:
            raise HTTPException(400, "Unbekanntes Modell")
        db.set_meta("transcribe_model", d["transcribe_model"])
    if "detail_model" in d:
        if d["detail_model"] not in [i for i, _ in models.DETAIL_CHOICES]:
            raise HTTPException(400, "Unbekanntes Modell")
        db.set_meta("detail_model", d["detail_model"])
    if "screen_voice" in d:
        v = str(d["screen_voice"] or "")
        if v and v not in voices.VOICES:
            raise HTTPException(400, "Unbekannte Stimme")
        db.set_meta("screen_voice", v)
    if "screen_ai_name" in d:
        nm = re.sub(r"[^\w .'-]", "", str(d["screen_ai_name"] or ""), flags=re.U).strip()[:30]       # nur ein Name, keine Anweisungen im Prompt
        db.set_meta("screen_ai_name", nm)
    if "screen_wait_text" in d:
        db.set_meta("screen_wait_text", str(d["screen_wait_text"] or "").strip()[:300])
    if ring_changed:
        # Asterisk muss die Nummer(n) fuer "alle Telefone klingeln" kennen (AOR + Endpoint in fritz.conf, astconf.ring_lines)
        ring_all_warn = " ".join(w for w in (ring_all_warn, await ring_apply()) if w)
    if ring_all_warn:
        return dict(get_settings(), warn=ring_all_warn)
    return get_settings()


async def _ring_all_missing(nums):
    """Hinweis, wenn Klingel-Rufnummern fuer "alle Telefone klingeln" in der Fritzbox nicht angelegt sind ('' = alles da oder nicht lesbar)."""
    try:
        have = {x["number"] for x in await asyncio.wait_for(asyncio.to_thread(fritz.ring_numbers, True), 15)}
    except Exception as e:
        log.info("Klingel-Rufnummern der Fritzbox nicht lesbar (%s)", type(e).__name__)
        return ""
    miss = [n for n in nums if n not in have]
    if not miss:
        return ""
    return (f"Gespeichert, aber {', '.join(miss)} ist in der Fritzbox nicht angelegt – dort klingelt nichts. Unter Telefone → "
            "Klingel-Rufnummern einschalten (legt sie an) und in der Fritzbox allen Telefonen zuweisen, die klingeln sollen.")


async def ring_apply():
    """Klingel-Rufnummern (meta ring_lines/ring_ok) nach fritz.conf schreiben und PJSIP neu laden (laufende Gespraeche bleiben bestehen).
    Rueckgabe: '' oder ein Warnhinweis (nie eine Ausnahme: die Einstellung selbst ist gespeichert)."""
    if not astconf.db_creds():
        return ""
    try:
        if await asyncio.to_thread(astconf.write_fritz):
            await ami.command("module reload res_pjsip.so")
    except Exception as e:
        log.warning("Klingel-Rufnummern: PJSIP nicht neu geladen (%s)", type(e).__name__)
        return "Gespeichert, aber Asterisk hat die Klingel-Rufnummern noch nicht übernommen (wird beim nächsten Start nachgeholt)."
    return ""


@app.get("/api/hold-tracks", dependencies=[Depends(auth)])
def hold_tracks():
    return hold.list_tracks()


@app.post("/api/hold-tracks", dependencies=[Depends(auth)])
async def hold_upload(request: Request):
    """Wartemusik hochladen (JSON: name, data = Base64 der Datei)."""
    d = await request.json()
    try:
        raw = base64.b64decode(str(d.get("data") or ""), validate=True)
        t = await asyncio.to_thread(hold.add_track, d.get("name"), raw)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    await asyncio.to_thread(hold.rebuild_classes)
    await hold.reload_moh()
    return {"ok": True, "track": t, "tracks": hold.list_tracks()}


@app.put("/api/hold-tracks/{tid}", dependencies=[Depends(auth)])
async def hold_track_put(tid: str, request: Request):
    d = await request.json()
    try:
        hold.set_active(tid, bool(d.get("active")))
    except KeyError:
        raise HTTPException(404, "Unbekanntes Stück")
    except ValueError as e:
        raise HTTPException(400, str(e))
    await asyncio.to_thread(hold.rebuild_classes)
    await hold.reload_moh()
    return hold.list_tracks()


@app.delete("/api/hold-tracks/{tid}", dependencies=[Depends(auth)])
async def hold_track_delete(tid: str):
    try:
        hold.delete_track(tid)
    except KeyError:
        raise HTTPException(404, "Unbekanntes Stück")
    except ValueError as e:
        raise HTTPException(400, str(e))
    await asyncio.to_thread(hold.rebuild_classes)
    await hold.reload_moh()
    return hold.list_tracks()


@app.get("/api/hold-music/{tid}", dependencies=[Depends(auth)])
def hold_preview(tid: str):
    p = hold.track_file(tid) if re.fullmatch(r"[0-9a-f]{8}|melodie", tid) else None
    if not p:
        raise HTTPException(404, "Keine Datei vorhanden")
    return FileResponse(p, media_type="audio/wav")


FRITZ_ST = {"t": 0.0, "v": "unknown"}


async def fritz_state():
    """Ist Asterisk bei der Fritzbox angemeldet? ('ok' / 'down' / 'unknown'), alle 15 s neu gelesen."""
    if time.time() - FRITZ_ST["t"] < 15:
        return FRITZ_ST["v"]
    FRITZ_ST["t"] = time.time()
    try:
        st = await fritz_reg_status(4)
        FRITZ_ST["v"] = "ok" if st == "Registered" else ("down" if st else "unknown")
    except Exception:
        FRITZ_ST["v"] = "unknown"
    return FRITZ_ST["v"]


@app.get("/api/status")
async def status(user: dict = Depends(user_auth)):
    if user["role"] != "admin":
        return {"alert": None, "usd_eur": 0, "usd_eur_date": "", "active": 0, "model": "", "key": True, "role": "user"}
    r, d = rate.get()
    try:
        alert = json.loads(db.get_meta("api_alert", "") or "null")
    except ValueError:
        alert = None
    return {"role": "admin", "alert": alert, "usd_eur": r, "usd_eur_date": d, "active": len(active_calls()), "model": models.DEFAULT,
            "key": gclient.ready("text") and gclient.ready("live"), "access": gclient.settings()["scope"] or "key",
            "fritz": await fritz_state()}


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    origin = ws.headers.get("origin")
    if origin and urlparse(origin).netloc != ws.headers.get("host", ""):
        await ws.close(code=4403)
        return
    ip = ws.client.host if ws.client else "?"
    tok = ws.cookies.get(SESSION_COOKIE)
    u = users.session_user(tok) if tok else None
    if not u and ALLOW_BASIC:
        try:
            usr, _, pw = base64.b64decode(ws.headers.get("authorization", "").split(" ", 1)[1]).decode().partition(":")
        except Exception:
            usr = pw = ""
        u = PSEUDO_ADMIN if check_basic(usr, pw) else None
    if not u or u["role"] != "admin" or blocked(ip):
        if not u and ws.headers.get("authorization"):          # nur echte Basic-Fehlversuche zaehlen, nicht den Wiederverbindungsversuch eines Tabs ohne Sitzung
            throttle(ip, False)
        await ws.close(code=4401)
        return
    await ws.accept()
    q = hub.subscribe()
    try:
        while True:
            await ws.send_json(await q.get())
    except Exception:
        pass
    finally:
        hub.unsubscribe(q)


def _page(name):
    # no-cache: der Browser fragt jedes Mal nach (ETag), zeigt also nie eine veraltete Seite
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", name), headers={"Cache-Control": "no-cache"})


@app.get("/favicon.svg", include_in_schema=False)
@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "favicon.svg"), media_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/")
def index(request: Request):
    # Die Seite selbst nur mit Formular-Anmeldung (Basic-Daten, die der Browser noch gespeichert hat, zaehlen hier nicht)
    if not current_user(request, basic=False):
        return RedirectResponse("/login", status_code=302)
    return _page("index.html")


@app.get("/help.js", include_in_schema=False)
def help_js(request: Request):
    """Inhalt der Hilfe-Seite (nur angemeldet)."""
    if not current_user(request, basic=False):
        return RedirectResponse("/login", status_code=302)
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "help.js"), media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/login")
def login_page(request: Request):
    if current_user(request, basic=False):
        return RedirectResponse("/", status_code=302)
    return _page("login.html")


async def prepare_notice():
    try:
        await rectrans.ensure_notice()
    except Exception as e:
        log.warning("Mitschrift-Ansage nicht erzeugt: %s", type(e).__name__)


async def recover_recordings():
    """Nach einem Neustart: Mitschrift-Anrufe zu Ende bringen (Aufnahme vorhanden -> transkribieren)."""
    for c in db.q("SELECT id, uuid, status, direction FROM calls WHERE (direction='rec' AND status IN ('waehlt','aktiv','transkribiere','transkription fehlgeschlagen')) "
                  "OR (direction='in' AND status IN ('transkribiere','transkription fehlgeschlagen'))"):
        f = rectrans.files(c["uuid"])
        recent = any(os.path.exists(p) and time.time() - os.path.getmtime(p) < 30 for p in f.values())
        if any(os.path.exists(f[k]) for k in ("local", "remote")) and not recent:
            track(asyncio.create_task(transcribe_then_notify(c["id"], c["uuid"], c["direction"])))
        elif c["direction"] == "in":
            if not recent:                    # keine Spuren mehr: Status aus dem Ablauf ableiten statt "transkribiere" stehen zu lassen
                db.x("UPDATE calls SET status=? WHERE id=?", (db.end_status(c["id"]), c["id"]))
        elif c["status"] in ("waehlt",) or not recent and not any(os.path.exists(p) for p in f.values()):
            db.x("UPDATE calls SET status='abgebrochen', ended=started WHERE id=?", (c["id"],))




def purge_old():
    cutoff = time.time() - config.retention_days() * 86400
    for r in db.q("SELECT id FROM calls WHERE started < ? AND status NOT IN ('aktiv','waehlt') AND archived=0", (cutoff,)):
        remove_call(r["id"])


RAW_MAX_DAYS = 14                        # Rohspuren in rec-in (nur fuer "erneut transkribieren"): hoechstens so lange, auch ohne Aufbewahrungsfrist
VOICE_CACHE_MAX = 200 * 1024 * 1024      # Stimmproben/Ansagen-Cache: darueber die aeltesten Dateien loeschen (werden bei Bedarf neu erzeugt)


def housekeeping():
    """Etwa stuendlich (Janitor): abgelaufene Sitzungen, alte Rohaufnahmen in rec-in (nur mit Aufbewahrungsfrist,
    aelter als die Frist), Stimmproben-Cache nur, wenn er zu gross wird."""
    now = time.time()
    db.x("DELETE FROM sessions WHERE expires < ? OR created < ?", (now, now - users.SESSION_MAX_REMEMBER))
    days = config.retention_days()
    raw_days = min(days, RAW_MAX_DAYS) if days else RAW_MAX_DAYS
    if raw_days:
        # laufende und archivierte Anrufe (Archiv = von der Loeschung ausgenommen, z. B. fuer "neu transkribieren") behalten ihre Dateien
        busy = {r["uuid"] for r in db.q("SELECT uuid FROM calls WHERE status IN ('waehlt','aktiv','transkribiere') OR archived=1")}
        try:
            names = os.listdir(rectrans.REC_IN)
        except OSError:
            names = []
        for n in names:
            p = os.path.join(rectrans.REC_IN, n)
            try:
                if n[:36] not in busy and os.path.isfile(p) and now - os.path.getmtime(p) > raw_days * 86400:
                    os.remove(p)
                    log.info("Aufraeumen: alte Rohaufnahme %s geloescht", n[:36])
            except OSError:
                pass
    try:
        files = [(os.path.getmtime(p), os.path.getsize(p), p) for p in
                 (os.path.join(voices.CACHE, n) for n in os.listdir(voices.CACHE)) if os.path.isfile(p)]
    except OSError:
        files = []
    total = sum(s for _, s, _ in files)
    if total > VOICE_CACHE_MAX:
        for _, s, p in sorted(files):
            if total <= VOICE_CACHE_MAX * 3 // 4:
                break
            try:
                os.remove(p)
                total -= s
            except OSError:
                pass
        log.info("Aufraeumen: Stimmproben-Cache auf %d MB verkleinert", total // (1024 * 1024))


def remove_call(cid):
    """Anruf loeschen samt aller Dateien: Aufnahme, Nebendateien (z. B. call-N-gespraech.mp3, wenn das Zusammenfuegen scheiterte)
    und Transkript-Sicherungen; sonst unterliefen sie die Aufbewahrungsfrist."""
    rec = db.delete_call(cid)
    base = os.path.dirname(db.DB_PATH)
    pats = [os.path.join(base, "rec", f"call-{cid}.*"), os.path.join(base, "rec", f"call-{cid}-*"),
            os.path.join(base, "backups", f"call-{cid}-*.json")]
    paths = {rec} if rec else set()
    for p in pats:
        paths.update(glob.glob(p))
    for p in paths:
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            log.warning("Aufraeumen: %s nicht geloescht", os.path.basename(p))


async def janitor():
    """Angemeldete Anrufe, deren Audioverbindung nie kam; alte Nachlauf-Eintraege; Aufbewahrungsfrist."""
    tick = 0
    while True:
        await asyncio.sleep(15)
        tick += 1
        await janitor_tick(tick)


async def janitor_tick(tick, now=None):
    """Ein Durchlauf des Aufraeumers (alle 15 s)."""
    expired = CALLS.expire(now)          # alle Ablaufzeiten des Anrufzustands an einer Stelle (callstate.TTL)
    if config.retention_days() and tick % 240 == 1:      # etwa stuendlich
        await asyncio.to_thread(purge_old)
    if tick % 240 == 2:                                  # etwa stuendlich: Sitzungen, rec-in, Stimmproben-Cache
        try:
            await asyncio.to_thread(housekeeping)
        except Exception:
            log.exception("Aufraeumen fehlgeschlagen")
    for uid, name, meta in expired:
        if name == "pending":          # KI angemeldet, aber die Audioverbindung kam nie
            db.x("UPDATE calls SET status='abgebrochen', ended=started WHERE id=? AND status IN ('aktiv','waehlt')",
                 (meta["call_id"],))
            await plugins.emit("call_unreached", meta, "keine Verbindung")
            hub.publish({"type": "end", "call_id": meta["call_id"]})
    return expired


async def main():
    db.conn()
    config.apply()
    users.ensure_admin()
    # Reste von Anrufen, die beim Neustart noch liefen
    # Ende = letzter Satz im Transkript (nicht der Neustart-Zeitpunkt), sonst der Beginn
    db.x("UPDATE calls SET status='abgebrochen', ended=COALESCE("
         "(SELECT MAX(ts) FROM segments WHERE call_id=calls.id), started) "
         "WHERE status IN ('aktiv','waehlt') AND direction!='rec'")
    await recover_recordings()
    # Asterisk-Dateien aus der Datenbank (Migrationen adopt/upgrade, dann gewinnt die Datenbank), siehe astconf.py
    _, reload_task = await astconf.startup()
    if reload_task:
        track(reload_task)
    track(astconf.startup_globals())            # Vorwahl fuer den Dialplan (globals.conf, Setvar)
    track(asyncio.create_task(prepare_notice()))
    for a in db.list_agents(enabled_only=True):
        if a["mode"] == "assist":
            track(asyncio.create_task(warm_agent(a)))
    track(asyncio.create_task(rate.refresher()))
    track(asyncio.create_task(fritz.refresh_numbers()))
    track(asyncio.create_task(fritz.refresh_phones()))
    track(asyncio.create_task(presence.loop(lambda ch: hub.publish({"type": "staff"}))))      # WLAN-Anwesenheit: nicht erreichbar <-> nicht stoeren
    track(asyncio.create_task(outrec.loop()))          # ausgehende Mitschrift: 99NNN angemeldet? sonst Notbetrieb (bisherige Nummer)
    try:
        await asyncio.to_thread(hold.rebuild_classes)     # Wartemusik: eingebaute Melodie, Klassen und moh.conf fuer Asterisk
    except Exception:
        log.exception("Wartemusik: eingebaute Melodie konnte nicht angelegt werden (Rueckfrage laeuft ohne Musik)")
    track(asyncio.create_task(hold.reload_when_ready()))
    import types as _t
    plugins.load_all(_t.SimpleNamespace(start_outbound=start_outbound, dial_number=ami.dial_number),
                     os.path.dirname(os.environ.get("DB_PATH", "/data/lieschen.db")))
    plugins.mount(app, auth)
    await plugins.emit("startup")
    plugins.start_tasks(track)
    track(asyncio.create_task(notify.scheduler()))
    track(asyncio.create_task(janitor()))
    srv = await asyncio.start_server(audiosocket, "127.0.0.1", 9092)
    cfg = uvicorn.Config(app, host="0.0.0.0", port=int(os.environ.get("WEB_PORT", "8080")), log_level="info",
                       timeout_graceful_shutdown=3)   # offene SSE-Verbindungen sollen den Neustart nicht blockieren
    async with srv:
        await uvicorn.Server(cfg).serve()


if __name__ == "__main__":
    asyncio.run(main())
