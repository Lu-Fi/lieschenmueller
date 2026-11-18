"""Mitschrift ausgehender Gespraeche je Benutzer (staff.rec_out): die ausgehende Leitung der eigenen Telefone umstellen und bewachen.

Einstellung je Benutzer: rec_out = off | record (Aufnahme + Transkript) | transcript (nur Transkript, Audio wird nach der Transkription
verworfen: calls.rec_keep=0) | numbers (Aufnahme + Transkript nur an Nummern aus rec_out_numbers, "Anfang*" wie bei dnd_allow);
rec_out_notice = Hinweis-Ansage an den Gespraechspartner (Standard an).

Umschalten (apply): Ist rec_out != off, stellt die App jedes Telefon des Benutzers ("Klingelt im Buero an", nur **NNN) in der Fritzbox
auf ausgehend 99NNN - nur, wenn 99NNN eingerichtet (ring_lines), in der Fritzbox angelegt und bei Asterisk angemeldet ist. Die bisherige
ausgehende Nummer steht in meta out_orig:<**NNN> (JSON {orig, sid, kind}). Ein Fehler beim Umstellen stellt die in diesem Vorgang schon
umgestellten Telefone zurueck (nichts halb). Bei off (oder wenn das Telefon nicht mehr zum Benutzer gehoert) zurueck auf out_orig.
Gespraeche ueber 99NNN kommen bei Asterisk im Dialplan outcall an (/internal/outcall, main.internal_outcall -> active_owner).

Waechter (loop, alle INTERVAL_S): Ist die 99NNN eines umgestellten Telefons laenger als GRACE_S nicht bei Asterisk angemeldet (oder
Asterisk nicht erreichbar), stellt er das Telefon auf out_orig zurueck (Notbetrieb, meta out_fallback:<**NNN> = Zeitpunkt), mailt an alle
aktiven Mail-Empfaenger (wie ops/watchdog.py) und schreibt ins Log; ist sie wieder da, wieder auf 99NNN. Beim Start gleicht startup_sync
den Stand der Fritzbox mit den Metas ab.

Grenze: Faellt der ganze Rechner (Bridge UND Asterisk) aus, kann niemand zurueckstellen - die Telefone waehlen dann weiter ueber 99NNN
und erreichen niemanden, bis jemand in der Fritzbox die ausgehende Nummer zurueckstellt (Telefonie -> Telefoniegeraete -> Bearbeiten).
Faellt nur die Bridge aus, waehlt der Dialplan ohne Aufnahme normal weiter (outcall-Rueckfall)."""
import asyncio
import json
import logging
import re
import time

from . import ami, astconf, db, fritz, fritzweb

log = logging.getLogger("outrec")

INTERVAL_S = 30
GRACE_S = 90                 # so lange darf die 99NNN fehlen, bevor das Telefon in den Notbetrieb geht
MODES = {"off": "aus", "record": "Aufnahme und Transkript", "transcript": "nur Transkript", "numbers": "nur an bestimmte Nummern"}
_missing = {}                # **NNN -> seit wann seine 99NNN fehlt (nur im Speicher)
_locks = {}                  # Ereignisschleife -> Lock (Tests laufen in mehreren Schleifen)


class OutRecError(RuntimeError):
    def __init__(self, msg, phones=None):
        super().__init__(msg)
        self.phones = phones or []


def lock():
    loop = asyncio.get_running_loop()
    if loop not in _locks:
        _locks.clear()
        _locks[loop] = asyncio.Lock()
    return _locks[loop]


def ring_of(phone):
    return "99" + phone[2:]


def staff_phones(s):
    """Telefone des Benutzers aus "Klingelt im Buero an": nur interne Nummern **NNN (dreistellig wie die Klingel-Rufnummern 99NNN)."""
    out = []
    for p in re.split(r"[;,+\s]+", (s or {}).get("number") or ""):
        if re.fullmatch(r"\*\*\d{3}", p) and p not in out:
            out.append(p)
    return out


def orig_get(phone):
    try:
        v = json.loads(db.get_meta(f"out_orig:{phone}", "") or "null")
    except ValueError:
        return None
    return v if isinstance(v, dict) and v.get("orig") else None


