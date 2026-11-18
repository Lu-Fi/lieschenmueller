"""Dateien, die Asterisk liest und die aus den Einstellungen (Datenbank) entstehen - an EINER Stelle.

Grundsatz: Die Datenbank (meta cfg:*, mit .env-Werten als Voreinstellung, config.py) ist die Quelle; die Dateien sind nur Ausgabe.

| Datei (Host) | in Asterisk | Inhalt | erzeugt von |
|---|---|---|---|
| data/asterisk/fritz.conf | /etc/asterisk/dyn/fritz.conf | SIP-Zugang zur Fritzbox, IP-Sperre | sync_fritz() hier (aus cfg:fritz_user/-password/-host) |
| data/asterisk/moh.conf | /etc/asterisk/dyn/moh.conf | Wartemusik-Klassen | hold.rebuild_classes() (aus meta + Bibliothek) |
| data/asterisk/globals.conf | /etc/asterisk/dyn/globals.conf | Dialplan-Globale (REC_PREFIX = Vorwahl fuer aufgezeichnete ausgehende Gespraeche) | write_globals() hier (aus meta rec_prefix); render.py legt sie an, wenn sie fehlt |
| asterisk/conf/*.conf | /etc/asterisk/*.conf | pjsip, extensions, manager, modules | asterisk/render.py (Vorlagen + .env: AMI_SECRET, WEB_PORT) |

fritz.conf wird geschrieben beim Start der Bridge (sync_fritz) und nach jeder Aenderung der Zugangsdaten in der Oberflaeche
(main.config_put -> write_fritz), danach laedt Asterisk PJSIP neu. render.py legt fritz.conf nur an, wenn sie fehlt (dann ebenfalls
aus der Datenbank, sonst .env, sonst ein Platzhalter), und ueberschreibt nie eine vorhandene Datei.

Einmalige Migrationen (bleiben, damit aeltere Installationen ohne Handarbeit weiterlaufen):
- adopt (frueher main.sip_adopt): Stehen Benutzer UND Kennwort in der Datenbank leer, aber vollstaendige Zugangsdaten in einer von uns
  erzeugten fritz.conf, werden sie in die Datenbank uebernommen (Datei -> DB nur, wenn die DB leer ist).
- upgrade (frueher main.sip_upgrade): Sind die Zugangsdaten in der Datenbank unvollstaendig, bleibt die Datei die einzige Quelle; eine
  aeltere Datei (ohne IP-Sperre/identify_by=ip) wird dann mit ihren eigenen Zugangsdaten im aktuellen Format neu geschrieben.
- migrate_613: 99613 stand bis 2026-11 fest in pjsip.conf; einmal in ring_lines/ring_ok uebernommen (meta ring_613_free=1).
Sonst gilt: Datenbank gewinnt. Weicht die Datei ab, wird sie ueberschrieben (die alte bleibt als fritz.conf.vorher liegen).
"""
import asyncio
import logging
import os
import re

from . import ami, config, db, fritzconf

log = logging.getLogger("astconf")
SIP_FILE = os.path.join(os.path.dirname(os.environ.get("DB_PATH", "/data/lieschen.db")), "asterisk", "fritz.conf")
SIP_KEYS = ("fritz_user", "fritz_password", "fritz_host")


def db_creds():
    """(Benutzer, Kennwort, Adresse) aus den Einstellungen, oder None, wenn nicht vollstaendig."""
    u, p, h = (config.get(k) for k in SIP_KEYS)
    return (u, p, h) if (u and p and h) else None


RING_ALL_DEFAULT = "99613"      # Voreinstellung von meta ring_all ("alle Telefone klingeln") fuer neue Installationen


def ring_all():
    """Einstellung "alle Telefone klingeln" (meta ring_all): Ziel(e) mit +, leer = ausgeschaltet."""
    return (db.get_meta("ring_all", RING_ALL_DEFAULT) or "").strip()


def ring_all_lines():
    """Klingel-Rufnummern 99NNN, ueber die "alle Telefone klingeln" laeuft (Teile von meta ring_all). Sie brauchen immer AOR und Endpoint,
    damit Asterisk darueber waehlen kann - ring_lines()/ring_ok() nehmen sie deshalb von selbst mit auf (ohne sie in den Metas zu speichern)."""
    out = []
    for p in ring_all().split("+"):
        p = p.strip()
        if re.fullmatch(r"99\d{1,4}", p) and p not in out:
            out.append(p)
    return out


def stored_ring_lines():
    """Eingetragene Klingel-Rufnummern 99NNN (meta ring_lines, Haken in der Tabelle); fehlerhafte Eintraege werden ignoriert."""
    try:
        return fritzconf.clean_ring_lines(db.get_meta("ring_lines", ""))
    except ValueError:
        log.warning("Einstellung ring_lines ungueltig - weitere Klingel-Rufnummern werden ignoriert")
        return []


