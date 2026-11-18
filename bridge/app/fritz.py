import os
import threading
import time

from fritzconnection.lib.fritzphonebook import FritzPhonebook

from . import db


def _digits(n):
    return "".join(c for c in n if c.isdigit())


def _key(n):
    d = _digits(n)
    return d[-9:] if len(d) >= 7 else d


def _number(n):
    """fritzconnection liefert je nach Version Strings oder Objekte/Dicts."""
    if isinstance(n, str):
        return n
    if isinstance(n, dict):
        return n.get("number") or n.get("text") or ""
    return getattr(n, "number", None) or getattr(n, "text", None) or str(n)


def _fetch():
    user = os.environ.get("FRITZ_TR064_USER", "")
    pw = os.environ.get("FRITZ_TR064_PASSWORD", "")
    host = os.environ.get("FRITZ_HOST", "192.168.178.1")
    if not user or not pw:
        raise RuntimeError("FRITZ_TR064_USER / FRITZ_TR064_PASSWORD fehlen in .env")
    pb = FritzPhonebook(address=host, user=user, password=pw)
    entries = {}
    for pid in pb.phonebook_ids:
        for name, numbers in pb.get_all_name_numbers(pid):
            for raw in numbers:
                num = _number(raw).strip()
                # Systemeintraege (Ansagen, Rundruf) sind keine Anrufer
                if "@" in num or num.startswith("*") or not _key(num):
                    continue
                entries.setdefault(_key(num), (name, num))
    return entries


def sync_phonebooks():
    """Abgleich mit dem Fritzbox-Telefonbuch: neu, geaendert, geloescht.
    Von Hand angelegte Kontakte bleiben unberuehrt (und gewinnen bei gleicher Nummer)."""
    entries = _fetch()
    existing = db.q("SELECT * FROM contacts")
    if not entries and any(c["source"] == "fritz" for c in existing):
        # leeres Telefonbuch (Rechte? Fehler?) darf nicht alle importierten Kontakte loeschen
        raise RuntimeError("Das Fritzbox-Telefonbuch ist leer - Abgleich abgebrochen (Rechte des Benutzers pruefen)")
    manual = {_key(c["number"]) for c in existing if c["source"] != "fritz"}
    fritz = {}
    for c in existing:
        if c["source"] == "fritz":
            fritz.setdefault(_key(c["number"]), c)
    added = updated = removed = 0
    for k, (name, num) in entries.items():
        if k in manual:
            continue
        cur = fritz.get(k)
        if cur is None:
            db.x("INSERT INTO contacts(name,number,note,source) VALUES(?,?,?,?)", (name, num, "", "fritz"))
            added += 1
        elif cur["name"] != name or cur["number"] != num:
            db.x("UPDATE contacts SET name=?, number=? WHERE id=?", (name, num, cur["id"]))
            updated += 1
    for c in existing:
        if c["source"] == "fritz" and (_key(c["number"]) not in entries or fritz[_key(c["number"])]["id"] != c["id"]):
            db.x("DELETE FROM contacts WHERE id=?", (c["id"],))
            removed += 1
    db.set_meta("fritz_sync", time.time())
    return {"added": added, "updated": updated, "removed": removed, "total": len(entries)}


_numbers_cache = {"t": 0.0, "v": []}


_fc = None


def _fetch_numbers():
    import re
    import time as _t
    global _fc
    from fritzconnection import FritzConnection
    user, pw = os.environ.get("FRITZ_TR064_USER", ""), os.environ.get("FRITZ_TR064_PASSWORD", "")
    if not user or not pw:
        raise RuntimeError("FRITZ_TR064_USER / FRITZ_TR064_PASSWORD fehlen in .env")
    try:
        if _fc is None:        # der Verbindungsaufbau (Dienstbeschreibungen laden) dauert ~5 s: nur einmal
            _fc = FritzConnection(address=os.environ.get("FRITZ_HOST", "192.168.178.1"), user=user, password=pw)
        xml = _fc.call_action("X_VoIP1", "X_AVM-DE_GetNumbers").get("NewNumberList", "")
    except Exception:
        _fc = None
        raise
    out = [{"number": m.group(1), "name": m.group(2)} for m in
           re.finditer(r"<Item><Number>([^<]*)</Number>.*?<Name>([^<]*)</Name>", xml)]
    _numbers_cache.update(t=_t.time(), v=out)
    return out


