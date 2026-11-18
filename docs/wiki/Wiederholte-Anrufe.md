# Wiederholte Anrufe

**Wofür diese Seite:** Wenn dieselbe Nummer in kurzer Zeit mehrmals anruft (zum Beispiel weil niemand abgenommen hat), kann eine Anrufregel einen eigenen Agenten einsetzen, und die KI erfährt, wie oft und mit
welchem Ergebnis die Nummer schon angerufen hat.

## Die Bedingung „mehrfach angerufen“
Optional an jeder **Anrufregel** (Seite *Regeln → Anrufregeln*, siehe [Agenten und Regeln](Agenten-und-Regeln.md)). Die Regel gilt nur, wenn dieselbe Nummer in den letzten *X* Minuten schon mindestens *N*-mal angerufen hat.

| Feld | Bedeutung | Grenzen |
|---|---|---|
| frühere Anrufe *N* | so viele frühere Anrufe sind nötig | 1 bis 50 (0 = Bedingung aus) |
| in *X* Minuten | Zeitraum | 1 bis 1440 Minuten |
| zählen | welche früheren Anrufe zählen | *alle* · *nur aufgelegte* (niemanden erreicht, weder eine Person noch die KI) · *alle ohne hinterlassene Nachricht* |

Regeln dazu:
- Der **aktuelle Anruf zählt nicht mit.** Gezählt werden nur **eingehende** Anrufe derselben Nummer (Vergleich mit Vorwahl, nicht nur die letzten Ziffern). **Unterdrückte Nummern zählen nie.**
- Passt die Bedingung nicht (zu wenige frühere Anrufe), wird die Regel übersprungen und die nächste geprüft.
- Wie bei jeder Anrufregel gewinnt die **erste passende Regel von oben**. Eine „Mehrfach angerufen“-Regel gehört deshalb **vor** die allgemeinen Regeln.

Was als „aufgelegt“ gilt: Ausgang „aufgelegt, ohne mit jemandem zu sprechen“ oder „nicht angenommen“. Die Ausgänge, die die App unterscheidet: *Nachricht hinterlassen*, *mit jemandem gesprochen*, *mit der KI gesprochen, keine Nachricht*,
*aufgelegt, ohne mit jemandem zu sprechen*, *nicht angenommen*, *läuft noch*.

## Was die KI erfährt
Trifft die Regel zu, erhält der Agent im Prompt den Hinweis „WIEDERHOLTER ANRUF“ mit Zahl, Zeitraum und Uhrzeit sowie Ausgang jedes dieser Anrufe und geht verständnisvoll darauf ein. Für Begrüßung und System-Prompt gibt es drei Platzhalter:

| Platzhalter | Inhalt |
|---|---|
| `{anzahl}` | Zahl der gezählten früheren Anrufe |
| `{minuten}` | der Zeitraum der Regel (ohne Regel: 60) |
| `{verlauf}` | Uhrzeit und Ausgang dieser Anrufe, z. B. „09:12 Uhr: aufgelegt, ohne mit jemandem zu sprechen“ |

Ohne „Mehrfach angerufen“-Regel gelten die letzten **60 Minuten** (nur für die Platzhalter, nicht für den Hinweis im Prompt).

## Beispiel
Ganz oben eine Anrufregel „3 frühere Anrufe in 10 Minuten, nur aufgelegte“, Klingeln 0, Agent „Dringend“ mit der Begrüßung: „Sage: Guten Tag, Sie haben in den letzten {minuten} Minuten schon {anzahl}-mal angerufen. Frage, ob es dringend ist.“
Darunter die normalen Anrufregeln.

Die Anrufliste zeigt im Ablauf, welche Regel gegriffen hat („Agent … übernahm (Anrufregel …)“; [Anrufliste, Ablauf und Diagnose](Anrufliste-Ablauf-und-Diagnose.md)).
