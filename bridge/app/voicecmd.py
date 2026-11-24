"""Sprachbefehle von aussen (z. B. Home Assistant mit Alexa): eine kleine API mit eigenem Schluessel.

Zwei Ablaeufe, beide beginnen am Telefon im Haus (Geraet, interne Nummer **NNN):
  - verbinden:  das Geraet klingelt; nach dem Abheben waehlt Asterisk das Ziel (Nummer, Kontakt oder Mitarbeiter). Ohne KI.
  - assistent:  das Geraet klingelt; nach dem Abheben meldet sich die KI, fragt, mit wem verbunden werden soll, und verbindet.

Schutz: ohne gesetzten Schluessel und Schalter ist die API aus. Der Schluessel liegt nur als SHA-256 in der Datenbank
(meta vc_key_hash) und wird beim Erzeugen genau einmal angezeigt. Geraete sind nur interne Nummern; optional eine feste Liste.
Ziele laufen durch dieselbe Pruefung wie alle ausgehenden Anrufe (ami.dial_number: DIAL_BLOCK, keine Notrufnummern).
Jedes Geraet hoechstens einmal je MIN_GAP Sekunden (falls eine Automation in einer Schleife haengt).

Geraetenamen (meta vc_names, "Headset 1=**610"): Home Assistant/Alexa darf das Geraet mit Namen nennen.
Status je Anruf (nur im Speicher, STATES): klingelt -> abgenommen (verbinden) bzw. ki (assistent) -> verbunden -> beendet,
oder nicht_erreicht mit Grund. Jede Aenderung geht als Hub-Ereignis {"type": "vc"} an /api/voice/events (Server-Sent Events)."""
import hashlib
import logging
import hmac
import re
import secrets
import time

from . import ami, db, outrec
from .events import hub

MIN_GAP = 20
DEV = r"\*\*\d{1,4}"
MAX_DEVS = 6          # so viele Telefone klingeln hoechstens gleichzeitig


def devs_of(raw):
    """"**610+**611" bzw. "**610, **611" -> ["**610", "**611"]; None, wenn etwas anderes darin steht."""
    parts = [p for p in re.split(r"[+,;\s]+", raw or "") if p]
    if not parts or len(parts) > MAX_DEVS or not all(re.fullmatch(DEV, p) for p in parts):
        return None
    return list(dict.fromkeys(parts))


def _name_entries(raw):
    """Gerätenamen-Eingabe in Eintraege: getrennt mit ; oder Zeilenumbruch, auch mit Komma vor dem naechsten "Name="."""
    out = []
    for chunk in re.split(r"[;\n]+", raw or ""):
        out += [p for p in re.split(r",\s*(?=[^,=+*]*[^\d\s,=+*][^,=]*=)", chunk) if p.strip()]
    return out
log = logging.getLogger("voicecmd")
_last = {}


class VoiceError(Exception):
    def __init__(self, status, text):
        super().__init__(text)
        self.status = status


def _sha(k):
    return hashlib.sha256(k.encode()).hexdigest()


def settings():
    devs = devices_cfg()
    return {"on": db.get_meta("vc_on", "0") == "1", "key_set": bool(db.get_meta("vc_key_hash", "")),
            "key_hint": db.get_meta("vc_key_hint", ""), "agent_id": int(db.get_meta("vc_agent", "0") or 0),
            "devices": ", ".join(devs), "names": "; ".join(f"{n}={d}" for n, d in names_cfg()), "nolog": nolog()}


def nolog():
    """Gespraech mit der KI bei Sprachbefehl (Assistent) nicht mitschreiben: kein Transkript im Anrufprotokoll, nur Systemzeilen."""
    return db.get_meta("vc_nolog", "0") == "1"


def names_cfg():
    """Geraetenamen als Liste (Name, **NNN)."""
    out = []
    for p in re.split(r"[;\n]+", db.get_meta("vc_names", "") or ""):
        n, _, d = p.partition("=")
        if n.strip() and d.strip():
            out.append((n.strip(), d.strip()))
    return out


def devices_cfg():
    return [p for p in re.split(r"[;,\s]+", db.get_meta("vc_devices", "") or "") if p]


