#!/usr/bin/env python3
"""Rendert die Asterisk-Konfiguration aus .env (ohne Shell-Auswertung, ohne sed-Fallen)."""
import os
import re
import secrets
import sys

root = os.path.dirname(os.path.abspath(__file__))
# Pfade: Standard wie bisher (../.env, conf/, ../data); fuer Tests per Umgebung umlenkbar (tests/integration)
envp = os.environ.get("LM_ENV_FILE") or os.path.join(root, "..", ".env")
data = os.environ.get("LM_DATA_DIR") or os.path.join(root, "..", "data")


def load():
    env = {}
    for line in open(envp, encoding="utf-8"):
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.rstrip("\n"))
        if m:
            v = m.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            else:      # Kommentar hinter einem Wert ohne Anfuehrungszeichen ("WEB_PORT=8091   # ...") gehoert nicht zum Wert
                v = re.sub(r"(^|\s)#.*$", "", v).strip()
            env[m.group(1)] = v
    return env


env = load()
if not env.get("AMI_SECRET"):
    env["AMI_SECRET"] = secrets.token_urlsafe(18)
    with open(envp, "a", encoding="utf-8") as f:
        f.write(f"\nAMI_SECRET={env['AMI_SECRET']}\n")

# RING_PASSWORD (frueher fuer 99613 erzeugt) wird nicht mehr benutzt: 99613 ist ueber die IP der Fritzbox abgesichert
# (pjsip.conf.tpl, dyn/fritz.conf). Ein vorhandener Eintrag in der .env stoert nicht.
os.environ.setdefault("SIP_ACL", env.get("SIP_ACL", "1"))      # Notschalter fuer die IP-Sperre (fritzconf.py)

# In der Asterisk-Konfiguration beginnt ';' einen Kommentar, Zeilenumbrueche zerstoeren die Zeile
for key in ("AMI_SECRET",):
    v = env.get(key, "")
    if not v:
        sys.exit(f"{key} fehlt in .env")
    if re.search(r"[;\n\r]", v):
        sys.exit(f"{key} darf kein ';' oder Zeilenumbruch enthalten (Asterisk-Konfiguration)")

import hashlib
# Schluessel fuer die Aufrufe des Dialplans an die Bridge (/internal/*): abgeleitet, damit AMI_SECRET nicht in Asterisk-Protokollen steht
internal_key = hashlib.sha256(("internal:" + env["AMI_SECRET"]).encode()).hexdigest()[:24]
# Platzhalter der Vorlagen (die Fritzbox-Zugangsdaten stehen nicht hier, sondern in data/asterisk/fritz.conf, siehe fritzconf.py)
subst = {"@INTERNAL_KEY@": internal_key, "@AMI_SECRET@": env["AMI_SECRET"], "${BRIDGE_PORT}": env.get("WEB_PORT", "8080")}
out = os.environ.get("LM_CONF_DIR") or os.path.join(root, "conf")
os.makedirs(out, exist_ok=True)
# Zugangsdaten der Fritzbox: data/asterisk/fritz.conf (nur anlegen, wenn es fehlt; danach pflegt die Bridge die Datei aus den Einstellungen)
import importlib.util
spec = importlib.util.spec_from_file_location("fritzconf", os.path.join(root, "..", "bridge", "app", "fritzconf.py"))
fritzconf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fritzconf)
dyn = os.path.join(data, "asterisk", "fritz.conf")
os.makedirs(os.path.dirname(dyn), exist_ok=True)
os.chmod(os.path.dirname(dyn), 0o755)
def db_creds():
    """Zugangsdaten aus der Datenbank der Bridge (meta cfg:*, nur lesend), Vorrang vor der .env wie in config.py; None, wenn nicht lesbar."""
    import sqlite3
    path = os.path.join(data, "lieschen.db")
    if not os.path.exists(path):
        return None
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            rows = dict(c.execute("SELECT key, value FROM meta WHERE key IN ('cfg:fritz_user','cfg:fritz_password','cfg:fritz_host')").fetchall())
        finally:
            c.close()
    except sqlite3.Error as e:
        print(f"Hinweis: Datenbank nicht lesbar ({e}) - fritz.conf aus der .env bzw. als Platzhalter")
        return None
    vals = [rows.get("cfg:" + k) or env.get(k.upper(), "") for k in ("fritz_user", "fritz_password", "fritz_host")]
    return vals if all(vals) else None