def stored_ring_ok():
    """Klingel-Rufnummern, die in der Fritzbox einem Telefon zugewiesen sind (meta ring_ok) - nur ueber sie waehlt Asterisk.
    Fehlt die Einstellung (Stand vor dieser Unterscheidung), gelten alle eingetragenen als zugewiesen."""
    v = db.get_meta("ring_ok", None)
    if v is None:
        db.set_meta("ring_ok", ",".join(stored_ring_lines()))
        return stored_ring_lines()
    return [n for n in v.split(",") if n]


def _plus(base, extra):
    return base + [n for n in extra if n not in base]


def ring_lines():
    """Wirksame Klingel-Rufnummern (AOR fuer die Anmeldung der Fritzbox): die fuer "alle Telefone klingeln" (zuerst, damit die Grenze
    von 20 sie nie abschneidet) und die eingetragenen."""
    return _plus(ring_all_lines(), stored_ring_lines())[:20]


def ring_ok():
    """Wirksame Klingel-Rufnummern mit Endpoint (darueber waehlt Asterisk): die zugewiesenen und die fuer "alle Telefone klingeln"."""
    return _plus(ring_all_lines(), stored_ring_ok())


REC_PREFIX_DEFAULT = "*77"      # Voreinstellung von meta rec_prefix (Vorwahl fuer aufgezeichnete ausgehende Gespraeche)
GLOBALS_FILE = os.path.join(os.path.dirname(SIP_FILE), "globals.conf")


def rec_prefix():
    return (db.get_meta("rec_prefix", REC_PREFIX_DEFAULT) or "").strip()


def render_globals(prefix):
    """Inhalt von dyn/globals.conf (extensions.conf: #tryinclude). Der Dialplan braucht die Vorwahl, wenn die Bridge nicht antwortet:
    dann entfernt er sie selbst und waehlt ohne Aufnahme weiter ([from-fritz] outcall). LM_GLOBALS=1 zeigt, dass die Datei gelesen wurde
    (ohne sie nimmt der Dialplan die Voreinstellung *77)."""
    if prefix and not re.fullmatch(r"[0-9*#]{1,8}", prefix):
        raise ValueError("Vorwahl: 1 bis 8 Zeichen aus Ziffern, * und #")
    return ("; Erzeugt von der Bridge (Einstellungen -> Aufzeichnung: Vorwahl), nicht von Hand aendern.\n"
            f"[globals]\nLM_GLOBALS=1\nREC_PREFIX={prefix}\n")


def write_globals(path=None, prefix=None):
    """globals.conf schreiben, wenn sie abweicht. True = geschrieben."""
    path = path or GLOBALS_FILE
    want = render_globals(rec_prefix() if prefix is None else prefix)
    if _read(path) == want:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(want)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return True


async def apply_globals(tries=1, delay=6):
    """Datei schreiben und die Globalen in der laufenden Asterisk-Instanz setzen (AMI Setvar ohne Kanal = global; kein dialplan reload
    noetig). True, wenn Asterisk sie uebernommen hat; sonst gelten sie nach dem naechsten Laden des Dialplans aus der Datei."""
    try:
        await asyncio.to_thread(write_globals)
    except Exception:
        log.exception("globals.conf nicht geschrieben")
    for i in range(tries):
        try:
            for var, val in (("LM_GLOBALS", "1"), ("REC_PREFIX", rec_prefix())):
                resp, _ = await ami.action("Setvar", {"Variable": var, "Value": val})
                if resp.get("Response") != "Success":
                    raise RuntimeError(resp.get("Message") or "Setvar abgelehnt")
            return True
        except Exception:
            if i + 1 < tries:
                await asyncio.sleep(delay)
    log.warning("Vorwahl nicht an Asterisk uebergeben - sie gilt nach dem naechsten Laden des Dialplans (globals.conf)")
    return False


def migrate_613():
    """Einmalige Migration (meta ring_613_free): bis 2026-11 standen AOR und Endpoint 99613 fest in pjsip.conf. Damit auf bestehenden
    Installationen nichts wegfaellt (z. B. die Wahlregel "Vorwahl -> ueber 99613" fuer aufgezeichnete ausgehende Gespraeche), wird 99613
    einmal in ring_lines (und, wenn sie "alle Telefone klingeln" ist, in ring_ok) uebernommen; danach ist sie eine normale Klingel-Rufnummer und laesst sich in der Tabelle ausschalten.
    True, wenn etwas geaendert wurde."""
    if db.get_meta("ring_613_free", "") == "1":
        return False
    changed = False
    # ring_ok (= Asterisk waehlt ueber 99613 das Telefon **613 an) nur, wenn 99613 "alle Telefone klingeln" ist - sonst waere sie
    # als zugewiesen markiert, obwohl die Fritzbox sie **613 nicht zuweist (Testanruf 17.11.). Fuer Anmeldung und Vorwahl genuegt ring_lines.
    keys = [("ring_lines", stored_ring_lines())] + ([("ring_ok", stored_ring_ok())] if "99613" in ring_all_lines() else [])
    for key, cur in keys:
        if "99613" not in cur:
            db.set_meta(key, ",".join(cur + ["99613"]))
            changed = True
    db.set_meta("ring_613_free", "1")
    if changed:
        log.info("Klingel-Rufnummer 99613 steht nicht mehr fest in pjsip.conf - als eingetragene Nummer uebernommen")
    return changed


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def adopt(path=None):
    """Migration Datei -> Datenbank (nur wenn Benutzer und Kennwort in der Datenbank leer sind). True, wenn uebernommen."""
    path = path or SIP_FILE
    if config.get("fritz_user") or config.get("fritz_password"):
        return False
    text = _read(path)
    creds = fritzconf.parse(text) if text else None
    if not creds:
        return False
    user, password, host = creds
    config.set_value("fritz_user", user)
    config.set_value("fritz_password", password)
    if not config.get("fritz_host"):
        config.set_value("fritz_host", host)
    log.info("Zugangsdaten des IP-Telefons aus fritz.conf in die Einstellungen uebernommen")
    return True