def save(d):
    if "on" in d:
        db.set_meta("vc_on", "1" if d["on"] else "0")
    if "nolog" in d:
        db.set_meta("vc_nolog", "1" if d["nolog"] else "0")
    if "agent_id" in d:
        aid = int(d.get("agent_id") or 0)
        if aid and not any(a["id"] == aid and a["mode"] == "assist" for a in db.list_agents()):
            raise VoiceError(400, "Agent nicht gefunden (nur Assistenten)")
        db.set_meta("vc_agent", str(aid))
    if "devices" in d:
        out = []
        for p in re.split(r"[;,\s]+", str(d.get("devices") or "")):
            if not p:
                continue
            if not re.fullmatch(r"\*\*\d{1,4}", p):
                raise VoiceError(400, f"Geräte: nur interne Nummern wie **610 ({p[:20]})")
            if p not in out:
                out.append(p)
        db.set_meta("vc_devices", ",".join(out))
    if "names" in d:
        out, seen = [], set()
        for p in _name_entries(str(d.get("names") or "")):
            n, eq, dev = p.partition("=")
            # interne Nummern auch ohne ** (Headset=612): hier gibt es nur Telefone im Haus
            n, devs = n.strip(), devs_of(re.sub(r"(?<![\d*])([1-9]\d{0,3})\b", r"**\1", dev))
            if not eq or not n or len(n) > 40 or not devs:
                raise VoiceError(400, f"Gerätenamen: je Eintrag Name=**NNN oder mehrere mit +, z. B. Headset 1=**610 oder "
                                      f"Haus=**610+**611 ({p.strip()[:30]})")
            dev = "+".join(devs)
            if n.casefold() in seen:
                raise VoiceError(400, f"Gerätename doppelt: {n}")
            seen.add(n.casefold())
            out.append(f"{n}={dev}")
        db.set_meta("vc_names", ";".join(out))


def new_key():
    k = "lm_" + secrets.token_urlsafe(24)
    db.set_meta("vc_key_hash", _sha(k))
    db.set_meta("vc_key_hint", "…" + k[-4:])
    db.set_meta("vc_key_len", str(len(k)))
    return k


def drop_key():
    db.set_meta("vc_key_hash", "")
    db.set_meta("vc_key_hint", "")


def check(headers):
    """Schluessel aus "Authorization: Bearer <k>" oder "X-Api-Key: <k>". Wirft VoiceError."""
    h = db.get_meta("vc_key_hash", "")
    if db.get_meta("vc_on", "0") != "1" or not h:
        raise VoiceError(403, "Sprachbefehle sind ausgeschaltet")
    a = headers.get("authorization") or ""
    k = a[7:].strip() if a.lower().startswith("bearer ") else (headers.get("x-api-key") or "").strip()
    if not k or not hmac.compare_digest(h, _sha(k)):
        # nur Form und Laenge, nie der Schluessel: hilft beim Einrichten (fehlt "Bearer ", Anfuehrungszeichen, Leerzeichen?)
        log.warning("Sprachbefehl: Schluessel abgelehnt (Authorization: %s, Bearer: %s, X-Api-Key: %s, Laenge %d, erwartet %d, "
                    "Praefix lm_: %s, Leer-/Anfuehrungszeichen: %s)", "ja" if a else "nein", "ja" if a.lower().startswith("bearer ") else "nein",
                    "ja" if headers.get("x-api-key") else "nein", len(k), int(db.get_meta("vc_key_len", "35") or 35),
                    "ja" if k.startswith("lm_") else "nein", "ja" if re.search(r"[\s\"']", k) else "nein")
        raise VoiceError(401, "Schlüssel fehlt oder ist falsch")


def device(raw):
    """Geraet: interne Nummer (**610), Geraetename (Headset 1) oder Name eines Mitarbeiters (sein erstes Telefon **NNN)."""
    raw = (raw or "").strip()
    if not raw:
        raise VoiceError(400, "Gerät fehlt")
    named = next((d for n, d in names_cfg() if n.casefold() == raw.casefold()), None)
    if named:
        dev = named
    elif devs_of(raw):
        dev = "+".join(devs_of(raw))
    else:
        hits = [s for s in db.find_staff(raw) if not db.is_group(s)]
        if len(hits) != 1:
            raise VoiceError(404 if not hits else 409, "Gerät nicht gefunden" if not hits else
                             "Mehrere passen: " + ", ".join(sorted(s["name"] for s in hits)))
        ph = outrec.staff_phones(hits[0])
        if not ph:
            raise VoiceError(400, f"{hits[0]['name']} hat kein internes Telefon (**NNN)")
        dev = ph[0]
    allowed = devices_cfg()
    bad = [d for d in dev.split("+") if allowed and d not in allowed]
    if bad:
        raise VoiceError(403, f"Gerät {', '.join(bad)} ist für Sprachbefehle nicht freigegeben")
    return dev


