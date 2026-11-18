import asyncio
import os
import re

REASONS = {"0": "Verbindung fehlgeschlagen", "1": "Niemand hat abgehoben", "3": "Niemand hat abgehoben",
           "5": "Besetzt", "8": "Leitung nicht erreichbar"}


BLOCK = [p.strip() for p in os.environ.get("DIAL_BLOCK", "0900,0137,0180,0190,118,00").split(",") if p.strip()]


def dial_number(raw):
    """Fritzbox-Wahlformat mit Sicherheitsregeln: +49 171 ... -> 0171 ..., '(0)' wird entfernt,
    nur Ziffern oder interne **N-Nummern, gesperrte Praefixe (DIAL_BLOCK in .env, Standard:
    Mehrwert-/Service-/Auskunftsnummern und Ausland) werden abgelehnt."""
    n = re.sub(r"\(0\)", "", raw or "")
    n = re.sub(r"[\s\-/().]", "", n)
    if n.startswith("+49"):
        n = "0" + n[3:]
    elif n.startswith("+"):
        n = "00" + n[1:]
    if re.fullmatch(r"\*\*\d{1,4}", n):
        return n                              # interne Fritzbox-Nummer
    if not re.fullmatch(r"\d{3,20}", n):
        hint = (" Intern geht nur **NNN (z. B. **610), extern nur Ziffern, ohne Sonderzeichen oder eine "
                "Kombination aus beidem.") if "*" in n or "#" in n else " Erlaubt sind 3 bis 20 Ziffern."
        raise ValueError(f"Ungültige Nummer „{(raw or '').strip()}“.{hint}")
    if any(n.startswith(p) for p in BLOCK):
        raise ValueError("Diese Rufnummer ist gesperrt (DIAL_BLOCK in .env)")
    return n


def dial_list(raw):
    """Eine oder mehrere Zielnummern, mit Komma oder Semikolon getrennt (z. B. "**610, 0171 1234567"): jede wird mit dial_number
    geprueft und bereinigt. Rueckgabe: Liste ohne Doppelte (hoechstens 5). ValueError bei leerer Eingabe oder ungueltiger Nummer."""
    out = []
    for p in re.split(r"[;,]", raw or ""):
        if p.strip():
            n = dial_number(p)
            if n not in out:
                out.append(n)
    if not out:
        raise ValueError("Keine Nummer angegeben")
    if len(out) > 5:
        raise ValueError("Höchstens 5 Nummern")
    return out


def dial_numbers(raw):
    """Wie dial_list, als Zielstring fuer Asterisk: n1+n2 (die Nummern klingeln gleichzeitig)."""
    return "+".join(dial_list(raw))


async def _read_block(r):
    lines = []
    while True:
        raw = await r.readline()
        if not raw:                           # EOF: sonst Endlosschleife ohne await-Pause
            raise ConnectionError("AMI-Verbindung geschlossen")
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line == "" and lines:
            return dict(l.split(": ", 1) for l in lines if ": " in l)
        if line == "" and not lines:
            continue
        lines.append(line)


async def originate(number, uuid, timeout_s=45, context="outbound-ai", variables=None):
    """Laesst Asterisk die Nummer waehlen. Rueckgabe (ok, grund). Bei ok laeuft danach
    [outbound-ai] und der AudioSocket verbindet sich mit der Bridge."""
    secret = os.environ.get("AMI_SECRET", "")
    r, w = await asyncio.open_connection("127.0.0.1", 5038)
    try:
        await r.readline()  # Banner
        w.write(f"Action: Login\r\nUsername: bridge\r\nSecret: {secret}\r\nEvents: call\r\n\r\n".encode())
        if (await asyncio.wait_for(_read_block(r), 5)).get("Response") != "Success":
            return False, "AMI-Anmeldung fehlgeschlagen"
        w.write((f"Action: Originate\r\nActionID: {uuid}\r\nChannel: PJSIP/{number}@fritz\r\n"
                 f"Context: {context}\r\nExten: s\r\nPriority: 1\r\nTimeout: {timeout_s * 1000}\r\n"
                 f"Async: true\r\nVariable: CALLUUID={uuid}\r\n"
                 + "".join(f"Variable: {k}={v}\r\n" for k, v in (variables or {}).items())
                 + "\r\n").encode())
        end = asyncio.get_event_loop().time() + timeout_s + 15
        while True:
            left = end - asyncio.get_event_loop().time()
            if left <= 0:
                return False, "Keine Rueckmeldung von Asterisk"
            m = await asyncio.wait_for(_read_block(r), left)
            if m.get("Event") == "OriginateResponse" and m.get("ActionID") == uuid:
                if m.get("Response") == "Success":
                    return True, ""
                return False, REASONS.get(m.get("Reason", ""), "Anruf nicht zustande gekommen")
            if m.get("Response") == "Error" and m.get("ActionID") == uuid:
                return False, m.get("Message", "Fehler")
    finally:
        try:
            w.write(b"Action: Logoff\r\n\r\n")
            w.close()
        except Exception:
            pass


