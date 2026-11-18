"""Zugangsdaten der Fritzbox fuer Asterisk (PJSIP): wird von asterisk/render.py (aus der .env) und von der Bridge
(aus den Einstellungen in der Oberflaeche) geschrieben und von pjsip.conf per #include gelesen.

Absicherung (seit 2026-10): SIP-Anfragen nimmt Asterisk nur von der Fritzbox an (Abschnitt type=acl, IP aus der
Fritzbox-Adresse), und die Endpoints werden nur ueber die IP-Adresse erkannt (identify_by=ip). Sonst koennte sich ein
anderes Geraet im LAN als Klingel-Rufnummer 99NNN anmelden und die Fritzbox verdraengen. Notschalter: SIP_ACL=0 in der .env laesst den
ACL-Abschnitt weg (identify_by=ip bleibt)."""
import ipaddress
import os
import re
import socket

ASTERISK_GID = int(os.environ.get("ASTERISK_GID", "1000"))    # Gruppe des Asterisk-Benutzers im Container (Image andrius/asterisk)


def share_with_asterisk(path, mode=0o640, fallback=0o644):
    """Datei mit Zugangsdaten nur fuer Eigentuemer und die Asterisk-Gruppe lesbar machen. Ohne Recht zum Aendern der Gruppe (z. B. render.sh
    als normaler Benutzer) bleibt es beim alten, fuer alle lesbaren Modus, damit Asterisk die Datei weiter lesen kann."""
    try:
        os.chown(path, -1, ASTERISK_GID)
        os.chmod(path, mode)
    except OSError:
        os.chmod(path, fallback)

ACL_MARK = "[fritz-acl]"

TEMPLATE = """; Zugangsdaten der Fritzbox (IP-Telefon). Erzeugt von render.py bzw. der Bridge (Einstellungen), nicht von Hand aendern.
[fritz-auth]
type=auth
auth_type=userpass
username=@FRITZ_USER@
password=@FRITZ_PASSWORD@

[fritz]
type=aor
contact=sip:@FRITZ_HOST@

[fritz]
type=endpoint
context=from-fritz
disallow=all
allow=alaw,ulaw
aors=fritz@RING_AORS@
outbound_auth=fritz-auth
from_user=@FRITZ_USER@
from_domain=@FRITZ_HOST@
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
dtmf_mode=auto
; nur ueber die IP-Adresse der Fritzbox erkennen (nicht ueber den Benutzernamen im From-Header)
identify_by=ip

[fritz]
type=identify
endpoint=fritz
match=@FRITZ_HOST@
@ACL@"""

ACL = """
; Nur die Fritzbox (und dieser Rechner) duerfen SIP-Anfragen an Asterisk schicken (Anmeldung der Klingel-Rufnummern 99NNN, Anrufe).
; Gilt fuer alle eingehenden Anfragen; abschaltbar mit SIP_ACL=0 in der .env.
[fritz-acl]
type=acl
deny=0.0.0.0/0.0.0.0
permit=@FRITZ_IP@/255.255.255.255
permit=127.0.0.1/255.255.255.255
contact_deny=0.0.0.0/0.0.0.0
contact_permit=@FRITZ_IP@/255.255.255.255
contact_permit=127.0.0.1/255.255.255.255
"""

REG = """
[fritz-reg]
type=registration
outbound_auth=fritz-auth
server_uri=sip:@FRITZ_HOST@
client_uri=sip:@FRITZ_USER@@@FRITZ_HOST@
contact_user=@FRITZ_USER@
retry_interval=30
; Nach einer Ablehnung (404 = Telefon in der Fritzbox noch nicht angelegt, 401/403 = Zugangsdaten falsch) nicht fuer immer aufgeben:
; sonst bleibt die Anmeldung auf "Rejected" stehen, bis Asterisk neu gestartet wird, auch wenn die Fritzbox inzwischen richtig eingerichtet ist
fatal_retry_interval=20
forbidden_retry_interval=30
auth_rejection_permanent=no
max_retries=100000
expiration=300
"""


# Klingel-Rufnummern 99NNN (Einstellung "ring_lines" und die Nummer(n) fuer "alle Telefone klingeln", astconf.ring_lines). 99613 ist
# eine davon wie jede andere (stand bis 2026-11 fest in pjsip.conf.tpl). Die Fritzbox meldet sie als "Anderer Anbieter" an (die Anmeldung
# wird ueber die IP dem Endpoint "fritz" zugeordnet, dessen aors sie deshalb enthaelt). Ruft Asterisk ein Telefon **NNN
# gezielt an und hat die Fritzbox 99NNN angemeldet, laeuft der Anruf darueber (extensions.conf [build-targets]) und das Telefon zeigt die
# Nummer des Anrufers statt des Geraetenamens "LieschenMueller".
# AOR (fuer die Anmeldung der Fritzbox) gibt es fuer jede eingetragene Nummer; den Endpoint (zum Waehlen) erst, wenn die Nummer
# in der Fritzbox einem Telefon zugewiesen ist (ring_ok) - sonst wuerde Asterisk ueber eine Nummer anrufen, auf die kein Telefon reagiert.
RING_AOR = """
[@N@]
type=aor
max_contacts=1
remove_existing=yes
minimum_expiration=60
default_expiration=120
maximum_expiration=300
qualify_frequency=30
qualify_timeout=3
"""