def list_numbers():
    """Eigene Rufnummern der Fritzbox (TR-064, X_VoIP1). Zwischengespeichert; die Seite wartet nie auf die Fritzbox,
    solange es einen (auch aelteren) Stand gibt - der wird im Hintergrund erneuert (refresh_numbers)."""
    if _numbers_cache["v"]:
        return _numbers_cache["v"]
    return _fetch_numbers()


async def refresh_numbers():
    """Beim Start und danach alle 4 Minuten im Hintergrund auffrischen."""
    import asyncio
    while True:
        try:
            await asyncio.to_thread(_fetch_numbers)
        except Exception:
            pass
        await asyncio.sleep(240)


_phones_cache = {"t": 0, "v": []}
_pc = None
_pc_key = None


# Die gemeinsame Verbindung (ein requests-Session-Objekt) ist nicht threadsicher: ring_overview, ring_numbers und internal_phones
# laufen in Threads (asyncio.to_thread) auch gleichzeitig - nur unter diesem Lock benutzen (RLock: ring_overview ruft internal_phones)
_PC_LOCK = threading.RLock()


def _conn_error(e):
    """Lohnt ein Neuaufbau der Verbindung? Nur bei Verbindungsfehlern (Netz, Timeout, Fritzbox neu gestartet, HTTP-Schnittstelle),
    nicht bei Parse- oder Logikfehlern (falsche Aktion/Argumente, KeyError usw.) - die kaemen nach dem Neuaufbau genauso."""
    if isinstance(e, (ConnectionError, TimeoutError)):
        return True
    try:
        import requests
        if isinstance(e, (requests.ConnectionError, requests.Timeout)):
            return True
    except ImportError:
        pass
    try:
        from fritzconnection.core.exceptions import FritzConnectionException, FritzHttpInterfaceError
        if type(e) is FritzConnectionException or isinstance(e, FritzHttpInterfaceError):
            return True
    except ImportError:
        pass
    return isinstance(e, OSError) and not isinstance(e, (FileNotFoundError, PermissionError))


def _with_conn(fn):
    """fn(fc) mit der gemeinsamen Verbindung unter _PC_LOCK; bei einem Verbindungsfehler einmal neu verbinden, sonst Fehler weiter."""
    global _pc
    with _PC_LOCK:
        try:
            return fn(_shared_conn())
        except Exception as e:
            if not _conn_error(e):
                raise
            _pc = None                              # Verbindung veraltet (z. B. Fritzbox neu gestartet): einmal neu aufbauen
            return fn(_shared_conn())


def _shared_conn():
    """Eine dauerhafte TR-064-Verbindung (der Aufbau dauert ~5 s); bei Verbindungsfehlern wird sie neu aufgebaut (_with_conn).
    Nur unter _PC_LOCK aufrufen."""
    global _pc, _pc_key
    key = tuple(os.environ.get(k, "") for k in ("FRITZ_HOST", "FRITZ_TR064_USER", "FRITZ_TR064_PASSWORD"))
    if _pc is None or key != _pc_key:               # Zugangsdaten in den Einstellungen geaendert: neu verbinden
        _pc, _pc_key = _conn(), key
    return _pc


def internal_phones():
    """Interne Anschluesse der Fritzbox fuer die Auswahl: DECT-Handsets, analoge Anschluesse (FON) und IP-Telefone mit ihren internen Nummern.
    DECT-Nummern sind abgeleitet (Handset 1 = **610, 2 = **611 ...). Die eigenen SIP-Konten (Asterisk, LieschenMueller) fehlen. 5 Minuten zwischengespeichert."""
    with _PC_LOCK:
        return _internal_phones()