def _orig_set(phone, orig, sid, kind):
    db.set_meta(f"out_orig:{phone}", json.dumps({"orig": orig, "sid": sid, "kind": kind}))


def _orig_del(phone):
    db.x("DELETE FROM meta WHERE key IN (?, ?)", (f"out_orig:{phone}", f"out_fallback:{phone}"))
    _missing.pop(phone, None)


def fallback_since(phone):
    v = db.get_meta(f"out_fallback:{phone}", "")
    try:
        return float(v) if v else 0.0
    except ValueError:
        return 0.0


def switched():
    """{'**NNN': {orig, sid, kind}} aller Telefone, die die App auf ausgehend 99NNN gestellt hat."""
    out = {}
    for r in db.q("SELECT key FROM meta WHERE key LIKE 'out_orig:%'"):
        phone = r["key"].split(":", 1)[1]
        o = orig_get(phone)
        if o:
            out[phone] = o
    return out


def _staff(sid):
    r = db.q("SELECT * FROM staff WHERE id=?", (sid,)) if sid else []
    return r[0] if r else None


def wanted(phone, o):
    """Soll das Telefon (laut Einstellung) auf 99NNN stehen? Benutzer da, rec_out an, Telefon noch seins, 99NNN eingerichtet."""
    s = _staff(o.get("sid"))
    return bool(s and (s.get("rec_out") or "off") != "off" and phone in staff_phones(s) and ring_of(phone) in astconf.ring_lines())


def active_owner(frm):
    """Gespraech von Absender 99NNN: Benutzer (staff-Zeile), dessen Telefon bewusst auf ausgehend 99NNN steht und der rec_out an hat;
    sonst None (dann gilt das bisherige Verhalten: ohne Vorwahl nicht mitschreiben)."""
    if not re.fullmatch(r"99\d{3}", frm or ""):
        return None
    phone = "**" + frm[2:]
    o = orig_get(phone)
    if not o or fallback_since(phone) or not wanted(phone, o):
        return None
    return _staff(o["sid"])


def phone_kind(phone):
    return next((p.get("kind") for p in (fritz.cached_phones() or []) if p.get("number") == phone), None)


def _set_out(phone, kind, number):
    """Ausgehende Nummer in der Fritzbox setzen (blockierend). Rueckgabe: Stand mit out_was."""
    if kind == "IP":
        c = fritz.ip_clients().get(phone)
        if not c:
            raise fritzweb.FritzWebError(f"In der Fritzbox gibt es kein IP-Telefon {phone}")
        return fritzweb.ip_set_out(c["idx"], number)
    if kind == "DECT":
        return fritzweb.dect_set_out(phone, number)
    raise fritzweb.FritzWebError("Nur DECT- und IP-Telefone lassen sich umstellen" + (" (analoges Telefon)" if kind == "analog" else ""))


def _get_out(phone, kind):
    if kind == "IP":
        c = fritz.ip_clients().get(phone)
        if not c:
            raise fritzweb.FritzWebError(f"In der Fritzbox gibt es kein IP-Telefon {phone}")
        return fritzweb.ip_form(c["idx"])["out_num"]
    p = next((p for p in fritzweb.dect_phones(fresh=True) if p["intern"] == phone), None)
    if not p:
        raise fritzweb.FritzWebError(f"In der Fritzbox gibt es kein DECT-Telefon {phone}")
    return p["out_num"]


def _err(e):
    return str(e) if isinstance(e, (fritzweb.FritzWebError, RuntimeError)) else f"Fritzbox nicht erreichbar ({type(e).__name__})"


async def registered():
    """Bei Asterisk angemeldete Klingel-Rufnummern (Menge) oder None, wenn Asterisk nicht erreichbar ist."""
    try:
        txt = await ami.command("pjsip show contacts")
    except Exception:
        return None
    return set(re.findall(r"\s(99\d{3})/sip:", " " + (txt or "")))


