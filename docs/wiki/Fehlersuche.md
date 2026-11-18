# Fehlersuche und häufige Fragen

**Wofür diese Seite:** Symptom → mögliche Ursache → Maßnahme, dazu die häufigsten Fragen. Erster Blick bei Anrufproblemen: der **Ablauf** im Anruf ([Anrufliste, Ablauf und Diagnose](Anrufliste-Ablauf-und-Diagnose.md)). Logs:
`docker compose logs --tail 100 bridge` und `… asterisk`.

## Symptome
| Symptom | Ursache / Maßnahme |
|---|---|
| Es klingelt, aber die KI meldet sich nicht | Keine passende **Anrufregel**; Agent aus; IP-Telefon bei dieser Rufnummer nicht eingetragen; Asterisk nicht angemeldet (`docker compose exec asterisk asterisk -rx 'pjsip show registrations'`); **Kostenbremse**; Gemini-Guthaben leer (Warnleiste) |
| Anmeldung an der Fritzbox schlägt fehl | **404:** Benutzername falsch bzw. IP-Telefon nicht fertig angelegt (Benutzername ≠ Rufnummer). **401/403 („abgelehnt“):** Kennwort falsch. Kennwort mit `;` ist nicht erlaubt. Asterisk versucht es nach einer Ablehnung weiter |
| Fehler beim Ändern der Rufnummern (Kennwortzeichen, 803) | Das Kennwort des IP-Telefons enthält Zeichen, die TR-064 nicht annimmt (welche Zeichen erlaubt sind, meldet die Fritzbox selbst; Buchstaben, Ziffern und `- . _` sind sicher). Über die Weboberfläche der Fritzbox (Standardweg) stört das nicht; sonst Kennwort ändern |
| „Das Formular der Fritzbox … sieht anders aus als erwartet – nichts geändert“ | FRITZ!OS-Update: Die inoffizielle Weboberfläche passt nicht mehr (getestet mit 8.25). Von Hand in der Fritzbox einstellen ([Voraussetzungen und Fritzbox](Voraussetzungen-und-Fritzbox.md)) |
| „Fritzbox sperrt Anmeldungen noch … s“ | zu viele Fehlversuche mit den TR-064-Zugangsdaten; Kennwort prüfen, warten |
| Geräteliste/Kontakte/Rufnummern nicht lesbar | TR-064-Zugriff in der Fritzbox aus, Recht „FRITZ!Box Einstellungen“ fehlt, Zugangsdaten falsch (*Verbindung testen*). Ist das Fritzbox-Telefonbuch leer, bricht der Abgleich bewusst ab (Rechte prüfen) |
| Klingel-Rufnummer `99NNN` meldet sich nicht | in der Tabelle nicht eingeschaltet (Asterisk nimmt die Anmeldung sonst nicht an); Registrar in der Fritzbox falsch (muss die IP des Rechners sein); nach einem Asterisk-Neustart bis zu 5 Minuten warten; `pjsip show contacts` |
| Asterisk-Log `did not pass ACL test` | Anfrage kam nicht von der Fritzbox-Adresse aus den Einstellungen (z. B. Name statt IP). IP eintragen oder vorübergehend `SIP_ACL=0` |
| Telefone zeigen „LieschenMueller“ als Anrufer | Anruf läuft über das IP-Telefon: Klingel-Rufnummer einrichten ([Klingel-Rufnummern und Anruferanzeige](Klingel-Rufnummern-und-Anruferanzeige.md)) |
| Anrufer hört weder Freizeichen noch Musik | meist ein Zeitproblem mit der Fritzbox; Wartemusik ein und mindestens ein Stück aktiv? Sonst Mitschnitt/Fall melden |
| Kein Ton / Ton nur in einer Richtung | Leitungswerte und Seitenprüfung im Ablauf ansehen (0 empfangene Pakete = von dieser Seite kam nichts); Host-Netzwerk, Firewall (RTP), Fritzbox-Adresse als IP |
| Mehrere Ansagen zu Beginn | Hinweis-Ansage des Agenten und der Aufzeichnungsregel überlappen: zu einer zusammenlegen |
| Anrufe auf Benutzer klingeln nicht | Status „nicht erreichbar“ bzw. keine Nummer; Rufnummer nicht zugewiesen; „nicht stören“ ohne Ausnahme; Zeitplan – die **Vorschau** beim Benutzer zeigt „… weil …“ ([Benutzer und Status](Benutzer-und-Status.md)) |
| Mailversand schlägt fehl | Passwort fehlt, Anmeldung abgelehnt (App-Kennwort, SMTP-Authentifizierung); Meldung steht beim Testversand und im Log. Zu große Aufnahme geht ohne Audio raus |
| Ausgehende Gespräche kommen nicht durch | Telefon steht auf `99NNN`, aber diese ist nicht angemeldet → Notbetrieb abwarten oder von Hand zurückstellen ([Ausgehende Gespräche und Mitschrift](Ausgehende-Gespraeche-und-Mitschrift.md)); Ziel durch `DIAL_BLOCK` gesperrt (auch Ausland `00`) |
| Weboberfläche nicht erreichbar | Port belegt (`WEB_PORT` ändern), Container läuft nicht (`docker compose ps`) |
| KI klingt übersteuert/zu leise | *Einstellungen → KI → Stimme der KI → Lautstärke* |
| Vertex „unvollständig“ | Projekt-ID oder `data/vertex.json` fehlt; es gibt keinen stillen Rückfall |

## Häufige Fragen
**Gehen mehrere Telefonate gleichzeitig?** Ja. Jedes Gespräch ist ein eigener Kanal mit eigener Gemini-Sitzung; Grenzen setzen Kosten und Bandbreite.

**Warum nimmt die KI nicht an?** Siehe erste Zeile oben. Ohne Anrufregel nimmt niemand an.

**Wozu brauche ich Agent, Anrufregel und Aufzeichnungsregel?** Nur das, was du brauchst: Soll bloß ein Anrufer aufgezeichnet werden, genügt eine Aufzeichnungsregel. Siehe [Agenten und Regeln](Agenten-und-Regeln.md).

**Kann ich im Gespräch sagen „keine Aufzeichnung“?** Ja („Lieschen, keine Aufzeichnung“), sofern die Aufzeichnungsregel es nicht abschaltet. Eine Aufzeichnung lässt sich in der Anrufansicht auch zuschalten („Aufnahme starten“).

**Wie lange werden Anrufe aufbewahrt?** Nach der Einstellung des Administrators (Tage); archivierte Anrufe (★) bleiben.

**Was kostet es?** Die Gemini-Live-Modelle werden nach Tokens abgerechnet; die Seite *Kosten* zeigt Euro-Beträge, maßgeblich ist die Abrechnung bei Google ([Benachrichtigungen und Kosten](Benachrichtigungen-und-Kosten.md)).

**Wohin gehen meine Daten?** [Datenschutz und Sicherheit](Datenschutz-und-Sicherheit.md).

**Läuft es mit einem anderen Router?** Nein, nur mit einer AVM Fritzbox.