RING_EP = """
[@N@]
type=endpoint
context=from-fritzring
disallow=all
allow=alaw,ulaw
aors=@N@
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
send_pai=yes
trust_id_outbound=yes
identify_by=ip
"""


def clean_ring_lines(v):
    """Klingel-Rufnummern 99NNN (Komma/Leerzeichen), ohne Doppelte, hoechstens 20.
    ValueError bei anderen Eintraegen."""
    out = []
    for p in re.split(r"[,;\s]+", str(v or "").strip()):
        if not p:
            continue
        if not re.fullmatch(r"99\d{1,4}", p):
            raise ValueError(f"„{p}“ ist keine Klingel-Rufnummer (99 und die interne Nummer, z. B. 99610 für **610)")
        if p not in out:
            out.append(p)
    return out[:20]


def acl_enabled():
    return os.environ.get("SIP_ACL", "1").strip() != "0"


def host_ip(host):
    """IPv4-Adresse der Fritzbox (Adresse oder Name); None, wenn sie sich nicht aufloesen laesst."""
    try:
        return str(ipaddress.IPv4Address(host))
    except ValueError:
        pass
    try:
        return socket.gethostbyname(host)
    except OSError:
        return None


def render(user, password, host, acl=None, ring=(), ring_ok=None):
    for k, v in (("Benutzer", user), ("Kennwort", password), ("Adresse", host)):
        if not v or re.search(r"[;\n\r]", v):
            raise ValueError(f"{k} fehlt oder enthaelt ein ';' oder einen Zeilenumbruch")
    acl = acl_enabled() if acl is None else acl
    acl_text = ""
    if acl:
        ip = host_ip(host)
        if ip:
            acl_text = ACL.replace("@FRITZ_IP@", ip)
        else:       # Name nicht aufloesbar: lieber ohne Sperre als ohne Telefon (identify_by=ip schuetzt weiter)
            acl_text = f"\n; Fritzbox-Adresse {host} nicht aufloesbar: keine IP-Sperre (type=acl) gesetzt\n"
    ring = clean_ring_lines(",".join(ring))
    text = TEMPLATE.replace("@ACL@", acl_text).replace("@RING_AORS@", "".join("," + n for n in ring)) + REG
    ok = ring if ring_ok is None else [n for n in ring if n in set(ring_ok)]
    text += "".join(RING_AOR.replace("@N@", n) + (RING_EP.replace("@N@", n) if n in ok else "") for n in ring)
    return text.replace("@FRITZ_USER@", user).replace("@FRITZ_PASSWORD@", password).replace("@FRITZ_HOST@", host)


def write(path, user, password, host, ring=(), ring_ok=None):
    text = render(user, password, host, ring=ring, ring_ok=ring_ok)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    share_with_asterisk(tmp)   # der Asterisk-Benutzer im Container muss lesen koennen, sonst niemand (Kennwort)
    os.replace(tmp, path)


def parse(text):
    """Benutzer, Kennwort und Adresse aus einer von uns erzeugten Datei lesen (fuer die Umstellung alter Dateien)."""
    sec, vals = None, {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            sec = line
            continue
        k, _, v = line.partition("=")
        if sec == "[fritz-auth]" and k in ("username", "password"):
            vals[k] = v
        elif sec == "[fritz]" and k == "match":
            vals["host"] = v
    if all(vals.get(k) for k in ("username", "password", "host")):
        return vals["username"], vals["password"], vals["host"]
    return None


def upgrade(path, ring=(), ring_ok=None):
    """Alte Datei (ohne IP-Sperre/identify_by) mit denselben Zugangsdaten neu schreiben. True, wenn geaendert wurde
    (Asterisk muss PJSIP dann neu laden). Fremde oder unvollstaendige Dateien bleiben unveraendert."""
    try:
        old = open(path, encoding="utf-8").read()
    except OSError:
        return False
    creds = parse(old) if "[fritz-reg]" in old else None
    if not creds:
        return False
    new = render(*creds, ring=ring, ring_ok=ring_ok)
    if new == old:
        return False
    write(path, *creds, ring=ring, ring_ok=ring_ok)
    return True