async def eligibility(s, regs=None):
    """Je Telefon des Benutzers: {phone, ring, kind, ok, why} - laesst es sich jetzt auf ausgehend 99NNN stellen?"""
    phones = staff_phones(s)
    if not phones:
        return []
    if regs is None:
        regs = await registered()
    try:
        box = {n["number"] for n in await asyncio.to_thread(fritz.ring_numbers)}
    except Exception:
        box = None
    lines, sw = set(astconf.ring_lines()), switched()
    out = []
    for p in phones:
        ring, kind, why = ring_of(p), phone_kind(p), ""
        o = sw.get(p)
        if o and o.get("sid") != s["id"]:
            other = _staff(o.get("sid"))
            why = f"wird schon für {other['name'] if other else 'einen anderen Benutzer'} mitgeschrieben (gemeinsames Telefon)"
        elif ring not in lines:
            why = f"Klingel-Rufnummer {ring} ist nicht eingerichtet (Rufnummern → Anruferanzeige an den Telefonen)"
        elif box is None:
            why = "Fritzbox nicht lesbar"
        elif ring not in box:
            why = f"{ring} ist in der Fritzbox nicht angelegt"
        elif regs is None:
            why = "Asterisk nicht erreichbar"
        elif ring not in regs:
            why = f"{ring} ist (noch) nicht bei Asterisk angemeldet"
        elif kind not in ("DECT", "IP"):
            why = "nur DECT- und IP-Telefone lassen sich umstellen" + (" (Telefon der Fritzbox unbekannt)" if kind is None else "")
        out.append({"phone": p, "ring": ring, "kind": kind, "ok": not why, "why": why})
    return out


async def _restore(phone, o):
    """Zurueck auf die gemerkte Nummer; danach Metas loeschen. Wirft bei Fehler (Metas bleiben, der Waechter versucht es weiter)."""
    await asyncio.to_thread(_set_out, phone, o.get("kind") or phone_kind(phone), o["orig"])
    _orig_del(phone)
    log.info("Ausgehende Mitschrift: %s wieder ausgehend auf der bisherigen Nummer", phone)


async def restore(phone):
    """Fuer andere Stellen (z. B. Klingel-Rufnummer ausschalten): '' = ok oder nichts zu tun, sonst Fehlertext."""
    o = orig_get(phone)
    if not o:
        return ""
    async with lock():
        try:
            await _restore(phone, o)
            return ""
        except Exception as e:
            log.warning("Ausgehende Mitschrift: %s nicht zurueckgestellt (%s)", phone, type(e).__name__)
            return _err(e)


async def apply(s):
    """Telefone des Benutzers `s` (staff-Zeile mit den NEUEN Werten rec_out/number) passend umstellen. Wirft OutRecError, wenn ein
    Telefon nicht umgestellt werden konnte (die in diesem Vorgang schon umgestellten sind dann zurueckgestellt). Rueckgabe:
    {switched, restored, skipped: [{phone, why}], warnings: [...]}."""
    sid = s["id"]
    on = (s.get("rec_out") or "off") != "off"
    async with lock():
        mine = {p: o for p, o in switched().items() if o.get("sid") == sid}
        phones, lines = staff_phones(s), set(astconf.ring_lines())
        keep = {p for p in mine if on and p in phones and ring_of(p) in lines}
        elig = await eligibility(s) if on else []
        to_on = [e for e in elig if e["ok"] and e["phone"] not in mine]
        done, errors = [], []
        for e in to_on:
            p, ring = e["phone"], e["ring"]
            try:
                st = await asyncio.to_thread(_set_out, p, e["kind"], ring)
            except Exception as ex:
                errors.append(f"{p}: {_err(ex)}")
                break
            was = st.get("out_was") or ""
            if was == ring or not was:
                errors.append(f"{p}: steht in der Fritzbox schon ausgehend auf {ring} (nicht von der App umgestellt) – bitte dort zuerst "
                              "eine echte Rufnummer als ausgehende Nummer einstellen")
                break
            _orig_set(p, was, sid, e["kind"])
            done.append(p)
            log.info("Ausgehende Mitschrift: %s ausgehend auf %s gestellt", p, ring)
        if errors:
            back_err = []
            for p in done:
                try:
                    await _restore(p, orig_get(p))
                except Exception as ex:
                    back_err.append(f"{p}: {_err(ex)}")
            msg = "Telefon nicht umgestellt – " + "; ".join(errors)
            if done:
                msg += (" Bereits umgestellte Telefone wurden zurückgestellt." if not back_err else
                        " Zurückstellen scheiterte bei " + "; ".join(back_err) + " (wird automatisch erneut versucht).")
            raise OutRecError(msg + " Die Einstellung wurde nicht gespeichert.", errors)
        restored, warnings = [], []
        for p, o in mine.items():
            if p in keep:
                continue
            try:
                await _restore(p, o)
                restored.append(p)
            except Exception as ex:
                warnings.append(f"{p} ließ sich nicht zurückstellen ({_err(ex)}) – wird automatisch erneut versucht")
        skipped = [{"phone": e["phone"], "why": e["why"]} for e in elig if not e["ok"] and e["phone"] not in mine]
    return {"switched": done, "restored": restored, "skipped": skipped, "warnings": warnings}


