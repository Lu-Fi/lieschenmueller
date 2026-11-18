# Schnellstart für Einsteiger

**Wofür diese Seite:** Eine Schritt-für-Schritt-Anleitung ohne Vorwissen: von „ich habe eine Fritzbox und einen kleinen Rechner“ bis zum ersten Testanruf mit der KI. Wer es schon kennt, nimmt die Kurzfassung
in [Installation](Installation.md). Kurz: `./install.sh` aufrufen, die angezeigte Adresse öffnen, dem Einrichtungsassistenten folgen. Menünamen der Fritzbox ändern sich zwischen Versionen; die Pfade hier sind für FRITZ!OS 8 beschrieben (die App ist mit 8.25 getestet). Weicht ein Menü ab, hilft die Suche oben in der Fritzbox-Oberfläche.

## 0. Was man braucht
| Was | Hinweis |
|---|---|
| **AVM Fritzbox** mit Telefonie, **FRITZ!OS 8** (getestet: 8.25) | andere Router gehen nicht ([Voraussetzungen](Voraussetzungen-und-Fritzbox.md)) |
| **Ein Rechner im selben Netz** wie die Fritzbox, dauerhaft eingeschaltet | z. B. Raspberry Pi, Mini-PC oder NAS mit Docker; Linux (Debian, Ubuntu, Raspberry Pi OS). Kabel (LAN) ist besser als WLAN |
| **Google-Konto** | für den Zugang zur KI (Gemini); **Abrechnung muss aktiviert werden**, die KI kostet Geld pro Gespräch |
| Ein Browser und etwas Geduld | |

**Zeitbedarf:** etwa 1 bis 2 Stunden beim ersten Mal.

Tipp: Notiere dir unterwegs alle Namen und Kennwörter (Papier oder Passwortverwaltung). Du brauchst sie in verschiedenen Schritten wieder.

## 1. Docker installieren
Docker führt Lieschen Müller in „Containern“ aus. Installiere **Docker Engine mit dem Compose-Plugin** nach der offiziellen Anleitung für dein System (docs.docker.com → „Install Docker Engine“ → Debian, Ubuntu bzw. für
Raspberry Pi OS die Debian-Anleitung). Danach prüfen:
```bash
docker --version
```
Erwartet: eine Zeile wie `Docker version …`.
```bash
docker compose version
```
Erwartet: `Docker Compose version …`. (Statt Docker geht auch Podman mit `podman-compose`; die Befehle unten lauten dann `podman-compose …`.)

Außerdem muss `python3` installiert sein (bei Debian/Ubuntu/Raspberry Pi OS meist schon):
```bash
python3 --version
```

## 2. Lieschen Müller herunterladen
**Variante A – git** (empfohlen, dann lässt es sich später leicht aktualisieren):
```bash
git clone <Adresse-des-Repositorys> lieschenmueller
```
Das lädt alle Dateien in den neuen Ordner `lieschenmueller`. (Die Adresse bekommst du von dem, der dir das Projekt gegeben hat; `git` ggf. mit `sudo apt install git` installieren.)

**Variante B – ZIP:** Das ZIP-Archiv auf den Rechner kopieren und entpacken, z. B. mit `unzip lieschenmueller.zip`, und den Ordner `lieschenmueller` nennen.

In den Ordner wechseln und prüfen:
```bash
cd lieschenmueller
ls
```
Erwartet: u. a. `docker-compose.yml`, `asterisk`, `bridge`, `README.md`.

## 3. Installieren mit `install.sh`
Im Ordner `lieschenmueller`:
```bash
./install.sh
```
Kommt „Permission denied“: `sh install.sh`. Meldet das Skript, dass Docker für diesen Benutzer nicht erlaubt ist: `sudo ./install.sh`.

Das Skript macht alles, was beim ersten Mal nötig ist, und erklärt bei jedem Problem, was zu tun ist:
1. Es prüft **Linux, Docker mit Compose-Plugin** (oder podman mit podman-compose) und **python3**. Fehlt etwas, steht dort, wie man es installiert.
2. Es legt die Einstellungsdatei **`.env`** an (nur wenn es sie noch nicht gibt; eine vorhandene wird **nie** überschrieben) – mit einem **zufälligen Admin-Passwort**
   und einem zufälligen internen Schlüssel (`AMI_SECRET`).
