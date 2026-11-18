# Fernabfrage und Kalender

**Wofür diese Seite:** Wie ein Benutzer per Telefon seine Anrufe abfragt (Fernabfrage) und wie ein Google Kalender angebunden wird, damit die KI Auskunft zum Aufenthalt geben und Termine verwalten kann.

## Fernabfrage
Beim Benutzer (Tab *Fernabfrage*): „Fernabfrage erlauben“, weitere Rufnummern (die eigene Nummer und die Nummer außer Haus gelten automatisch) und **Codewörter**.
1. Der Anruf muss von einer erlaubten Nummer kommen **und** der Anrufer muss **alle Codewörter** nennen (jedes als ganzes Wort). Ohne Freischaltung gilt der Anruf als normaler Anruf.
2. Danach: neue Anrufe auflisten, Zusammenfassungen/Transkripte vorlesen, Aufnahmen abspielen, **Rückruf** (optional mit Mitschrift), **Verbinden** mit Kontakten/Benutzern.
3. Nach 3 Anrufen ohne Freischaltung innerhalb einer Stunde ist die Fernabfrage 30 Minuten gesperrt („Fernabfrage gesperrt“).
4. Codewörter werden im Transkript geschwärzt. Ein Anruf von einer Fernabfrage-Nummer geht direkt an die KI (persönliche Anrede, ohne Hinweis-Ansage).

Rufnummern von außen lassen sich fälschen – deshalb Nummer **und** Codewörter; Codewörter wählen, die man nicht erraten kann ([Sicherheitsbewertung](Sicherheitsbewertung.md)).

## Google Kalender des Benutzers
Beim Benutzer (Tab *Kalender*) trägt man die Kalender-ID ein.
- **Wo ist der Benutzer?** Fragt ein **berechtigter** Anrufer danach (eigene Nummern, auf Wunsch andere Benutzer und eine Liste weiterer Nummern), sagt die KI, ob der Benutzer laut Kalender in einem Termin ist und bis wann – je nach Einstellung mit Titel/Ort oder nur „ist in einem Termin“.
  Alle anderen erhalten keine Auskunft.
- **Per Fernabfrage** Termine abfragen, freie Zeiten erfragen (Mo–Fr 8–18 Uhr) und eintragen; die KI wiederholt vorher Titel, Tag und Uhrzeit und prüft auf Überschneidungen.

## Einrichtung des Kalenders (Dienstkonto)
Einmalig, ohne Browser-Login der App (Stand Oktober 2026, Menünamen können abweichen):
1. In der Google Cloud Console ein Projekt anlegen, die **Google Calendar API** aktivieren.
2. **IAM & Verwaltung → Dienstkonten:** Dienstkonto erstellen, Schlüssel (JSON) erzeugen.
3. In Google Kalender den gewünschten Kalender mit der `client_email` des Dienstkontos teilen (Berechtigung „Änderungen an Terminen vornehmen“); die **Kalender-ID** kopieren.
4. *Einstellungen → Anbindungen → Google Kalender*: JSON einfügen, *Schlüssel speichern*, mit einer Kalender-ID *Verbindung testen*.
Der Kalender-Pool im Plugin *Kampagnen* nutzt dieselbe Anbindung ([Plugins](Plugins.md)). Ausführlich: `docs/INSTALLATION.md`.
