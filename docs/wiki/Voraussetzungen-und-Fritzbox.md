# Voraussetzungen und Fritzbox

**Wofür diese Seite:** Sie sagt, was vorhanden sein muss, bevor man installiert, und welche Funktionen der Fritzbox Lieschen Müller im Einzelnen benutzt. Wer wissen will, was nach einem
FRITZ!OS-Update noch geht, findet es unten.

> **Nur AVM Fritzbox.** Andere Router und Telefonanlagen werden nicht unterstützt.

## 1. Was die Fritzbox leisten muss
Lieschen Müller (Asterisk + Bridge) hängt als Telefon an der Fritzbox. Gebraucht werden vier Dinge, in dieser Reihenfolge der Wichtigkeit:

| # | Funktion der Fritzbox | Wofür | Pflicht? |
|---|---|---|---|
| 1 | **IP-Telefon** (Telefonie → Telefoniegeräte → Neues Gerät einrichten → Telefon → LAN/WLAN): Asterisk meldet sich per SIP an der Fritzbox an (Registrar = Fritzbox) | Anrufe annehmen, ausgehende KI-Anrufe, alles Weitere | **ja** |
| 2 | **Rufnummern „Anderer Anbieter“ `99NNN`**: Die Fritzbox meldet sie bei Asterisk an (Registrar = **der Rechner mit Asterisk**); `NNN` ist die interne Nummer eines Telefons (`99610` für `**610`) | alle Telefone klingeln lassen, Anrufernummer im Display, Vorwahl-Weg für aufgezeichnete ausgehende Gespräche, Umstellen der ausgehenden Nummer | empfohlen |
| 3 | **TR-064** (offizielle Schnittstelle der Fritzbox) | siehe unten | für Telefonbuch, Rufnummern, Anwesenheit |
| 4 | **Weboberfläche der Fritzbox** (inoffiziell) | Rufnummern von DECT-/IP-Telefonen lesen und schreiben, ausgehende Nummer umstellen | nur für die Komfortfunktionen |

### Was über TR-064 geschieht
Mit dem TR-064-Benutzer liest und schreibt die App:
- **Telefonbuch** (Seite *Kontakte → Mit Fritzbox abgleichen*; Einträge der Fritzbox sind danach schreibgeschützt).
- **Eigene Rufnummern** und **interne Anschlüsse** (DECT-Handsets, analoge Anschlüsse, IP-Telefone) für die Seiten *Rufnummern* und *Benutzer*.
- **Anrufliste**: Welches Telefon hat einen Anruf angenommen (Anzeige „Angenommen von …“)? Die App vergleicht dazu Anrufer und Zeit mit der Anrufliste der Fritzbox.
- **Hosts/WLAN-Anwesenheit**: Geräteliste und „ist das Gerät gerade im Netz?“ je MAC-Adresse ([WLAN-Anwesenheit](WLAN-Anwesenheit.md)).
- **IP-Telefone**: eingehende Rufnummern und ausgehende Nummer des Asterisk-Telefons und anderer IP-Telefone lesen; im Notfall (wenn die Weboberfläche nicht geht) auch die Rufnummern
  des Asterisk-Telefons schreiben.
- **Konten anlegen**: Beim Einschalten der „Anruferanzeige“ legt die App die Klingel-Rufnummer `99NNN` als „Anderer Anbieter“-Konto an (zufälliges Kennwort; Asterisk prüft es nicht).

### Was über die Weboberfläche der Fritzbox geschieht (inoffiziell)
Dieselben Zugangsdaten wie bei TR-064 dienen zur Anmeldung an der Weboberfläche (`login_sid.lua`; gelesen wird mit `query.lua`, gespeichert mit `data.lua`, Seiten
`edit_dect_num` für DECT-Telefone und `edit_ipfon_num` für IP-Telefone und FRITZ!Apps). Das tut die App, was ein Klick in der Fritzbox täte:
- **Ankommende Rufnummern** eines DECT- oder IP-Telefons anhaken/abhaken (z. B. die Klingel-Rufnummer `99NNN` dem Telefon zuweisen). TR-064 kann das für DECT nicht schreiben.
- **Ausgehende Rufnummer** eines Telefons ändern (für die Mitschrift ausgehender Gespräche ohne Vorwahl; auch die ausgehende Nummer des Asterisk-Telefons).
- Das Kennwort eines IP-Telefons wird dabei **nicht** gesendet (kein Kennwortfeld im Formular), Sonderzeichen im Kennwort stören also nicht.