async def redirect(channel, exten, label, variables=None, context="from-fritz"):
    """Leitet einen laufenden (klingelnden) Anruf im Dialplan um: optional Variablen setzen, dann Sprung zu exten/label."""
    secret = os.environ.get("AMI_SECRET", "")
    r, w = await asyncio.open_connection("127.0.0.1", 5038)
    try:
        await r.readline()
        w.write(f"Action: Login\r\nUsername: bridge\r\nSecret: {secret}\r\nEvents: off\r\n\r\n".encode())
        if (await asyncio.wait_for(_read_block(r), 5)).get("Response") != "Success":
            return False, "AMI-Anmeldung fehlgeschlagen"
        for k, v in (variables or {}).items():
            w.write(f"Action: Setvar\r\nChannel: {channel}\r\nVariable: {k}\r\nValue: {v}\r\n\r\n".encode())
            m = await asyncio.wait_for(_read_block(r), 5)
            if m.get("Response") != "Success":
                return False, m.get("Message", "Anruf nicht (mehr) vorhanden")
        w.write(f"Action: Redirect\r\nChannel: {channel}\r\nContext: {context}\r\nExten: {exten}\r\nPriority: {label}\r\n\r\n".encode())
        m = await asyncio.wait_for(_read_block(r), 5)
        if m.get("Response") != "Success":
            return False, m.get("Message", "Umleiten fehlgeschlagen")
        return True, ""
    finally:
        try:
            w.write(b"Action: Logoff\r\n\r\n")
            w.close()
        except Exception:
            pass


