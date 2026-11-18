#!/bin/sh
# Lieschen Mueller installieren, starten oder aktualisieren (POSIX sh, Linux).
#
#   ./install.sh              Einrichten (falls noetig) und starten
#   ./install.sh --no-start   nur vorbereiten (.env, Asterisk-Konfiguration), nichts starten
#   ./install.sh --update     Sicherung von data/, neue Version holen (git pull), neu bauen und starten
#   ./install.sh --help       diese Hilfe
#
# Wiederholbar: eine vorhandene .env wird nie ueberschrieben, vorhandene Zugangsdaten (data/) bleiben, Container werden nur
# neu gebaut/gestartet. Danach geht es in der Weboberflaeche weiter (Einrichtungsassistent).
set -eu

ROOT=$(cd "$(dirname "$0")" && pwd)
cd "$ROOT"

MODE=install
START=1
ASSUME_YES=0

say() { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARNUNG: %s\n' "$*" >&2; }
die() { printf '\nFEHLER: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Lieschen Mueller - Installation und Aktualisierung

Aufruf (im Ordner von Lieschen Mueller):
  ./install.sh              einrichten (falls noetig) und starten
  ./install.sh --no-start   nur vorbereiten: .env anlegen, Asterisk-Konfiguration erzeugen, nichts starten
  ./install.sh --update     aktualisieren: data/ sichern (backup/), git pull, Konfiguration neu, neu bauen und starten
  ./install.sh --yes        keine Rueckfragen (Standardwerte nehmen), z. B. fuer Skripte
  ./install.sh --help       diese Hilfe
  ./update.sh               dasselbe wie ./install.sh --update

Was passiert beim Einrichten:
  1. Pruefen: Linux, Docker mit Compose-Plugin (oder podman mit podman-compose), python3
  2. .env aus .env.example anlegen (nur wenn es sie noch nicht gibt) mit zufaelligem Admin-Passwort
     und AMI_SECRET; Port der Weboberflaeche waehlen (Standard 8080, sonst ein freier)
  3. Asterisk-Konfiguration erzeugen (asterisk/render.sh)
  4. Container bauen und starten, warten bis sie laufen
  5. Adresse der Weboberflaeche und die naechsten Schritte anzeigen

Eine vorhandene .env wird nie ueberschrieben. Das Skript kann beliebig oft laufen.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --no-start) START=0 ;;
    --update) MODE=update ;;
    -y|--yes) ASSUME_YES=1 ;;
    *) usage >&2; die "Unbekannte Option: $1" ;;
  esac
  shift
done

interactive() { [ "$ASSUME_YES" = "0" ] && [ -t 0 ]; }

ask() {   # ask "Frage" "Standard" -> Antwort auf stdout
  if interactive; then
    printf '%s [%s]: ' "$1" "$2" >&2
    read -r a || a=""
    [ -n "$a" ] && { printf '%s' "$a"; return; }
  fi
  printf '%s' "$2"
}

# ---- Voraussetzungen ----------------------------------------------------------------------------
step "Voraussetzungen pruefen"
[ "$(uname -s)" = "Linux" ] || die "Lieschen Mueller laeuft nur unter Linux (gefunden: $(uname -s)). Benoetigt wird ein Linux-Rechner im selben Netz wie die Fritzbox (z. B. Raspberry Pi, Mini-PC, NAS mit Docker)."
[ -f docker-compose.yml ] && [ -f .env.example ] && [ -f asterisk/render.py ] || die "Bitte im Ordner von Lieschen Mueller aufrufen (dort, wo docker-compose.yml liegt)."

command -v python3 >/dev/null 2>&1 || die "python3 fehlt. Installieren, z. B. unter Debian/Ubuntu/Raspberry Pi OS:
    sudo apt install python3
Danach ./install.sh erneut aufrufen."

COMPOSE=""
ENGINE=""
if command -v docker >/dev/null 2>&1; then
  if ! docker info >/dev/null 2>&1; then
    if [ "$(id -u)" != "0" ]; then
      die "Docker ist installiert, aber dieser Benutzer darf es nicht benutzen (oder der Docker-Dienst laeuft nicht).
Entweder mit sudo aufrufen:
    sudo ./install.sh
oder den Benutzer in die Gruppe docker aufnehmen (danach ab- und wieder anmelden):
    sudo usermod -aG docker \$USER
Laeuft der Dienst nicht:  sudo systemctl enable --now docker"
    fi
    die "Docker antwortet nicht. Dienst starten:  systemctl enable --now docker"
  fi
  if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
  elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
  else
    die "Docker ist da, aber das Compose-Plugin fehlt. Installieren, z. B. unter Debian/Ubuntu:
    sudo apt install docker-compose-plugin
