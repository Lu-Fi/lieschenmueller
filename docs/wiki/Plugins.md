# Plugins

**Wofür diese Seite:** Wie man Erweiterungen ein- und ausschaltet und was das mitgelieferte Plugin *Kampagnen* kann. Die Programmierschnittstelle steht in [`docs/PLUGINS.md`](../PLUGINS.md).

> **Sicherheit:** Plugins laufen im Prozess der App mit allen ihren Rechten (Anrufe, Kontakte, Zugangsdaten). Nur Plugins aus vertrauenswürdiger Quelle installieren.

## Verwalten
Seite *Plugins*: Liste mit Ein-/Ausschalter. Eigene Plugins kommen nach `data/plugins/<name>/`; nach einem Neustart der Bridge erscheinen sie als „nicht freigegeben“ und werden **noch nicht ausgeführt**. Das Einschalten ist die **Freigabe**;
danach braucht es einen weiteren Neustart, damit der Code geladen wird. Ausschalten nimmt die Freigabe zurück. Eingebaute Plugins (Kampagnen) sind immer freigegeben. Ein Beispiel liegt in `examples/plugins/hello/`.

## Kampagnen (mitgeliefert)
Die KI ruft eine Liste von Zielen an, z. B. für Terminerinnerungen.
- **Allgemein:** Name, Agent, Auftrag, Auftraggeber, Terminpool, Pause zwischen Terminen, Mail, Kalendertitel.
- **Ziele:** Eingabe oder aus dem Telefonbuch. **Zeiten und Wiederholungen:** Wochentage, Uhrzeiten, Versuche pro Person, Abstand.
- **Anrufbeantworter:** später erneut anrufen, Nachricht sprechen oder auflegen.
- **Terminpools:** Termine, die die KI anbietet und exklusiv bucht, auf Wunsch aus einem Google Kalender (Platzhalter-Termine mit Präfix; Einrichtung: [Fernabfrage und Kalender](Fernabfrage-und-Kalender.md)).
- Ausgehende Kampagnenanrufe folgen keinen Aufzeichnungsregeln: immer Transkript, kein Audio. Einzelne Abläufe (Kampagnen-Anrufe) sind laut Projektstand noch ohne Erprobung am echten Telefon.