# Eine vorhandene fritz.conf wird nie ueberschrieben (die Bridge gleicht sie beim Start mit der Datenbank ab, astconf.py).
# Erste Datei: mit der Klingel-Rufnummer der Voreinstellung "alle Telefone klingeln" (99613); die Bridge schreibt die wirklichen Nummern.
if not os.path.exists(dyn):
    creds = db_creds()
    if creds:
        fritzconf.write(dyn, *creds, ring=["99613"])
    elif env.get("FRITZ_USER") and env.get("FRITZ_PASSWORD") and env.get("FRITZ_HOST"):
        fritzconf.write(dyn, env["FRITZ_USER"], env["FRITZ_PASSWORD"], env["FRITZ_HOST"], ring=["99613"])
    else:      # noch nicht eingerichtet: Zugangsdaten spaeter in der Weboberflaeche (Einstellungen) eintragen
        with open(dyn, "w", encoding="utf-8") as f:
            f.write("; Fritzbox noch nicht eingerichtet: Zugangsdaten in der Weboberflaeche eintragen (Einstellungen)\n")
        os.chmod(dyn, 0o644)
# Dialplan-Globale (Vorwahl fuer aufgezeichnete ausgehende Gespraeche, meta rec_prefix): data/asterisk/globals.conf, nur anlegen, wenn sie
# fehlt (danach schreibt die Bridge sie beim Start und beim Speichern der Vorwahl, astconf.write_globals). Ohne Datenbank: Voreinstellung *77.
gfile = os.path.join(data, "asterisk", "globals.conf")
if not os.path.exists(gfile):
    prefix = "*77"
    dbp = os.path.join(data, "lieschen.db")
    if os.path.exists(dbp):
        import sqlite3
        try:
            c = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True, timeout=5)
            try:
                row = c.execute("SELECT value FROM meta WHERE key='rec_prefix'").fetchone()
            finally:
                c.close()
            if row is not None and re.fullmatch(r"[0-9*#]{0,8}", (row[0] or "").strip()):
                prefix = (row[0] or "").strip()
        except sqlite3.Error:
            pass
    with open(gfile, "w", encoding="utf-8") as f:
        f.write("; Erzeugt von render.py (danach von der Bridge), nicht von Hand aendern.\n"
                f"[globals]\nLM_GLOBALS=1\nREC_PREFIX={prefix}\n")
    os.chmod(gfile, 0o644)
for name in ("pjsip", "extensions", "manager"):
    text = open(os.path.join(root, "templates", f"{name}.conf.tpl"), encoding="utf-8").read()
    for k, v in subst.items():
        text = text.replace(k, v)
    path = os.path.join(out, f"{name}.conf")
    open(path, "w", encoding="utf-8").write(text)
    fritzconf.share_with_asterisk(path)   # enthalten Schluessel (AMI, interne Anfragen): nur Eigentuemer und Asterisk-Gruppe
for plain in ("modules.conf", "musiconhold.conf"):
    text = open(os.path.join(root, "templates", plain), encoding="utf-8").read()
    open(os.path.join(out, plain), "w", encoding="utf-8").write(text)
    os.chmod(os.path.join(out, plain), 0o644)
# Gemeinsame Verzeichnisse mit der Bridge (Aufnahmen kommen von Asterisk, Ansagen von der Bridge)
# Wartemusik-Verzeichnisse muessen beim Start von Asterisk existieren, sonst schaltet es die Musik ganz ab (die Bridge fuellt sie)
for sub in ("sounds/moh",):
    os.makedirs(os.path.join(data, sub), exist_ok=True)
    os.chmod(os.path.join(data, sub), 0o755)
import wave
# Wartemusik: Asterisk liest die Klassen aus dyn/moh.conf (schreibt die Bridge). Damit die Musik nicht abgeschaltet wird, wenn Asterisk vor der
# Bridge startet, gibt es hier eine Klasse "default" mit einer Stille-Datei (die Bridge ersetzt sie durch die echte Musik).
cdir = os.path.join(data, "sounds", "moh", "c", "default")
os.makedirs(cdir, exist_ok=True)
for d in (os.path.join(data, "sounds", "moh"), os.path.join(data, "sounds", "moh", "c"), cdir):
    os.chmod(d, 0o755)
if not os.listdir(cdir):
    with wave.open(os.path.join(cdir, "_leer.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(bytes(8000))
    os.chmod(os.path.join(cdir, "_leer.wav"), 0o644)
mohconf = os.path.join(data, "asterisk", "moh.conf")
if not os.path.exists(mohconf):
    with open(mohconf, "w", encoding="utf-8") as f:
        f.write("[default]\nmode=files\ndirectory=/sounds/moh/c/default\nrandom=yes\n")
    os.chmod(mohconf, 0o644)
for sub in ("rec-in", "sounds"):
    os.makedirs(os.path.join(data, sub), exist_ok=True)
os.chmod(os.path.join(data, "sounds"), 0o755)
# rec-in: der Asterisk-Benutzer im Container muss schreiben koennen (Rohaufnahmen, werden nach der Transkription geloescht); wenn moeglich nur
# Eigentuemer und Asterisk-Gruppe, sonst wie bisher fuer alle
fritzconf.share_with_asterisk(os.path.join(data, "rec-in"), 0o2770, 0o777)
print("Asterisk-Konfiguration geschrieben")
