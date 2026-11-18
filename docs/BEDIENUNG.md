# Bedienung der Weboberfläche

Die Oberfläche hat oben die Hauptseiten **Anrufe, Agenten, Regeln, Anruf starten**, im Menü (☰) die weiteren Bereiche und rechts die Symbole
für das eigene Konto und das Abmelden. Die Adresse zeigt immer die aktuelle Ansicht (F5 und „Zurück“ funktionieren).
Konten mit der Rolle **Eingeschränkt** sehen nur **Anrufe** (auf ihren Rufnummern, freigegebene und selbst gestartete) und **Meine Einstellungen**. Hat ihr Benutzer-Eintrag einen eigenen Anschluss (interne Nummer), können sie außerdem unter **Anruf starten** Gespräche mit Mitschrift von diesem Anschluss aus führen (Hinweis-Ansage immer an); alles andere ist Administratoren vorbehalten.

## Hilfe
Das Symbol **?** oben rechts öffnet die Hilfe (für alle Konten): eine Einstiegsseite mit den wichtigsten Begriffen, links ein Menü mit allen Themen und darüber eine **Volltextsuche**. Die Texte stehen in `bridge/app/static/help.js` und werden bei Änderungen an der Oberfläche mitgepflegt.

## Anruf-Banner
Bei einem klingelnden oder laufenden Anruf erscheint oben mittig ein Banner (für alle, die die angerufene Nummer haben).
Klick darauf: **Jetzt von der KI annehmen lassen**, **Durchstellen** (Benutzer oder freie Nummer, optional mit Mitschrift), **Agent wechseln**,
**Auflegen**, **Gespräch ansehen**. Klingelnde Anrufe werden per Asterisk umgeleitet; beim Durchstellen mit Mitschrift hört die angerufene Person vorher den Aufzeichnungshinweis, ohne Mitschrift gibt es keine Ansage.

## Anrufe
Liste mit Suche (auch im Transkript), Filtern (Status, Richtung, Agent, Zeitraum) und Paging. Ein Klick öffnet das **Transkript**
(Sprecher getrennt, Agentenwechsel mit Grund), Notizen der KI, Kosten, Aufnahme und – wenn eingeschaltet – die **Zusatzinfos**
(Zusammenfassung, Anliegen, Dringlichkeit, Stimmung, Betrugsverdacht; Schalter „Zusatzinfos anzeigen“, Knopf „Auswerten“).
Laufende Gespräche lassen sich dort steuern (Aufnahme starten/stoppen, Agent wechseln, an einen Menschen übergeben, auflegen).
Der Abschnitt **Ablauf** zeigt als kurze Zeitleiste, was mit dem Anruf geschah (klingelte bei …, niemand nahm ab, Agent … übernahm (Anrufregel …),
Rückfrage bei … → abgelehnt, Aufnahme gestartet/angehängt, Kostenbremse, aufgelegt), mit Uhrzeit und Dauer je Schritt; zuklappbar. Anrufe von vor
dieser Funktion haben keinen Ablauf und sehen aus wie bisher.

## Agenten und Regeln
**Agenten** sind KI-Persönlichkeiten. **Regeln** legen fest, wer wann welchen Agenten bekommt und was aufgezeichnet wird.

**Agent** (Seite *Agenten*):
- **Verhalten:** Prompt, Begrüßung, Stimme, Modell (mit Preisen), Denkstufe, Schweige-Watchdog, Fragenliste, eigene **Aktionen**
  (weiterleiten, Agent wechseln, Wert notieren, Webhook, auflegen), feste **Hinweis-Ansage**, Modus *Assistent / Monitor / Durchstellen*.
- **Wechsel im Gespräch:** über **Codewörter** (erst bei *n* verschiedenen) und/oder **Stimmung** (aufgeregt, ärgerlich, ängstlich, panisch, unsicher,
  je Stimmung ab Stärke 1–5 oder aus; nach Tonfall und/oder Wortlaut), verknüpfbar mit UND/ODER; optional nur bei bestimmten Nummern/Zeiten.
- **Stimmung einschätzen** (Tonfall, Wortlaut) und **Zusatzinfos auswerten** (nur Transkript) sind optional.
- Auf der Karte: Schalter zum Aktivieren/Deaktivieren, Zahl der Regeln, die den Agenten nutzen.

- **Vor dem Verbinden nachfragen:** Die KI ruft den Benutzer an und fragt, ob er den Anrufer sprechen möchte; der Anrufer hört Musik (siehe Wiki „Rückfrage vor dem Verbinden“, Einstellungen unter *Einstellungen → Telefonie → Rückfrage vor dem Verbinden* bzw. *→ Wartemusik*).