def _internal_phones():
    import time as _t
    global _pc
    try:
        c = _shared_conn()
    except Exception:
        _pc = None
        raise
    out = []
    try:
        for i in range(0, 20):
            try:
                e = c.call_action("X_AVM-DE_Dect1", "GetGenericDectEntry", NewIndex=i)
            except Exception:
                break
            if str(e.get("NewID", "")).isdigit() and 1 <= int(e["NewID"]) <= 6:
                out.append({"number": f"**61{int(e['NewID']) - 1}", "name": e.get("NewName") or f"DECT {e['NewID']}", "kind": "DECT"})
    except Exception:
        pass
    for i in (1, 2):
        try:
            nm = c.call_action("X_VoIP1", "X_AVM-DE_GetPhonePort", NewIndex=i).get("NewX_AVM-DE_PhoneName", "")
            if nm.upper().startswith("FON"):
                out.append({"number": f"**{i}", "name": nm.split(":")[-1].strip() and nm or f"FON{i}", "kind": "analog"})
        except Exception:
            break
    for i in range(0, 12):
        try:
            e = c.call_action("X_VoIP1", "X_AVM-DE_GetClient3", **{"NewX_AVM-DE_ClientIndex": i})
        except Exception:
            break
        n = str(e.get("NewX_AVM-DE_InternalNumber", "")).strip()
        user = (e.get("NewX_AVM-DE_ClientUsername") or "").lower()
        if n and user not in ("asterisk", "lieschenmueller"):
            out.append({"number": "**" + n, "name": e.get("NewX_AVM-DE_PhoneName") or user, "kind": "IP"})
    if not out:
        _pc = None                             # nichts gelesen: Verbindung beim naechsten Mal neu aufbauen
    if out:                                    # bei einem Fehler den letzten guten Stand behalten
        _phones_cache.update(t=_t.time(), v=out)
    return _phones_cache["v"]


def cached_phones():
    """Letzter bekannter Stand, ohne Wartezeit (Weboberflaeche); aufgefrischt wird im Hintergrund (refresh_phones)."""
    return _phones_cache["v"]


async def refresh_phones():
    """Beim Start und danach alle 5 Minuten im Hintergrund auffrischen (nie in einer Anfrage der Weboberflaeche)."""
    import asyncio
    while True:
        try:
            await asyncio.to_thread(internal_phones)
        except Exception:
            pass
        await asyncio.sleep(300)


def answered_by(caller, t0, t1):
    """Welches Telefon hat einen eingehenden Anruf angenommen? Laut Anrufliste der Fritzbox (TR-064): Eintrag 'angenommen' mit gleicher
    Anrufernummer zur gleichen Zeit, dessen Geraet nicht der Asterisk-Anschluss ist. Leer, wenn unbekannt."""
    from datetime import datetime
    from fritzconnection import FritzConnection
    from fritzconnection.lib.fritzcall import FritzCall
    user, pw = os.environ.get("FRITZ_TR064_USER", ""), os.environ.get("FRITZ_TR064_PASSWORD", "")
    key = _key(caller or "")
    if not (user and pw and key):
        return ""
    fc = FritzConnection(address=os.environ.get("FRITZ_HOST", "192.168.178.1"), user=user, password=pw)
    own = (os.environ.get("FRITZ_USER", "") or "").strip().lower()
    best, best_dt = "", None
    for c in FritzCall(fc).get_calls(days=1):
        if str(c.Type) != "1":
            continue
        dev = str(c.Device or "").strip()
        if not dev or dev.lower() in ("none", own, "lieschenmueller"):
            continue
        if _key(str(c.Caller or "")) != key:
            continue
        try:
            ts = datetime.strptime(str(c.Date), "%d.%m.%y %H:%M").timestamp()
        except ValueError:
            continue
        if t0 - 120 <= ts <= (t1 or t0) + 60 and (best_dt is None or abs(ts - t0) < abs(best_dt - t0)):
            best, best_dt = dev, ts
    return best


# --- Auf welche Rufnummern antwortet Asterisk (das IP-Telefon in der Fritzbox)? ---
import re as _re
import xml.etree.ElementTree as _ET
import logging as _logging

