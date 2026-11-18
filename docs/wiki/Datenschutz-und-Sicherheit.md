# Datenschutz und Sicherheit

**Wofür diese Seite:** Wohin Daten gehen, was die App schützt und was beim Betreiber liegt. Die Konstellationen im Einzelnen (Prompt-Injection, gefälschte Rufnummern) bewertet die [Sicherheitsbewertung](Sicherheitsbewertung.md). Rechtliche Pflichten sind keine Rechtsberatung.

## Wohin gehen die Daten?
- **Google (Gemini):** Gespräche und Transkripte werden verarbeitet – über die einfache Gemini-API oder, wenn gewählt, über **Vertex AI im Standort eu** ([Einstellungen](Einstellungen.md)). Für die DSGVO ist zusätzlich die Auftragsverarbeitung (Cloud Data Processing Addendum) abzuschließen.
- **Mailanbieter:** Mails enthalten Zusammenfassung und (auf Wunsch) Transkript und Aufnahme.
- **Alles andere** bleibt auf dem eigenen Rechner im Ordner `data/`.
- Das Bridge-Log kürzt Rufnummern und Mailadressen.

## Aufzeichnen und KI-Hinweis
- Der Anrufer wird zu Beginn auf die KI hingewiesen (Hinweis-Ansage des Agenten); die KI gibt sich auf Nachfrage immer als KI zu erkennen.
- Aufzeichnungen werden angekündigt („Das Gespräch wird aufgezeichnet“). Eine Aufzeichnung ohne Einwilligung der Beteiligten ist nicht zulässig; heimliches Aufnehmen oder Mitschreiben ist strafbar (§ 201 StGB). Wer die Hinweis-Ansage abschaltet, trägt die Verantwortung ([Aufzeichnung](Aufzeichnung.md), [Ausgehende Gespräche und Mitschrift](Ausgehende-Gespraeche-und-Mitschrift.md)).
- Die Stimmungserkennung aus der Stimme kann nach dem EU AI Act als Emotionserkennung gelten; sie ist optional und abschaltbar.
- Notrufnummern werden nie aufgezeichnet. Rohspuren werden nach der Transkription gelöscht; Anrufe werden nach der Aufbewahrungsfrist gelöscht (archivierte ★ bleiben).
- **WLAN-Anwesenheit** ist freiwillig und speichert keinen Verlauf ([WLAN-Anwesenheit](WLAN-Anwesenheit.md)).

## Zugangsdaten
Gemini-Schlüssel, Fritzbox-Kennwörter (TR-064 und IP-Telefon), Mail-Passwort und Google-Dienstkonto liegen **im Klartext in der Datenbank** (`data/lieschen.db`), das SIP-Kennwort zusätzlich in `data/asterisk/fritz.conf`. **Den Ordner `data/` und Sicherungen schützen.**
Der TR-064-Benutzer hat das weitreichende Recht „FRITZ!Box Einstellungen“.

## Netzwerk und Oberfläche
- Die Weboberfläche ist **nicht für das offene Internet** gedacht (kein HTTPS im Lieferumfang). Fernzugriff nur über VPN oder einen Reverse-Proxy mit HTTPS (Pfad `/internal/` nicht weiterreichen, Host-Header unverändert lassen, WebSocket/SSE erlauben; siehe `docs/INSTALLATION.md`).
- Anmeldung mit Konten (Passwörter mit scrypt, mindestens 10 Zeichen, Sperre nach Fehlversuchen); Rollen *Administrator* und *Eingeschränkt*; schreibende Aufrufe verlangen passenden `Origin`. Basic-Anmeldung nur mit `ALLOW_BASIC=1`.
- **SIP:** Asterisk nimmt Anfragen nur von der Fritzbox-IP an (Notschalter `SIP_ACL=0`); AMI nur lokal; die `/internal/*`-Aufrufe nur von `127.0.0.1` mit Schlüssel.
- **Wählschutz:** nur Ziffern/interne `**N`, gesperrte Präfixe (`DIAL_BLOCK`), die KI wählt nur freigegebene Anrufer.
- **Fernabfrage:** Rufnummer **und** Codewörter. **Plugins** laufen mit allen Rechten der App – nur vertrauenswürdige installieren (eigene erst nach Freigabe durch einen Administrator).
- Bekannte Lücke: Der Bridge-Container läuft als root ([Betrieb und Überwachung](Betrieb-und-Ueberwachung.md)).

## Sichtbarkeit
Benutzer sehen nur Anrufe auf ihnen zugewiesene Rufnummern (ab der Zuweisung), freigegebene und selbst gestartete Gespräche; Administratoren sehen alles.