def target(raw):
    """Ziel: Rufnummer bzw. interne Nummer, Mitarbeiter oder Telefonbuch-Kontakt. Rueckgabe (Ziele "n1+n2", Anzeigename)."""
    raw = (raw or "").strip()
    if not raw:
        raise VoiceError(400, "Ziel fehlt")
    if re.fullmatch(r"[+0-9*#][0-9 ()/*#-]*", raw):
        try:
            return ami.dial_number(raw), ""
        except ValueError as e:
            raise VoiceError(400, str(e))
    staff = db.find_staff(raw)
    if len(staff) > 1:
        raise VoiceError(409, "Mehrere passen: " + ", ".join(sorted(s["name"] for s in staff)))
    if staff:
        s = staff[0]
        try:
            nums = ami.dial_list(s.get("number") or "")
        except ValueError:
            nums = []
        if not nums:
            raise VoiceError(400, f"{s['name']} hat keine Nummer")
        return "+".join(nums), s["name"]
    hits = {}
    for h in db.find_contacts_by_name(raw):
        hits.setdefault(re.sub(r"\D", "", h["number"])[-9:], h)
    if not hits:
        raise VoiceError(404, "Niemanden mit diesem Namen gefunden")
    if len(hits) > 1:
        raise VoiceError(409, "Mehrere passen: " + ", ".join(sorted(f"{h['name']}{' (' + h['note'] + ')' if h['note'] else ''}"
                                                                    for h in hits.values())))
    h = next(iter(hits.values()))
    try:
        return ami.dial_number(h["number"]), h["name"]
    except ValueError as e:
        raise VoiceError(400, f"{h['name']}: {e}")


def gate(dev, now=None):
    now = time.time() if now is None else now
    if now - _last.get(dev, 0) < MIN_GAP:
        raise VoiceError(429, f"Gerät {dev} wurde gerade schon angerufen")
    _last[dev] = now


def agent():
    """Agent fuer den Ablauf "assistent": eingestellt oder der erste aktive Assistent."""
    aid = int(db.get_meta("vc_agent", "0") or 0)
    ags = [a for a in db.list_agents(enabled_only=True) if a["mode"] == "assist"]
    a = next((a for a in ags if a["id"] == aid), None) or (ags[0] if ags else None)
    if not a:
        raise VoiceError(400, "Kein aktiver Assistent (Agent) vorhanden")
    return a


def owner(dev):
    """Benutzer, dem das Geraet gehoert (fuer die Anrede), nur wenn eindeutig."""
    own = [s for s in db.q("SELECT * FROM staff WHERE kind!='group'") if dev in outrec.staff_phones(s)]
    return own[0] if len(own) == 1 else None


def device_list():
    """Geraete fuer Home Assistant (Auswahl): eigene Geraetenamen, dann die Telefone der Fritzbox (letzter bekannter Stand),
    dann freigegebene ohne Namen. Mit Freigabeliste nur die freigegebenen."""
    from . import fritz
    out = [{"name": n, "number": d} for n, d in names_cfg()]
    have = {x["number"] for x in out}
    for p in fritz.cached_phones() or []:
        if re.fullmatch(r"\*\*\d{1,4}", p.get("number") or "") and p["number"] not in have:
            out.append({"name": p.get("name") or p["number"], "number": p["number"]})
            have.add(p["number"])
    out += [{"name": d, "number": d} for d in devices_cfg() if d not in have]
    allowed = devices_cfg()
    return [x for x in out if not allowed or all(d in allowed for d in x["number"].split("+"))]


