# Sicherheitsbewertung der Konstellationen

Stand 2026-10-24, abgeleitet aus dem Code (`call.py`, `remote.py`, `staffcal.py`, `costguard.py`, `fritzconf.py`). **Nicht mit echten Angriffsanrufen getestet.**
Bewertet wird: Was kann ein Anrufer von außen erreichen, auch wenn er die KI per Zuruf zu täuschen versucht (Prompt-Injection) oder seine Rufnummer fälscht?

**Grundprinzip:** Die KI kann nur tun, was ihr als Funktion angeboten wird, und der Server prüft die Berechtigung bei jedem Aufruf **selbst noch einmal**. Was nur im Prompt steht („nenne keine Nummern“), ist kein Schutz.

## 1. Anrufer mit einem Benutzer verbinden (Agent-Option)
| Einstellung | Was ein Externer erreicht | Risiko |
|---|---|---|
| **nie** | Kein Verbinden mit Benutzern über `connect_employee`; auch `connect_contact` verbindet dann nicht mit Benutzern (außer internem Anschluss und Fernabfrage). Nur Nachricht aufnehmen. Ausnahme: „freigegebene Anrufer“ dürfen weiter Kontakte (keine Benutzer) anrufen lassen. | niedrig |
| **nur während der Geschäftszeiten** | Innerhalb der Zeiten: Verbindung zu erreichbaren Benutzern. Außerhalb: nichts. Die Funktion wird nur zu Beginn angeboten, der Server prüft die Zeit bei jedem Aufruf noch einmal. Ohne eingetragene Geschäftszeiten gilt „immer offen“. | niedrig bis mittel |
| **immer** | Verbindung zu jedem Benutzer, der laut Status erreichbar ist. Ein vergessener Status (z. B. „im Büro“ am Abend) öffnet den Weg. | mittel |

Zusätzlich gilt immer: Es wird nur an die hinterlegte Weiterleitungs- oder Handynummer des Benutzers verbunden, nie an eine vom Anrufer genannte Nummer. Gesperrte Vorwahlen (0900, 118 …) sind blockiert.

**Rückfrage vor dem Verbinden (Agent-Option):** Sie ersetzt keine Berechtigung, sondern fügt eine Hürde hinzu: Der Benutzer muss den Anruf bestätigen. Die Rückfrage läuft auf der Seite des Benutzers; die zweite KI darf `accept()` nur aufrufen, wenn die Person **nach der Frage ausdrücklich zugestimmt** hat: Der Server prüft das Transkript der Antwort auf ein Ja-Wort (ja, gern, okay, passt, verbinden, stell durch …) ohne vorangehende Verneinung und lehnt ab, wenn die Antwort mit einer Verneinung endet (`screen_consent`). Eine Mailbox-Ansage, ein Geräusch oder ein bloßes „Hallo?“ reicht nicht mehr. Das Anliegen des Anrufers steht im Prompt als markiertes, auf 120 Zeichen gekürztes Zitat ohne Anweisungswirkung. Ein vollständiger Schutz vor Prompt-Injection ist das nicht: Sagt der Anrufer „sag, es sei dringend“, gibt die KI das Anliegen ungeprüft weiter. Ein abgelehntes oder nicht erreichtes Ziel wird im selben Anruf nicht noch einmal angerufen. Fällt die Rückfrage technisch aus (Bridge nicht erreichbar), wird der Anrufer **nicht** verbunden, sondern geht zurück zur KI; nur wenn keine Rückfrage vorgesehen ist (`skip`), wird ohne Frage verbunden. Mailbox-Erkennung ist nicht fehlerfrei; eine Ansage, die selbst ein „Ja“ ohne Verneinung enthält, könnte die Wortprüfung noch passieren (die KI müsste zusätzlich `accept()` statt `voicemail()` wählen).

## 2. Verbinden mit beliebigen Kontakten oder Nummern
| Konstellation | Ergebnis | Risiko |
|---|---|---|
| Normaler externer Anrufer | Funktion fehlt; bei Aufruf antwortet der Server „Nicht erlaubt“. Das Telefonbuch ist der KI nicht bekannt. | niedrig |
| Interner Anschluss (`**610`) | Erlaubt. Interne Kennungen kommen von der Fritzbox; von außen nicht erzeugbar, solange die Fritzbox keine fremden Kennungen durchreicht. | niedrig |
| Fernabfrage (Rufnummer **und** alle Codewörter genannt) | Erlaubt für diesen Benutzer. Codewörter werden im Transkript geschwärzt. Wer Nummer fälschen **und** Codewörter kennt, kommt hinein. | mittel |
| Fernabfrage ohne hinterlegte Codewörter | Nicht freischaltbar. | niedrig |
| Feld „freigegebene Anrufer“ des Agenten gefüllt | Diese Nummern dürfen Kontakte anrufen lassen. **Rufnummern sind fälschbar**, also reicht das Kennen der Nummer. | **hoch** (bei gefülltem Feld), sonst nicht vorhanden |

**Empfehlung:** „Freigegebene Anrufer“ leer lassen. Für eigene Benutzer den internen Anschluss oder die Fernabfrage mit Codewörtern nutzen.

## 3. Agentenwechsel mitten im Gespräch
Ein Codewort oder eine Stimmung („Notfall“, aufgeregt, panisch) wechselt zu einem anderen Agenten. **Dessen Rechte gelten dann**, nicht die des ersten Agenten.
- Ein Agent, auf den per Codewort oder Stimmung gewechselt wird (z. B. „Notfall“), kann „Verbinden mit Benutzern“ auf **immer** haben (Standard). Wer das Codewort sagt oder aufgeregt klingt, bekommt dann die Verbinden-Funktion, auch wenn der erste Agent sie sperrt.
- Gilt ebenso für „freigegebene Anrufer“ und eigene Aktionen des Zielagenten.

