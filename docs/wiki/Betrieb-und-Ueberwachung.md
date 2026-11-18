# Betrieb und Überwachung

**Wofür diese Seite:** Aktualisieren, Sichern, Überwachung und Logs im laufenden Betrieb.

## Aufbau auf dem Host
`/opt/lieschenmueller` mit `docker-compose.yml`, `.env`, `asterisk/`, `bridge/`, `data/`. Beide Container laufen mit Host-Netzwerk. Die Weboberfläche lauscht auf `WEB_PORT`.

## Aktualisieren
1. Sicherung der Datenbank (siehe unten).
2. Dateien kopieren (`.env`, `data/` und `asterisk/conf/` nicht überschreiben).
3. `./asterisk/render.sh && docker compose up -d --build`.
4. Möglichst, wenn gerade kein Anruf läuft – ein Neustart beendet laufende Gespräche. Beim Start werden hängende Anrufe als abgebrochen markiert.

## Sicherung
Alles Veränderliche liegt in `data/` (Datenbank, Aufnahmen, Ansagen, Plugins). Die Datenbank im Betrieb sichern:
```bash
docker compose exec bridge python -c "import sqlite3;s=sqlite3.connect('/data/lieschen.db');d=sqlite3.connect('/data/backups/manuell.db');s.backup(d)"
```
Die Datenbank enthält auch Mail-Passwort und Google-Schlüssel im Klartext: `data/` und Sicherungen schützen.

## Überwachung
- **Docker-Healthchecks:** Bridge (Weboberfläche antwortet) und Asterisk (läuft).
- **Wächter** `ops/watchdog.py` (Cron auf dem Host, jede Minute): prüft Bridge, Asterisk und die Anmeldung an der Fritzbox sowie jede eingetragene Klingel-Rufnummer 99NNN (aus `data/asterisk/fritz.conf`; Alarm je Nummer nach 7 Minuten, ohne Klingel-Rufnummer nur die Grundleitung), startet kranke Container neu und schickt bei einer Störung (und bei Entwarnung) eine Mail an die Empfänger der Benachrichtigungen. Log: `/var/log/lieschen-watchdog.log`.
- **Dialplan-Rückfall:** Antwortet die Bridge nicht, wählt Asterisk ausgehende Gespräche ohne Aufnahme normal weiter und entfernt dabei selbst die eingestellte Vorwahl (Asterisk-Global `REC_PREFIX` aus `data/asterisk/globals.conf`, schreibt die Bridge).
- Die Kopfzeile der Oberfläche zeigt den Fritzbox-Status; eine Warnleiste meldet leeres Gemini-Guthaben.

## Logs
```bash
docker compose logs --tail 100 bridge
docker compose logs --tail 100 asterisk
```
Die Container-Logs sind mit Docker auf 3 × 10 MB je Dienst begrenzt (`logging` in `docker-compose.yml`; podman mit k8s-file/journald beachtet `max-file` nicht). Rufnummern und Mailadressen stehen im Bridge-Log nur gekürzt.

## Container-Härtung (offen)
- **Bridge läuft als root im Container.** Ein unprivilegierter `USER` im `bridge/Dockerfile` ist noch nicht gesetzt: `data/` gehört auf dem Host teils dem Benutzer, teils root (z. B. `data/asterisk/`, von der Bridge geschrieben), und im rootless-podman der Entwicklung bildet ein anderer Benutzer im Container nicht auf den Eigentümer der Bind-Mounts ab. Umstellung später so: im Dockerfile `RUN useradd -u 1000 -r lm` und `USER lm`, vorher auf dem Host `chown -R 1000:1000 data` (rec-in bleibt 0777 für Asterisk), danach Schreibrechte prüfen (Datenbank, `data/asterisk/fritz.conf`, `moh.conf`, `data/sounds`, `data/rec`, `data/voices`).
- **Asterisk-Image nicht gepinnt** (`docker.io/andrius/asterisk:latest`). Ein `docker compose pull` holt ungeprüft eine neue Version. Empfehlung: auf die getestete Version bzw. den Digest festlegen (derzeit Asterisk 23.5.0, `docker image inspect --format '{{index .RepoDigests 0}}' docker.io/andrius/asterisk:latest`) und Updates bewusst testen.
