# Einstellungen

**Wofür diese Seite:** Ein Überblick, was wo in den Einstellungen steht (nur Administratoren). Die Einstellungen haben vier Tabs und ein Suchfeld, das alle Tabs durchsucht. Zugangsdaten stehen in der Datenbank im Ordner `data` und werden nie wieder angezeigt.

| Tab | Inhalt |
|---|---|
| **Allgemein** | **Grundkonfiguration** (wird jedem Agenten vorangestellt), **Geschäftszeiten** (globaler Zeitplan, Hinweis für die KI, wenn geschlossen), **Aufbewahrung** der Anrufe |
| **KI** | Stimme der KI, Auswertung der Gespräche, Google-Zugang (Vertex AI), Kostenbremse |
| **Telefonie** | Aufzeichnung, Klingeln der Telefone, Rückfrage, Wartemusik |
| **Anbindungen** | Google Gemini (Schlüssel), Fritzbox (TR-064), Fritzbox-Anmeldung von Asterisk, Google Kalender |

## Anbindungen
- **Google Gemini:** API-Schlüssel (Google AI Studio), *Verbindung testen*.
- **Fritzbox (Abfragen):** Adresse (am besten die IP; Standard `192.168.178.1`), TR-064-Benutzer und -Kennwort, Recht „FRITZ!Box Einstellungen“. Beim Ändern der Adresse fragt die App nach, weil Asterisk sich dann neu anmeldet (bei laufenden Gesprächen mit Warnung).
- **Fritzbox: Anmeldung von Asterisk:** Benutzername und Kennwort des IP-Telefons. *Speichern und neu anmelden* schreibt `data/asterisk/fritz.conf`, lädt PJSIP neu und prüft die Anmeldung (bis 30 s); bei Fehler werden die alten Werte wiederhergestellt.
  **Maßgeblich sind die Einstellungen in der App**: Wurde die Datei von Hand geändert, gilt beim nächsten Start wieder, was in den Einstellungen steht (die alte Datei bleibt als `fritz.conf.vorher`).
- **Google Kalender:** JSON-Schlüssel eines Dienstkontos ([Fernabfrage und Kalender](Fernabfrage-und-Kalender.md)).
- **Aufbewahrung** (Karte im Reiter *Allgemein*, „Anrufe löschen nach (Tagen)“): Anrufe samt Transkript und Aufnahme werden nach so vielen Tagen gelöscht (0 = nie; archivierte bleiben).

## KI
- **Stimme der KI:** Lautstärke der KI-Stimme am Telefon (Standard 60 %); für Rückfragen: Name, Stimme, Tempo, Stil, eigene Anweisungen, Stimmprobe.
- **Auswertung der Gespräche:** Modelle für ausführliche Zusammenfassung und Transkript.
- **Google-Zugang (Datenschutz):** Standard ist die einfache Gemini-API. **Vertex AI** läuft über ein eigenes Cloud-Projekt im Standort **eu** (andere Standorte sind bewusst nicht wählbar). *Verwenden für*: Aus · Transkript/Zusammenfassung/Auswertung · Alles (dann entfällt der Gemini-Schlüssel).
  Nötig: Projekt-ID und die Schlüsseldatei `data/vertex.json` (Dienstkonto, Rolle „Vertex AI User“). **Kein stiller Rückfall:** Ist Vertex gewählt, aber nicht eingerichtet, schlagen Anfragen fehl („Vertex unvollständig“). *Verbindung testen* prüft jedes Textmodell im Standort eu.
  Für die DSGVO zusätzlich: Auftragsverarbeitung (Cloud Data Processing Addendum) in der Google-Cloud-Konsole annehmen.
- **Kostenbremse:** höchstens *n* KI-Anrufe je Rufnummer und Stunde, *n* KI-Minuten je Rufnummer und Tag, *n* KI-Minuten insgesamt pro Tag; je Agent überschreibbar, dazu die Höchstdauer eines Gesprächs. 0 = aus ([Benachrichtigungen und Kosten](Benachrichtigungen-und-Kosten.md)).

## Telefonie
- **Aufzeichnung:** Audio dauerhaft speichern, Hinweis-Ansage („Das Gespräch wird aufgezeichnet“), MP3-Bitrate (16 bis 64 kbit/s, Standard 32), **Vorwahl für aufgezeichnete ausgehende Gespräche**, „auch ohne Vorwahl mitschreiben“ ([Ausgehende Gespräche und Mitschrift](Ausgehende-Gespraeche-und-Mitschrift.md), [Aufzeichnung](Aufzeichnung.md)).
- **Klingeln der Telefone:** die Klingel-Rufnummer für „alle Telefone klingeln“, Übersicht der Klingel-Rufnummern je Telefon, Wartemusik statt Freizeichen, Verzögerung ([Klingel-Rufnummern und Anruferanzeige](Klingel-Rufnummern-und-Anruferanzeige.md)).
- **Rückfrage:** Klingelzeit beim Benutzer (10–60 s), Höchstdauer nach dem Abheben (20–120 s), Ansage an den Anrufer ([Rückfrage vor dem Verbinden](Rueckfrage-vor-dem-Verbinden.md)).
- **Wartemusik:** Stücke hochladen (MP3, WAV, FLAC, OGG; bis 25 MB, 3 s bis 15 min), ein-/ausschalten (mindestens eines bleibt an), Lautstärke der Musik und zusätzliche Dämpfung beim Klingeln (0 bis 30 dB). Rechte an der Musik beachten. Am Telefon kommen nur etwa 300 bis 3400 Hz an.

## Was nicht in den Einstellungen steht
In der `.env`: `UI_PASSWORD`, `WEB_PORT`, `AMI_SECRET`, `GEMINI_MODEL`, `ALLOW_BASIC`, `SIP_ACL`, `MAX_CALL_SECONDS`, `DIAL_BLOCK`, `LIVE_INTERVAL`, `PLUGIN_DIR`, `SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASSWORD`/`SMTP_FROM`/`SMTP_SECURITY` ([Installation](Installation.md)). Werte aus der `.env` für Fritzbox, Gemini und Aufbewahrung gelten nur als Voreinstellung; die Oberfläche hat Vorrang.