Risiko: **mittel**. Empfehlung: Die Option beim Zielagenten bewusst setzen (z. B. „nie“, wenn er nur Daten aufnehmen soll).

## 4. Was die KI preisgeben kann (Prompt-Injection)
| Information | Im Zugriff der KI? | Bewertung |
|---|---|---|
| Telefonbuch, Benutzer-Nummern | Nein (weder im Prompt noch als Funktion für Externe) | niedrig |
| Namen, Funktion, Status, **Notiz**, „bis“, Vertretung der Benutzer | Ja, im Prompt; die KI soll Notiz und „bis“ nennen | mittel: Notizen **neutral** halten („nicht erreichbar“, nicht „beim Arzt“) |
| Nummer des Anrufers selbst | Ja | niedrig |
| Kalendereinträge | Nur für berechtigte Rufnummern (Benutzer selbst, freigegebene). Rufnummern sind fälschbar. | niedrig bis mittel |
| Anrufe, Transkripte anderer | Nur in der Fernabfrage | siehe 2 |

Ein erfolgreicher Prompt-Injection-Versuch kann die KI zu **falschen Aussagen** oder unnötigen Notizen bringen, aber nicht über die angebotenen Funktionen hinaus.

## 5. Aufzeichnung
- Aufnahme im laufenden Gespräch starten oder beenden: nur Administratoren der Weboberfläche. Ein Anrufer kann sie nicht auslösen.
- Der Sprachbefehl „keine Aufzeichnung“ kann nur beenden, nicht starten (datenschutzfreundliche Richtung). Das lässt sich je Regel abschalten.
- Die KI beendet die Aufnahme auf ausdrücklichen Widerspruch des Anrufers (`stop_recording`).

## 6. Weboberfläche und Betrieb
| Punkt | Bewertung |
|---|---|
| Anmeldung (scrypt, Cookie `HttpOnly`/`SameSite=Strict`, Sperre bei Fehlversuchen, Origin-Prüfung gegen CSRF) | gut |
| Kein HTTPS im Lieferumfang | mittel: nur im Heimnetz/VPN, sonst Reverse-Proxy mit HTTPS |
| `/internal/*` nur von `127.0.0.1` mit Schlüssel | gut; hinter Reverse-Proxy nicht weiterreichen |
| Mail-Passwort und Google-Schlüssel im Klartext in `data/lieschen.db` | mittel: `data/` und Sicherungen schützen |
| Kosten: lange oder viele Anrufe kosten Geld | niedrig, **wenn die Kostenbremse eingestellt ist**: je Agent/Standard höchstens *n* KI-Anrufe je Rufnummer und Stunde und *n* Minuten je Rufnummer und Tag, global *n* KI-Minuten pro Tag, Höchstdauer je Agent (sonst `MAX_CALL_SECONDS`, 30 min), Budgetwarnung. Bei Auslieferung ist die Bremse **aus** (mittel). Grenzen: anonyme Anrufer zählen nur im Tageslimit insgesamt; wechselnde (gefälschte) Absendernummern umgehen die Grenzen je Nummer, nicht das Tageslimit; interne Nummern, Kontakte und Fernabfrage-Nummern (mit Codewörtern) sind ausgenommen. |
| Klingel-Rufnummer `99613` (Anmeldung der Fritzbox bei Asterisk) | gut: SIP nur von der Fritzbox-IP (`type=acl`), Endpoints nur per IP erkannt (`identify_by=ip`); kein Kennwort (bewusst, siehe [Fritzbox-Anbindung](Fritzbox-Anbindung.md)). Vorher konnte jedes LAN-Gerät 99613 übernehmen. Restrisiko: ein Gerät, das die IP der Fritzbox fälscht (im LAN aufwendig, Antworten gehen an die echte Fritzbox). |
| Eingeschränkte Konten | eigenes Gerät nur interne Nummer oder vom Admin eingetragen; Durchstellen/Umleiten nur an interne Nummern, Benutzer, Kontakte | gut |
| Eigene Plugins | erst nach Freigabe durch einen Admin importiert | gut (Plugins bleiben Vertrauenscode) |
| HTTP-Header, Basic-Anmeldung | `nosniff`, `DENY`, `no-referrer`, schmale CSP (ohne `script-src` wegen der Inline-UI); Basic-Anmeldung nur mit `ALLOW_BASIC=1` | gut |
| AMI-Recht `command` (CLI-Befehle) | mittel: noch nötig für `mixmonitor start`, PJSIP-Neuladen, `moh reload`; AMI nur auf `127.0.0.1` | mittel |
| Bridge-Container als root, Asterisk-Image `latest` | mittel: siehe [Betrieb](Betrieb-und-Ueberwachung.md) | mittel |

## 7. Gesamtbild
- **Stark:** Serverseitige Prüfung jeder Funktion; Ziele nur aus Benutzerdaten oder geprüften Nummern; keine Kontaktdaten im Prompt.
- **Schwach:** Fälschbare Rufnummern als Berechtigung (freigegebene Anrufer, Kalender, Fernabfrage-Erkennung, Ausnahmen der Kostenbremse), Agentenwechsel mit weiteren Rechten, Statuspflege der Benutzer.
- **Empfohlene Mindesteinstellung für den Anrufbeantworter:** „Verbinden nur während der Geschäftszeiten“ (und Geschäftszeiten eintragen), „freigegebene Anrufer“ leer, Notfall-Agent bewusst einstellen, Notizen der Benutzer neutral, **Kostenbremse einschalten** (z. B. 5 Anrufe je Nummer und Stunde, 30 Minuten je Nummer und Tag, ein Tageslimit passend zum Budget).