**Anrufregeln** (Seite *Regeln*): Die **erste passende Regel von oben** bestimmt Agent und **Klingelzeit** (alle Telefone klingeln über die
Klingel-Rufnummer, dann übernimmt die KI; 0 = sofort wie ein Anrufbeantworter). Bedingungen: angerufene Nummer/Leitung, Anrufer (Kontakte, Benutzer
oder freie Nummern, mit Vorschlägen), Zeitfenster (Geschäftszeit/außerhalb). Reihenfolge per ▲▼. Ist die Rufnummer einem Benutzer zugewiesen,
gilt zuerst dessen Weiterleitung (Seite *Benutzer*); die Klingelzeit der Regel gilt, bevor die KI übernimmt. Hat die erste passende Anrufregel einen Assistenten mit „Vorher klingeln“ 0, nimmt die KI sofort an: Dann klingelt auch bei zugewiesenen Rufnummern nur, wem eine Wählregel das ausdrücklich erlaubt, und die Vertretung wird nicht angerufen.

**Aufzeichnungsregeln** (Seite *Regeln*): **Alle passenden Regeln gelten gemeinsam.** Eine Regel mit Agent gilt nur, wenn dieser Agent das
Gespräch führt; ohne Agent gilt sie immer (z. B. für bestimmte Anrufer). Je Regel: **Audio** (Gespräche, die ein Mensch annimmt), **Transkript**,
**KI-Audio** (KI-Gespräche, mit Ansage) und **Sprachbefehl** („Lieschen, keine Aufzeichnung“ beendet die Aufnahme). KI-Gespräche werden immer
transkribiert. Ist nur das Transkript an, wird das Audio danach gelöscht.
**Ausgehende Gespräche** (mit der Vorwahl der Fritzbox-Wahlregel, Standard `*77`, z. B. auch `000`) haben eigene Regeln („Gilt für: Ausgehende Gespräche“): Zielnummer/Kontakt und Zeitfenster als Bedingung, Audio, Transkript, Hinweis-Ansage an den Angerufenen und Sprachbefehl. Passt keine aktive Regel, wird normal ohne Aufzeichnung gewählt. Notrufnummern werden nie aufgezeichnet.
Ausgehende **KI-Anrufe** (Assistent ruft an, Kampagnen) folgen keinen Aufzeichnungsregeln: Sie werden immer transkribiert, Audio wird nicht aufgenommen.

## Anruf starten
- **Assistent ruft an:** Kontakt/Nummer, Auftrag (Platzhalter `{name}`, `{datum}`), Agent, „im Namen von“; mit **Vorlagen**.
- **Ich telefoniere – mit Mitschrift:** zuerst klingelt der eigene Anschluss, danach wird das Ziel gewählt; beide Seiten werden getrennt aufgenommen
  und (fast live) transkribiert. Audio speichern, Transkript und Hinweis-Ansage an den Angerufenen sind einzeln wählbar (Standard: alles an; ohne „Audio speichern“ wird das Audio nach der Transkription gelöscht). Eingeschränkte Konten: Hinweis-Ansage immer an.

## Benutzer
Status (im Büro / außer Haus / nicht erreichbar), Weiterleitungsnummer, **Nummer außer Haus** (Handy), Vertretung, „abwesend bis“, Notiz,
**Zeitplan** (Tage, Uhrzeit, Status, Freitext), **Wählregeln** (je Anrufer/Tag/Uhrzeit → Status oder Aktion, einzeln ein-/ausschaltbar, Reihenfolge),
„Wenn niemand abnimmt, übernimmt die KI“, Mitschrift-Option, **Fernabfrage** und das **Benutzerkonto** des Benutzers.
Rangfolge: „nicht stören“ (nur Ausnahmen) → Wählregeln → Zeitplan (wenn angewendet) → manueller Status/„bis“ → Vertretung → KI.

## Google Kalender des Benutzers
Beim Benutzer lässt sich ein Google Kalender anbinden (Einrichtung: `docs/INSTALLATION.md`).
- **Wo ist der Benutzer?** Fragt ein **berechtigter** Anrufer danach, sagt die KI, ob der Benutzer laut Kalender gerade in einem Termin ist, bis wann und was als Nächstes ansteht –
  je nach Einstellung mit Titel und Ort oder nur „ist in einem Termin“. Berechtigt sind die eigenen Nummern, auf Wunsch andere Benutzer (anhand ihrer Nummern) und eine Liste weiterer Rufnummern.
  Alle anderen bekommen keine Auskunft. Achtung: Rufnummern von außen lassen sich fälschen.
- **Per Fernabfrage** kann der Benutzer seine Termine abfragen („Was habe ich heute?“), freie Zeiten erfragen (Mo–Fr 8–18 Uhr) und Termine eintragen. Die KI wiederholt vor dem Eintragen Titel, Tag und Uhrzeit
  und prüft auf Überschneidungen.

