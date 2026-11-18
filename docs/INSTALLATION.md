# Installation und Betrieb

**Kurzweg:** `./install.sh` im Projektordner erledigt Abschnitt 3 und 4 (prüft Docker/Compose bzw. podman-compose und python3, legt die `.env` mit zufälligem `UI_PASSWORD`
und `AMI_SECRET` an, ohne eine vorhandene zu überschreiben, rendert, startet, wartet auf „healthy“, zeigt URL, Admin-Passwort und LAN-IP); Abschnitt 5 übernimmt der
**Einrichtungsassistent** in der Weboberfläche. Aktualisieren: `./install.sh --update` (Sicherung von `data/` nach `backup/`, `git pull`, render, neu bauen). Die Abschnitte
unten beschreiben den Weg von Hand und alle Einzelheiten.

## 1. Fritzbox vorbereiten
Asterisk meldet sich an der Fritzbox wie ein IP-Telefon an.
1. **Telefonie → Telefoniegeräte → Neues Gerät einrichten → Telefon (mit und ohne Anrufbeantworter) → LAN/WLAN (IP-Telefon)**.
2. Name z. B. `LieschenMueller`. Die Fritzbox vergibt/verlangt **Benutzername** und **Kennwort**: Das sind `FRITZ_USER` und `FRITZ_PASSWORD`
   (der Benutzername ist *nicht* die Rufnummer). Das Kennwort darf kein `;` enthalten.
   Beides trägst du nach dem Start in der Oberfläche unter **Einstellungen → Anbindungen → Fritzbox: Anmeldung von Asterisk (IP-Telefon)** ein (nicht in die `.env`).
3. Bei den **Rufnummern** festlegen, auf welche Nummern das Gerät reagiert (eingehend) und welche es für ausgehende Anrufe nutzt.
   Ein Anruf auf eine Nummer, bei der das Gerät nicht eingetragen ist, erreicht die KI nicht.
4. Für den Telefonbuch- und Rufnummernabgleich einen **Fritzbox-Benutzer** (System → Fritzbox-Benutzer) mit dem Recht
   „FRITZ!Box Einstellungen“ anlegen und ebenfalls in den Einstellungen eintragen (**Anbindungen → Fritzbox (Abfragen …)**). Außerdem in der Fritzbox unter Heimnetz → Netzwerk → Netzwerkeinstellungen → Heimnetzfreigaben
   „Zugriff für Anwendungen zulassen“ einschalten (je nach FRITZ!OS-Version hinter „Weitere Einstellungen“).
5. **Klingel-Rufnummer (empfohlen):** siehe Abschnitt „Klingeln über Asterisk“ unten bzw. [Wiki → Fritzbox-Anbindung](wiki/Fritzbox-Anbindung.md).

