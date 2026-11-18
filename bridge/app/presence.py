"""Anwesenheit ueber ein WLAN-Geraet (z. B. Handy) im Netz der Fritzbox.

Option je Benutzer (staff.presence_on, staff.presence_mac, mehrere Geraete mit Komma): Ist eines der Geraete im Netz und der Status "nicht erreichbar", wird er auf
"nicht stoeren" gestellt (die Person ist da, will aber in Ruhe arbeiten; Ausnahmen kommen durch). Ist das Geraet laenger als
GRACE_S nicht mehr im Netz und der Status "nicht stoeren", wird er wieder "nicht erreichbar" - aber nur, wenn die Anwesenheit dieses
"nicht stoeren" selbst gesetzt hat (staff.status_src = 'presence'); von Hand gesetzte Status bleiben stehen. Andere Status (erreichbar,
ausser Haus) bleiben unberuehrt. Die Wartezeit beim Verlassen faengt Handys ab, die im Ruhezustand kurz aus dem WLAN gehen.

Datenschutz: Einschalten kann nur die Person selbst (Meine Einstellungen, PUT /api/me/staff). Wann ein Geraet zuletzt im Netz war, steht
nur im Speicher (_seen), nicht in der Datenbank und nicht im Protokoll; nach einem Neustart beginnt die Wartezeit von vorn.

Abfrage: TR-064 Hosts1/GetSpecificHostEntry je MAC-Adresse (schnell, ~20 ms), alle INTERVAL_S Sekunden im Hintergrund. Der TR-064-Benutzer
braucht dafuer das Recht "FRITZ!Box Einstellungen"."""
import asyncio
import logging
import re
import time

from . import db

log = logging.getLogger("presence")

INTERVAL_S = 60
GRACE_S = 600            # so lange darf das Geraet fehlen, bevor "nicht stoeren" wieder "nicht erreichbar" wird
_hosts_cache = {"t": 0.0, "v": []}
_seen = {}               # staff.id -> zuletzt im Netz (nur im Speicher)


def clean_mac(v):
    """MAC-Adresse in der Schreibweise der Fritzbox (AA:BB:CC:DD:EE:FF) oder '' bei ungueltiger Eingabe."""
    h = re.sub(r"[^0-9A-Fa-f]", "", str(v or ""))
    return ":".join(h[i:i + 2] for i in range(0, 12, 2)).upper() if len(h) == 12 else ""


def clean_macs(v):
    """Mehrere Geraete (Komma): gueltige MAC-Adressen ohne Doppelte, hoechstens 5."""
    out = []
    for p in str(v or "").split(","):
        m = clean_mac(p)
        if m and m not in out:
            out.append(m)
    return ",".join(out[:5])


def any_active(is_active, macs):
    """Mehrere Geraete: True, sobald eines im Netz ist; None, wenn keins im Netz ist und die Fritzbox nicht (ganz) lesbar war."""
    res = []
    for m in [x for x in str(macs or "").split(",") if x]:
        try:
            r = is_active(m)
        except Exception:
            r = None
        if r:
            return True
        res.append(r)
    return None if (not res or None in res) else False