def write_fritz(path=None, backup=False):
    """fritz.conf aus der Datenbank schreiben, wenn sie abweicht. Rueckgabe: True = geschrieben (PJSIP neu laden), False = schon gleich.
    backup: abweichende Zugangsdaten der bisherigen Datei als fritz.conf.vorher aufheben (beim Start).
    ValueError, wenn die Datenbank keine vollstaendigen Zugangsdaten hat."""
    path = path or SIP_FILE
    creds = db_creds()
    if not creds:
        raise ValueError("Zugangsdaten des IP-Telefons in den Einstellungen unvollstaendig")
    ring, ok = ring_lines(), ring_ok()
    want = fritzconf.render(*creds, ring=ring, ring_ok=ok)
    have = _read(path)
    if have == want:
        return False
    if backup and have and fritzconf.parse(have) and fritzconf.parse(have) != creds:
        try:
            with open(path + ".vorher", "w", encoding="utf-8") as f:
                f.write(have)
            os.chmod(path + ".vorher", 0o600)
        except OSError:
            log.warning("Sicherung der bisherigen fritz.conf nicht geschrieben")
        log.warning("fritz.conf wich von den Einstellungen ab - die Einstellungen gelten (alte Datei: fritz.conf.vorher)")
    fritzconf.write(path, *creds, ring=ring, ring_ok=ok)
    return True


def sync_fritz(path=None):
    """Beim Start: Migrationen, dann Datenbank -> Datei. Rueckgabe: 'adopted+written', 'written', 'same', 'upgraded' oder 'none'
    (ohne Datei-/Datenbankwerte nichts zu tun). Blockierend (Dateizugriff), ohne AMI."""
    path = path or SIP_FILE
    try:
        migrate_613()
    except Exception:
        log.exception("Migration 99613 fehlgeschlagen")
    adopted = adopt(path)
    if db_creds():
        written = write_fritz(path, backup=True)
        return ("adopted+" if adopted else "") + ("written" if written else "same")
    # Datenbank unvollstaendig: Datei bleibt Quelle, nur ins aktuelle Format bringen (Migration 'upgrade')
    try:
        return "upgraded" if fritzconf.upgrade(path, ring=ring_lines(), ring_ok=ring_ok()) else "none"
    except Exception:
        log.exception("fritz.conf konnte nicht umgestellt werden")
        return "none"


async def reload_pjsip(tries=20, delay=6):
    """PJSIP neu laden (Asterisk startet eventuell noch). True, wenn es geklappt hat."""
    for _ in range(tries):
        try:
            await ami.command("module reload res_pjsip.so")
            return True
        except Exception:
            await asyncio.sleep(delay)
    log.warning("PJSIP nicht neu geladen: die neue fritz.conf gilt erst nach einem Neustart von Asterisk")
    return False


def needs_reload(res):
    return res.endswith("written") or res == "upgraded"


async def startup():
    """Beim Start der Bridge: fritz.conf mit der Datenbank abgleichen (wartet darauf); PJSIP wird bei Aenderung im Hintergrund
    neu geladen (Rueckgabe: Ergebnis von sync_fritz und ggf. die Aufgabe dafuer)."""
    try:
        res = await asyncio.to_thread(sync_fritz)
    except Exception:
        log.exception("fritz.conf nicht mit den Einstellungen abgeglichen")
        return "error", None
    if needs_reload(res):
        log.info("fritz.conf aus den Einstellungen neu geschrieben (%s) - PJSIP wird neu geladen", res)
        return res, asyncio.get_running_loop().create_task(reload_pjsip())
    return res, None


def startup_globals():
    """Beim Start der Bridge: Vorwahl fuer den Dialplan (globals.conf + AMI, im Hintergrund, Asterisk startet eventuell noch)."""
    return asyncio.get_running_loop().create_task(apply_globals(tries=20))