def target_list():
    """Ziele fuer Home Assistant (Auswahl): Benutzer (alle Nummern oder einzeln), Telefonbuch, Telefone im Haus.
    value ist, was an /api/voice/connect geht (Name bzw. Nummer), label die Anzeige. Gesperrte Nummern fehlen."""
    out = []

    def add(value, label, kind, name):
        if value and all(o["value"] != value for o in out):
            out.append({"value": value, "label": label, "kind": kind, "name": name})

    for st in db.list_staff(effective=False):
        try:
            nums = ami.dial_list(st.get("number") or "")
        except ValueError:
            continue
        add(st["name"], f"{st['name']} (alle Nummern)" if len(nums) > 1 else f"{st['name']} ({nums[0]})", "benutzer", st["name"])
        if len(nums) > 1:
            for n in nums:
                add(n, f"{st['name']}: {n}", "benutzer", st["name"])
    for c in db.list_contacts():
        try:
            n = ami.dial_number(c.get("number") or "")
        except ValueError:
            continue
        add(n, f"{c['name']}{' (' + c['note'] + ')' if c.get('note') else ''}: {n}", "kontakt",
            f"{c['name']} {c['note']}" if c.get("note") and len(c["note"]) <= 20 else c["name"])
    from . import fritz
    for p in fritz.cached_phones() or []:
        if re.fullmatch(r"\*\*\d{1,4}", p.get("number") or ""):
            add(p["number"], f"Telefon {p.get('name') or p['number']} ({p['number']})", "telefon", p.get("name") or p["number"])
    return out


# --- Status der Sprachbefehl-Anrufe (fuer Home Assistant) ---
STATES = {}
FINAL = ("beendet", "nicht_erreicht")
KEEP_S = 600          # beendete Anrufe so lange im Stand
STALE_S = 7200        # nicht beendete (Ereignis verloren) so lange


def public(s):
    return {k: s[k] for k in ("call_id", "mode", "device", "answered", "target", "state", "reason", "since", "ts")}


def _prune(now=None):
    now = time.time() if now is None else now
    for cid, s in list(STATES.items()):
        if now - s["ts"] > (KEEP_S if s["state"] in FINAL else STALE_S):
            STATES.pop(cid, None)


def track(cid, uuid, mode, dev, target):
    _prune()
    now = time.time()
    STATES[cid] = {"call_id": cid, "uuid": uuid, "mode": mode, "device": dev, "target": target, "state": "klingelt",
                   "reason": "", "chan": "", "answered": "" if "+" in dev else dev,
                   "since": now, "ts": now}
    hub.publish({"type": "vc", "call": public(STATES[cid])})


def by_uuid(uuid):
    return next((s for s in STATES.values() if s["uuid"] == uuid), None)


def set_state(cid, state, reason="", chan=""):
    """Neuer Stand; nach beendet/nicht_erreicht keine Aenderung mehr. Rueckgabe: Stand oder None (kein Sprachbefehl)."""
    s = STATES.get(cid)
    if not s or s["state"] in FINAL:
        return s
    if chan and re.fullmatch(r"[A-Za-z0-9/_@.:+;-]+", chan):
        s["chan"] = chan
    if s["state"] == state and s["reason"] == reason:
        return s
    s.update(state=state, reason=reason, ts=time.time())
    hub.publish({"type": "vc", "call": public(s)})
    return s


def snapshot():
    _prune()
    return [public(s) for s in sorted(STATES.values(), key=lambda x: x["call_id"])]


WHY = {"BUSY": "besetzt", "NOANSWER": "keine Antwort", "CONGESTION": "nicht erreichbar", "CHANUNAVAIL": "nicht erreichbar",
       "CANCEL": "vorher aufgelegt"}


def ended(s, st):
    """Auflegen (Dialplan, DIALSTATUS st): verbunden -> beendet; sonst beendet (KI ohne Verbindung) oder nicht_erreicht mit Grund."""
    if s["state"] == "verbunden" or st == "ANSWER":
        return set_state(s["call_id"], "beendet")
    if s["mode"] == "ki" and s["state"] == "ki":
        return set_state(s["call_id"], "beendet", WHY.get(st, "") if st else "ohne Verbindung")
    return set_state(s["call_id"], "nicht_erreicht", WHY.get(st, st or "aufgelegt"))


def answered_dev(endpoint, uri):
    """Welches Telefon hat abgehoben (Dialplan [vc-who] auf dem Kanal des Telefons): Endpunkt 99NNN = Klingel-Rufnummer
    von **NNN, sonst die interne Nummer aus der SIP-Adresse (sip:**610@fritz.box)."""
    m = re.fullmatch(r"99(\d{1,4})", endpoint or "")
    if m:
        return "**" + m.group(1)
    m = re.search(r"sip:(\*\*\d{1,4})@", uri or "") or re.search(r"(\*\*\d{1,4})", uri or "")
    return m.group(1) if m else ""


def set_answered(s, dev):
    if dev and s.get("answered") != dev and s["state"] not in FINAL:
        s["answered"] = dev
        s["ts"] = time.time()
        hub.publish({"type": "vc", "call": public(s)})