_log = _logging.getLogger("fritz")


def _conn():
    from fritzconnection import FritzConnection
    user, pw = os.environ.get("FRITZ_TR064_USER", ""), os.environ.get("FRITZ_TR064_PASSWORD", "")
    if not (user and pw):
        raise RuntimeError("FRITZ_TR064_USER / FRITZ_TR064_PASSWORD fehlen in .env")
    return FritzConnection(address=os.environ.get("FRITZ_HOST", "192.168.178.1"), user=user, password=pw)


def _items(xml):
    out = []
    try:
        for it in _ET.fromstring(xml or "<List/>").findall("Item"):
            out.append({"number": (it.findtext("Number") or "").strip(), "type": (it.findtext("Type") or "").strip(),
                        "index": (it.findtext("Index") or "").strip()})
    except _ET.ParseError:
        pass
    return out


def _asterisk_client(fc):
    """(Index, Daten) des IP-Telefons, das Asterisk ist (Benutzername wie FRITZ_USER)."""
    own = (os.environ.get("FRITZ_USER", "") or "").strip().lower()
    n = fc.call_action("X_VoIP1", "X_AVM-DE_GetNumberOfClients")["NewX_AVM-DE_NumberOfClients"]
    for i in range(n):
        c = fc.call_action("X_VoIP1", "X_AVM-DE_GetClient3", **{"NewX_AVM-DE_ClientIndex": i})
        if str(c.get("NewX_AVM-DE_ClientUsername", "")).strip().lower() == own:
            return i, c
    raise RuntimeError("In der Fritzbox gibt es kein IP-Telefon mit dem Benutzernamen aus FRITZ_USER")


PW_HINT = ("Das Kennwort des IP-Telefons „{phone}“ enthält Zeichen, die die Fritzbox beim Ändern über die Schnittstelle (TR-064) "
           "nicht annimmt (erlaubt sind nur Buchstaben, Ziffern und - . _), obwohl sie in der Oberfläche der Fritzbox gehen. "
           "Entweder die Rufnummern in der Fritzbox selbst einstellen (Telefonie → Telefoniegeräte → {phone} → Bearbeiten → "
           "Ankommende Anrufe) oder das Kennwort dort und hier in den Einstellungen (Fritzbox → Kennwort des IP-Telefons) auf "
           "eines nur aus Buchstaben, Ziffern und - . _ ändern.")


def write_block(fc, phone=""):
    """Warum die Rufnummernliste nicht ueber TR-064 geschrieben werden kann ('' = geht). SetClient3 schickt das Kennwort mit;
    erlaubt sind dort nur die Zeichen aus GetInfoEx (X_AVM-DE_ClientPasswordAllowedChars), sonst UPnPError 803."""
    pw = os.environ.get("FRITZ_PASSWORD", "")
    if not pw:
        return "Das Kennwort des IP-Telefons fehlt in den Einstellungen; ohne es kann die Fritzbox nicht geändert werden."
    try:
        info = fc.call_action("X_VoIP1", "GetInfoEx")
        allowed = info.get("NewX_AVM-DE_ClientPasswordAllowedChars") or ""
        lo, hi = int(info.get("NewX_AVM-DE_ClientPasswordMinChars") or 0), int(info.get("NewX_AVM-DE_ClientPasswordMaxChars") or 999)
    except Exception:
        return ""                       # unbekannt: Schreiben versuchen, die Fritzbox meldet dann selbst einen Fehler
    if (allowed and any(c not in allowed for c in pw)) or not lo <= len(pw) <= hi:
        return PW_HINT.format(phone=phone or "LieschenMueller")
    return ""