async def status(s):
    """Stand je Telefon des Benutzers fuer die Oberflaeche: [{phone, ring, state: active|fallback|other|off, why}]."""
    sw = switched()
    on = (s.get("rec_out") or "off") != "off"
    elig = {e["phone"]: e for e in (await eligibility(s) if on else [])}
    out = []
    for p in staff_phones(s):
        o, why = sw.get(p), ""
        if o and o.get("sid") == s["id"]:
            state = "fallback" if fallback_since(p) else "active"
        elif o:
            state = "other"
            why = (elig.get(p) or {}).get("why", "")
        else:
            state = "off"
            why = (elig.get(p) or {}).get("why", "") if on else ""
        out.append({"phone": p, "ring": ring_of(p), "state": state, "why": why, "orig": (o or {}).get("orig", "") if state != "other" else ""})
    return out


def overview():
    """{'99NNN': {state, owner, orig}} aller umgestellten Telefone (Tabelle "Anruferanzeige")."""
    out = {}
    for p, o in switched().items():
        s = _staff(o.get("sid"))
        out[ring_of(p)] = {"state": "fallback" if fallback_since(p) else "active", "owner": s["name"] if s else "", "orig": o["orig"]}
    return out


def _alert(subject, text):
    """Mail an alle aktiven Empfaenger (wie ops/watchdog.py); Fehler nur ins Log."""
    from . import notify
    if not notify.configured():
        log.warning("Ausgehende Mitschrift: Mail nicht eingerichtet - keine Benachrichtigung (%s)", subject)
        return
    rcpt = sorted({r["email"] for r in db.q("SELECT email FROM mail_recipients WHERE enabled=1") if r["email"]})
    if not rcpt:
        return
    try:
        notify.send_mail(rcpt, subject, text)
    except Exception as e:
        log.warning("Ausgehende Mitschrift: Mail nicht gesendet (%s)", type(e).__name__)