(Anleitung: docs.docker.com -> Install Docker Engine). Danach ./install.sh erneut aufrufen."
  fi
  ENGINE=docker
elif command -v podman >/dev/null 2>&1; then
  command -v podman-compose >/dev/null 2>&1 || die "podman ist da, aber podman-compose fehlt. Installieren, z. B.:
    sudo apt install podman-compose     (Debian/Ubuntu)
    sudo dnf install podman-compose     (Fedora)
Danach ./install.sh erneut aufrufen."
  COMPOSE="podman-compose"
  ENGINE=podman
else
  die "Weder Docker noch podman gefunden. Bitte Docker Engine mit dem Compose-Plugin installieren:
    Anleitung: https://docs.docker.com/engine/install/ (Debian, Ubuntu; Raspberry Pi OS: Debian-Anleitung)
Kurzform fuer Debian/Ubuntu/Raspberry Pi OS (offizielles Skript von Docker):
    curl -fsSL https://get.docker.com | sudo sh
Pruefen:  docker --version  und  docker compose version
Danach ./install.sh erneut aufrufen."
fi
say "Linux ok, python3 ok, Container: $COMPOSE"

# ---- Hilfsfunktionen ----------------------------------------------------------------------------
envget() {   # Wert einer Zeile KEY=... aus .env (ohne Anfuehrungszeichen)
  sed -n "s/^$1=//p" .env 2>/dev/null | tail -n 1 | sed "s/^[\"']//; s/[\"']\$//"
}

randstr() {   # zufaellige Zeichenkette aus Buchstaben und Ziffern (keine Sonderzeichen: die machen in der .env Probleme)
  python3 -c "import secrets,string;a=string.ascii_letters+string.digits;print(''.join(secrets.choice(a) for _ in range($1)))"
}

port_busy() {   # 0 = auf dem Port lauscht schon etwas (TCP)
  python3 - "$1" <<'PY'
import socket, sys
s = socket.socket()
s.settimeout(0.5)
sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
PY
}

udp_busy() {   # 0 = UDP-Port belegt (bind schlaegt fehl); unbekannt = frei
  python3 - "$1" <<'PY'
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.bind(("0.0.0.0", int(sys.argv[1])))
except PermissionError:
    sys.exit(1)
except OSError:
    sys.exit(0)
sys.exit(1)
PY
}

lan_ip() {
  ip=""
  if command -v ip >/dev/null 2>&1; then
    ip=$(ip -4 route get 192.168.178.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -n 1)
  fi
  [ -n "$ip" ] || ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  [ -n "$ip" ] || ip=$(python3 -c "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.connect(('192.168.178.1',9));print(s.getsockname()[0])" 2>/dev/null || true)
  printf '%s' "${ip:-<IP-dieses-Rechners>}"
}

containers_up() {   # Bridge antwortet und keiner der Dienste ist (noch) "starting"/"unhealthy"
  port=$1
  python3 - "$port" 2>/dev/null <<'PY' || return 1
import sys, urllib.request
urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/login", timeout=3)
PY
  st=$($COMPOSE ps 2>/dev/null || true)
  case "$st" in *unhealthy*|*starting*) return 1 ;; esac
  n=$(printf '%s\n' "$st" | grep -c "(healthy)\|healthy" || true)
  [ "$n" -ge 2 ]
}

wait_healthy() {
  port=$1
  say "Warte, bis Bridge und Asterisk laufen (Gesundheitspruefung alle 30 Sekunden, hoechstens 4 Minuten) ..."
  i=0
  while [ $i -lt 48 ]; do
    if containers_up "$port"; then
      say "Laeuft."
      return 0
    fi
    sleep 5
    i=$((i + 1))
  done
  if python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:$port/login',timeout=3)" 2>/dev/null; then
    warn "Die Weboberflaeche antwortet, aber die Gesundheitspruefung meldet noch nicht beide Dienste als gesund:"
    $COMPOSE ps || true
    return 0
  fi
  $COMPOSE ps || true
  die "Die Weboberflaeche antwortet nicht. Protokolle ansehen:
    $COMPOSE logs --tail 80 bridge
    $COMPOSE logs --tail 80 asterisk
Haeufige Ursache: Port $port oder 5060 ist schon belegt (in .env WEB_PORT aendern, dann ./install.sh erneut)."
}