## Fernabfrage (per Telefon)
Aktivieren beim Benutzer: Fernabfrage erlauben, weitere Rufnummern (die eigene Nummer und die Nummer außer Haus gelten automatisch) und **Codewörter**.
Ein Anruf von einer dieser Nummern geht direkt an die KI (persönliche Anrede mit Vornamen, ohne Hinweis-Ansage). Nennt der Anrufer **alle Codewörter**,
startet die Abfrage: neue Anrufe auflisten, Zusammenfassungen oder Transkripte vorlesen, Aufnahmen abspielen, **Rückruf** zu einem Anrufer
(optional mit Mitschrift) und **Verbinden** mit Kontakten/Benutzern. Die Codewörter werden im Transkript geschwärzt.
Vom internen Anschluss (z. B. `**610`) darf man sich außerdem direkt verbinden lassen.

## Kontakte
Telefonbuch; Fritzbox-Einträge sind schreibgeschützt und werden per **„Mit Fritzbox abgleichen“** aktualisiert. Die KI prüft damit Angaben des Anrufers.

## Rufnummern
Die Fritzbox-Nummern werden Benutzern zugewiesen: **einem** (exklusiv) oder **mehreren** (geteilt – dann klingeln alle Erreichbaren gleichzeitig).
Wer eine Nummer hat, sieht die Anrufe darauf. Nur Administratoren weisen zu.

## Benachrichtigungen
E-Mail-Konto (SMTP, einschließlich Passwort; „Testmail senden“) und **Empfänger**: je Empfänger ausgewählte Rufnummern (leer = alle), Benachrichtigung bei jedem/nur wichtigen Anrufen
(Nachricht hinterlassen, hohe Dringlichkeit, starke Aufregung/Angst), Kurzfassung oder mit Transkript, **Tagesbericht** zur eingestellten Uhrzeit.
„Bericht jetzt“ und „Testmail“ zum Ausprobieren.

## Kosten
Tokens und Kosten je Anruf, Monatsübersicht, je Agent, Budget und Rest. Umrechnung in Euro mit dem täglichen EZB-Kurs. Ansagen/Stimmproben werden mitgezählt.
Die tatsächliche Abrechnung steht bei Google; Abweichungen sind möglich (z. B. Sitzungen ohne Nutzungsangabe).

## Einstellungen
Gemini-Schlüssel, Fritzbox (Adresse, TR-064, Anmeldung von Asterisk), Aufbewahrung der Anrufe, Grundkonfiguration (Prompt-Vorsatz für alle Agenten), Geschäftszeiten/Zeitplan (das Budget steht auf der Seite *Kosten*).

**Kostenbremse** (Einstellungen, je Agent überschreibbar unter Agent → Kostenbremse): höchstens *n* KI-Anrufe je Rufnummer und Stunde, höchstens *n* KI-Minuten je Rufnummer und Tag, höchstens *n* KI-Minuten insgesamt pro Tag; je Agent zusätzlich die Höchstdauer eines Gesprächs (sonst `MAX_CALL_SECONDS`). 0 = aus bzw. Standard; bei Auslieferung ist alles aus. Ist eine Grenze erreicht, nimmt die KI den Anruf nicht an – Asterisk behandelt ihn wie „kein Agent“ (nicht annehmen, die Fritzbox lässt andere Telefone weiter klingeln; klingelte Asterisk schon selbst, legt es nach der Klingelzeit auf). In der Anrufliste steht „Kostenbremse (Agent)“ mit Status *Abgelehnt* und dem Grund im Verlauf; Mails gehen wie bei anderen abgelehnten Anrufen nach den Einstellungen der Empfänger. Ausgenommen sind interne Nummern, Kontakte und Anrufe von den Fernabfrage-Nummern eines Benutzers (nur bei eingeschalteter Fernabfrage mit Codewörtern); externe Büro- und Handynummern der Benutzer werden gebremst; Anrufe ohne Nummer zählen nur für das Tageslimit insgesamt. Gezählt werden Anrufe, in denen die KI gesprochen hat, aus der Anrufliste (auch laufende). Manuelles „Jetzt von der KI annehmen lassen“ im Banner wird nicht gebremst.

## Plugins
Liste der Erweiterungen mit Ein-/Ausschalter. **Kampagnen** ist mitgeliefert: Kampagne mit Zielen (Eingabe oder aus dem Telefonbuch), Anrufzeiten,
Versuchen und Abstand, Anrufbeantworter-Verhalten (später erneut / Nachricht sprechen / auflegen) und **Terminpools** (Termine erzeugen, die
die KI anbietet und exklusiv bucht). Zu den Terminen gehören die **Pause zwischen Terminen** (Mindestabstand, auch zu anderen Kalendereinträgen) und ein optionaler **Google Kalender**:
ein *Kalender-Pool* bildet seine freien Termine aus Platzhalter-Terminen mit einem Präfix im Titel und trägt gebuchte Termine als Kalendereinträge ein
(Einrichtung: `docs/INSTALLATION.md`). Mehr zur Plugin-Schnittstelle in [PLUGINS.md](PLUGINS.md).