async def tick(now=None):
    """Ein Durchlauf des Waechters. Rueckgabe: Ereignisse [(art, telefon)] mit art fallback | back | restored."""
    now = now or time.time()
    events = []
    async with lock():
        sw = switched()
        for p in set(_missing) - set(sw):
            _missing.pop(p, None)
        if not sw:
            return events
        regs = await registered()
        for p, o in sw.items():
            ring = ring_of(p)
            if not wanted(p, o):                      # Einstellung aus / Telefon nicht mehr beim Benutzer: zurueck
                try:
                    await _restore(p, o)
                    events.append(("restored", p))
                except Exception as e:
                    log.warning("Ausgehende Mitschrift: %s nicht zurueckgestellt (%s) - neuer Versuch in %d s", p, type(e).__name__, INTERVAL_S)
                continue
            fb = fallback_since(p)
            if regs is not None and ring in regs:
                _missing.pop(p, None)
                if fb:
                    try:
                        await asyncio.to_thread(_set_out, p, o.get("kind") or phone_kind(p), ring)
                    except Exception as e:
                        log.warning("Ausgehende Mitschrift: %s nicht wieder auf %s gestellt (%s)", p, ring, type(e).__name__)
                        continue
                    db.x("DELETE FROM meta WHERE key=?", (f"out_fallback:{p}",))
                    events.append(("back", p))
                    log.warning("Ausgehende Mitschrift: %s ist wieder angemeldet - %s waehlt wieder ueber %s (Notbetrieb beendet)", ring, p, ring)
                continue
            since = _missing.setdefault(p, now)
            if fb or now - since < GRACE_S:
                continue
            try:
                await asyncio.to_thread(_set_out, p, o.get("kind") or phone_kind(p), o["orig"])
            except Exception as e:
                log.warning("Ausgehende Mitschrift: %s fehlt, %s nicht zurueckgestellt (%s) - neuer Versuch in %d s", ring, p,
                            type(e).__name__, INTERVAL_S)
                continue
            db.set_meta(f"out_fallback:{p}", str(now))
            events.append(("fallback", p))
            log.warning("Ausgehende Mitschrift: %s seit %d s nicht bei Asterisk angemeldet%s - %s waehlt im Notbetrieb ueber die "
                        "bisherige Nummer (ohne Mitschrift)", ring, int(now - since), " (Asterisk nicht erreichbar)" if regs is None else "", p)
    for kind, p in events:
        s = _staff((sw.get(p) or {}).get("sid"))
        who = f"{p}{' (' + s['name'] + ')' if s else ''}"
        if kind == "fallback":
            await asyncio.to_thread(_alert, "LieschenMüller: Ausgehende Mitschrift im Notbetrieb",
                                    f"Die Klingel-Rufnummer {ring_of(p)} ist seit über {GRACE_S} s nicht bei Asterisk angemeldet"
                                    f"{' (Asterisk nicht erreichbar)' if regs is None else ''}.\n\nDas Telefon {who} wurde in der Fritzbox auf seine "
                                    f"bisherige ausgehende Nummer zurückgestellt: Gespräche gehen normal hinaus, aber ohne Mitschrift. "
                                    f"Sobald {ring_of(p)} wieder angemeldet ist, stellt die App es wieder um.")
        elif kind == "back":
            await asyncio.to_thread(_alert, "LieschenMüller: Ausgehende Mitschrift wieder aktiv",
                                    f"{ring_of(p)} ist wieder bei Asterisk angemeldet. Das Telefon {who} wählt wieder über {ring_of(p)} "
                                    "(Mitschrift nach der Einstellung des Benutzers).")
    return events


async def startup_sync():
    """Beim Start: Stand der Fritzbox mit den Metas abgleichen (soll: Notbetrieb oder Einstellung aus -> bisherige Nummer, sonst 99NNN).
    Hat jemand die ausgehende Nummer in der Fritzbox von Hand auf eine andere echte Nummer gestellt, gilt diese als neue bisherige."""
    async with lock():
        for p, o in switched().items():
            ring, kind = ring_of(p), o.get("kind") or phone_kind(p)
            try:
                if not wanted(p, o):
                    await _restore(p, o)
                    continue
                cur = await asyncio.to_thread(_get_out, p, kind)
                want = o["orig"] if fallback_since(p) else ring
                if cur != want:
                    if want == ring and cur != o["orig"] and not re.fullmatch(r"99\d{1,4}", cur or ""):
                        _orig_set(p, cur, o["sid"], kind)
                    await asyncio.to_thread(_set_out, p, kind, want)
                    log.info("Ausgehende Mitschrift: %s beim Start angeglichen (ausgehend %s)", p, "bisherige Nummer" if want != ring else ring)
            except Exception as e:
                log.warning("Ausgehende Mitschrift: %s beim Start nicht angeglichen (%s)", p, type(e).__name__)


async def loop():
    """Hintergrund: beim Start angleichen, dann alle INTERVAL_S Sekunden pruefen (nur wenn Telefone umgestellt sind)."""
    try:
        if switched():
            await startup_sync()
    except Exception:
        log.exception("Ausgehende Mitschrift: Abgleich beim Start fehlgeschlagen")
    while True:
        try:
            if switched():
                await tick()
        except Exception:
            log.exception("Ausgehende Mitschrift: Pruefung fehlgeschlagen")
        await asyncio.sleep(INTERVAL_S)