# ---- Aktualisieren: Sicherung und neue Version ----------------------------------------------------
if [ "$MODE" = "update" ]; then
  [ -f .env ] || die "Keine .env gefunden - Lieschen Mueller ist hier noch nicht eingerichtet. Zuerst ./install.sh aufrufen."
  if [ -d data ]; then
    step "data/ sichern"
    mkdir -p backup
    STAMP=$(date +%Y-%m-%d_%H%M%S)
    # Datenbank zuerst konsistent kopieren (laeuft die Bridge, waere eine einfache Dateikopie evtl. halb geschrieben)
    if [ -f data/lieschen.db ]; then
      python3 - <<'PY' || warn "Konsistente Kopie der Datenbank nicht moeglich - die Sicherung enthaelt die Datei wie sie ist."
import sqlite3
src = sqlite3.connect("file:data/lieschen.db?mode=ro", uri=True, timeout=30)
dst = sqlite3.connect("data/lieschen-sicherung.db")
src.backup(dst)
dst.close()
src.close()
PY
    fi
    if tar -czf "backup/lieschen-data-$STAMP.tar.gz" data 2>/dev/null; then
      chmod 600 "backup/lieschen-data-$STAMP.tar.gz"
      say "Gesichert: backup/lieschen-data-$STAMP.tar.gz (enthaelt Zugangsdaten - geschuetzt aufbewahren)"
    else
      rm -f "backup/lieschen-data-$STAMP.tar.gz"
      rm -f data/lieschen-sicherung.db
      die "Sicherung von data/ fehlgeschlagen (oft fehlende Rechte: Dateien gehoeren root). Mit sudo erneut aufrufen:  sudo ./install.sh --update"
    fi
    rm -f data/lieschen-sicherung.db
  fi
  step "Neue Version holen"
  if [ -d .git ] && command -v git >/dev/null 2>&1; then
    git pull --ff-only || die "git pull hat nicht geklappt (lokale Aenderungen?). 'git status' zeigt, was geaendert wurde."
  else
    say "Kein git-Ordner: Bei einer ZIP-Installation die neue Version entpacken und ueber diesen Ordner kopieren,"
    say "dabei .env, data/ und asterisk/conf/ NICHT ueberschreiben (danach geht es hier weiter)."
    if interactive; then
      a=$(ask "Sind die neuen Dateien schon hineinkopiert? (j/n)" "n")
      case "$a" in j|J|ja|Ja|y|Y) ;; *) say "Abgebrochen, nichts geaendert (die Sicherung bleibt liegen)."; exit 0 ;; esac
    else
      say "Ohne Rueckfrage: weiter mit den Dateien, die jetzt im Ordner liegen."
    fi
  fi
fi

# ---- .env -----------------------------------------------------------------------------------------
NEW_PW=""
step ".env (Einstellungen fuer den Start)"
if [ -f .env ]; then
  say ".env ist schon vorhanden - sie bleibt unveraendert."
  WEB_PORT=$(envget WEB_PORT)
  WEB_PORT=${WEB_PORT:-8080}
  if [ -z "$(envget UI_PASSWORD)" ] && [ ! -f data/lieschen.db ]; then
    warn "UI_PASSWORD ist in der .env leer: beim ersten Start wird dann kein Administrator angelegt.
    In .env die Zeile 'UI_PASSWORD=' um ein Passwort ergaenzen (nur Buchstaben und Ziffern, nichts weiter in der Zeile)
    oder die .env loeschen und ./install.sh erneut aufrufen (legt eine neue mit zufaelligem Passwort an)."
  fi
