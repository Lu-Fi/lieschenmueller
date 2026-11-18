# Aufzeichnung

**Wofür diese Seite:** Was aufgezeichnet werden kann, wie man es steuert und beendet, und was rechtlich zu beachten ist. Eigene Telefonate nach draußen: [Ausgehende Gespräche und Mitschrift](Ausgehende-Gespraeche-und-Mitschrift.md).

## Was wird gespeichert?
| Option | Bedeutung |
|---|---|
| **Audio** | Aufnahme der Gespräche, die ein Mensch annimmt (abspielbar, auf Wunsch als MP3). Ohne Audio wird die Aufnahme nach dem Transkript gelöscht. |
| **Transkript** | Text des Gesprächs (nachträglich und fast live). |
| **KI-Audio** | Aufnahme der KI-Gespräche, mit Ansage zu Beginn. KI-Gespräche haben immer ein Transkript. |
| **Ansage** | „Das Gespräch wird aufgezeichnet“ vor der Aufnahme (je Regel abschaltbar; ohne Ansage ist die Aufzeichnung nur mit anderweitiger Information aller Beteiligten zulässig). |
| **Mail** | Empfänger mit „Auch bei Gesprächen mit Mitschrift“ bekommen nach aufgezeichneten Gesprächen eine Mail; ausgehende Regeln können zusätzliche Empfänger haben. |
| **Sprachbefehl** | „Lieschen, keine Aufzeichnung“ / „Aufzeichnung beenden“ beendet die Aufnahme nach etwa 10–15 s. |

Geregelt wird das über **Aufzeichnungsregeln** ([Agenten und Regeln](Agenten-und-Regeln.md)).

**Ausgehende KI-Anrufe** (Assistent ruft an, Kampagnen) haben keine Aufzeichnungsregeln: Sie werden immer transkribiert, Audio wird nicht aufgenommen.

## Während des Gesprächs
- Läuft keine Aufzeichnung, kann man sie in der Anrufansicht mit **● Aufnahme starten** zuschalten – auf Wunsch mit Ansage an die Beteiligten. **■ Aufnahme beenden** verwirft das bisher Aufgezeichnete.
- Ausgehende Gespräche werden aufgezeichnet, wenn man die Vorwahl (Standard **`*77`**, einstellbar unter *Einstellungen → Telefonie → Aufzeichnung von Gesprächen*, z. B. `000`) vor die Nummer wählt und eine Aufzeichnungsregel „Ausgehende Gespräche“ passt (Audio, Transkript, Hinweis-Ansage je Regel einstellbar) ([Ausgehende Gespräche und Mitschrift](Ausgehende-Gespraeche-und-Mitschrift.md), [Fritzbox-Anbindung](Fritzbox-Anbindung.md)).

## Aufbewahren, teilen, löschen
- **Archiv (★):** archivierte Anrufe samt Aufnahme werden von der automatischen Löschung ausgenommen.
- **Aufbewahrung:** Einstellung des Administrators (Tage). Ältere, nicht archivierte Anrufe werden stündlich entfernt.
- **Notiz und Freigabe:** je Anruf eine Notiz; einzelne Anrufe lassen sich anderen Benutzern freigeben (sie sehen Transkript, Notiz, Aufnahme).
- **Mail:** Empfänger unter *Benachrichtigungen* (je Rufnummer, Kurzfassung oder mit Transkript/Aufnahme).

## Rechtliches
Eine Aufzeichnung ohne Einwilligung der Beteiligten ist in Deutschland nicht zulässig. Die Hinweis-Ansage ist deshalb standardmäßig an; ohne sie liegt die Verantwortung beim Betreiber. Die Stimmungserkennung aus der Stimme kann nach dem EU AI Act als Emotionserkennung gelten – die Oberfläche weist darauf hin und lässt sie abschalten.