def tick(is_active, now=None):
    """Ein Durchlauf: is_active(mac) -> True/False/None (None = Fritzbox nicht lesbar: nichts aendern).
    Rueckgabe: Liste der Aenderungen (Name, alt, neu) fuer die Oberflaeche (nicht fuers Protokoll)."""
    now = now or time.time()
    changes = []
    rows = db.q("SELECT id, name, status, status_src, presence_mac, until FROM staff WHERE presence_on=1 AND presence_mac!='' AND kind!='group'")
    for sid in set(_seen) - {s["id"] for s in rows}:
        _seen.pop(sid, None)                     # Option aus: nichts mehr merken
    for s in rows:
        active = any_active(is_active, s["presence_mac"])        # mehrere Geraete: eines im Netz genuegt
        if active is None:
            continue
        # bedingte UPDATEs: hat jemand den Status inzwischen geaendert, gilt dessen Aenderung
        if active:
            _seen[s["id"]] = now
            # von Hand gesetzter Urlaub ("nicht erreichbar" mit "abwesend bis" in der Zukunft) bleibt: sonst kaemen mit "nicht
            # stoeren" die Ausnahmen durch, obwohl die Person bewusst ganz weg ist
            nowl = db._now_local().strftime("%Y-%m-%dT%H:%M")
            if s["status"] == "off" and not (s["until"] and s["until"] > nowl) and db.x_count(
                    "UPDATE staff SET status='dnd', status_src='presence' WHERE id=? AND status='off' AND (until='' OR until<=?)", (s["id"], nowl)):
                changes.append((s["name"], "off", "dnd"))
        elif s["status"] == "dnd" and s["status_src"] == "presence":
            # nur ein selbst gesetztes "nicht stoeren" zuruecknehmen; unbekannt, seit wann das Geraet fehlt (Neustart): ab jetzt warten
            if now - _seen.setdefault(s["id"], now) >= GRACE_S and db.x_count(
                    "UPDATE staff SET status='off' WHERE id=? AND status='dnd' AND status_src='presence'", (s["id"],)):
                changes.append((s["name"], "dnd", "off"))
        else:
            _seen.pop(s["id"], None)
    if changes:
        log.debug("Anwesenheit: %d Status geaendert", len(changes))        # ohne Namen und Zeiten (kein Bewegungsprofil im Protokoll)
    return changes


def fritz_active(mac):
    """Ist das Geraet gerade im Netz der Fritzbox? None, wenn die Fritzbox nicht antwortet."""
    from . import fritz
    try:
        r = fritz._shared_conn().call_action("Hosts1", "GetSpecificHostEntry", NewMACAddress=mac)
    except Exception as e:
        if "NoSuchEntry" in type(e).__name__ or "714" in str(e):      # der Fritzbox unbekannt: nicht im Netz
            return False
        fritz._pc = None
        return None
    return str(r.get("NewActive")).lower() in ("1", "true")


def hosts(force=False):
    """Geraete der Fritzbox fuer die Auswahl (aktive zuerst), 2 Minuten zwischengespeichert."""
    if not force and _hosts_cache["v"] and time.time() - _hosts_cache["t"] < 120:
        return _hosts_cache["v"]
    from fritzconnection.lib.fritzhosts import FritzHosts
    from . import fritz
    out = []
    for h in FritzHosts(fc=fritz._shared_conn()).get_hosts_attributes():
        mac = clean_mac(h.get("MACAddress"))
        if not mac:
            continue
        out.append({"mac": mac, "name": h.get("HostName") or h.get("X_AVM-DE_FriendlyName") or mac, "ip": h.get("IPAddress") or "",
                    "active": bool(h.get("Active")), "wlan": "802.11" in str(h.get("InterfaceType") or "")})
    out.sort(key=lambda x: (not x["active"], x["name"].lower()))
    _hosts_cache.update(t=time.time(), v=out)
    return out


async def loop(on_change=None):
    """Hintergrund: alle INTERVAL_S Sekunden pruefen (nur wenn ein Benutzer die Option nutzt)."""
    while True:
        try:
            if db.q("SELECT 1 FROM staff WHERE presence_on=1 AND presence_mac!='' LIMIT 1"):
                ch = await asyncio.to_thread(tick, fritz_active)
                if ch and on_change:
                    on_change(ch)
        except Exception:
            log.exception("Anwesenheit: Pruefung fehlgeschlagen")
        await asyncio.sleep(INTERVAL_S)


def device_by_ip(ip):
    """Geraet mit dieser IP-Adresse im Netz der Fritzbox ({'mac', 'name'}) oder None - fuer "Dieses Geraet hinzufuegen"
    (die Person sieht nur das Geraet, mit dem sie gerade die Seite oeffnet, nicht die Geraete anderer)."""
    from . import fritz
    try:
        r = fritz._shared_conn().call_action("Hosts1", "X_AVM-DE_GetSpecificHostEntryByIP", NewIPAddress=ip)
    except Exception:
        return None
    mac = clean_mac(r.get("NewMACAddress"))
    return {"mac": mac, "name": r.get("NewHostName") or mac} if mac else None