3. Es fragt nach dem **Port der Weboberfläche**. Einfach **Enter** drücken: Standard ist `8080`; ist der Port belegt, schlägt es einen freien vor.
4. Es erzeugt die Asterisk-Konfiguration, baut und startet die Container und **wartet**, bis beide laufen (beim ersten Mal einige Minuten).
5. Am Ende zeigt es einen Kasten mit der **Adresse der Weboberfläche**, dem **Admin-Passwort** und der **IP-Adresse des Rechners**.

**Wichtig:** Das Admin-Passwort wird nur dieses eine Mal angezeigt. **Sofort notieren** (Papier oder Passwortverwaltung) und nach der ersten Anmeldung unter „Mein Konto“ ein eigenes setzen.

Erwartet am Ende (Beispiel):
```
 Weboberflaeche:  http://192.168.178.50:8080
 Anmeldung:  Benutzer  admin
             Passwort  ...
 IP-Adresse dieses Rechners: 192.168.178.50
```
Das Skript kann **beliebig oft** laufen (z. B. nach einem Fehler): Es ändert nichts an einer vorhandenen `.env` und an den Daten im Ordner `data/`.
Weitere Möglichkeiten: `./install.sh --help` (u. a. `--no-start`: nur vorbereiten, nichts starten).

### Für Fortgeschrittene: von Hand statt mit `install.sh`
```bash
cp .env.example .env
```
In der `.env` (z. B. `nano .env`) nur zwei Zeilen ändern: `UI_PASSWORD=` (Passwort des ersten Administrators `admin`, **Pflicht**; direkt hinter das `=`, nur Buchstaben und Ziffern,
nichts weiter in der Zeile, kein Kommentar mit `#`) und ggf. `WEB_PORT=8080`, wenn der Port belegt ist. Dann:
```bash
./asterisk/render.sh
docker compose up -d --build
docker compose ps
```
`render.sh` erzeugt die Asterisk-Konfiguration (Ordner `asterisk/conf/`), legt `data/` an und ergänzt `AMI_SECRET` in der `.env`; **vor** dem ersten Start ausführen.
`docker compose ps` soll zwei Einträge (`asterisk`, `bridge`) mit `Up` zeigen, nach einer Minute `healthy`. Mit podman: `podman-compose …` statt `docker compose …`.

## 4. Fritzbox vorbereiten
Öffne die Oberfläche der Fritzbox im Browser (meist `http://fritz.box` oder `http://192.168.178.1`) und melde dich an.

### 4.1 IP-Telefon für Lieschen anlegen
1. **Telefonie → Telefoniegeräte → „Neues Gerät einrichten“.**
2. **„Telefon (mit und ohne Anrufbeantworter)“ → Weiter → „LAN/WLAN (IP-Telefon)“.**
3. **Name:** z. B. `LieschenMueller`. **Benutzername:** z. B. `LieschenMueller` (nur Buchstaben und Ziffern). **Kennwort:** wähle eins **nur aus Buchstaben, Ziffern und `- . _`**, **ohne Semikolon `;`**.
   Der Benutzername ist **nicht** die Rufnummer. **Notiere Benutzername und Kennwort.**
4. Auf der Seite **Rufnummern** wählst du, **auf welchen Nummern die KI Anrufe annehmen soll** (eingehend) und welche Nummer für ausgehende Anrufe der KI gilt. Nummern, bei denen das Gerät nicht eingetragen ist,
   erreichen die KI nicht. Hakst du alle an, nimmt die KI alles an – bei einer Testphase lieber nur **eine** Nummer.
5. Fertigstellen/Übernehmen. (Die Fritzbox fragt evtl. nach einer Bestätigung am Telefon oder an einer Taste.)

Das Gerät erscheint in der Liste der Telefoniegeräte; als „registriert“ gilt es erst, wenn Lieschen läuft und die Zugangsdaten eingetragen sind (Schritt 7, Einrichtungsassistent).

### 4.2 Fritzbox-Benutzer für die Schnittstelle (TR-064)
1. **System → Fritzbox-Benutzer → „Benutzer hinzufügen“.**
2. Name und Kennwort wählen (z. B. `lieschen-api`; Kennwort mindestens 8 Zeichen, **notieren**). Diese Zugangsdaten sind **anders** als die des IP-Telefons.
3. Haken bei den **Berechtigungen:**
   - **„FRITZ!Box Einstellungen“** – **nötig** (Telefonbuch, Rufnummern, Anwesenheit, Umstellen von Rufnummern),
   - **„Sprachnachrichten, Faxnachrichten, FRITZ!App Fon und Anrufliste“** – empfohlen (Anrufliste der Fritzbox für „Angenommen von …“; ob das Recht dafür nötig ist, hängt von der FRITZ!OS-Version ab),
   - **„Smart Home“**: **nicht** nötig, nicht anhaken; ebenso nicht „Zugang aus dem Internet“.
