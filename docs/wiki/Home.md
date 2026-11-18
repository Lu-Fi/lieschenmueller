# Lieschen Müller – Wiki

Ein KI-Telefonassistent an der **AVM Fritzbox**: Er nimmt Anrufe an (Google Gemini Live), vermittelt an Benutzer, ruft selbst an, schreibt mit und wird über eine Weboberfläche verwaltet.

> **Neu hier? Starte mit dem [Schnellstart für Einsteiger](Schnellstart-fuer-Einsteiger.md)** – Schritt für Schritt ohne Vorwissen.
> **Voraussetzung:** Es funktioniert nur mit einer AVM Fritzbox ([Voraussetzungen und Fritzbox](Voraussetzungen-und-Fritzbox.md)).

## Inhaltsverzeichnis
**Einrichten**
| Seite | Wofür |
|---|---|
| [Schnellstart für Einsteiger](Schnellstart-fuer-Einsteiger.md) | Installation ohne Vorwissen |
| [Voraussetzungen und Fritzbox](Voraussetzungen-und-Fritzbox.md) | was gebraucht wird, welche Fritzbox-Funktionen genutzt werden, Rechte, Netzwerk |
| [Installation](Installation.md) | Kurzfassung für Kenner |
| [Erste Schritte](Erste-Schritte.md) | Checkliste bis zum Testanruf |
| [Einstellungen](Einstellungen.md) | was wo eingestellt wird |

**Anrufe steuern**
| Seite | Wofür |
|---|---|
| [Benutzer und Status](Benutzer-und-Status.md) | erreichbar/außer Haus/nicht erreichbar/nicht stören, Vertretung, Zeitplan, Wählregeln, Rangfolge |
| [Agenten und Regeln](Agenten-und-Regeln.md) | wer wann annimmt, wie die KI arbeitet |
| [Rückfrage vor dem Verbinden](Rueckfrage-vor-dem-Verbinden.md) | die KI fragt vor dem Verbinden nach |
| [Wiederholte Anrufe](Wiederholte-Anrufe.md) | wer immer wieder anruft |
| [Klingel-Rufnummern und Anruferanzeige](Klingel-Rufnummern-und-Anruferanzeige.md) | alle Telefone klingeln, Anrufer im Display |
| [Fernabfrage und Kalender](Fernabfrage-und-Kalender.md) | Anrufe per Telefon abfragen, Google Kalender |
| [WLAN-Anwesenheit](WLAN-Anwesenheit.md) | Status automatisch über das Handy im WLAN |

**Aufzeichnen und nachvollziehen**
| Seite | Wofür |
|---|---|
| [Aufzeichnung](Aufzeichnung.md) | was gespeichert wird, Regeln, Recht |
| [Ausgehende Gespräche und Mitschrift](Ausgehende-Gespraeche-und-Mitschrift.md) | Vorwahl, Mitschrift je Benutzer, Wächter/Notbetrieb |
| [Anrufliste, Ablauf und Diagnose](Anrufliste-Ablauf-und-Diagnose.md) | was mit einem Anruf geschah, Leitungswerte |
| [Benachrichtigungen und Kosten](Benachrichtigungen-und-Kosten.md) | Mail, Tagesbericht, Kosten, Kostenbremse |

**Betrieb, Sicherheit, Hilfe**
| Seite | Wofür |
|---|---|
| [Betrieb und Überwachung](Betrieb-und-Ueberwachung.md) | Aktualisieren, Sichern, Wächter, Logs |
| [Fritzbox-Anbindung](Fritzbox-Anbindung.md) | technische Details zu SIP und Klingel-Rufnummern |
| [Datenschutz und Sicherheit](Datenschutz-und-Sicherheit.md) · [Sicherheitsbewertung](Sicherheitsbewertung.md) | Daten, Schutz, Risiken |
| [Fehlersuche](Fehlersuche.md) | Symptome, häufige Fragen |
| [Plugins](Plugins.md) | Erweiterungen, Kampagnen |

Die vollständige Referenz steht in `docs/` (BEDIENUNG, INSTALLATION, ARCHITEKTUR, PLUGINS) und im [Changelog](../../CHANGELOG.md). In der Oberfläche erklärt das Symbol **?** die wichtigsten Begriffe mit Volltextsuche.

## Die Bausteine in einem Satz
**Fritzbox** (Telefonanlage) – **Asterisk** (hängt als Telefon daran) – **Bridge** (Entscheidungen, Gespräch mit Gemini, Weboberfläche) – **Datenbank** (alles Veränderliche in `data/`).

## Wichtig zu wissen
- Anrufer werden zu Beginn auf die KI hingewiesen; Aufzeichnungen werden angekündigt. Die rechtliche Verantwortung (Einwilligung, Auftragsverarbeitung) liegt beim Betreiber.
- Die Weboberfläche gehört nicht ins offene Internet (kein HTTPS im Lieferumfang).