def asterisk_numbers():
    """Rufnummern der Fritzbox und die, auf die Asterisk antwortet. Nur lesend. write_via: 'web' (Weboberflaeche der Fritzbox,
    aendert das Kennwort nicht) oder 'tr064' (SetClient3); write_block nur, wenn keins von beiden geht."""
    fc = _conn()
    idx, c = _asterisk_client(fc)
    inc = _items(c.get("NewX_AVM-DE_InComingNumbers", ""))
    all_calls = any(i["type"] == "eAllCalls" for i in inc)
    mine = [i["number"] for i in inc if i["number"]]
    nums = _items(fc.call_action("X_VoIP1", "X_AVM-DE_GetNumbers")["NewNumberList"])
    avail = [{"number": i["number"], "index": i["index"], "on": all_calls or i["number"] in mine}
             for i in nums if i["number"] and not _re.fullmatch(r"99\d{3}", i["number"])]   # 99NNN sind unsere Klingel-Rufnummern (Schleife!)
    phone = c.get("NewX_AVM-DE_PhoneName", "")
    why, via = write_block(fc, phone), "tr064"
    if why:                             # TR-064 geht nicht: die Weboberflaeche probieren (nur lesend; eine Anmeldung an der Box)
        from . import fritzweb
        try:
            fritzweb.ip_form(idx)
            why, via = "", "web"
        except Exception as e:
            web = str(e) if isinstance(e, fritzweb.FritzWebError) else f"nicht erreichbar ({type(e).__name__})"
            why += f" Über die Weboberfläche der Fritzbox ging es auch nicht: {web}"
            via = ""
    return {"phone": phone, "internal": c.get("NewX_AVM-DE_InternalNumber", ""),
            "outgoing": c.get("NewX_AVM-DE_OutGoingNumber", ""), "all_calls": all_calls, "numbers": avail,
            "write_block": why, "write_via": via}


def set_asterisk_numbers(selected):
    """Eingehende Rufnummern des Asterisk-Telefons in der Fritzbox setzen: ueber die Weboberflaeche (Formular edit_ipfon_num, mit
    Gegenlesen; das Kennwort wird dabei nicht gesendet). Nur wenn die Weboberflaeche nicht geht (und dort nichts geschrieben wurde),
    ueber TR-064 SetClient3 (gleiche Werte, nur die Nummernliste aendert sich; braucht das Kennwort)."""
    from . import fritzweb
    fc = _conn()
    idx, c = _asterisk_client(fc)
    nums = {i["number"]: i for i in _items(fc.call_action("X_VoIP1", "X_AVM-DE_GetNumbers")["NewNumberList"])}
    selected = [s for s in dict.fromkeys(selected) if s]
    bad = [s for s in selected if s not in nums or _re.fullmatch(r"99\d{3}", s)]
    if bad or not selected:
        raise RuntimeError("Ungueltige Auswahl: " + (", ".join(bad) if bad else "mindestens eine Rufnummer waehlen"))
    try:
        fritzweb.ip_set_numbers(idx, selected)
        return asterisk_numbers()
    except fritzweb.FritzWebError as e:
        if e.wrote:                     # gespeichert, aber falscher Stand (zurueckgeschrieben): nicht noch einmal anders versuchen
            raise
        web = str(e)
        _log.info("Fritzbox: Weboberflaeche fuer die Rufnummern des Asterisk-Telefons nicht nutzbar - Rueckfall TR-064")
    pw = os.environ.get("FRITZ_PASSWORD", "")
    why = write_block(fc, c.get("NewX_AVM-DE_PhoneName", ""))
    if why or not pw:
        raise RuntimeError(f"Weboberfläche der Fritzbox: {web} – {why or 'FRITZ_PASSWORD fehlt (Kennwort des IP-Telefons)'}")
    xml = "<List>" + "".join(f"<Item><Number>{s}</Number><Type>eVoIP</Type><Index>{nums[s]['index']}</Index><Name></Name></Item>" for s in selected) + "</List>"
    fc.call_action("X_VoIP1", "X_AVM-DE_SetClient3", **{
        "NewX_AVM-DE_ClientIndex": idx, "NewX_AVM-DE_ClientPassword": pw,
        "NewX_AVM-DE_ClientId": c.get("NewX_AVM-DE_ClientId", ""), "NewX_AVM-DE_PhoneName": c.get("NewX_AVM-DE_PhoneName", ""),
        "NewX_AVM-DE_OutGoingNumber": c.get("NewX_AVM-DE_OutGoingNumber", ""), "NewX_AVM-DE_InComingNumbers": xml,
        "NewX_AVM-DE_ExternalRegistration": bool(c.get("NewX_AVM-DE_ExternalRegistration", False))})
    return asterisk_numbers()