4. Speichern.

Dieses Recht ist weitreichend. Nutze ein langes Kennwort und gib es nicht weiter.

### 4.3 Zugriff für Anwendungen zulassen (TR-064)
**Heimnetz → Netzwerk → Netzwerkeinstellungen → Heimnetzfreigaben → Haken bei „Zugriff für Anwendungen zulassen“ → Übernehmen.** (Je nach FRITZ!OS-Version steht der Abschnitt erst nach einem Klick auf „Weitere Einstellungen“.)

### 4.4 IP-Adresse des Rechners notieren
Auf dem Rechner:
```bash
hostname -I
```
Erwartet: eine oder mehrere Adressen, die erste ist meist die richtige, z. B. `192.168.178.50` (`install.sh` zeigt sie am Ende ebenfalls an). Sie wird später für Klingel-Rufnummern und für den Browser gebraucht. Gib dem Rechner in der Fritzbox eine **feste Adresse**
(je nach FRITZ!OS-Version z. B. Heimnetz → Netzwerk → Netzwerkverbindungen → beim Gerät auf Bearbeiten → „Diesem Netzwerkgerät immer die gleiche IPv4-Adresse zuweisen“), sonst können spätere Einstellungen ins Leere laufen.

## 5. Zugang zur KI (Google Gemini)
Der einfachste Weg ist ein **API-Schlüssel aus Google AI Studio**:
1. Im Browser **aistudio.google.com** öffnen und mit dem Google-Konto anmelden.
2. Links **„Get API key“** (API-Schlüssel abrufen) → **„Create API key“** (neuen Schlüssel erstellen), Projekt wählen oder neu anlegen lassen.
3. Den Schlüssel (beginnt mit `AIza…`) **kopieren und sicher notieren**; behandle ihn wie ein Passwort.
4. **Abrechnung aktivieren:** Das zum Schlüssel gehörende Projekt braucht ein Rechnungskonto (in AI Studio beim Schlüssel bzw. in der Google Cloud Console unter „Abrechnung“). Ohne Abrechnung funktionieren die Live-Modelle nicht
   oder nur mit sehr kleinen Kontingenten; das Menü von Google ändert sich oft.

Fortgeschrittene, die die Verarbeitung **in der EU** halten wollen, nutzen stattdessen Vertex AI (Cloud-Projekt, Dienstkonto, Datei `data/vertex.json`); das ist in [Einstellungen](Einstellungen.md) beschrieben und nicht Teil dieser Anleitung.

## 6. In der Weboberfläche anmelden
Im Browser die Adresse öffnen, die `install.sh` angezeigt hat, z. B. `http://192.168.178.50:8080` (bei geändertem Port diese Nummer). Anmelden mit Benutzer **`admin`** und dem
angezeigten Passwort (beim Weg von Hand: das Passwort aus `UI_PASSWORD`).

Erwartet: Es öffnet sich der **Einrichtungsassistent** (nächster Schritt). Oben stehen **Anrufe, Agenten, Regeln, Anruf starten**; **Einstellungen**, Benutzer, Kontakte und Rufnummern stehen im Menü (☰) rechts oben.

## 7. Einrichtungsassistent
Der Assistent führt in fünf Schritten durch alles, was für den ersten Anruf nötig ist; oben zeigt eine Leiste, welche Schritte erledigt sind (✓). Jeder Schritt hat eine kurze Anleitung
und Erklärungen an den Feldern (Fragezeichen anfahren).

