#!/usr/bin/env python3
"""Waechter fuer LieschenMueller (laeuft auf dem Docker-Host, per Cron jede Minute; nur Standardbibliothek).

Prueft: Bridge antwortet, Asterisk laeuft, Asterisk ist bei der Fritzbox angemeldet (Grundleitung), und jede eingetragene
Klingel-Rufnummer 99NNN (data/asterisk/fritz.conf, siehe ring_numbers) ist bei Asterisk angemeldet. Startet Container, die Docker als "unhealthy" meldet, neu.
Bei einer Stoerung (nach 2 Fehlversuchen in Folge, bei Klingel-Rufnummern nach 7) und bei Entwarnung geht eine E-Mail an die Empfaenger der Benachrichtigungen
(Mailserver und Empfaenger aus der Datenbank der Bridge). Zustand: /var/tmp/lieschen-watchdog.json."""
import json
import os
import re
import smtplib
import sqlite3
import ssl
import subprocess
import sys
import time
import urllib.request
from email.message import EmailMessage

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STATE = "/var/tmp/lieschen-watchdog.json"
AST = os.environ.get("LM_ASTERISK", "lieschenmueller-asterisk-1")
BRI = os.environ.get("LM_BRIDGE", "lieschenmueller-bridge-1")
FAILS_RING = 7      # Minuten bis zum Alarm fuer eine Klingel-Rufnummer: sie meldet sich nach einem Neustart von Asterisk erst nach bis zu 5 Minuten wieder an
FAILS_DEFAULT = 2


def env(key, default=""):
    try:
        for line in open(os.path.join(ROOT, ".env"), encoding="utf-8"):
            if line.startswith(key + "="):
                return line.split("=", 1)[1].split("#")[0].strip().strip("'\"")
    except OSError:
        pass
    return default


def ring_numbers():
    """Klingel-Rufnummern 99NNN, die die Fritzbox bei Asterisk anmelden soll: AOR-Abschnitte in data/asterisk/fritz.conf (schreibt die
    Bridge aus den Einstellungen, seit 2026-11). Aeltere Installationen hatten 99613 fest in asterisk/conf/pjsip.conf - die gilt weiter,
    solange sie dort steht. Ohne eingetragene Nummer wird nur die Grundleitung (Anmeldung bei der Fritzbox) geprueft."""
    nums = []
    for path in (os.path.join(ROOT, "data", "asterisk", "fritz.conf"), os.path.join(ROOT, "asterisk", "conf", "pjsip.conf")):
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            continue
        sec = None
        for line in text.splitlines():
            line = line.split(";", 1)[0].strip()
            m = re.fullmatch(r"\[(\w+)\]", line)
            if m:
                sec = m.group(1)
            elif sec and re.fullmatch(r"99\d{1,4}", sec) and line.replace(" ", "") == "type=aor" and sec not in nums:
                nums.append(sec)
    return nums


def run(*cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def ast(cmd):
    return run("docker", "exec", AST, "asterisk", "-rx", cmd)


def checks():
    bad = {}
    port = env("WEB_PORT", "8080")
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/login", timeout=6)
    except Exception as e:
        bad["bridge"] = f"Die Bridge antwortet nicht ({type(e).__name__})"
    rc, out = ast("core show uptime")
    if rc != 0:
        bad["asterisk"] = "Asterisk läuft nicht oder antwortet nicht"
        return bad
    rc, out = ast("pjsip show registrations")
    if "Registered" not in out:
        bad["fritz-reg"] = "Asterisk ist nicht bei der Fritzbox angemeldet (Telefonie nach außen und Anrufannahme betroffen)"
    rc, out = ast("pjsip show contacts")
    for n in ring_numbers():
        if f"{n}/" not in out:
            bad["ring:" + n] = (f"Die Klingel-Rufnummer {n} ist nicht bei Asterisk angemeldet (Wahlregel/Aufzeichnung per Vorwahl, "
                                "Anzeige der Anrufernummer und Klingeln über diese Nummer betroffen)")
    return bad


def heal():
    """Als unhealthy gemeldete Container neu starten."""
    rc, out = run("docker", "ps", "--filter", "health=unhealthy", "--format", "{{.Names}}")
    done = []
    for name in out.split():
        if name in (AST, BRI):
            run("docker", "restart", name, timeout=90)
            done.append(name)
    return done


def mail(subject, text):
    try:
        db = sqlite3.connect(os.path.join(ROOT, "data", "lieschen.db"), timeout=5)
        meta = dict(db.execute("SELECT key, value FROM meta WHERE key LIKE 'smtp_%'").fetchall())
        rcpt = sorted({r[0] for r in db.execute("SELECT email FROM mail_recipients WHERE enabled=1")})
    except Exception as e:
        print("Mail: Datenbank nicht lesbar:", e)
        return
    host, user, pw = meta.get("smtp_host", ""), meta.get("smtp_user", ""), meta.get("smtp_password", "")
    if not (host and user and pw and rcpt):
        print("Mail: nicht eingerichtet, kein Empfaenger")
        return
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = meta.get("smtp_from") or user, ", ".join(rcpt), subject
    msg.set_content(text)
    port, sec = int(meta.get("smtp_port") or 587), meta.get("smtp_security", "starttls")
    try:
        ctx = ssl.create_default_context()        # Zertifikat und Hostname pruefen (wie bridge/app/notify.py)
        s = smtplib.SMTP_SSL(host, port, timeout=25, context=ctx) if sec == "ssl" else smtplib.SMTP(host, port, timeout=25)
        with s:
            if sec == "starttls":
                s.starttls(context=ctx)
            s.login(user, pw)
            s.send_message(msg)
    except Exception as e:
        print("Mail: Versand fehlgeschlagen:", e)


def main():
    try:
        st = json.load(open(STATE))
    except (OSError, ValueError):
        st = {}
    fails, alerted = st.get("fails", {}), st.get("alerted", {})
    bad = checks()
    healed = heal()
    now = time.strftime("%d.%m.%Y %H:%M")
    new_alerts, cleared = [], []
    for key, msg in bad.items():
        fails[key] = fails.get(key, 0) + 1
        if fails[key] >= (FAILS_RING if key.startswith("ring") else FAILS_DEFAULT) and key not in alerted:
            alerted[key] = msg
            new_alerts.append(msg)
    for key in list(fails):
        if key not in bad:
            fails.pop(key)
            if key in alerted:
                cleared.append(alerted.pop(key))
    if new_alerts:
        mail("LieschenMüller: Störung", f"{now}\n\n" + "\n".join("- " + m for m in new_alerts)
             + (f"\n\nNeu gestartet: {', '.join(healed)}" if healed else "")
             + "\n\nBei ausgefallener Asterisk-Anmeldung wählt man ausgehend einfach ohne die Vorwahl für die Aufzeichnung (Standard *77).")
    if cleared:
        mail("LieschenMüller: Störung behoben", f"{now}\n\nWieder in Ordnung:\n" + "\n".join("- " + m for m in cleared))
    json.dump({"fails": fails, "alerted": alerted}, open(STATE, "w"))
    if bad:
        print(now, "Stoerung:", "; ".join(bad.values()))


if __name__ == "__main__":
    sys.exit(main())