async def action(name, fields=None, list_end=None, timeout_s=10):
    """Eine AMI-Aktion (statt CLI-Befehl ueber Command). Rueckgabe (Antwort, Ereignisse): Antwort = dict der Antwort
    (Response: Success/Error, Message ...); bei Listen-Aktionen (list_end = Name des Abschluss-Ereignisses) die Ereignisse dazu."""
    import uuid as _uuid
    secret = os.environ.get("AMI_SECRET", "")
    aid = _uuid.uuid4().hex
    r, w = await asyncio.open_connection("127.0.0.1", 5038)
    try:
        await r.readline()
        w.write(f"Action: Login\r\nUsername: bridge\r\nSecret: {secret}\r\nEvents: off\r\n\r\n".encode())
        if (await asyncio.wait_for(_read_block(r), 5)).get("Response") != "Success":
            raise RuntimeError("AMI-Anmeldung fehlgeschlagen")
        for k, v in (fields or {}).items():
            if re.search(r"[\r\n]", str(v)):
                raise ValueError(f"AMI-Feld {k}: Zeilenumbruch nicht erlaubt")
        w.write((f"Action: {name}\r\nActionID: {aid}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in (fields or {}).items()) + "\r\n").encode())

        async def read():
            resp, events = None, []
            while True:
                m = await _read_block(r)
                if m.get("ActionID") != aid:
                    continue
                if resp is None and "Response" in m:
                    resp = m
                    if not list_end or m.get("Response") != "Success":
                        return resp, events
                elif list_end and m.get("Event") == list_end:
                    return resp, events
                elif "Event" in m:
                    events.append(m)
        return await asyncio.wait_for(read(), timeout_s)
    finally:
        try:
            w.write(b"Action: Logoff\r\n\r\n")
            w.close()
        except Exception:
            pass


async def registration_status(name, timeout_s=10):
    """Status der ausgehenden Anmeldung (z. B. fritz-reg): 'Registered', 'Rejected', 'Unregistered' ... oder '' (unbekannt).
    AMI-Aktion PJSIPShowRegistrationsOutbound (Recht write = reporting)."""
    resp, events = await action("PJSIPShowRegistrationsOutbound", list_end="OutboundRegistrationDetailComplete", timeout_s=timeout_s)
    if resp.get("Response") != "Success":
        if "ermission" in resp.get("Message", ""):
            raise RuntimeError("AMI: Aktion nicht erlaubt (manager.conf: write = …,reporting)")
        return ""
    for e in events:
        if e.get("Event") == "OutboundRegistrationDetail" and e.get("ObjectName") == name:
            return e.get("Status", "")
    return ""


async def bridged_peer(chan, timeout_s=10):
    """Gegenseite eines Kanals ueber die gemeinsame BridgeId (AMI CoreShowChannels, Recht reporting); '' wenn nicht ermittelbar.
    Asterisk 23 gibt die Zeile 'Bridged Channel:' in 'core show channel' nicht mehr aus."""
    resp, events = await action("CoreShowChannels", list_end="CoreShowChannelsComplete", timeout_s=timeout_s)
    if resp.get("Response") != "Success":
        return ""
    chans = [e for e in events if e.get("Event") == "CoreShowChannel"]
    bid = next((e.get("BridgeId") for e in chans if e.get("Channel") == chan), "")
    if not bid:
        return ""
    return next((e["Channel"] for e in chans if e.get("BridgeId") == bid and e.get("Channel") != chan), "")


async def command(cmd, timeout_s=15):
    """CLI-Befehl in Asterisk ausfuehren (AMI Command); gibt die Ausgabe als Text zurueck."""
    secret = os.environ.get("AMI_SECRET", "")
    r, w = await asyncio.open_connection("127.0.0.1", 5038)
    try:
        await r.readline()
        w.write(f"Action: Login\r\nUsername: bridge\r\nSecret: {secret}\r\nEvents: off\r\n\r\n".encode())
        if (await asyncio.wait_for(_read_block(r), 5)).get("Response") != "Success":
            raise RuntimeError("AMI-Anmeldung fehlgeschlagen")
        w.write(f"Action: Command\r\nCommand: {cmd}\r\n\r\n".encode())
        out = []

        async def read_all():
            seen = False
            while True:
                raw = await r.readline()
                if not raw:
                    raise ConnectionError("AMI-Verbindung geschlossen")
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("--END COMMAND--"):       # aeltere Asterisk-Versionen
                    return
                if line == "":
                    if seen:                                  # neuere Versionen: Antwort endet mit einer Leerzeile
                        return
                    continue
                seen = True
                if line.startswith("Output: "):
                    out.append(line[8:])
                elif not line.startswith(("Response:", "Message:", "ActionID:", "Privilege:")):
                    out.append(line)
        await asyncio.wait_for(read_all(), timeout_s)
        text = "\n".join(out)
        if "Permission denied" in text:
            raise RuntimeError("AMI: Befehl nicht erlaubt (manager.conf: write = …,command)")
        return text
    finally:
        try:
            w.write(b"Action: Logoff\r\n\r\n")
            w.close()
        except Exception:
            pass


async def play_into(key, sound):
    """Spielt eine Ansage in einen laufenden Kanal (Local-Kanal mit ChanSpy im Fluestermodus, Dialplan [recspy]); key: Nummer, die die Bridge dem Kanal zugeordnet hat."""
    secret = os.environ.get("AMI_SECRET", "")
    r, w = await asyncio.open_connection("127.0.0.1", 5038)
    try:
        await r.readline()
        w.write(f"Action: Login\r\nUsername: bridge\r\nSecret: {secret}\r\nEvents: off\r\n\r\n".encode())
        if (await asyncio.wait_for(_read_block(r), 5)).get("Response") != "Success":
            return False, "AMI-Anmeldung fehlgeschlagen"
        w.write((f"Action: Originate\r\nChannel: Local/{key}@recspy\r\nApplication: Playback\r\nData: {sound}\r\nTimeout: 20000\r\nAsync: true\r\n\r\n").encode())
        m = await asyncio.wait_for(_read_block(r), 8)
        return (m.get("Response") == "Success"), m.get("Message", "")
    finally:
        try:
            w.write(b"Action: Logoff\r\n\r\n")
            w.close()
        except Exception:
            pass