| Schritt | Was eintragen | Erwartet |
|---|---|---|
| **1. Google Gemini** | den API-Schlüssel aus Schritt 5 → *Speichern und testen* | „Schlüssel gültig“ |
| **2. Fritzbox** | Adresse (`192.168.178.1` bzw. die deiner Fritzbox, **als Zahlen**), Benutzer und Kennwort aus 4.2 → *Speichern und testen* | „Verbunden: … IP-Telefone, … Rufnummern“ |
| **3. IP-Telefon** | Benutzername und Kennwort des IP-Telefons aus 4.1 → *Speichern und anmelden* | „angemeldet“ (die Anzeige prüft alle 5 Sekunden neu; „abgelehnt“ = Kennwort oder Benutzername falsch) |
| **4. Rufnummern** | die Nummern anhaken, auf denen die KI antworten soll → *Übernehmen* | die Fritzbox übernimmt die Auswahl |
| **5. Abschluss** | – | Hinweise zur Anrufregel und zum Testanruf |

Mit **„Später“** blendet man den Assistenten aus; er bleibt unter **Einstellungen → Allgemein → Einrichtungsassistent** erreichbar. Dieselben Felder gibt es auch einzeln unter
**Einstellungen → Anbindungen** (Google Gemini, Fritzbox, „Fritzbox: Anmeldung von Asterisk“) und **Rufnummern** („Asterisk antwortet auf“).

Danach unter **Einstellungen → Allgemein:** *Grundkonfiguration* (ein paar Sätze: wer ihr seid und was die KI tun soll), *Aufbewahrung* der Anrufe („Anrufe löschen nach (Tagen)“; 0 = nie löschen).

**Woran man sieht, dass alles klappt:** In der **Kopfzeile** der Oberfläche stehen „API-Key ok“ und „Fritzbox ok“ (statt „API-Key fehlt“ bzw. „Fritzbox getrennt“); in der Fritzbox unter **Telefonie → Telefoniegeräte** steht das IP-Telefon als verbunden/registriert.
Alternativ auf dem Rechner:
```bash
docker compose exec asterisk asterisk -rx 'pjsip show registrations'
```
Erwartet: eine Zeile mit `Registered`.

## 8. KI einrichten und ersten Testanruf machen
1. **Kontakte** (Menü ☰) **→ „Mit Fritzbox abgleichen“** übernimmt dein Telefonbuch.
2. **Agenten:** den vorhandenen Agenten „Assistent“ öffnen, Auftrag (Prompt) und Begrüßung anpassen, mit der Stimmprobe eine Stimme wählen, speichern.
3. **Regeln → Anrufregeln:** Eine neue Installation hat schon die Regel **„Assistent“** (keine Bedingungen, Agent „Assistent“, „Vorher klingeln“ **0** = die KI nimmt **jeden** Anruf auf den Nummern aus 4.1 **sofort** an). Für den ersten Test passt das. **Ohne aktive Anrufregel nimmt die KI nicht an.**
   Später „Vorher klingeln“ auf z. B. 20 Sekunden stellen, wenn zuerst die Telefone klingeln sollen: Bei 0 klingelt auch bei Benutzern niemand (außer eine Wählregel erlaubt es ausdrücklich).
4. **Testanruf:** Von einem Handy oder Telefon die Rufnummer anrufen, bei der in 4.1 das IP-Telefon eingetragen wurde. Erwartet: Die KI meldet sich (nach einer kurzen Hinweis-Ansage, falls eingestellt).
5. Danach unter **Anrufe** den Eintrag öffnen: Transkript, Kosten und der **Ablauf** (Zeitleiste) sind zu sehen ([Anrufliste, Ablauf und Diagnose](Anrufliste-Ablauf-und-Diagnose.md)).

Danach: Benutzer, Rufnummern zuweisen, Klingeln aller Telefone, Aufzeichnung → [Erste Schritte](Erste-Schritte.md).