else
  [ "$MODE" = "update" ] && die "Keine .env gefunden."
  WEB_PORT=8080
  if port_busy 8080; then
    p=8081
    while [ $p -lt 8200 ] && port_busy $p; do p=$((p + 1)); done
    say "Port 8080 ist auf diesem Rechner schon belegt - Vorschlag: $p"
    WEB_PORT=$p
  fi
  while :; do
    WEB_PORT=$(ask "Port fuer die Weboberflaeche" "$WEB_PORT")
    case "$WEB_PORT" in ''|*[!0-9]*) say "Bitte eine Zahl eingeben (z. B. 8080)."; WEB_PORT=8080; continue ;; esac
    if [ "$WEB_PORT" -lt 1024 ] || [ "$WEB_PORT" -gt 65535 ]; then say "Bitte eine Zahl zwischen 1024 und 65535."; WEB_PORT=8080; continue; fi
    if port_busy "$WEB_PORT"; then
      say "Port $WEB_PORT ist belegt."
      interactive || die "Port $WEB_PORT ist belegt."
      continue
    fi
    break
  done
  NEW_PW=$(randstr 20)
  AMI=$(randstr 32)
  umask 077
  # Werte ohne Kommentar in der Zeile (Compose liest "KEY=   # Text" sonst als Wert)
  sed -e "s/^UI_PASSWORD=.*/UI_PASSWORD=$NEW_PW/" -e "s/^WEB_PORT=.*/WEB_PORT=$WEB_PORT/" .env.example > .env.tmp
  printf '\n# erzeugt von install.sh (Schluessel fuer Asterisk-Manager und interne Aufrufe)\nAMI_SECRET=%s\n' "$AMI" >> .env.tmp
  mv .env.tmp .env
  umask 022
  grep -q "^UI_PASSWORD=$NEW_PW\$" .env || die ".env konnte nicht richtig angelegt werden (UI_PASSWORD fehlt)."
  say ".env angelegt (Port $WEB_PORT, zufaelliges Admin-Passwort, AMI_SECRET)."
  if [ -f data/lieschen.db ]; then
    warn "Es gibt schon eine Datenbank (data/lieschen.db). Das neue Admin-Passwort gilt nur fuer eine NEUE Installation;
    vorhandene Konten behalten ihr Passwort."
  fi
fi

if [ "$MODE" = "install" ] && udp_busy 5060; then
  warn "UDP-Port 5060 (SIP) ist auf diesem Rechner schon belegt. Asterisk braucht ihn - laeuft hier schon eine andere
    Telefonanlage oder eine zweite Lieschen-Mueller-Installation? (Ein bereits laufendes Lieschen Mueller ist in Ordnung.)"
fi

# ---- Asterisk-Konfiguration -------------------------------------------------------------------------
step "Asterisk-Konfiguration erzeugen"
sh asterisk/render.sh
if [ "$ENGINE" = "podman" ] && [ "$(id -u)" != "0" ]; then
  # podman ohne root: die Gruppe 1000 im Container (Asterisk) ist auf dem Rechner eine andere Nummer. render.py gibt die Dateien mit
  # Schluesseln nur Eigentuemer und Gruppe frei - die Gruppe deshalb im Namensraum von podman setzen, sonst kann Asterisk sie nicht lesen.
  G=${ASTERISK_GID:-1000}
  for f in asterisk/conf/*.conf data/asterisk data/asterisk/* data/rec-in; do
    [ -e "$f" ] && podman unshare chgrp "$G" "$f" 2>/dev/null || true
  done
fi

LAN=$(lan_ip)

if [ "$START" = "0" ]; then
  step "Fertig vorbereitet (--no-start)"
  say "Starten mit:  $COMPOSE up -d --build    (oder ./install.sh ohne --no-start)"
else
  step "Container bauen und starten (beim ersten Mal einige Minuten)"
  $COMPOSE up -d --build
  wait_healthy "$WEB_PORT"
fi

# ---- Zusammenfassung ----------------------------------------------------------------------------------
printf '\n'
say "=============================================================================="
say " Lieschen Mueller"
say "=============================================================================="
say " Weboberflaeche:  http://$LAN:$WEB_PORT"
if [ -n "$NEW_PW" ]; then
  say ""
  say " Anmeldung:  Benutzer  admin"
  say "             Passwort  $NEW_PW"
  say ""
  say " >>> Dieses Passwort JETZT notieren (Papier oder Passwortverwaltung). <<<"
  say "     install.sh zeigt es nur dieses eine Mal an. Nach der ersten Anmeldung"
  say "     unter 'Mein Konto' ein eigenes Passwort setzen."
fi
say ""
say " IP-Adresse dieses Rechners: $LAN"
say "   - in der Fritzbox diesem Rechner immer dieselbe IPv4-Adresse geben"
say "     (Heimnetz -> Netzwerk -> Geraet bearbeiten -> 'immer die gleiche IPv4-Adresse')"
say "   - wird spaeter fuer Klingel-Rufnummern gebraucht (Registrar = diese Adresse)"
say ""
if [ "$MODE" = "update" ]; then
  say " Aktualisiert. In der Weboberflaeche pruefen, ob oben 'Fritzbox ok' steht."
else
  say " Naechste Schritte: Weboberflaeche oeffnen und anmelden - der Einrichtungsassistent"
  say " fuehrt durch Gemini-Schluessel, Fritzbox, IP-Telefon und Rufnummern."
  say " Anleitung: docs/wiki/Schnellstart-fuer-Einsteiger.md"
fi
say "=============================================================================="
