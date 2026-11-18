# Installation

**Wofür diese Seite:** Schritt für Schritt vom leeren Rechner bis zum ersten Testanruf. Die ausführliche Referenz (Kalender, Mail, TrueNAS, Reverse-Proxy, Sicherung) steht in
[`docs/INSTALLATION.md`](../INSTALLATION.md). Vorher bitte die [Voraussetzungen](Voraussetzungen-und-Fritzbox.md) lesen: Es geht nur mit einer Fritzbox.

## Überblick
1. In der **Fritzbox** das IP-Telefon anlegen (und einen Benutzer für TR-064).
2. Auf dem Rechner `./install.sh` aufrufen (legt `.env` an, erzeugt die Konfiguration, startet die Container).
3. In der **Weboberfläche** dem **Einrichtungsassistenten** folgen: Gemini, Fritzbox, IP-Telefon, Rufnummern.
4. Optional: Klingel-Rufnummer `99NNN` und Wahlregel in der Fritzbox.
5. Agent, Anrufregel, Benutzer und Rufnummern einrichten, Testanruf.

## 1. Fritzbox vorbereiten
1. **Telefonie → Telefoniegeräte → Neues Gerät einrichten → Telefon (mit und ohne Anrufbeantworter) → LAN/WLAN (IP-Telefon)**.
2. Name z. B. `LieschenMueller`. **Benutzername** und **Kennwort** merken (der Benutzername ist *nicht* die Rufnummer; das Kennwort darf kein `;` enthalten).
3. Bei den **Rufnummern** die auswählen, auf denen die KI antworten soll (eingehend) und eine für ausgehende Anrufe. Nummern, bei denen das Gerät nicht eingetragen ist, erreichen die KI nicht.
   Klingel-Rufnummern `99…` dort **nicht** anhaken.
4. **System → Fritzbox-Benutzer:** Benutzer mit dem Recht „FRITZ!Box Einstellungen“ für TR-064 und die Weboberfläche anlegen; unter Heimnetz → Netzwerk → Netzwerkeinstellungen → Heimnetzfreigaben „Zugriff für Anwendungen zulassen“ einschalten (je nach FRITZ!OS-Version hinter „Weitere Einstellungen“).

## 2. Rechner vorbereiten
```bash
./install.sh                    # prueft Docker/Compose (oder podman-compose) und python3, legt .env an, startet, zeigt URL und Admin-Passwort
```
`install.sh` legt die `.env` nur an, wenn sie fehlt (nie überschreiben), mit zufälligem `UI_PASSWORD` und `AMI_SECRET`, wählt einen freien Port (Standard 8080), führt
`./asterisk/render.sh` und `docker compose up -d --build` aus, wartet auf „healthy“ und zeigt Adresse, Admin-Passwort (einmal) und die LAN-IP. Optionen: `--no-start`, `--yes`, `--update`, `--help`.

**Für Fortgeschrittene, von Hand:**
```bash
cp .env.example .env            # nur UI_PASSWORD (Passwort des ersten Administrators "admin", Pflicht) und ggf. WEB_PORT setzen
./asterisk/render.sh            # vor dem ersten Start: erzeugt asterisk/conf/ aus der .env (und AMI_SECRET), legt data/ an
docker compose up -d --build    # oder: podman-compose up -d --build
```
- Der Rechner muss im LAN der Fritzbox stehen; beide Container laufen mit Host-Netzwerk. Freie Ports: 5060/udp, RTP-Bereich, `WEB_PORT`.
- Fritzbox-Zugang, Gemini-Schlüssel und Aufbewahrung stehen **nicht** in der `.env`, sondern in der Datenbank (Einstellungen). Werte aus der `.env` gelten nur als Voreinstellung.
- Weitere optionale `.env`-Schalter: `ALLOW_BASIC`, `GEMINI_MODEL`, `MAX_CALL_SECONDS`, `DIAL_BLOCK`, `SIP_ACL`, `LIVE_INTERVAL`, `PLUGIN_DIR`, `SMTP_*`.
- Kommentare in der `.env` nur in eigenen Zeilen (`# …`), nie hinter einem Wert: Compose liest `UI_PASSWORD=   # Text` als Passwort `# Text`.