## 2. Google-Schlüssel
In [Google AI Studio](https://aistudio.google.com/) einen API-Schlüssel erzeugen und die Abrechnung aktivieren und in der Oberfläche unter **Einstellungen → Anbindungen → Google Gemini** eintragen.

## 3. Konfiguration
```bash
cp .env.example .env
```
In der `.env` genügt `UI_PASSWORD` (Passwort des ersten Administrators `admin`; Pflicht, sonst wird kein Administrator angelegt) und ggf. `WEB_PORT`. Kommentare nur in eigenen Zeilen, nie hinter einem Wert (Compose liest `UI_PASSWORD=   # Text` als Passwort `# Text`). Fritzbox-Adresse, Fritzbox-Anmeldung von Asterisk, TR-064-Benutzer,
Gemini-Schlüssel und die Aufbewahrungsdauer der Anrufe stellt der Administrator in der Oberfläche unter **Einstellungen** ein; sie liegen in der Datenbank. Werte in der `.env`
(`FRITZ_*`, `GEMINI_API_KEY`, `RETENTION_DAYS`) gelten nur als Voreinstellung. Die Datei enthält Geheimnisse und steht nicht im Repository.

Weitere (optionale) Variablen: `GEMINI_MODEL`, `ALLOW_BASIC` (`1` = HTTP-Basic-Anmeldung admin + `UI_PASSWORD` für Skripte erlauben, Standard aus), `MAX_CALL_SECONDS`, `DIAL_BLOCK`, `LIVE_INTERVAL`, `PLUGIN_DIR`, `SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASSWORD`/`SMTP_FROM`/`SMTP_SECURITY` (Rückfall, die Oberfläche hat Vorrang);
`AMI_SECRET` erzeugt `asterisk/render.sh`. `SIP_ACL=0` schaltet die IP-Sperre für SIP ab (nur zur Fehlersuche, siehe unten).

## 4. Starten
```bash
./asterisk/render.sh                # vor dem ersten Start: schreibt asterisk/conf/*.conf aus .env, legt data/ an (rec-in, sounds, asterisk/fritz.conf)
podman-compose up -d --build        # oder: docker compose up -d --build
```
Die Weboberfläche läuft auf `http://<host>:8080`. Anmeldung: **admin** + `UI_PASSWORD`.

Nach Änderungen:
| Änderung | Maßnahme |
|---|---|
| `.env` (Bridge) | `podman-compose up -d --force-recreate bridge` |
| `WEB_PORT` in der `.env` | `./asterisk/render.sh` (der Dialplan ruft die Bridge über diesen Port), dann alle Container neu erstellen (`… up -d --force-recreate`) |
| Python-Code oder Weboberfläche | `podman-compose up -d --build --force-recreate bridge` |
| `docker-compose.yml` (z. B. neues Volume `musiconhold.conf` für die Wartemusik) | zuerst `./asterisk/render.sh` (legt `asterisk/conf/musiconhold.conf` und die Musik-Verzeichnisse an), dann `docker compose up -d` (erstellt Asterisk neu) |
| Dialplan (`asterisk/templates/extensions.conf.tpl`) | `./asterisk/render.sh`, dann `podman exec <asterisk-container> asterisk -rx 'dialplan reload'` |
| `pjsip`/`manager`-Vorlagen | `./asterisk/render.sh`, dann Asterisk-Container neu starten (die Fritzbox-Zugangsdaten ändert man in der Oberfläche; die App lädt Asterisk dann selbst neu) |

## 5. Ersteinrichtung in der Oberfläche
Der **Einrichtungsassistent** (öffnet sich von selbst, solange etwas fehlt; sonst Einstellungen → Allgemein) führt durch Gemini-Schlüssel, Fritzbox (TR-064), IP-Telefon mit Anzeige der Anmeldung und „Asterisk antwortet auf“. Danach bzw. von Hand:
1. **Einstellungen:** Gemini-Schlüssel, Fritzbox (Adresse, TR-064, Anmeldung von Asterisk), Aufbewahrung, globale Grundkonfiguration (Prompt-Vorsatz), Geschäftszeiten; das Budget auf der Seite **Kosten**.
2. **Kontakte:** „Mit Fritzbox abgleichen“ (Telefonbuch importieren).
3. **Agenten:** den vorhandenen Agenten anpassen (Prompt, Stimme, Modell, Hinweis-Ansage) oder weitere anlegen. **Regeln:** Anrufregel prüfen (welcher Agent nimmt an, wie lange klingeln?); eine neue Installation hat schon die Regel „Assistent“ (alle Nummern, sofort annehmen). Aufzeichnungsregeln nach Bedarf.
4. **Benutzer:** Benutzer mit Weiterleitungsnummer, Vertretung und Zeitplan; optional ein Benutzerkonto je Benutzer.
5. **Rufnummern:** Fritzbox-Rufnummern Benutzern zuweisen (bestimmt, wer welche Anrufe sieht und wohin Anrufe geleitet werden).
6. Einen Testanruf auf die Nummer machen, an der das Gerät eingetragen ist.

## Klingeln über Asterisk (Rufnummer 99613)
Damit alle Telefone klingeln und die Nummer des Anrufers zeigen, legt man in der Fritzbox eine zusätzliche Rufnummer „Anderer Anbieter“ an:
Benutzername/Rufnummer `99613` (oder `99<intern>`), ein beliebiges Kennwort (wird nicht geprüft), **Registrar = IP des Hosts**, und weist sie allen Telefonen zu.
Abgesichert ist die Rufnummer über die IP: Asterisk nimmt SIP nur von der Fritzbox-Adresse aus den Einstellungen an (`type=acl` in `data/asterisk/fritz.conf`)
und erkennt die Endpoints nur an der IP (`identify_by=ip`).
Fehlt sie, klingelt die Fritzbox selbst, und die KI übernimmt über das IP-Telefon (wie früher). Mit einer Wahlregel „Vorwahl → 99613“ (Vorwahl wie unter *Einstellungen → Telefonie → Aufzeichnung von Gesprächen*, Standard `*77`, z. B. `000`) werden
ausgehende Gespräche aufgezeichnet. Ausführlich: [Wiki → Fritzbox-Anbindung](wiki/Fritzbox-Anbindung.md).

## Google Kalender (Benutzer und Kampagnen)
Google Kalender wird für die **Kalender der Benutzer** (Aufenthalt, Termine per Fernabfrage) und für Terminpools im Plugin *Kampagnen* genutzt. Der Zugang läuft über ein **Dienstkonto**
(kostenlos, kein Browser-Login der App, kein Ablauf nach 7 Tagen). Einmalige Einrichtung (Stand Oktober 2026, Menünamen können abweichen):
1. In der [Google Cloud Console](https://console.cloud.google.com/) ein **Projekt** anlegen (z. B. „Lieschen Müller“). Eine Rechnungsadresse ist dafür nicht nötig.
2. **APIs & Dienste → Bibliothek →** „Google Calendar API“ → **Aktivieren**.
3. **IAM & Verwaltung → Dienstkonten → Dienstkonto erstellen** (Name beliebig, keine Rollen nötig).
4. Das Dienstkonto öffnen → **Schlüssel → Schlüssel hinzufügen → Neuen Schlüssel erstellen → JSON**. Die heruntergeladene Datei enthält den privaten Schlüssel – sicher aufbewahren.
   (Verbietet eine Organisations-Richtlinie das Erstellen von Schlüsseln, muss der Administrator sie für dieses Projekt erlauben.)
5. In Google Kalender die **Einstellungen des gewünschten Kalenders → Für bestimmte Personen oder Gruppen freigeben** → die `client_email` aus der JSON-Datei hinzufügen,
   Berechtigung **„Änderungen an Terminen vornehmen“**.
6. Dort unter **Kalender integrieren** die **Kalender-ID** kopieren.
7. In der Weboberfläche: **Einstellungen → Anbindungen → Google Kalender**: JSON-Inhalt einfügen, *Schlüssel speichern*, mit einer Kalender-ID *Verbindung testen*.
   Danach beim **Benutzer** (Abschnitt „Google Kalender“) die Kalender-ID eintragen und/oder im Plugin *Kampagnen* einen Kalender-Pool anlegen. Jeder Kalender muss mit dem Dienstkonto geteilt sein.

**Platzhalter im Kalender:** Termine, deren Titel mit einem frei wählbaren Präfix beginnt (z. B. `[Termin] Beratung`, Dienstag 9–12 Uhr), legen fest, wann die KI Termine vergeben darf.
Sie werden in Termine der eingestellten Dauer geteilt. Alle anderen Kalendereinträge (außer ganztägigen und „Verfügbar“-Einträgen) gelten als belegt.
Unter *Terminpools → Kalender-Pool …* Kalender-ID, Präfix, Dauer je Termin und Vorausschau eintragen. Gebucht wird als neuer Kalendereintrag (Titel einstellbar,
z. B. `Termin: {name}`); die Platzhalter bleiben unverändert. Gibt man einen Termin in der Oberfläche frei, wird der Kalendereintrag gelöscht.
Die **Pause zwischen Terminen** (Minuten) stellt man pro Kampagne ein; sie gilt auch für Pools ohne Kalender.

## E-Mail-Benachrichtigungen (Outlook / Microsoft 365)
Die komplette Einrichtung (Server, Port, Verschlüsselung, Benutzer, Absender, **Passwort**, Tagesbericht-Uhrzeit, Empfänger) erfolgt in der Oberfläche unter
**Benachrichtigungen**; mit „Testmail senden“ prüft man sie. Das Passwort wird nie wieder angezeigt; es liegt in der Datenbank (`data/`) und lässt sich dort
entfernen. Alternativ kann es in der `.env` stehen (`SMTP_PASSWORD=…`); ein in der Oberfläche gespeichertes Passwort hat Vorrang.
- **Outlook.com:** `smtp-mail.outlook.com`, Port 587, STARTTLS, App-Kennwort (bei aktiver Zwei-Faktor-Anmeldung).
- **Microsoft 365:** `smtp.office365.com`, Port 587; „SMTP-Authentifizierung“ muss für das Postfach erlaubt sein. Microsoft schaltet das bei vielen
  Firmenkonten ab; dann wäre eine Anbindung über Microsoft Graph nötig (noch nicht eingebaut).

## TrueNAS SCALE (24.10 oder neuer)
Vorgesehen ist der Betrieb als Docker-Compose-App (Apps → „Install via YAML“) mit dem Inhalt der `docker-compose.yml`, Host-Netzwerk und
einem Datensatz für `./data`. Zu beachten:
- `asterisk/render.sh` einmal außerhalb ausführen (oder die erzeugten Dateien in `asterisk/conf/` bereitstellen) – die Konfiguration entsteht aus der `.env`.
- Der Ordner `data/rec-in` muss für den Asterisk-Benutzer im Container beschreibbar sein.
- Die Weboberfläche nur im Heimnetz oder über VPN/Reverse-Proxy mit HTTPS erreichbar machen.
- Dieser Weg ist **noch nicht erprobt**.

## Reverse-Proxy (HTTPS)
Die Weboberfläche bringt kein HTTPS mit. Hinter einem Proxy (nginx, Caddy, …) gilt:
- den Pfad **`/internal/` nicht weiterreichen** (er ist nur für Asterisk auf demselben Rechner),
- den **Host-Header unverändert** weitergeben (`proxy_set_header Host $host;`), sonst weist der Schutz vor Fremdanfragen (Origin-Prüfung) alle schreibenden Aufrufe mit 403 ab,
- WebSocket und Server-Sent Events erlauben (`/ws/live`, `/api/live/stream`; keine Pufferung).

## Datensicherung
Alles Veränderliche liegt in `./data`: `lieschen.db` (SQLite, WAL), `voices/` (zwischengespeicherte Ansagen), `rec/` (Aufnahmen),
`sounds/`, `plugins/`. Sicherung der Datenbank im laufenden Betrieb mit `sqlite3 data/lieschen.db ".backup backup.db"`.
Die Aufbewahrung von Anrufen stellt der Administrator unter *Einstellungen* ein (archivierte Anrufe sind ausgenommen).

## Fehlersuche
| Symptom | Mögliche Ursache |
|---|---|
| Es klingelt, aber die KI meldet sich nicht | Gerät bei der Rufnummer nicht eingetragen; Asterisk nicht an der Fritzbox registriert (`asterisk -rx 'pjsip show registrations'`); Gemini-Schlüssel/Guthaben (Warnleiste in der Oberfläche) |
| Registrierung schlägt fehl | Benutzername des IP-Telefons ist die Rufnummer statt des Gerätebenutzers; Kennwort mit `;` |
| KI antwortet nicht / Warnleiste „Guthaben“ | Google-Kontingent oder Guthaben aufgebraucht |
| Anrufe auf Benutzer klingeln nicht | Benutzer hat Status „nicht erreichbar“ oder keine Weiterleitungsnummer; Nummer im Rufnummern-Dialog nicht zugewiesen |
| Mailversand schlägt fehl | Passwort fehlt (Seite Benachrichtigungen oder `SMTP_PASSWORD` in der `.env`); der Mailanbieter lehnt die Anmeldung ab (App-Kennwort, SMTP-Authentifizierung) – die Meldung steht beim Testversand und im Log |
| Logs | `docker compose logs --tail 100 bridge` bzw. `asterisk` (podman-compose: `podman logs lieschenmueller_bridge_1`; Docker benennt die Container `lieschenmueller-bridge-1`) |
