# Benachrichtigungen und Kosten

**Wofür diese Seite:** Wer bekommt wann eine Mail zu Anrufen, und was kostet die KI – mit Budget und Kostenbremse gegen Dauer- und Robo-Anrufe.

## E-Mail (Menü ☰ → Benachrichtigungen)
Zwei Teile: das **Mailkonto** (SMTP: Server, Port, Benutzer, Absender, Verschlüsselung, Kennwort; *Testmail senden*) und die **Empfänger**. Typische Werte: Outlook.com `smtp-mail.outlook.com`, Port 587, STARTTLS, App-Kennwort;
Microsoft 365 `smtp.office365.com` (SMTP-Authentifizierung muss erlaubt sein, was viele Firmenkonten abschalten); Gmail `smtp.gmail.com`, Port 587, App-Passwort. Das Passwort wird nie wieder angezeigt (Datenbank oder alternativ `SMTP_PASSWORD` in der `.env`).
- **Größte Mail mit Aufnahme** (Standard 10 MB): Ist die Aufnahme größer, geht die Mail ohne Audio raus (mit Hinweis).
- **Empfänger:** je Empfänger Rufnummern (leer = alle), „Mail bei einzelnen Anrufen“ (Ergebnis des Anrufs, nur wichtige Anrufe: Nachricht hinterlassen, hohe Dringlichkeit, starke Aufregung/Angst), Inhalt (Details, alle besprochenen Punkte, Transkript, Aufnahme),
  „Auch bei Gesprächen mit Mitschrift benachrichtigen“, **Tagesbericht** zur eingestellten Uhrzeit (*Bericht jetzt* zum Ausprobieren).
- Wird ein Anruf von der KI durchgestellt, kommt die Mail erst am Ende des ganzen Gesprächs.
- Mails enthalten Zusammenfassung und Transkript und gehen über den Mailanbieter ([Datenschutz und Sicherheit](Datenschutz-und-Sicherheit.md)).

## Kosten (Seite *Kosten*)
Tokens und Kosten je Anruf, je Monat und je Agent, Budget und Rest. Umrechnung in Euro mit dem täglichen EZB-Kurs; Ansagen und Stimmproben werden mitgezählt. **Maßgeblich ist die Abrechnung bei Google**; Abweichungen sind möglich.

## Kostenbremse
*Einstellungen → KI → Kostenbremse* (je Agent im Tab „Grenzen und Optionen“ überschreibbar): höchstens *n* KI-Anrufe je Rufnummer und Stunde, *n* KI-Minuten je Rufnummer und Tag, *n* KI-Minuten insgesamt pro Tag; je Agent die Höchstdauer eines Gesprächs
(sonst `MAX_CALL_SECONDS`). **0 = aus; bei Auslieferung ist alles aus.**
- Ist eine Grenze erreicht, nimmt die KI **nicht** an (wie „kein Agent“): Die Fritzbox lässt andere Telefone weiter klingeln; die Anrufliste zeigt „Kostenbremse (Agent)“ mit Status *Abgelehnt* und dem Grund im Ablauf.
- **Ausgenommen:** interne Nummern, Kontakte, Anrufe von den Fernabfrage-Nummern eines Benutzers. Externe Nummern, die Benutzer selbst eintragen (Büro, außer Haus), werden gebremst. Anrufe ohne Nummer zählen nur für das Tageslimit.
- Manuelles „Jetzt von der KI annehmen lassen“ im Banner wird nicht gebremst.