**Schutz vor Fehlern:** Vor dem Schreiben prüft die App, ob das Formular so aussieht wie erwartet. Sonst ändert sie nichts. Nach dem Speichern liest sie zurück und schreibt bei einer Abweichung den
alten Stand zurück und meldet den Fehler. Jede Anmeldung erscheint im Ereignisprotokoll der Fritzbox; die Telefonliste wird deshalb 30 Sekunden zwischengespeichert.
Kennwörter und Sitzungskennungen stehen nie in Logs oder Fehlermeldungen.

### Getestet mit FRITZ!OS 8.25 – kann nach Updates ausfallen
Die Weboberfläche ist **keine dokumentierte Schnittstelle**. Getestet ist sie mit **FRITZ!OS 8.25**. Ein Update kann Seiten oder Felder ändern; dann meldet die App „Das Formular der
Fritzbox (Telefon bearbeiten) sieht anders aus als erwartet … nichts geändert“ und lässt die Fritzbox unberührt.

| Bereich | Funktioniert nach einem Ausfall der Weboberfläche weiter? |
|---|---|
| Telefonieren, KI-Gespräche, Durchstellen, Rückfrage | ja (laufen über SIP) |
| Klingeln aller Telefone über `99NNN`, Anruferanzeige bei bereits eingerichteten Nummern | ja |
| Telefonbuch, Rufnummernliste, „Angenommen von …“, WLAN-Anwesenheit, Konto `99NNN` anlegen | ja (TR-064) |
| Rufnummern des Asterisk-IP-Telefons ändern | ja, mit Rückfall über TR-064 (dort nur Buchstaben, Ziffern und `- . _` im Kennwort des IP-Telefons) |
| Rufnummer `99NNN` einem DECT-/IP-Telefon automatisch zuweisen | **nein** – von Hand in der Fritzbox zuweisen (Telefonie → Telefoniegeräte → Bearbeiten → Ankommende Anrufe), in der Oberfläche das Häkchen „zugewiesen“ setzen |
| Ausgehende Nummer eines Telefons umstellen (Mitschrift **ohne** Vorwahl) | **nein** – von Hand umstellen; Mitschrift mit Vorwahl (Wahlregel) geht weiter |

## 2. Rechte des Fritzbox-Benutzers
- Für TR-064 **und** die Weboberfläche genügt ein Fritzbox-Benutzer mit dem Recht **„FRITZ!Box Einstellungen“** (System → Fritzbox-Benutzer). Er wird unter
  *Einstellungen → Anbindungen → „Fritzbox (Abfragen …)“* mit Adresse eingetragen; **Verbindung testen** liest Telefone und Rufnummern.
- In der Fritzbox muss der Zugriff für Anwendungen erlaubt sein: *Heimnetz → Netzwerk → Netzwerkeinstellungen → Heimnetzfreigaben → „Zugriff für Anwendungen zulassen“* (je nach FRITZ!OS-Version hinter „Weitere Einstellungen“)
  (die Menünamen ändern sich zwischen Versionen).
- Das Recht ist weitreichend (Einstellungen der Fritzbox ändern). Kennwort und Datenbank schützen ([Datenschutz und Sicherheit](Datenschutz-und-Sicherheit.md)). Ohne dieses Recht
  meldet z. B. die Geräteliste: „Geräteliste der Fritzbox nicht lesbar … Hat der TR-064-Benutzer das Recht ‚FRITZ!Box Einstellungen‘?“
- Das **IP-Telefon** hat einen **eigenen** Benutzernamen und ein eigenes Kennwort (nicht die Rufnummer, Kennwort ohne `;`). Es trägt nur Rufnummern, auf die die KI antworten soll –
  **keine** Klingel-Rufnummern `99…`. Die Anmeldung trägt man unter *Einstellungen → Anbindungen → „Fritzbox: Anmeldung von Asterisk“* ein.
- Ob die Anrufliste über TR-064 ein weiteres Recht („Sprachnachrichten, Faxnachrichten, FRITZ!App Fon und Anrufliste“) verlangt, steht in der Projektdokumentation nicht; bei „Angenommen von …“-Lücken im Zweifel dieses Recht dem Benutzer geben.