| Änderung | Maßnahme |
|---|---|
| `.env` (Bridge) | `docker compose up -d --force-recreate bridge` |
| `WEB_PORT` in der `.env` | `./asterisk/render.sh` (der Dialplan ruft die Bridge über diesen Port), dann `docker compose up -d --force-recreate` |
| Python-Code oder Weboberfläche | `docker compose up -d --build --force-recreate bridge` |
| Dialplan (`asterisk/templates/extensions.conf.tpl`) | `./asterisk/render.sh`, dann `docker exec <asterisk-container> asterisk -rx 'dialplan reload'` |
| `docker-compose.yml` | zuerst `./asterisk/render.sh`, dann `docker compose up -d` |

## 3. Anmelden und Einstellungen
1. `http://<host>:8080` öffnen, Benutzer **admin**, Passwort = das von `install.sh` angezeigte (`UI_PASSWORD`).
2. Der **Einrichtungsassistent** öffnet sich von selbst, solange Gemini-Zugang, Fritzbox-Zugangsdaten oder die Anmeldung von Asterisk fehlen („Später“ blendet ihn aus;
   wieder aufrufen unter Einstellungen → Allgemein). Er nutzt dieselben Felder und Tests wie die Einstellungen:
   Gemini-Schlüssel, Fritzbox (TR-064), IP-Telefon mit Live-Anzeige der Anmeldung, „Asterisk antwortet auf“ (Rufnummern), Abschluss mit Hinweisen zum Testanruf.
   Ohne Assistent dieselben Werte unter **Einstellungen → Anbindungen:**
   - **Google Gemini:** API-Schlüssel eintragen und *Verbindung testen* – oder Vertex AI (Einstellungen → KI, [Einstellungen](Einstellungen.md)).
   - **Fritzbox (Abfragen: Rufnummern, Telefonbuch, Anrufliste):** Adresse (am besten als IP, Standard `192.168.178.1`), TR-064-Benutzer und -Kennwort, *Verbindung testen*.
   - **Fritzbox: Anmeldung von Asterisk (IP-Telefon):** Benutzername und Kennwort des IP-Telefons, *Speichern und neu anmelden*. Die App schreibt die Zugangsdaten für Asterisk, lädt PJSIP neu und prüft die Anmeldung;
     schlägt sie fehl, stellt sie die alten Werte wieder her.
3. **Einstellungen → Allgemein:** Grundkonfiguration (wer ihr seid, in welchem Rahmen die KI arbeitet), Geschäftszeiten, Aufbewahrung der Anrufe.

## 4. Klingeln über Asterisk (empfohlen)
Damit alle Telefone klingeln und die Nummer des Anrufers zeigen, braucht Asterisk eine Klingel-Rufnummer `99NNN` – Einzelheiten und Alternativen:
[Klingel-Rufnummern und Anruferanzeige](Klingel-Rufnummern-und-Anruferanzeige.md). Kurz: In der Fritzbox eine Rufnummer „Anderer Anbieter“ mit Benutzername = Rufnummer = `99NNN`,
beliebigem Kennwort und **Registrar = IP des Rechners** anlegen, allen Telefonen zuweisen, und unter *Einstellungen → Telefonie → Klingeln der Telefone* eintragen. Einfacher: Die Seite
*Rufnummern → Anruferanzeige an den Telefonen* legt sie an.

## 5. Einrichten und testen
Siehe [Erste Schritte](Erste-Schritte.md): Kontakte abgleichen, Agent anpassen, **Anrufregel** prüfen (eine neue Installation hat schon die Regel „Assistent“: alle Nummern, „Vorher klingeln“ 0 = die KI nimmt sofort an; ohne aktive Regel nimmt die KI nicht an), Benutzer und Rufnummern,
Testanruf auf die Nummer, bei der das IP-Telefon eingetragen ist.

## Aktualisieren und Sichern
- Sicherung: der Ordner `data/` (Datenbank `lieschen.db`, Aufnahmen, Ansagen, Plugins). Datenbank im Betrieb: `sqlite3 data/lieschen.db ".backup backup.db"`.
- Aktualisieren: `./install.sh --update` (oder `./update.sh`): sichert `data/` nach `backup/lieschen-data-<Datum>.tar.gz`, `git pull` (bei ZIP vorher die Dateien kopieren:
  `.env`, `data/` und `asterisk/conf/` nicht überschreiben), `./asterisk/render.sh`, `docker compose up -d --build`; möglichst ohne laufendes Gespräch.
  Mehr: [Betrieb und Überwachung](Betrieb-und-Ueberwachung.md).

Probleme? [Fehlersuche](Fehlersuche.md).
