# Rückfrage vor dem Verbinden

**Wofür diese Seite:** Wie die KI vor dem Verbinden beim Benutzer nachfragt, wann sie verbindet und wo die Grenzen liegen. Wer erreichbar ist, bestimmt [Benutzer und Status](Benutzer-und-Status.md).

Ruft jemand an und möchte einen Benutzer sprechen, kann die KI **vorher beim Benutzer nachfragen**, ob er den Anruf annehmen möchte. Die Funktion ist pro Agent einschaltbar und standardmäßig **aus**.

## So läuft es ab
1. Der Anrufer verlangt einen Benutzer. Die KI sagt ihm kurz, dass sie nachfragt, ob der Benutzer Zeit hat (ohne eine Verbindung zu versprechen); fehlt das, spielt das System nach wenigen Sekunden die feste Ansage aus den Einstellungen. Dabei merkt sich die KI Namen und Anliegen des Anrufers.
2. Asterisk ruft den Benutzer an. Der Anrufer hört währenddessen **Musik** (Wartemusik).
3. Hebt der Benutzer ab, fragt eine zweite KI: „Hier ist der Telefonassistent. *Name* ist am Telefon, es geht um … Möchten Sie den Anruf annehmen?“
4. Sagt der Benutzer **ja**, wird der Anrufer verbunden.
5. Sagt er **nein**, meldet sich eine **Mailbox oder ein Anrufbeantworter** oder hebt **niemand ab** (Klingelzeit abgelaufen), geht der Anrufer zurück zur KI. Sie sagt, dass gerade niemand erreichbar ist, und bietet an, eine Nachricht aufzunehmen. Der Anrufer erfährt nicht, ob der Benutzer abgelehnt hat.

Alles steht in **einem Anruf**: Die Fragen der Rückfrage-KI (Name „Rückfrage“) und die Antworten des Benutzers erscheinen im Transkript des Anrufs des Anrufers, dazu ein Hinweis, was herauskam. Kosten und Mail enthalten das ganze Gespräch.

## Aufzeichnung nach der Zusage
Die **Aufzeichnungsregeln** gelten auch hier: Ist für den Anruf Audio oder Transkript aktiv, wird das verbundene Gespräch aufgezeichnet (beide Seiten getrennt, Transkript wie bei Gesprächen mit Mitschrift), sonst nicht. Die Hinweis-Ansage („Das Gespräch wird aufgezeichnet“) hört der Anrufer vor dem Warten und der Benutzer nach seiner Zusage, wenn die Regel sie vorsieht. Die Musik während des Wartens wird nicht aufgenommen. Hat der Anruf zusätzlich KI-Audio, liegt die Aufnahme des verbundenen Teils als eigene Datei (`call-<Nr>-gespraech`) auf dem Server und ist in der Anrufliste nicht verlinkt.

## Einstellen
- **Agent:** Option **Vor dem Verbinden nachfragen** (nur bei Agenten im Modus Assistent).
- **Einstellungen → Telefonie → Rückfrage vor dem Verbinden** (und Karte *Wartemusik*): Klingelzeit beim Benutzer (10–60 s), Höchstdauer der Rückfrage nach dem Abheben (20–120 s), Ansage an den Anrufer, Wartemusik.
- **Wartemusik:** eine Liste von Stücken. Eingebaut ist eine Spieluhr-Melodie im Wiegenlied-Stil (selbst komponiert und rechtefrei). Eigene Dateien (MP3, WAV, FLAC, OGG, bis 25 MB, 3 Sekunden bis 15 Minuten) lädst du in den Einstellungen hoch; sie werden für das Telefon umgerechnet. Jedes Stück lässt sich anhören, ein- und ausschalten und löschen. Asterisk spielt von den eingeschalteten Stücken zufällig eines; mindestens eines bleibt eingeschaltet. Achte darauf, dass du die Rechte an der Musik hast.

## Schutz
- Die Rückfrage-KI darf erst zustimmen (`accept`), wenn die Person **nach der Frage ausdrücklich ja gesagt hat**: Die Bridge prüft den Wortlaut der Antwort (Transkript) gegen eine kleine Wortliste (ja, gern, klar, okay, passt, in Ordnung, meinetwegen, einverstanden, verbinden, stell durch …, englisch yes/sure/go ahead) und lehnt ab, wenn im selben Satz davor eine Verneinung steht (nein, nicht, jetzt nicht, keine Zeit, später …) oder die Antwort mit einer Verneinung endet. Eine Mailbox-Ansage oder ein Geräusch reicht nicht mehr. Im Zweifel fragt die KI noch einmal. Die Wortlisten stehen als Konstanten `SCREEN_YES`/`SCREEN_NO` in `bridge/app/call.py`.
- Das Anliegen des Anrufers ist für die Rückfrage-KI nur ein markiertes Zitat (höchstens 120 Zeichen), keine Anweisung.
- Ein abgelehntes oder nicht erreichtes Ziel wird im selben Anruf nicht noch einmal angerufen.

## Grenzen
- Die Erkennung von Mailbox und Anrufbeantworter macht die KI nach Gehör. Sie ist **nicht fehlerfrei**: Eine ungewöhnliche Ansage kann als Mensch gelten (dann wird gefragt, aber niemand antwortet; nach Ablauf der Höchstdauer geht der Anrufer zurück zur KI), ein Mensch kann selten als Mailbox gelten.
- Die Rückfrage gilt nur für das Verbinden mit Benutzern durch die KI (`connect_employee`). Das Durchstellen per Klick im Live-Banner, das Verbinden mit Kontakten und die Fernabfrage fragen nicht nach.
- Der Anrufer wartet während der Rückfrage. Klingelzeit plus Rückfrage sollten zusammen nicht zu lang sein.
- Asterisk braucht beim ersten Start mit dieser Funktion die neue Datei `musiconhold.conf` (`./asterisk/render.sh`, danach `docker compose up -d`).