## 3. Netzwerk
- Der Rechner mit Asterisk steht im **selben LAN wie die Fritzbox**. Beide Container laufen mit **Host-Netzwerk** (kein NAT für SIP/RTP).
- Frei sein müssen: **5060/udp** (SIP), der **RTP-Bereich** von Asterisk und der **Web-Port** (`WEB_PORT`, Standard 8080).
- **SIP-IP-Sperre:** Asterisk nimmt SIP-Anfragen **nur von der Fritzbox-Adresse** aus den Einstellungen an (PJSIP `type=acl` in `data/asterisk/fritz.conf`) und erkennt die Endpoints nur an der IP
  (`identify_by=ip`). Ein anderes Gerät im LAN kann sich so nicht als `99NNN` ausgeben. Folgen: Die Fritzbox-Adresse sollte als **IP** eingetragen sein (ein Name, der anders aufgelöst wird,
  führt zu `did not pass ACL test` im Asterisk-Log); der Notschalter `SIP_ACL=0` in der `.env` schaltet die Sperre ab (nur zur Fehlersuche).
- Die Klingel-Rufnummern `99NNN` haben in der Fritzbox als **Registrar die IP des Rechners mit Asterisk** (nicht die der Fritzbox).
- Zwei Asterisk-Instanzen an derselben Fritzbox verdrängen sich (z. B. Entwicklungs- und Produktivrechner): nur eine laufen lassen.

## 4. Google Gemini
Die KI spricht über **Google Gemini Live**. Zwei Wege (Einstellungen → Anbindungen bzw. KI):
- **API-Schlüssel aus Google AI Studio** mit **aktivierter Abrechnung**. Die Modelle kosten Geld (Preise je Modell im Agenten-Editor, Auswertung auf der Seite *Kosten*).
- **Vertex AI** (Google Cloud, **Standort `eu`**): Google-Cloud-Projekt, Dienstkonto mit der Rolle „Vertex AI User“, Schlüsseldatei `data/vertex.json` auf dem Server, Projekt-ID in den Einstellungen.
  Dann bleibt die Verarbeitung in der EU. Einstellung „Verwenden für“: *Aus*, *Transkript, Zusammenfassung, Auswertung* oder *Alles* (auch die Telefon-KI; der Schlüssel entfällt dann).
  Es gibt keinen stillen Rückfall auf die einfache API. Mehr: [Einstellungen](Einstellungen.md).

## 5. Software auf dem Host
- **Docker Compose** (`docker compose`) oder **Podman Compose** (`podman-compose`); zwei Container: Asterisk (`andrius/asterisk`) und Bridge (Python 3.12, wird gebaut).
- **python3** auf dem Host für `./asterisk/render.sh` (erzeugt die Asterisk-Konfiguration aus der `.env`).
- Ein Browser für die Weboberfläche. **Kein HTTPS** im Lieferumfang: nur im Heimnetz, per VPN oder hinter einem Reverse-Proxy.

## 6. Weitere Zugänge (nur für Zusatzfunktionen)
- **E-Mail** (SMTP, z. B. Outlook.com oder Microsoft 365) für Benachrichtigungen und Tagesbericht – [Benachrichtigungen und Kosten](Benachrichtigungen-und-Kosten.md).
- **Google-Dienstkonto** mit Kalender-API für Benutzerkalender und Terminpools – [Fernabfrage und Kalender](Fernabfrage-und-Kalender.md).

## Tabelle: Funktion → braucht
| Funktion | braucht |
|---|---|
| KI nimmt Anrufe an | IP-Telefon in der Fritzbox, Gemini, Anrufregel |
| Alle Telefone klingeln, Anrufer im Display | `99NNN` („Anderer Anbieter“, Registrar = Rechner), allen Telefonen zugewiesen |
| Durchstellen, Rückfrage | interne Nummern in der Fritzbox, Benutzer mit Nummern |
| Telefonbuch, Rufnummern, „Angenommen von“ | TR-064-Benutzer („FRITZ!Box Einstellungen“) |
| WLAN-Anwesenheit | TR-064 (Geräteliste) |
| Anruferanzeige je Telefon (automatisch) | TR-064 (Konto) + Weboberfläche der Fritzbox (Zuweisung) |
| Aufzeichnen mit Vorwahl | Wahlregel in der Fritzbox → `99NNN` |
| Mitschrift ohne Vorwahl | Weboberfläche der Fritzbox (ausgehende Nummer), `99NNN` angemeldet |

Weiter: [Installation](Installation.md) · [Klingel-Rufnummern und Anruferanzeige](Klingel-Rufnummern-und-Anruferanzeige.md) · [Fritzbox-Anbindung](Fritzbox-Anbindung.md)