def set_asterisk_out(number):
    """Ausgehende Rufnummer des Asterisk-Telefons (diese Nummer sieht der Angerufene bei Gespraechen ueber Asterisk) ueber die
    Weboberflaeche setzen (Formular edit_ipfon_num, mit Gegenlesen; das Kennwort wird nicht gesendet). Nur echte Rufnummern der Box."""
    from . import fritzweb
    fc = _conn()
    idx, _ = _asterisk_client(fc)
    nums = {i["number"] for i in _items(fc.call_action("X_VoIP1", "X_AVM-DE_GetNumbers")["NewNumberList"])}
    number = str(number or "").strip()
    if number not in nums or _re.fullmatch(r"99\d{1,4}", number):
        raise RuntimeError("Ungültige ausgehende Rufnummer: " + (number or "keine angegeben"))
    fritzweb.ip_set_out(idx, number)
    return asterisk_numbers()


_ring_cache = {"t": 0.0, "v": None}
RING_TTL = 60


def ring_numbers(fresh=False):
    """Klingel-Rufnummern 99NNN, die in der Fritzbox als eigene Rufnummer angelegt sind, mit dem Telefon **NNN (Name), falls bekannt.
    Ueber die dauerhafte Verbindung und 60 s zwischengespeichert: die Nummernfelder der Oberflaeche warten darauf (ein neuer
    Verbindungsaufbau dauert ~5 s)."""
    import time as _t
    if not fresh and _ring_cache["v"] is not None and _t.time() - _ring_cache["t"] < RING_TTL:
        return [dict(x) for x in _ring_cache["v"]]
    lst = _with_conn(lambda fc: fc.call_action("X_VoIP1", "X_AVM-DE_GetNumbers")["NewNumberList"])
    phones = {p["number"]: p["name"] for p in cached_phones()}
    out = []
    for i in _items(lst):
        n = i["number"]
        if _re.fullmatch(r"99\d{1,4}", n or ""):
            out.append({"number": n, "phone": "**" + n[2:], "phone_name": phones.get("**" + n[2:], "")})
    _ring_cache.update(t=_t.time(), v=out)
    return [dict(x) for x in out]


def ring_cache_clear():
    _ring_cache.update(t=0.0, v=None)


def _voip_accounts(fc):
    """Eigene Rufnummern der Fritzbox (Konten): [{index, number, registrar}] - ohne Kennwoerter."""
    out = []
    n = int(fc.call_action("X_VoIP1", "GetMaxVoIPNumbers").get("NewMaxVoIPNumbers") or 20)
    for i in range(n):
        try:
            a = fc.call_action("X_VoIP1", "X_AVM-DE_GetVoIPAccount", NewVoIPAccountIndex=i)
        except Exception:
            continue                    # Platz frei
        out.append({"index": i, "number": str(a.get("NewVoIPNumber") or ""), "registrar": str(a.get("NewVoIPRegistrar") or "")})
    return out, n


def _own_ip(accounts):
    """Adresse dieses Rechners aus Sicht der Fritzbox (Registrar der Klingel-Rufnummern): wie bei einer vorhandenen 99NNN,
    sonst die Quelladresse zur Fritzbox."""
    for a in accounts:
        if _re.fullmatch(r"99\d{1,4}", a["number"]) and _re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", a["registrar"]):
            return a["registrar"]
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((os.environ.get("FRITZ_HOST", "192.168.178.1"), 5060))
        return s.getsockname()[0]
    finally:
        s.close()


