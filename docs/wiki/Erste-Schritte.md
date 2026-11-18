# Erste Schritte

**Wofür diese Seite:** Die Reihenfolge der ersten Einrichtung als Checkliste – von der Fritzbox bis zum ersten Testanruf. Voraussetzung: Es läuft eine **AVM Fritzbox**
([Voraussetzungen und Fritzbox](Voraussetzungen-und-Fritzbox.md)); die Installation steht auf der Seite [Installation](Installation.md).

1. **Fritzbox:** IP-Telefon `LieschenMueller` anlegen (Benutzername und Kennwort merken), Rufnummern zuweisen, einen Benutzer mit dem Recht „FRITZ!Box Einstellungen“ anlegen.
2. **Installieren:** `cp .env.example .env`, `UI_PASSWORD` setzen, `./asterisk/render.sh`, `docker compose up -d --build`.
3. **Anmelden:** `http://<host>:8080`, Benutzer `admin`, Passwort = `UI_PASSWORD`.
4. **Einstellungen (Administrator):** Gemini-Schlüssel (oder Vertex AI), Fritzbox-Adresse, TR-064-Benutzer, SIP-Anmeldung von Asterisk, Aufbewahrung der Anrufe, Grundkonfiguration, Geschäftszeiten.
   Die Oberfläche prüft die Anmeldung von Asterisk und stellt bei Fehlern die alten Werte wieder her. → [Einstellungen](Einstellungen.md)
5. **Kontakte:** *Mit Fritzbox abgleichen* – die KI prüft damit Angaben des Anrufers.
6. **Agent anpassen** (Prompt, Stimme, Hinweis-Ansage; mit der Stimmprobe eine Stimme wählen) und die **Anrufregel** prüfen – eine neue Installation hat schon die Regel „Assistent“ (alle Nummern, „Vorher klingeln“ 0 = sofort); **ohne aktive Regel nimmt die KI keinen Anruf an**.
   → [Agenten und Regeln](Agenten-und-Regeln.md)
7. **Benutzer** anlegen (Status, Nummern, Vertretung, Zeitplan) und unter **Rufnummern** den Benutzern die Fritzbox-Rufnummern zuweisen. → [Benutzer und Status](Benutzer-und-Status.md)
8. **Klingel-Rufnummer** `99NNN` einrichten, damit alle Telefone klingeln und die Anrufernummer zeigen. → [Klingel-Rufnummern und Anruferanzeige](Klingel-Rufnummern-und-Anruferanzeige.md)
9. **Optional:** Aufzeichnungsregeln ([Aufzeichnung](Aufzeichnung.md)), Mitschrift eigener Gespräche ([Ausgehende Gespräche und Mitschrift](Ausgehende-Gespraeche-und-Mitschrift.md)),
   Mail ([Benachrichtigungen und Kosten](Benachrichtigungen-und-Kosten.md)), Kalender und Fernabfrage ([Fernabfrage und Kalender](Fernabfrage-und-Kalender.md)).
10. **Testanruf** auf die Nummer, bei der das IP-Telefon eingetragen ist. Danach in der Anrufliste das Transkript und den **Ablauf** ansehen ([Anrufliste, Ablauf und Diagnose](Anrufliste-Ablauf-und-Diagnose.md)).

Die Hilfe in der Oberfläche (Symbol **?** oben rechts) erklärt dieselben Schritte mit Volltextsuche. Die genaue Anleitung mit allen Menüs steht in `docs/INSTALLATION.md`, die Seiten der Oberfläche in `docs/BEDIENUNG.md`.

Klappt etwas nicht? → [Fehlersuche](Fehlersuche.md)