## 9. Typische Stolpersteine
| Problem | Ursache und Lösung |
|---|---|
| `install.sh` meldet „Docker ist installiert, aber dieser Benutzer darf es nicht benutzen“ | `sudo ./install.sh` oder den Benutzer in die Gruppe `docker` aufnehmen (`sudo usermod -aG docker $USER`, danach ab- und wieder anmelden) |
| Admin-Passwort aus `install.sh` nicht notiert | Es steht in der `.env` in der Zeile `UI_PASSWORD=` (gilt nur für den allerersten Start; danach das in der Oberfläche gesetzte) |
| Weboberfläche nicht erreichbar, `docker compose ps` zeigt die Bridge als beendet | Port belegt: in `.env` `WEB_PORT` auf z. B. `8091` ändern, dann `./install.sh` erneut (bzw. `./asterisk/render.sh` – der Dialplan ruft die Bridge über diesen Port – und `docker compose up -d --force-recreate`). Port 5060/udp muss ebenfalls frei sein (kein anderer SIP-Dienst auf dem Rechner) |
| Anmeldung `admin` klappt nicht | `UI_PASSWORD` war beim ersten Start leer oder enthielt einen Kommentar hinter dem Wert (bei älteren Fassungen der `.env.example` stand der Kommentar in derselben Zeile; ein leer gelassenes Passwort wurde dann als `# Passwort des ersten …` gelesen). Der Administrator wird nur beim **ersten** Start angelegt: `.env` korrigieren, `docker compose down`, `data/` löschen (nur bei neuer Installation!), `./asterisk/render.sh`, `docker compose up -d` |
| Fritzbox lehnt die Anmeldung ab („abgelehnt“, 401/403) | Kennwort des IP-Telefons falsch; Groß-/Kleinschreibung beachten |
| Fehler 404 beim Anmelden | Benutzername falsch oder das IP-Telefon ist in der Fritzbox nicht fertig angelegt; der **Benutzername** ist nicht die Rufnummer |
| Anmeldung der Fritzbox schlägt immer fehl | Kennwort enthält `;` (nicht erlaubt); am besten nur Buchstaben, Ziffern und `- . _` |
| Beim Ändern der Rufnummern meldet die App Kennwortzeichen-Fehler (Fehler 803) | Sonderzeichen im Kennwort des IP-Telefons: Kennwort in Fritzbox **und** Einstellungen auf Buchstaben/Ziffern/`- . _` ändern |
| „Verbindung testen“ der Fritzbox scheitert | Adresse falsch, „Zugriff für Anwendungen zulassen“ aus, Recht „FRITZ!Box Einstellungen“ fehlt oder falsches Kennwort |
| Es klingelt, aber die KI meldet sich nicht | Keine passende **Anrufregel**, Agent ausgeschaltet, IP-Telefon nicht bei dieser Rufnummer eingetragen, Asterisk nicht angemeldet oder Gemini-Guthaben leer (Warnleiste oben) |
| KI antwortet nicht / Warnleiste „Guthaben“ | Abrechnung beim Google-Projekt prüfen, Schlüssel testen |
| **Kein Ton** (Anrufer hört die KI nicht oder umgekehrt) | Meist Netzwerk: Rechner nicht im selben Netz, Firewall blockiert den RTP-Bereich von Asterisk, Container nicht im Host-Netzwerk (`network_mode: host` in `docker-compose.yml` nicht ändern); Fritzbox-Adresse als IP eintragen, danach `docker compose logs --tail 100 asterisk` ansehen; im Asterisk-Log `did not pass ACL test` = Adresse stimmt nicht |
| Telefone zeigen „LieschenMueller“ als Anrufer | normal ohne Klingel-Rufnummer; einrichten: [Klingel-Rufnummern und Anruferanzeige](Klingel-Rufnummern-und-Anruferanzeige.md) |

Mehr: [Fehlersuche](Fehlersuche.md).

## 10. Aktualisieren und Sichern
**Aktualisieren** (möglichst ohne laufendes Gespräch – ein Neustart beendet Gespräche):
```bash
./install.sh --update
```
(oder `./update.sh`, dasselbe). Das Skript **sichert zuerst den Ordner `data/`** als `backup/lieschen-data-<Datum>.tar.gz` (die Datenbank vorher konsistent kopiert), holt die neue
Version mit `git pull`, erzeugt die Konfiguration neu, baut und startet die Container. Bei einer **ZIP-Installation**: die neue Version entpacken und über den Ordner kopieren
(`.env`, `data/` und `asterisk/conf/` **nicht** überschreiben), dann `./install.sh --update`. Danach in der Oberfläche prüfen, ob oben „Fritzbox ok“ steht.

**Sichern:** Alles Veränderliche liegt im Ordner `data/` (Datenbank, Aufnahmen, Ansagen, Plugins). `./install.sh --update` legt bei jedem Update eine Sicherung in `backup/` an;
kopiere `backup/` bzw. `data/` regelmäßig auf ein anderes Gerät. Die Sicherungen enthalten Zugangsdaten im Klartext: geschützt aufbewahren.

**Von Hand (Fortgeschrittene):** `sqlite3 data/lieschen.db ".backup backup.db"` sichert die Datenbank im laufenden Betrieb; Aktualisieren mit `git pull`, `./asterisk/render.sh`, `docker compose up -d --build`.