def ip_clients(fc=None):
    """IP-Telefone (auch FRITZ!Apps) nach interner Nummer: {'**NNN': {idx, name, incoming, all, out}} - idx ist der TR-064-Client-
    Index (X_AVM-DE_GetClient3) und zugleich ip_idx der Weboberflaeche; all = "alle Rufnummern" (eAllCalls)."""
    if fc is None:
        return _with_conn(ip_clients)
    out = {}
    for i in range(int(fc.call_action("X_VoIP1", "X_AVM-DE_GetNumberOfClients")["NewX_AVM-DE_NumberOfClients"])):
        c = fc.call_action("X_VoIP1", "X_AVM-DE_GetClient3", **{"NewX_AVM-DE_ClientIndex": i})
        n = str(c.get("NewX_AVM-DE_InternalNumber", "")).strip()
        if not n:
            continue
        inc = _items(c.get("NewX_AVM-DE_InComingNumbers", ""))
        out["**" + n] = {"idx": i, "name": c.get("NewX_AVM-DE_PhoneName", ""), "incoming": [x["number"] for x in inc if x["number"]],
                         "all": any(x["type"] == "eAllCalls" for x in inc), "out": str(c.get("NewX_AVM-DE_OutGoingNumber", "") or "")}
    return out


def ring_overview():
    """Interne Telefone (**NNN) mit ihrer Klingel-Rufnummer 99NNN: in der Fritzbox angelegt? Mit Registrar = dieser Rechner (unsere)?
    Ueber die dauerhafte TR-064-Verbindung (ein neuer Aufbau dauert ~5 s); ist sie veraltet, einmal neu verbinden."""
    accounts, _ = _with_conn(_voip_accounts)
    me = _own_ip(accounts)
    by_num = {a["number"]: a for a in accounts}
    phones = cached_phones() or internal_phones()

    with _PC_LOCK:                    # IP-Telefone: eingehende Rufnummern lassen sich ueber TR-064 lesen (DECT/analog nicht)
        try:
            ip = ip_clients(_shared_conn())
        except Exception:
            ip = {}
    out = []
    for p in phones:
        if not _re.fullmatch(r"\*\*\d{1,4}", p["number"]):
            continue
        n = "99" + p["number"][2:]
        a = by_num.get(n)
        c = ip.get(p["number"])
        row = {"phone": p["number"], "name": p["name"], "kind": p["kind"], "ring": n, "exists": bool(a),
               "ours": bool(a) and a["registrar"] == me, "registrar": a["registrar"] if a else "", "own_ip": me,
               "assigned": (n in c["incoming"]) if c else None}      # None = nicht lesbar (DECT)
        if c:                           # IP-Telefon: Client-Index (fuer die Weboberflaeche), "alle", ausgehende Nummer
            row.update(client_idx=c["idx"], ip_all=c["all"], ip_out=c["out"])
        out.append(row)
    return out


def create_ring_account(n):
    """Klingel-Rufnummer 99NNN in der Fritzbox anlegen ("Anderer Anbieter", Registrar = dieser Rechner, zufaelliges Kennwort,
    Asterisk prueft es nicht). Gibt es die Nummer schon, wird nichts angelegt (RuntimeError)."""
    import secrets
    if not _re.fullmatch(r"99\d{1,4}", n or ""):
        raise RuntimeError("Ungültige Klingel-Rufnummer")
    fc = _conn()
    accounts, mx = _voip_accounts(fc)
    if any(a["number"] == n for a in accounts):
        raise RuntimeError(f"Die Rufnummer {n} ist in der Fritzbox schon angelegt – es wurde nichts geändert.")
    used = {a["index"] for a in accounts}
    free = next((i for i in range(mx) if i not in used), None)
    if free is None:
        raise RuntimeError(f"In der Fritzbox ist kein Platz für eine weitere Rufnummer frei (höchstens {mx}).")
    fc.call_action("X_VoIP1", "X_AVM-DE_AddVoIPAccount", NewVoIPAccountIndex=free, NewVoIPRegistrar=_own_ip(accounts),
                   NewVoIPNumber=n, NewVoIPUsername=n, NewVoIPPassword=secrets.token_hex(8),
                   NewVoIPOutboundProxy="", NewVoIPSTUNServer="")
    ring_cache_clear()                      # neue Nummer sofort in den Auswahllisten
    return free
