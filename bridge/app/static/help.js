// Inhalt der Hilfe-Seite (Menue -> Hilfe). Jedes Thema: id, Gruppe, Titel, Stichwoerter fuer die Suche, HTML-Text.
// Beim Aendern der Oberflaeche bitte die betroffenen Themen mitpflegen. Links zwischen Themen: <a href="#/help/<id>">.

export const GROUPS = ["Einstieg", "KI und Regeln", "Menschen und Nummern", "Anrufe", "Aufnahme und Mail", "Einstellungen", "Hilfe bei Problemen"];

export const START = `
<h2 style="margin:0 0 6px">Willkommen bei Lieschen Müller</h2>
<p style="margin:0 0 16px;font-size:15px">Lieschen Müller nimmt Anrufe über die Fritzbox entgegen. Eine <b>KI</b> spricht mit dem Anrufer, fragt nach Name und Anliegen,
verbindet auf Wunsch mit einer Person im Büro oder nimmt eine Nachricht auf. Danach gibt es ein <b>Transkript</b>, auf Wunsch die <b>Aufnahme</b> und eine <b>Mail</b>.</p>

<div class="hgrid">
 <a class="hcard" href="#/help/agenten"><div class="hk">Agent</div><b>Die KI-Persönlichkeit</b>
  <p>Ein Agent legt fest, <i>wie</i> die KI arbeitet: Auftrag (Prompt), Stimme, Begrüßung, Hinweis-Ansage und was sie tun darf.
  Beispiele: „Anrufbeantworter“, „Notfall“.</p></a>
 <a class="hcard" href="#/help/anrufregeln"><div class="hk">Anrufregel</div><b>Wer geht wann ran?</b>
  <p>Die <b>erste passende Regel von oben</b> bestimmt, welcher Agent den Anruf annimmt und wie lange vorher alle Telefone klingeln.</p></a>
 <a class="hcard" href="#/help/aufzeichnungsregeln"><div class="hk">Aufzeichnungsregel</div><b>Was wird aufgezeichnet?</b>
  <p>Alle passenden Regeln gelten <b>gemeinsam</b>: Audio, Transkript, Hinweis-Ansage, Sprachbefehl, Mail.</p></a>
 <a class="hcard" href="#/help/rueckfrage"><div class="hk">Rückfrage</div><b>Vor dem Verbinden nachfragen</b>
  <p>Möchte ein Anrufer jemanden sprechen, ruft die KI die Person an, nennt Anrufer und Anliegen und fragt, ob sie ihn annimmt. Der Anrufer hört solange Musik. Nur bei „Ja“ wird verbunden.</p></a>
 <a class="hcard" href="#/help/benutzer"><div class="hk">Benutzer</div><b>Personen im Büro</b>
  <p>Jede Person hat Status, Telefone („Klingelt an“), Vertretung und Zeitplan. Die KI verbindet nur mit Erreichbaren.</p></a>
</div>

<h3>So läuft ein Anruf</h3>
<ol class="hflow">
 <li><b>Der Anruf kommt</b> über die Fritzbox bei Asterisk an.</li>
 <li><b>Die Telefone klingeln</b>: die Benutzer, denen die Rufnummer zugewiesen ist (nach Status und Zeitplan), und zwar so lange, wie die Anrufregel sagt.</li>
 <li><b>Nimmt niemand ab</b>, übernimmt der Agent, den die erste passende Anrufregel bestimmt.</li>
 <li><b>Die KI</b> begrüßt, fragt nach Name und Anliegen und verbindet (auf Wunsch nach einer <a href="#/help/rueckfrage">Rückfrage</a>), nimmt eine Nachricht auf oder legt nach dem Gespräch auf.</li>
 <li><b>Danach</b>: Transkript und Zusammenfassung in der Anrufliste, Aufnahme nach den Aufzeichnungsregeln, Mail an die Empfänger.</li>
</ol>

<h3>Erste Schritte</h3>
<p>Am einfachsten mit dem <a href="#/help/einrichtung">Einrichtungsassistenten</a> (Einstellungen → Allgemein → Einrichtungsassistent): er führt durch die ersten drei Punkte und die Rufnummern.</p>
<ol>
 <li><b>Google-Zugang</b> einrichten: Gemini-Schlüssel (<a href="#/help/einstellungen">Einstellungen → Anbindungen</a>) oder <a href="#/help/vertex">Vertex AI (EU)</a>.</li>
 <li><b>Fritzbox verbinden</b>: IP-Telefon und Klingel-Rufnummer einrichten, Zugangsdaten eintragen (<a href="#/help/fritzbox">Fritzbox einrichten</a>).</li>
 <li><b>Grundkonfiguration</b> schreiben: wer ihr seid und in welchem Rahmen die KI arbeitet.</li>
 <li><b>Agenten prüfen</b> und mit der Stimmprobe eine Stimme wählen.</li>
 <li><b>Anrufregel</b> anlegen, sonst nimmt niemand an.</li>
 <li><b>Benutzer</b> anlegen und unter <a href="#/help/rufnummern">Rufnummern</a> zuweisen.</li>
 <li><b>Mail</b> einrichten (<a href="#/help/mail">Benachrichtigungen</a>) und einen <b>Testanruf</b> machen.</li>
</ol>
<div class="hint">Tipp: Oben in der Suche findest du jedes Stichwort in allen Themen, zum Beispiel „Musik“, „Kennwort“, „Aufnahme“ oder „Klingel-Rufnummer“.</div>
`;

export const TOPICS = [
{
 id: "einrichtung", group: "Einstieg", title: "Einrichtungsassistent",
 keys: "einrichtung assistent installation erste schritte gemini schlüssel api key fritzbox tr-064 ip-telefon anmeldung rufnummern testanruf install.sh später",
 html: `
<p>Der <b>Einrichtungsassistent</b> führt Administratoren in fünf Schritten zum ersten Anruf mit der KI. Er öffnet sich nach der Installation von selbst, solange etwas fehlt
(Gemini-Zugang, Fritzbox-Zugangsdaten oder die Anmeldung von Asterisk an der Fritzbox), und lässt sich mit <b>„Später“</b> ausblenden.
Jederzeit wieder erreichbar: <b>Einstellungen → Allgemein → Einrichtungsassistent</b>. Bereits Eingetragenes bleibt erhalten; jeder Schritt lässt sich überspringen und später nachholen.</p>
<ol>
 <li><b>Google Gemini</b>: API-Schlüssel aus Google AI Studio einfügen, „Speichern und testen“. Wer die Verarbeitung in der EU halten will, nutzt stattdessen <a href="#/help/vertex">Vertex AI</a>.</li>
 <li><b>Fritzbox</b>: Adresse (meist <code>192.168.178.1</code>), ein eigener Fritzbox-Benutzer mit dem Recht „FRITZ!Box Einstellungen“ und „Zugriff für Anwendungen zulassen“. „Speichern und testen“ liest zur Probe Telefone und Rufnummern.</li>
 <li><b>IP-Telefon</b>: In der Fritzbox ein IP-Telefon „LieschenMueller“ anlegen; Kennwort nur aus Buchstaben, Ziffern und <code>- . _</code> (mindestens 8 Zeichen). Nach dem Speichern zeigt der Assistent laufend, ob Asterisk angemeldet ist.</li>
 <li><b>Rufnummern</b>: die eigenen Nummern anhaken, auf denen die KI antworten soll (dasselbe wie „Asterisk antwortet auf“ unter <a href="#/help/rufnummern">Rufnummern</a>).</li>
 <li><b>Abschluss</b>: Die vorhandene Anrufregel „Assistent“ nimmt jeden Anruf sofort an. Ein Testanruf von einem Handy zeigt, ob alles klappt.</li>
</ol>
<div class="hint">Vorher auf dem Rechner: <code>./install.sh</code> im Ordner von Lieschen Müller. Es legt die <code>.env</code> mit einem zufälligen Admin-Passwort an, startet die Container und zeigt die Adresse der Weboberfläche. Aktualisieren: <code>./install.sh --update</code>.</div>`
},
{
 id: "ablauf", group: "Einstieg", title: "Wie ein Anruf abläuft",
 keys: "anruf ablauf klingeln annehmen weiterleiten reihenfolge status zeitplan vertretung",
 html: `
<p>Ein eingehender Anruf durchläuft diese Stationen. Die Reihenfolge hilft bei der Fehlersuche („Warum klingelt niemand?“).</p>
<ol>
 <li><b>Ankunft</b>: Die Fritzbox leitet den Anruf an das IP-Telefon von Asterisk. Über die Klingel-Rufnummer für „alle Telefone klingeln“ (Einstellungen → Klingeln der Telefone, z. B. <code>99613</code> oder die des Empfangs) klingeln alle Telefone und zeigen die echte Nummer des Anrufers.</li>
 <li><b>Wer klingelt?</b> Gehört die angerufene Rufnummer Benutzern (Seite <a href="#/help/rufnummern">Rufnummern</a>), klingeln die Telefone aus ihrem Feld „Klingelt an“. Maßgeblich ist die Rangfolge:
  <b>„nicht stören“</b> → <b>Wählregeln</b> des Benutzers → <b>Zeitplan</b> → <b>manueller Status</b> → <b>Vertretung</b> → <b>KI</b> (Einzelheiten unter <a href="#/help/benutzer">Benutzer → Rangfolge</a>).</li>
 <li><b>Klingelzeit</b>: Die Anrufregel legt fest, wie viele Sekunden geklingelt wird (0 = die KI nimmt sofort an wie ein Anrufbeantworter).</li>
 <li><b>KI übernimmt</b>: Der Agent der ersten passenden <a href="#/help/anrufregeln">Anrufregel</a> nimmt an. Vorher läuft die feste Hinweis-Ansage (zum Beispiel „Sie sprechen mit einer KI“).</li>
 <li><b>Im Gespräch</b>: Die KI kann nachfragen, notieren, <a href="#/help/rueckfrage">verbinden</a>, eine Nachricht aufnehmen oder auflegen.</li>
 <li><b>Danach</b>: Transkript, Zusammenfassung, Kosten und Aufnahme stehen in der <a href="#/help/anrufe">Anrufliste</a>; Mails gehen an die <a href="#/help/mail">Empfänger</a>.</li>
</ol>
<div class="hint">Eine Anrufregel ist Pflicht für die KI: Ohne passende Regel nimmt niemand den Anruf an.</div>`
},
{
 id: "agenten", group: "KI und Regeln", title: "Agenten",
 keys: "agent prompt stimme modell begrüßung hinweis ansage aktionen codewort stimmung modus monitor durchstellen sprechtempo sprechstil tabs",
 html: `
<p>Ein <b>Agent</b> ist eine KI-Persönlichkeit. Er legt fest, <i>wie</i> die KI klingt und arbeitet. Welcher Agent einen Anruf bekommt, bestimmen die <a href="#/help/anrufregeln">Anrufregeln</a>.</p>
<h3>Die Tabs im Agent-Editor</h3>
<ul>
 <li><b>Allgemein</b>: Name, Reihenfolge, <b>Modus</b>: <i>Assistent</i> (spricht mit dem Anrufer), <i>Monitor</i> (hört nur mit, Transkript) oder <i>Durchstellen</i> (ohne KI zu einer Nummer).</li>
 <li><b>Wechsel</b>: Wann mitten im Gespräch zu diesem Agenten gewechselt wird, per <b>Codewort</b> oder <b>Stimmung</b> (aufgeregt, ärgerlich, ängstlich, panisch, unsicher; Stärke 1–5; nach Tonfall oder Wortlaut). Dazu optional nur bei bestimmten Nummern oder Zeiten.</li>
 <li><b>Verhalten</b>: <b>System-Prompt</b> (Auftrag), feste <b>Hinweis-Ansage</b>, Begrüßung, Fragenliste, Stille-Watchdog („Sind Sie noch da?“, Auflegen).</li>
 <li><b>Platzhalter</b> in Begrüßung und System-Prompt: <code>{name}</code>, <code>{vorname}</code>, <code>{nachname}</code> (aus der Kontaktliste; „Lorenz, Anja“ ergibt Vorname Anja, Nachname Lorenz), <code>{anrede}</code> („Frau Lorenz“ mit der beim Kontakt hinterlegten Anrede, sonst „Anja Lorenz“), <code>{anzahl}</code> und <code>{minuten}</code> (frühere Anrufe dieser Nummer, Zeitraum aus der Anrufregel „Mehrfach angerufen“, sonst 60 Minuten) und <code>{verlauf}</code> (Uhrzeit und Ausgang dieser Anrufe). Ist die Nummer unbekannt, fallen Name und ein „Herr“/„Frau“ davor weg und die KI begrüßt ohne Namen. Aus dem Namen rät die App das Geschlecht nie: „Herr“/„Frau“ vor einem Platzhalter wird durch die <b>Anrede des Kontakts</b> ersetzt (Seite Kontakte, Spalte Anrede, auch bei Kontakten aus der Fritzbox) und fehlt, wenn keine hinterlegt ist. Die Hinweis-Ansage kennt keine Platzhalter (sie wird vorab gesprochen).</li>
 <li><b>Stimme und Modell</b>: KI-Modell mit Preisen, Stimme mit <b>Stimmprobe</b>, <b>Sprechtempo</b> (langsam, normal, zügig) und optional ein <b>Sprechstil</b> („warm, mit kurzen Pausen“).</li>
 <li><b>Vermittlung und Aktionen</b>: Wer vom Anrufer genannt verbunden werden darf, eigene Aktionen (weiterleiten, Agent wechseln, Wert notieren, Webhook, auflegen).</li>
 <li><b>Grenzen und Optionen</b>: <a href="#/help/kosten">Kostenbremse</a>, „Grundkonfiguration voranstellen“, „Vor dem Verbinden nachfragen“, „Anrufer mit Benutzern verbinden“.</li>
</ul>
<h3>Gut zu wissen</h3>
<ul>
 <li>Die <b>Grundkonfiguration</b> (Einstellungen → Allgemein) wird jedem Agenten vorangestellt: Hier steht, wer ihr seid.</li>
 <li>Die KI nennt keine Namen, die nicht im Prompt oder in der Grundkonfiguration stehen. Schreibe also Firmen- und Personennamen hinein.</li>
 <li>Die KI gibt sich auf Nachfrage immer als KI zu erkennen.</li>
 <li>Ein Agent kann mit dem Schalter auf seiner Karte ein- und ausgeschaltet werden.</li>
</ul>
<div class="hint">Sprechtempo und Stil sind Wünsche an das Modell, keine exakten Werte. Der Unterschied zwischen „langsam“ und „zügig“ ist hörbar, aber nicht auf die Sekunde genau.</div>`
},
{
 id: "anrufregeln", group: "KI und Regeln", title: "Anrufregeln: wer nimmt an?",
 keys: "anrufregel regel reihenfolge klingeln klingelzeit agent catch-all geschäftszeiten zeitfenster angerufene nummer anrufer mehrfach wiederholt immer wieder angerufen platzhalter name",
 html: `
<p>Seite <b>Regeln → Anrufregeln</b>. Eine Anrufregel sagt: <i>Wenn der Anruf so aussieht, nimmt dieser Agent an, nachdem die Telefone eine Weile geklingelt haben.</i></p>
<ul>
 <li>Die <b>erste passende Regel von oben</b> gewinnt. Die Reihenfolge ändern ▲ und ▼.</li>
 <li><b>Bedingungen</b> (leer = alle): angerufene Nummer/Leitung, Anrufer (Kontakte, Benutzer oder freie Nummern) und Zeitfenster (<i>nur während</i> oder <i>nur außerhalb</i> der Geschäftszeiten).</li>
 <li><b>Agent übernimmt</b>: Welcher Agent antwortet.</li>
 <li><b>Vorher klingeln</b>: Sekunden, die alle Telefone klingeln, bevor die KI übernimmt. <b>0 = sofort</b> (Anrufbeantworter ohne Klingeln).</li>
 <li><b>Mehrfach angerufen</b> (optional): Die Regel gilt nur, wenn dieselbe Nummer in den letzten <i>X</i> Minuten schon mindestens <i>N</i>-mal angerufen hat. Wählbar ist, welche Anrufe zählen: <i>alle</i>, <i>nur aufgelegte</i> (niemanden erreicht, weder eine Person noch die KI) oder <i>alle ohne hinterlassene Nachricht</i>. Der aktuelle Anruf zählt nicht mit, unterdrückte Nummern nie. <code>{anzahl}</code> ist die Zahl der gezählten Anrufe. Der Agent erfährt Uhrzeit und Ausgang jedes dieser Anrufe: aufgelegt, mit jemandem gesprochen, mit der KI gesprochen oder Nachricht hinterlassen.</li>
 <li>Eine „Immer“-Regel ohne Bedingung gehört <b>ans Ende</b>. Alles darunter würde nie erreicht.</li>
</ul>
<h3>Beispiele</h3>
<ul>
 <li><b>Anrufbeantworter:</b> Regel „Immer“ → Agent „Anrufbeantworter“, 30 Sekunden klingeln.</li>
 <li><b>Nach Feierabend nur Nachricht:</b> Zeitfenster „außerhalb der Geschäftszeiten“, Klingeln 0.</li>
 <li><b>Ruft immer wieder an:</b> ganz oben eine Regel „Mehrfach angerufen: 3 frühere Anrufe in 10 Minuten“, Klingeln 0, Agent „Dringend“ mit der Begrüßung „Sage: Hallo {anrede}, Sie haben in den letzten {minuten} Minuten schon {anzahl}-mal angerufen. Frage, ob es dringend ist.“</li>
 <li><b>Notfall:</b> kein Regelschritt nötig: Der Agent „Notfall“ übernimmt per Codewort („Notfall“) oder Stimmung mitten im Gespräch.</li>
</ul>
<div class="hint">Die Geschäftszeiten stellst du unter Einstellungen → Allgemein ein.</div>`
},
{
 id: "aufzeichnungsregeln", group: "KI und Regeln", title: "Aufzeichnungsregeln",
 keys: "aufzeichnung regel audio transkript sprachbefehl ansage ausgehend vorwahl 000 mail ki-audio rückfrage mit aufnehmen",
 html: `
<p>Seite <b>Regeln → Aufzeichnungsregeln</b>. <b>Alle passenden Regeln gelten gemeinsam.</b> Audio, Transkript und KI-Audio werden zusammengeführt.</p>
<ul>
 <li>Eine Regel <b>ohne Agent</b> gilt immer (zum Beispiel für einen bestimmten Anrufer), eine Regel <b>mit Agent</b> nur, wenn dieser Agent das Gespräch führt.</li>
 <li><b>Audio</b>: Gespräche, die ein Mensch annimmt. <b>Transkript</b>: Text des Gesprächs. <b>KI-Gespräche als Audio</b>: Gespräche mit der KI (mit Ansage zu Beginn). KI-Gespräche haben <b>immer</b> ein Transkript.</li>
 <li><b>Sprachbefehl</b> („Lieschen, keine Aufzeichnung“) beendet die Aufnahme. Er ist aus, sobald eine passende Regel ihn abschaltet.</li>
 <li><b>Ansage „wird aufgezeichnet“</b>: Ohne Ansage ist die Aufzeichnung nur zulässig, wenn alle Beteiligten anderweitig informiert sind. Das liegt in der Verantwortung des Betreibers.</li>
 <li><b>Rückfrage mit aufnehmen</b> (optional): nimmt auch die Rückfrage der KI beim Benutzer auf, mit Hinweis an diesen.</li>
 <li><b>Ausgehende Gespräche</b> (mit der Vorwahl der Fritzbox-Wahlregel, zum Beispiel <code>000</code>, die über eine Klingel-Rufnummer 99… wählt, z. B. die des Empfangs) haben eigene Regeln inklusive Mail-Empfängern. Notrufnummern werden nie aufgezeichnet. Ohne Vorwahl wird nichts mitgeschrieben, auch wenn ein Gespräch über Asterisk läuft (z. B. Rückruf aus der Anrufliste eines Telefons über die Klingel-Rufnummer 99…). Das lässt sich unter Einstellungen → Aufzeichnung mit „Auch ausgehende Gespräche ohne Vorwahl mitschreiben“ ändern.</li>
</ul>
<p>Mehr zu Dateien und Wiedergabe: <a href="#/help/aufnahme">Aufnahme</a>.</p>`
},
{
 id: "rueckfrage", group: "KI und Regeln", title: "Rückfrage vor dem Verbinden",
 keys: "rückfrage nachfragen verbinden ja nein weitergeben zentrale sammelnummer kollege anliegen wartemusik kosename anrede name ki stimme",
 html: `
<p>Möchte ein Anrufer mit einer Person sprechen, kann die KI <b>vorher nachfragen</b>: Sie ruft die Person an, nennt Anrufer und Anliegen und fragt, ob sie den Anruf annimmt. Der Anrufer hört währenddessen Wartemusik.</p>
<ol>
 <li>Einschalten: Agent → <b>Grenzen und Optionen</b> → „Vor dem Verbinden nachfragen“.</li>
 <li>Die KI fragt den Anrufer zuerst nach <b>Name und Anliegen</b>, falls sie das nicht schon weiß.</li>
 <li>Die zweite KI-Sitzung ruft die Person an. Meldet sich eine Mailbox, legt sie auf und der Anrufer bleibt bei der KI.</li>
 <li>Sagt die Person „Ja“, wird verbunden. Bei „Nein“ oder keiner Antwort nimmt die KI den Anrufer wieder entgegen (Nachricht aufnehmen).</li>
 <li>Sagt die Person „Gib ihn an die Zentrale“ oder „Stell zu Clemens durch“, verbindet die KI den Anrufer mit diesem Kollegen (der Name muss unter <b>Mitarbeiter</b> eindeutig sein; mit Rückfrage bei ihm, wenn eingeschaltet). Ist er nicht erreichbar, bietet sie eine Nachricht an.</li>
</ol>
<p><b>Tipp Sammelziel:</b> Lege unter Benutzer mit <b>„+ Sammelziel“</b> einen Eintrag „Zentrale“ mit den Nummern an, die bei allgemeinen Anfragen klingeln sollen (auch dieselben wie bei einem Kollegen), und trage ihn als Vertretung ein. Ein Sammelziel ist keine Person: kein Status, keine Anwesenheit, kein Konto; die Rückfrage nennt es nicht als Kollegen, die Weitergabe dorthin klappt aber.</p>
<h3>Einstellungen</h3>
<ul>
 <li><b>Einstellungen → Telefonie → Rückfrage</b>: Klingelzeit, Höchstdauer, Ansage an den Anrufer, Wartemusik.</li>
 <li><b>Einstellungen → KI → Stimme der KI</b>: <b>Name</b>, <b>Stimme</b>, <b>Sprechtempo</b> und <b>Stil</b> der Rückfrage-KI und eigene Anweisungen für Ton und Wortwahl.</li>
 <li><b>Benutzer → Allgemein → Anreden</b>: eine Liste (zum Beispiel „Petra, Biene, Schatzi“); bei jeder Rückfrage wird eine ausgewählt, nie zweimal dieselbe hintereinander. Mit <b>▶ Test</b> kannst du es anhören.</li>
</ul>
<div class="hint">Zugestimmt wird nur, wenn die Person nach der Frage ein „Ja“ sagt und kein „Nein“ davor steht. Die KI kann also nicht aus Versehen verbinden.</div>`
},
{
 id: "benutzer", group: "Menschen und Nummern", title: "Benutzer",
 keys: "benutzer mitarbeiter sammelziel zentrale empfang status büro außer haus nicht erreichbar vertretung weiterleitung mehrere nummern zeitplan wählregeln konto rolle eingeschränkt administrator passwort anrede",
 html: `
<p>Seite <b>Benutzer</b> (Menü ☰). Ein <b>Benutzer</b> ist eine Person im Büro; ein <b>Konto</b> ist ihr Zugang zur Oberfläche (optional).</p>
<h3>Tabs beim Bearbeiten</h3>
<ul>
 <li><b>Allgemein</b>: Name, Funktion, <b>Anreden</b> für die Rückfrage, zugewiesene Rufnummern, „Nimmt niemand ab, übernimmt die KI“, Mitschrift-Option.</li>
 <li><b>Erreichbarkeit</b>: Nummer im Büro, <b>Nummer außer Haus</b> (zum Beispiel Handy), Status, Vertretung, Notiz, „abwesend bis“, <b>Zeitplan</b>, <b>Wählregeln</b>.</li>
 <li><b>Kalender</b> und <b>Fernabfrage</b>: siehe <a href="#/help/kalender">Kalender</a> und <a href="#/help/fernabfrage">Fernabfrage</a>.</li>
 <li><b>Anmeldung</b>: Konto, Rolle und Passwort. Beim Anmelden: ohne <b>„Angemeldet bleiben“</b> endet die Anmeldung mit dem Schließen des Browsers oder nach 12 Stunden ohne Aktivität; mit Häkchen nach 30 Tagen ohne Aktivität, spätestens nach 90 Tagen. Auf fremden Geräten das Häkchen nicht setzen. Ein neues Passwort beendet die Anmeldung auf allen anderen Geräten.</li>
</ul>
<h3>Sammelziele (z. B. Zentrale)</h3>
<p>Mit <b>„+ Sammelziel“</b> legst du ein Ziel ohne Person an, zum Beispiel die Zentrale oder den Empfang: nur Name und Nummern (klingeln gleichzeitig), optional Vertretung, Zeitplan und Wählregeln. Ein Sammelziel hat keinen Status (es ist immer unter seinen Nummern erreichbar, außer der Zeitplan sagt etwas anderes), keine WLAN-Anwesenheit und kein Anmeldekonto. Es kann Vertretung sein und von der KI oder bei der Rückfrage als Ziel gewählt werden. Ein vorhandener Eintrag „Zentrale“ bleibt, wie er ist, und funktioniert weiter; über <b>Bearbeiten → Art: Sammelziel</b> stellst du ihn um (vorher ein ggf. verknüpftes Konto lösen).</p>
<h3>Mehrere Nummern</h3>
<p>In den Feldern „Klingelt im Büro an“ und „Klingelt außer Haus an“ (beim Sammelziel „Klingelt an“) kannst du <b>mehrere Nummern</b> eintragen (zum Beispiel <code>**610, **620</code>). Sie klingeln gleichzeitig; wer zuerst abnimmt, bekommt das Gespräch. Höchstens 5 Nummern.</p>
<h3>Rangfolge</h3>
<p>Ob ein Anrufer jemanden gerade erreicht, wird an <b>einer</b> Stelle entschieden – gleich beim Klingeln, wenn die KI verbindet, in der Mitarbeiterliste der KI und in der Vorschau beim Bearbeiten:</p>
<ol>
 <li><b>„nicht stören“</b>: Nur Anrufer aus den Ausnahmen kommen weiter. Für alle anderen hilft auch keine Wählregel „erreichbar“.</li>
 <li><b>Wählregeln</b>: Die erste passende eingeschaltete Regel entscheidet (erreichbar, abwesend, nicht erreichbar, weiterleiten, Anrufbeantworter).</li>
 <li><b>Zeitplan</b> (wenn angewendet): gilt bei „erreichbar“ <i>und</i> bei „nicht stören“. Sagt er „abwesend“, klingelt die Nummer außer Haus; „nicht erreichbar“ oder „außerhalb der Bürozeit“ heißt: niemand – auch keine Ausnahme von „nicht stören“. Soll eine Ausnahme nachts trotzdem durchkommen, lege für diesen Anrufer eine Wählregel „erreichbar“ an (Wählregeln stehen über dem Zeitplan).</li>
 <li><b>Manueller Status</b>: erreichbar → Nummer im Büro, außer Haus → Nummer außer Haus, nicht erreichbar → niemand. „abwesend bis“ setzt den Status danach von selbst auf „erreichbar“.</li>
</ol>
<p>Ist die Person danach nicht erreichbar, gilt die <b>Vertretung</b> – nach denselben Regeln für denselben Anrufer (ohne Vertretung der Vertretung). Ausnahme: die Wählregel „direkt zum Anrufbeantworter“ geht ohne Vertretung zur KI. Ist niemand erreichbar, übernimmt die KI.</p>
<p><b>Vorschau:</b> Unter Benutzer → Erreichbarkeit (und in Meine Einstellungen → Wählregeln) gibst du eine Anrufernummer oder einen Namen aus dem Telefonbuch ein und siehst: „Ein Anruf von … ginge jetzt an …, weil …“ – mit genau dieser Rangfolge und dem gespeicherten Stand.</p>
<h3>Rollen</h3>
<ul>
 <li><b>Eingeschränkt</b>: sieht eigene und freigegebene Anrufe, ändert den eigenen Status und seine Wählregeln, trägt nur interne Nummern (<code>**NNN</code>) selbst ein.</li>
 <li><b>Administrator</b>: sieht und ändert alles.</li>
</ul>`
},
{
 id: "status", group: "Menschen und Nummern", title: "Eigenen Status schnell setzen",
 keys: "status header kopfzeile büro außer haus nicht erreichbar nicht stören ruhe ausnahme durchstellen wlan handy anwesenheit gerät notiz abwesend",
 html: `
<p>Oben rechts in der Kopfzeile steht dein <b>Status</b> mit einem farbigen Punkt: grün <i>erreichbar</i>, gelb <i>außer Haus</i>, rot <i>nicht erreichbar</i>, lila <i>nicht stören</i>.</p>
<ul>
 <li>Ein Klick öffnet die Auswahl. Die Umschaltung gilt sofort für Anrufe.</li>
 <li><b>Notiz, Nummern …</b> öffnet „Meine Einstellungen“ (Notiz, „abwesend bis“, „Klingelt an“, Wählregeln).</li>
 <li>Der Knopf erscheint nur, wenn dein Konto mit einem Benutzer verknüpft ist.</li>
 <li><b>Anwesenheit über WLAN</b> (Meine Einstellungen → Mein Status): Ist eines deiner Geräte (zum Beispiel dein Handy) im Netz der Fritzbox, wird „nicht erreichbar“ automatisch zu „nicht stören“; ist es 10 Minuten nicht mehr im Netz, wird „nicht stören“ wieder „nicht erreichbar“ – aber nur, wenn die Anwesenheit es selbst gesetzt hat. Ein von Hand gesetztes „nicht stören“ bleibt stehen. „erreichbar“ und „außer Haus“ bleiben unberührt. Automatisch gesetzte Status sind in der Oberfläche mit ⚙ markiert.
  <br><b>Datenschutz:</b> Einschalten kannst nur du selbst; ein Administrator kann die Geräte vorbelegen und die Anwesenheit ausschalten. Gespeichert wird nur der Status – kein Verlauf, wann du im WLAN warst, auch nicht im Protokoll. Geprüft wird jede Minute. Der TR-064-Benutzer der Fritzbox braucht dafür das weitreichende Recht <b>„FRITZ!Box Einstellungen“</b> (siehe <a href="#/help/fritzbox">Fritzbox einrichten</a>).</li>
 <li><b>Nicht stören</b>: wie „nicht erreichbar“ (Vertretung bzw. Anrufbeantworter), aber Anrufer aus der Liste <b>„Bei ‚nicht stören‘ trotzdem durchstellen“</b> (Meine Einstellungen bzw. Benutzer → Erreichbarkeit) werden weiter verbunden, an die Nummer im Büro, sonst außer Haus. Mit <b>*</b> am Ende gilt ein Eintrag für eine ganze Nummerngruppe, z. B. <code>0221555*</code> für alle Durchwahlen einer Firma (mindestens 4 Ziffern; jede Schreibweise, auch +49…). Das gilt beim Klingeln und wenn die KI verbindet. Die KI sagt anderen Anrufern nur, dass du gerade nicht erreichbar bist. „Nicht stören“ steht über den Wählregeln: eine Regel „erreichbar“ gilt dann nur noch für die Ausnahmen. Wendest du einen <b>Zeitplan</b> an, kommen auch Ausnahmen nur zu den Zeiten durch, in denen er „erreichbar“ sagt (nachts also nicht) – soll jemand trotzdem immer durchkommen, lege für ihn eine Wählregel „erreichbar“ an (siehe <a href="#/help/benutzer">Rangfolge</a>).</li>
</ul>
<div class="hint">Eine Notiz (zum Beispiel „im Urlaub bis Montag“) darf die KI dem Anrufer nennen.</div>`
},
{
 id: "rufnummern", group: "Menschen und Nummern", title: "Rufnummern zuweisen",
 keys: "rufnummer zuweisen msn fritzbox nummer anrufe sehen exklusiv geteilt 99613 99611 klingelnummer klingel-rufnummer",
 html: `
<p>Seite <b>Rufnummern</b>. Die Fritzbox-Nummern werden hier <b>Benutzern zugewiesen</b>: einem (exklusiv) oder mehreren (geteilt, dann klingeln alle Erreichbaren gleichzeitig).</p>
<ul>
 <li>Wer eine Nummer hat, <b>sieht die Anrufe auf dieser Nummer</b> in der Anrufliste, und zwar erst ab der Zuweisung. Ältere Anrufe auf der Nummer bleiben für Administratoren sichtbar (oder lassen sich einzeln freigeben).</li>
 <li>Nur Administratoren weisen zu.</li>
 <li>Die Seite zeigt auch, auf welchen Nummern das IP-Telefon von Asterisk antwortet, und ändert sie („Übernehmen“) über die Weboberfläche der Fritzbox – das Kennwort des IP-Telefons wird dabei nicht gesendet, Sonderzeichen darin stören also nicht. Nur wenn die Weboberfläche nicht erreichbar ist, schreibt die App über TR-064 (dort sind im Kennwort nur Buchstaben, Ziffern und - . _ erlaubt).</li>
 <li>Die Klingel-Rufnummern <code>99…</code> (z. B. <code>99613</code> für **613) sind keine echten Nummern, sondern der Weg, über den Asterisk Telefone mit der Nummer des Anrufers klingeln lässt. Welche davon „alle Telefone“ klingeln lässt, steht unter Einstellungen → Klingeln der Telefone (<a href="#/help/klingelnummern">Klingel-Rufnummern 99…</a>, <a href="#/help/fritzbox">Fritzbox einrichten</a>).</li>
 <li><b>Anruferanzeige an den Telefonen</b>: Damit ein Telefon beim gezielten Anruf („Klingelt an“) die Nummer des Anrufers zeigt, bekommt es eine eigene Klingel-Rufnummer <code>99NNN</code> (bei <code>**610</code> also <code>99610</code>). Der Schalter legt sie in der Fritzbox an und weist sie dem Telefon <b>selbst als ankommende Nummer zu</b> – DECT-Telefonen, IP-Telefonen und FRITZ!Apps, über die Weboberfläche der Fritzbox (wie Telefonie → Telefoniegeräte → Bearbeiten → Ankommende Anrufe); danach liest die App nach, ob die Fritzbox es übernommen hat (sonst stellt sie den alten Stand wieder her). Ausschalten nimmt die Nummer beim Telefon wieder heraus. Geht Lesen oder Schreiben nicht, erscheint das Häkchen „zugewiesen“ zum Setzen von Hand.</li>
 <li>Klingelt ein Telefon bei <b>allen Rufnummern</b> („alle“), stellt die App es nicht von selbst um – es klingelt ohnehin auch bei seiner 99NNN, allerdings auch, wenn Asterisk ein anderes Telefon gezielt anruft. „Umstellen?“ setzt es nach Rückfrage auf feste Nummern: die bisherige ausgehende Nummer und die eigene 99NNN.</li>
</ul>`
},
{
 id: "klingelnummern", group: "Menschen und Nummern", title: "Klingel-Rufnummern 99…",
 keys: "klingel-rufnummer klingelnummer 99613 99611 99610 anruferanzeige alle telefone klingeln grundleitung wahlregel 000 vorwahl umstellen alle rufnummern empfang anderer anbieter migration update",
 html: `
<p>Eine <b>Klingel-Rufnummer</b> ist keine echte Rufnummer, sondern eine Nummer <code>99</code> + interne Nummer eines Telefons (z. B. <code>99611</code> für <code>**611</code>). Die Fritzbox legt sie als „Anderer Anbieter“ mit Registrar = dieser Rechner an und meldet sie bei Asterisk an. Ruft Asterisk darüber an, ist das für die Fritzbox ein externer Anruf: Das Telefon zeigt die <b>Nummer des Anrufers</b> statt „LieschenMueller“.</p>
<h3>Je Telefon</h3>
<p>Seite <b>Rufnummern → „Anruferanzeige an den Telefonen“</b>: Der Schalter legt die Nummer an, richtet sie in Asterisk ein und weist sie dem Telefon zu. Ausschalten nimmt sie beim Telefon heraus und trägt sie in Asterisk aus (in der Fritzbox bleibt sie stehen). Jede Nummer lässt sich so ein- und ausschalten, auch <code>99613</code>.</p>
<h3>Grundleitung „alle Telefone klingeln“</h3>
<p>Unter <b>Einstellungen → Klingeln der Telefone</b> steht die Nummer, über die Asterisk <b>alle Telefone</b> klingeln lässt (Voreinstellung <code>99613</code>). Nimm eine Klingel-Rufnummer, die in der Fritzbox <b>allen Telefonen zugewiesen</b> ist, die klingeln sollen – zum Beispiel die des <b>Empfangs</b> (<code>99611</code>). Asterisk richtet diese Nummer immer von selbst ein; in der Tabelle ist sie mit „klingelt alle Telefone“ markiert und lässt sich dort nicht ausschalten. Zum Wechseln zuerst in den Einstellungen die neue Nummer eintragen, danach kann die alte in der Tabelle aus.</p>
<h3>Aufgezeichnete ausgehende Gespräche (Wahlregel, z. B. <code>000</code>)</h3>
<p>Die Fritzbox-Wahlregel (Telefonie → Eigene Rufnummern → Wahlregeln: „beginnt mit <code>000</code>“) wählt <b>über eine Klingel-Rufnummer</b>. Jede bei Asterisk eingeschaltete 99… funktioniert; am einfachsten dieselbe wie für „alle Telefone klingeln“. Wird die Nummer der Wahlregel ausgeschaltet, nimmt Asterisk ihre Anmeldung nicht mehr an und die Wahlregel geht nicht mehr.</p>
<h3>Ausgehend über 99… (Mitschrift je Benutzer)</h3>
<p>Hat ein Benutzer „<a href="#/help/ausgehend">Ausgehende Gespräche mitschreiben</a>“ an, steht sein Telefon in der Fritzbox auf ausgehend 99NNN; die Tabelle zeigt dann „ausgehend: 99NNN (Mitschrift)“ bzw. „Notbetrieb“. Ausschalten der Anruferanzeige stellt das Telefon zuerst auf seine bisherige ausgehende Nummer zurück.</p>
<h3>Telefone auf „alle Rufnummern“</h3>
<p>Ein Telefon, das in der Fritzbox bei allen Rufnummern klingelt, klingelt auch bei jeder fremden 99…. Darum ruft Asterisk es direkt an (ohne Anrufernummer), bis du „<b>Umstellen?</b>“ wählst: Das Telefon bekommt dann eine feste Auswahl (echte Rufnummern bzw. die seiner Benutzer, die Nummer für „alle Telefone klingeln“ und seine eigene 99…). „<b>auf „alle“ zurück</b>“ macht das rückgängig.</p>
<h3>Nach dem Update (bis 2026-11 stand 99613 fest)</h3>
<p>Früher war <code>99613</code> fest in Asterisk eingebaut. Beim ersten Start nach dem Update trägt die App <code>99613</code> deshalb einmal als eingeschaltete Klingel-Rufnummer ein – es ändert sich nichts. Gehört <code>**613</code> jemandem, der das System nicht nutzt: zuerst die Grundleitung (Einstellungen → Klingeln der Telefone) und die Wahlregel in der Fritzbox auf eine andere Nummer (z. B. <code>99611</code>) umstellen, dann <code>99613</code> in der Tabelle ausschalten.</p>`
},
{
 id: "fernabfrage", group: "Menschen und Nummern", title: "Fernabfrage per Telefon",
 keys: "fernabfrage codewörter rückruf anrufe abfragen vorlesen unterwegs sicherheit",
 html: `
<p>Mit der <b>Fernabfrage</b> fragt ein Benutzer per Telefon seine Anrufe ab: neue Anrufe auflisten, Zusammenfassungen oder Transkripte vorlesen, Aufnahmen abspielen, einen <b>Rückruf</b> starten und mit Kontakten oder Benutzern <b>verbinden</b>.</p>
<ol>
 <li>Beim Benutzer (Tab <b>Fernabfrage</b>): „Fernabfrage erlauben“, weitere Rufnummern und <b>Codewörter</b> eintragen.</li>
 <li>Der Anruf muss von einer erlaubten Nummer kommen (eigene Nummer, Nummer außer Haus oder weitere) <b>und</b> der Anrufer muss <b>alle Codewörter</b> nennen.</li>
 <li>Jedes Codewort muss als ganzes Wort fallen („Rose“ zählt nicht in „Rosenkohl“).</li>
 <li>Nach 3 Anrufen ohne Freischaltung innerhalb einer Stunde ist die Fernabfrage 30 Minuten gesperrt. Im Anruf steht dann „Fernabfrage gesperrt“.</li>
 <li>Die Codewörter werden im Transkript geschwärzt.</li>
</ol>
<div class="hint">Rufnummern von außen lassen sich fälschen. Deshalb sind Nummer <b>und</b> Codewörter nötig. Wähle Codewörter, die man nicht erraten kann.</div>`
},
{
 id: "kalender", group: "Menschen und Nummern", title: "Google Kalender",
 keys: "kalender google termin wo ist freie zeiten fernabfrage dienstkonto",
 html: `
<p>Beim Benutzer lässt sich ein <b>Google Kalender</b> anbinden (Tab <b>Kalender</b>).</p>
<ul>
 <li><b>Einrichtung:</b> Den Kalender mit dem Dienstkonto der App teilen (Berechtigung „Änderungen an Terminen vornehmen“). Die Adresse des Dienstkontos steht unter Einstellungen → Anbindungen → Google Kalender.</li>
 <li><b>Wo ist der Benutzer?</b> Fragt ein <i>berechtigter</i> Anrufer danach, sagt die KI, ob der Benutzer laut Kalender in einem Termin ist und bis wann. Alle anderen bekommen keine Auskunft.</li>
 <li><b>Per Fernabfrage</b> kann der Benutzer seine Termine abfragen, freie Zeiten erfragen und Termine eintragen. Vor dem Eintragen wiederholt die KI Titel, Tag und Uhrzeit.</li>
</ul>`
},
{
 id: "anrufe", group: "Anrufe", title: "Anrufliste, Transkript und Banner",
 keys: "anrufliste transkript suche filter banner übernehmen auflegen durchstellen notizen kosten zusammenfassung archiv ablauf zeitleiste klingelte rückfrage verlauf animation animationen bewegung darstellung vorschau",
 html: `
<p>Die Seite <b>Anrufe</b> zeigt alle Anrufe mit <b>Suche</b> (auch im Transkript), Filtern (Status, Richtung, Agent, Zeitraum) und Seitenwechsel.</p>
<ul>
 <li>Ein Klick öffnet das <b>Transkript</b> (Sprecher getrennt, Agentenwechsel mit Grund), die Notizen der KI, Kosten, die <b>Aufnahme</b> und die <b>Zusatzinfos</b> (Zusammenfassung, Anliegen, Dringlichkeit, Stimmung).</li>
 <li><b>Ausführlich zusammenfassen</b> (Karte „Zusatzinfos“): erstellt eine Liste <b>aller besprochenen Punkte</b> in der Reihenfolge des Gesprächs, dazu <b>Zusagen und Vereinbarungen</b> und <b>offene Punkte</b>. Automatisch für jeden Anruf: Agent → Wechsel → „Zusatzinfos ausführlich“. Das Modell wählst du unter Einstellungen → KI → „Auswertung der Gespräche“. In die Mail kommt sie, wenn beim Empfänger „Alle besprochenen Punkte“ eingeschaltet ist. Einen einzelnen Anruf schickst du mit <b>Per E-Mail senden</b> (Anrufansicht): Dort kannst du „Alle besprochenen Punkte“, Transkript und Aufnahme an- und abwählen.</li>
 <li><b>Wiedergabeleiste</b>: Die Aufnahme hat eine eigene Leiste mit Wellenform. Ein Klick in die Wellenform springt zur Stelle. Dazu gibt es ±10 Sekunden, Tempo (0,75× bis 2×), Lautstärke und <b>Herunterladen</b>. Tasten: Leertaste = Pause, ←/→ = 5 Sekunden.</li>
 <li><b>★ Archivieren</b> hält einen Anruf über die Aufbewahrungsfrist hinaus.</li>
 <li><b>Status:</b> <i>Angenommen</i> = eine Person hat abgenommen, bevor die KI dran war. <i>Durchgestellt</i> = die KI hat den Anrufer mit einer Person verbunden. <i>Nicht angenommen</i> = niemand hat abgenommen und der Anrufer legte vor der KI auf. <i>Beendet</i> = die KI hat das Gespräch geführt.</li>
 <li><b>Laufende Gespräche</b> steuerst du dort: Aufnahme starten oder stoppen, Agent wechseln, an einen Menschen übergeben, auflegen.</li>
</ul>
<h3>Ablauf eines Anrufs</h3>
<p>Im geöffneten Anruf zeigt der Abschnitt <b>Ablauf</b> eine kurze Zeitleiste, was mit dem Anruf passiert ist, mit Uhrzeit und Dauer bis zum nächsten Schritt, zum Beispiel:</p>
<ul>
 <li>„klingelte bei **610 + **620 – Regel: Abends, 25 s“, dann „niemand nahm ab“,</li>
 <li>„Agent Empfang übernahm (Anrufregel Standard)“, „Rückfrage bei Petra → abgelehnt“, „KI nahm den Anrufer wieder an“,</li>
 <li>„Aufnahme gestartet“ bzw. „… angehängt“, „Kostenbremse: …“, „aufgelegt“.</li>
</ul>
<p>Der Abschnitt lässt sich zuklappen (das merkt sich der Browser). Ältere Anrufe von vor dieser Funktion haben keinen Ablauf; dort steht alles wie bisher im Transkript.</p>
<h3>Banner bei laufenden Anrufen</h3>
<p>Bei einem klingelnden oder laufenden Anruf erscheint oben ein Banner. Darüber: <b>KI jetzt annehmen lassen</b>, <b>Durchstellen</b> (optional mit Mitschrift), <b>Agent wechseln</b>, <b>Auflegen</b>, <b>Gespräch ansehen</b>.</p>
<h3>Kleine Bilder im Banner</h3>
<p>Ändert sich ein Anruf, spielt das Banner eine kurze Szene ab, ohne Ton und nach gut einer Sekunde wieder weg:</p>
<ul>
 <li><b>Es klingelt:</b> ein altes Telefon wackelt und sendet Schallwellen.</li>
 <li><b>Die KI nimmt an:</b> Lieschen setzt das Headset auf, zwinkert, und in der Sprechblase pulsieren drei Punkte.</li>
 <li><b>Durchgestellt:</b> der Hörer fliegt im Bogen zum zweiten Telefon und zieht eine Punkte-Spur hinter sich her – sobald die KI übergibt oder jemand den klingelnden Anruf annimmt.</li>
 <li><b>Nachricht hinterlassen:</b> ein Briefumschlag verschwindet im Briefkasten, die Fahne geht hoch (wenn die KI eine Nachricht aufgenommen hat).</li>
 <li><b>Aufgelegt:</b> der Hörer fällt auf die Gabel, ein Wölkchen staubt, dann schrumpft die Zeile weg.</li>
</ul>
<p>Mehrere Anrufe haben jeweils ihre eigene Zeile und ihre eigene Szene. Ausschalten und alle Szenen einmal ansehen: <b>Meine Einstellungen → Darstellung</b> (Schalter „Kleine Animationen im Anruf-Banner“ und die Knöpfe unter „Vorschau“; die Vorschau löst keinen Anruf aus). Der Schalter gilt nur für den jeweiligen Browser. Ist im Betriebssystem „Bewegung reduzieren“ eingestellt, zeigt das Banner statt der Szene nur ein ruhiges Symbol.</p>`
},
{
 id: "anruf-starten", group: "Anrufe", title: "Anruf starten",
 keys: "anruf starten assistent ruft an ich telefoniere mitschrift vorlage auftrag",
 html: `
<p>Seite <b>Anruf starten</b>, zwei Varianten:</p>
<ul>
 <li><b>Assistent ruft an:</b> Die KI ruft eine Nummer oder einen Kontakt an und erledigt einen <b>Auftrag</b> (Platzhalter <code>{name}</code>, <code>{datum}</code>), im Namen eines Auftraggebers. Mit <b>Vorlagen</b>.</li>
 <li><b>Ich telefoniere mit Mitschrift:</b> Zuerst klingelt dein eigener Anschluss, danach wird das Ziel gewählt. Beide Seiten werden aufgenommen und transkribiert. Der Angerufene hört vorher einen Hinweis.</li>
</ul>
<div class="hint">Ausgehende KI-Anrufe folgen keinen Aufzeichnungsregeln: Sie werden immer transkribiert, Audio wird nicht aufgenommen.</div>`
},
{
 id: "kontakte", group: "Anrufe", title: "Kontakte",
 keys: "kontakte telefonbuch fritzbox abgleichen anrufer prüfen",
 html: `
<p>Das <b>Telefonbuch</b>. Einträge aus der Fritzbox sind schreibgeschützt und werden mit <b>„Mit Fritzbox abgleichen“</b> aktualisiert. Die KI prüft damit Angaben des Anrufers (zum Beispiel einen genannten Namen). Kontakte dürfen auch als Ziel der Weiterleitung dienen.</p>`
},
{
 id: "kampagnen", group: "Anrufe", title: "Kampagnen (Plugin)",
 keys: "kampagne plugin terminpool termine ziele anrufbeantworter versuche",
 html: `
<p><b>Kampagnen</b> ist ein mitgeliefertes Plugin: Die KI ruft eine Liste von Zielen an, zum Beispiel für Terminerinnerungen.</p>
<ul>
 <li><b>Allgemein</b>: Name, Agent, Auftrag, Auftraggeber, Terminpool, Pause zwischen Terminen, Mail, Kalendertitel.</li>
 <li><b>Zeiten und Wiederholungen</b>: Wochentage, Uhrzeiten, Versuche pro Person, Abstand.</li>
 <li><b>Anrufbeantworter</b>: später erneut anrufen, Nachricht sprechen oder auflegen.</li>
 <li><b>Terminpools</b>: Termine, die die KI anbietet und exklusiv bucht, auf Wunsch aus einem Google Kalender.</li>
</ul>`
},
{
 id: "aufnahme", group: "Aufnahme und Mail", title: "Aufnahmen: Dateien, Wiedergabe, Kosten",
 keys: "aufnahme mp3 mono stereo wiedergabe teile rückfrage gespräch zusammengefügt bitrate speicher aufbewahrung löschen",
 html: `
<p>Eine Aufnahme ist <b>eine Datei</b> pro Anruf (MP3, Mono). Anrufer und KI liegen beide auf allen Kanälen, du hörst also alles, auch mit einem Ohrhörer.</p>
<ul>
 <li>Besteht ein Anruf aus mehreren Teilen (KI mit Anrufer → Gespräch mit dem Benutzer, oder die KI übernimmt nach einer abgelehnten Rückfrage wieder), werden die Teile <b>in zeitlicher Reihenfolge angehängt</b>, mit einer Sekunde Pause dazwischen. Die Wartezeit des Anrufers fehlt.</li>
 <li><b>Aufbewahrung</b>: Anrufe werden nach der eingestellten Zahl von Tagen gelöscht (Einstellungen → Allgemein → Aufbewahrung). Archivierte Anrufe (★) bleiben.</li>
 <li><b>Qualität</b>: Telefonaufnahmen haben 8 kHz. Mehr als 64 kbit/s bringt nichts. Einstellung unter Einstellungen → Telefonie → Aufzeichnung.</li>
 <li><b>Sprachbefehl</b>: „Lieschen, keine Aufzeichnung“ beendet die Aufnahme und verwirft sie.</li>
</ul>`
},
{
 id: "ausgehend", group: "Aufnahme und Mail", title: "Ausgehende Gespräche mitschreiben",
 keys: "ausgehend ausgehende gespräche mitschrift mitschreiben transkript aufnahme 99610 99nnn vorwahl 000 notbetrieb rückruf anrufliste hinweis ansage nur an nummern meine einstellungen",
 html: `
<p>Jeder Benutzer entscheidet unter <b>Meine Einstellungen → Ausgehende Gespräche</b> selbst, ob seine Gespräche nach draußen mitgeschrieben werden – <b>ohne Vorwahl</b> (Administratoren auch im Benutzer-Editor, Reiter „Erreichbarkeit“).</p>
<ul>
 <li><b>Aus</b>: wie bisher. Mitgeschrieben wird nur, wer bewusst die Vorwahl (z. B. <code>000</code>) wählt – dann gelten die Aufzeichnungsregeln für ausgehende Gespräche.</li>
 <li><b>Aufnahme und Transkript</b>, <b>nur Transkript</b> (das Audio wird nach dem Transkribieren gelöscht) oder <b>nur an bestimmte Nummern</b> (Liste wie bei „nicht stören“, mit <code>*</code> am Ende eine ganze Nummerngruppe).</li>
 <li><b>Hinweis-Ansage</b> an den Gesprächspartner: standardmäßig an. Abschalten nur mit Warnhinweis – heimliches Aufnehmen oder Mitschreiben ist strafbar (§ 201 StGB).</li>
</ul>
<h3>Was die App dafür tut</h3>
<p>Beim Einschalten stellt die App jedes Telefon unter „Klingelt im Büro an“ (<code>**NNN</code>) in der Fritzbox auf die <b>ausgehende Nummer 99NNN</b> (seine <a href="#/help/klingelnummern">Klingel-Rufnummer</a>). Das geht nur, wenn die 99NNN eingerichtet (Rufnummern → Anruferanzeige), in der Fritzbox angelegt und bei Asterisk angemeldet ist; sonst steht beim Telefon der Grund. Die bisherige ausgehende Nummer merkt sich die App; „Aus“ stellt sie wieder her. Scheitert das Umstellen eines Telefons, stellt die App die schon umgestellten zurück und speichert nichts.</p>
<p>Der Angerufene sieht bei diesen Gesprächen die <b>ausgehende Nummer von Asterisk</b> (Rufnummern → „Asterisk antwortet auf“ → Ausgehende Nummer). Die Mitschrift erscheint in der Anrufliste mit dem Namen des Benutzers; der Benutzer sieht sie selbst.</p>
<ul>
 <li><b>Notrufe</b> (110, 112) werden nie mitgeschrieben.</li>
 <li><b>Rückrufe aus der Anrufliste</b> des Telefons laufen bei eingeschalteter Mitschrift ebenfalls über 99NNN und werden mitgeschrieben. Ohne Umstellung (Einstellung aus) bleibt es dabei: ohne Vorwahl keine Mitschrift.</li>
 <li>Ein gemeinsames Telefon kann nur ein Benutzer umstellen; für die anderen steht „anderer Benutzer“.</li>
</ul>
<h3>Notbetrieb</h3>
<p>Alle 30 Sekunden prüft die App, ob die 99NNN der umgestellten Telefone bei Asterisk angemeldet ist. Fehlt sie länger als 90 Sekunden (oder ist Asterisk nicht erreichbar), stellt sie das Telefon auf seine bisherige Nummer zurück (<b>Notbetrieb</b>: telefonieren geht, aber ohne Mitschrift), schickt eine Mail an alle aktiven Mail-Empfänger und schreibt es ins Protokoll. Ist die Nummer wieder da, stellt sie es wieder um. Beim Start gleicht die App den Stand mit der Fritzbox ab.</p>
<p><b>Grenze:</b> Fällt der ganze Rechner aus (Bridge und Asterisk), kann niemand zurückstellen – die umgestellten Telefone erreichen dann nach draußen niemanden, bis jemand in der Fritzbox (Telefonie → Telefoniegeräte → Bearbeiten → ausgehende Rufnummer) die bisherige Nummer einstellt. Fällt nur die Bridge aus, wählt Asterisk ohne Mitschrift normal weiter.</p>`
},
{
 id: "mail", group: "Aufnahme und Mail", title: "Benachrichtigungen und Mail",
 keys: "mail smtp empfänger tagesbericht gmail outlook größe anhang audio limit testmail kennwort",
 html: `
<p>Menü (☰) → <b>Benachrichtigungen</b>. Zwei Teile: das <b>Mailkonto</b> (SMTP) und die <b>Empfänger</b>.</p>
<h3>Mailkonto</h3>
<ul>
 <li>Server, Port, Benutzer, Absender (optional), Verschlüsselung und Kennwort. Typische Werte stehen auf der Seite (Gmail: <code>smtp.gmail.com</code>, Port 587, STARTTLS, <b>App-Passwort</b>).</li>
 <li>Mit <b>Testmail senden</b> prüfst du alles.</li>
 <li><b>Größte Mail mit Aufnahme</b> (Standard 10 MB): Ist die Aufnahme größer, geht die Mail <b>ohne Audio</b> raus und enthält einen Hinweis. Scheitert der Versand mit Anhang, versucht die App es einmal ohne Audio.</li>
</ul>
<h3>Empfänger</h3>
<ul>
 <li><b>Rufnummern</b>: für welche Nummern der Empfänger Mails bekommt (keine gewählt = alle).</li>
 <li><b>Mail bei einzelnen Anrufen senden</b>: aus = nur der Tagesbericht. Ist der Schalter an:
  <ul>
   <li><b>Ergebnis des Anrufs</b> (Mehrfachauswahl, z. B. nur „Von einer Person angenommen“ oder nur „Von der KI bearbeitet“; alle angekreuzt = jedes Ergebnis). Ist eine Transkription fehlgeschlagen, zählt das eigentliche Ergebnis des Anrufs.</li>
   <li><b>Nur bei wichtigen Anrufen</b>: Nachricht hinterlassen, hohe Dringlichkeit oder starke Aufregung oder Angst des Anrufers.</li>
   <li><b>Inhalt der Anruf-Mail</b>: Details, alle besprochenen Punkte, Transkript, Aufnahme als Anhang (jeweils ankreuzen).</li>
  </ul></li>
 <li><b>Auch bei Gesprächen mit Mitschrift benachrichtigen</b>: Mails zu Gesprächen mit Mitschrift (ausgehend mit Vorwahl und „Ich telefoniere“). Das gilt unabhängig von den Rufnummern, der Ergebnis-Filter gilt dafür nicht.</li>
 <li><b>Täglicher Bericht</b> über alle Anrufe auf diese Rufnummern, zur eingestellten Uhrzeit.</li>
 <li><b>Störungs-/Auffälligkeitsmeldungen</b> (Standard aus): Sammelmail der <a href="#/help/auffaelligkeiten">Selbstprüfung</a>, unabhängig von den Anruf-Mails und den Rufnummern.</li>
 <li>Wird ein Anruf von der KI durchgestellt, kommt die Mail erst, wenn das ganze Gespräch zu Ende ist.</li>
</ul>
<div class="hint">Mails enthalten Zusammenfassung und Transkript von Gesprächen und gehen über den Mailanbieter. Prüfe, ob das für deine Daten in Ordnung ist.</div>`
},
{
 id: "assistent", group: "Einstieg", title: "Assistent „Lieschen“ (Chat)",
 keys: "assistent chat lieschen helferin fragen erreichbarkeit warum ki anruf erklären regel vorschlag übernehmen rückgängig protokoll gemini",
 html: `
<p>Der <b>Assistent</b> (💬 in der Kopfzeile oder <b>Lieschen</b> unten rechts) beantwortet Fragen in normaler Sprache und schlägt auf Wunsch Änderungen vor.
Er nutzt das Gemini-Textmodell über denselben Zugang wie die Zusammenfassungen (Vertex EU bzw. API-Schlüssel); die Kosten stehen unter Kosten als „Assistent“.</p>
<h3>Was er beantwortet (nur lesen)</h3>
<ul>
 <li><b>Erreichbarkeit:</b> „Wer ist jetzt erreichbar?“, „Wohin ginge ein Anruf von Herrn Huber morgen um 18 Uhr?“ – nach derselben Rangfolge wie die Vorschau (nicht stören, Wählregel, Zeitplan, Status, Vertretung).</li>
 <li><b>Regeln:</b> welche Anrufregel greift, welcher Agent annimmt, wie lange es vorher klingelt (Anrufregeln nur für Administratoren).</li>
 <li><b>Anrufe:</b> „Warum ging der Anruf um 10:05 an die KI?“ – er sucht den Anruf und erklärt den Ablauf (wer klingelte, wie lange, warum das Klingeln endete, welche Regel), dazu Zusammenfassung und Auffälligkeiten.</li>
 <li><b>Rufnummern und Telefone</b> aus dem zwischengespeicherten Stand (die Fritzbox wird dafür nicht neu gelesen).</li>
</ul>
<h3>Ändern nur mit Bestätigung</h3>
<p>Wünschst du eine Änderung („Ich bin bis Freitag außer Haus“, „Herr Huber soll immer sofort mit der KI sprechen“), zeigt der Assistent eine <b>Karte mit vorher → nachher</b>.
Erst <b>Übernehmen</b> speichert – mit denselben Prüfungen wie die normalen Seiten. Möglich sind: Anrufregeln (Administratoren), Status, Notiz, „abwesend bis“, Vertretung (Administratoren),
Ausnahmen bei „nicht stören“, Wählregeln und „Ausgehende Gespräche mitschreiben“ (stellt die Telefone in der Fritzbox um).</p>
<p>Jede übernommene Änderung steht im <b>Protokoll</b> (unten im Chat bzw. Einstellungen → Diagnose → Assistent: Änderungsprotokoll) und lässt sich mit <b>Rückgängig</b> zurücknehmen, solange dort nichts anderes geändert wurde.</p>
<h3>Grenzen und Sicherheit</h3>
<ul>
 <li>Der Assistent arbeitet mit <b>deinen Rechten</b>: Benutzer sehen nur ihre eigenen Anrufe und ihren eigenen Eintrag; Nummern anderer Benutzer (z. B. außer Haus) erscheinen als „…“.</li>
 <li><b>Nie:</b> Zugangsdaten, Passwörter, Schlüssel, Fritzbox-Zugang, Benutzerkonten und Rollen, Löschen von Anrufen oder Benutzern, Mails, Anrufe.</li>
 <li>Inhalte aus Anrufen (Transkripte, Notizen) sind für ihn nur Daten. Entsteht ein Vorschlag nach dem Lesen solcher Inhalte, trägt die Karte einen Warnhinweis – übernimm ihn nur, wenn du die Änderung selbst wolltest.</li>
 <li>Der Verlauf bleibt nur in diesem Browser-Tab („Neu“ leert ihn); gespeichert wird nur das Protokoll der Änderungen. Vorschläge gelten 30 Minuten.</li>
 <li>Lieschen unten rechts lässt sich unter Meine Einstellungen → Darstellung ausblenden; ein Administrator kann den Assistenten unter Einstellungen → KI → Assistent (Chat) ganz abschalten und das Modell wählen.</li>
</ul>`
},
{
 id: "einstellungen", group: "Einstellungen", title: "Einstellungen im Überblick",
 keys: "einstellungen gemini schlüssel grundkonfiguration geschäftszeiten aufbewahrung tabs allgemein ki telefonie anbindungen suche",
 html: `
<p>Die Einstellungen haben vier Tabs und ein Suchfeld, das alle Tabs durchsucht:</p>
<ul>
 <li><b>Allgemein</b>: <b>Grundkonfiguration</b> (wird jedem Agenten vorangestellt), <b>Geschäftszeiten</b>, <b>Aufbewahrung</b> der Anrufe.</li>
 <li><b>KI</b>: <b>Stimme der KI</b> (Lautstärke, Name, Stimme, Tempo, Stil für die Rückfrage), <b>Auswertung der Gespräche</b> (Modelle für ausführliche Zusammenfassung und Transkript), <a href="#/help/assistent">Assistent (Chat)</a> (an/aus, Modell), <a href="#/help/vertex">Google-Zugang (Vertex AI)</a> und <a href="#/help/kosten">Kostenbremse</a>.</li>
 <li><b>Telefonie</b>: Aufzeichnung, Klingeln der Telefone, <a href="#/help/rueckfrage">Rückfrage</a>, <a href="#/help/wartemusik">Wartemusik</a>.</li>
 <li><b>Anbindungen</b>: Google Gemini (Schlüssel), Fritzbox (Abfragen per TR-064), Fritzbox-Anmeldung von Asterisk, Google Kalender.</li>
</ul>
<div class="hint">Zugangsdaten stehen in der Datenbank im Ordner <code>data</code> und werden nie wieder angezeigt. Schütze den Ordner und Sicherungen entsprechend.</div>`
},
{
 id: "ki-stimme", group: "Einstellungen", title: "Stimme der KI: Lautstärke, Name, Tempo",
 keys: "stimme lautstärke übersteuert name sprechtempo stil probe voice kosename",
 html: `
<p>Einstellungen → <b>KI</b> → <b>Stimme der KI</b>.</p>
<ul>
 <li><b>Lautstärke</b> der KI-Stimme am Telefon (Standard 60 %). Klingt sie zu laut oder übersteuert, den Wert senken. Der Bass unter 200 Hz wird entfernt, laute Spitzen werden weich begrenzt.</li>
 <li><b>Interne Anrufe</b> (Rückfrage beim Benutzer): <b>Name</b> der KI, <b>Stimme</b>, <b>Sprechtempo</b>, <b>Sprechstil</b>, <b>Eigene Anweisungen</b> für Ton und Wortwahl. Mit <b>▶ Probe</b> hörst du Stimme, Tempo und Stil an.</li>
 <li>Die Stimme der Gespräche mit dem Anrufer wählst du im Agenten.</li>
</ul>
<div class="hint">Die KI hält sich nicht immer exakt an Tempo und Stil. Für feste Anreden und Kosenamen ist das Benutzerfeld „Anreden“ zuverlässiger als ein Satz in den Anweisungen.</div>`
},
{
 id: "wartemusik", group: "Einstellungen", title: "Wartemusik und Freizeichen",
 keys: "wartemusik musik freizeichen klingeln hochladen mp3 lautstärke zufällig stück verzögerung",
 html: `
<p>Einstellungen → <b>Telefonie</b> → Karten <b>Klingeln der Telefone</b> und <b>Wartemusik</b>.</p>
<ul>
 <li>Du lädst MP3, WAV, FLAC oder OGG hoch (bis 25 MB, 3 Sekunden bis 15 Minuten). Die Stücke werden fürs Telefon aufbereitet (Bass raus, Mitten betont, gleiche Lautheit) und in 16 kHz zusätzlich für Breitband gespeichert.</li>
 <li>Die eingeschalteten Stücke spielen in zufälliger Reihenfolge, nie dasselbe zweimal hintereinander. Es muss immer eines eingeschaltet sein.</li>
 <li><b>Lautstärke</b> (eigener Kasten, Knopf <b>Lautstärke übernehmen</b>, gilt nur für die beiden Werte darin):
  <ul>
   <li><b>Musik</b> in % (Standard 90): 100 = unverändert, 10 = sehr leise. Die Skala ist logarithmisch wie bei einem Hi-Fi-Regler. Jede Änderung wird aus dem Original neu berechnet, es geht also keine Qualität verloren.</li>
   <li><b>Zusätzlich leiser beim Klingeln</b> in dB (0 = aus, 6 = halb so laut, bis 30): Asterisk senkt die Lautstärke nur, solange es klingelt. Nach der Annahme, beim Umleiten und beim Wechsel zur KI ist sie wieder normal.</li>
  </ul></li>
 <li>Die Freizeichen-Töne bleiben unverändert.</li>
 <li>Wartemusik kann <b>anstelle des Freizeichens</b> laufen, ab dem x-ten Klingeln, und beim Verbinden und bei der <a href="#/help/rueckfrage">Rückfrage</a>. Ein Stück lässt sich auch fest wählen.</li>
 <li><b>Verzögerung</b>: Die ersten Sekunden spielt der Dialplan selbst das Freizeichen.</li>
</ul>
<div class="hint">Am Telefon kommen nur etwa 300 bis 3400 Hz an. Musik klingt dort dünner als im Browser. Das ist eine Eigenschaft der Leitung.</div>`
},
{
 id: "vertex", group: "Einstellungen", title: "Google-Zugang: Vertex AI (EU)",
 keys: "vertex google cloud eu dsgvo region standort projekt dienstkonto schlüsseldatei vertex.json datenschutz auftragsverarbeitung",
 html: `
<p>Einstellungen → <b>KI</b> → Karte <b>Google-Zugang (Datenschutz)</b>.</p>
<ul>
 <li>Standard ist die einfache <b>Gemini-API</b> mit einem Schlüssel (Einstellungen → Anbindungen).</li>
 <li>Mit <b>Vertex AI</b> laufen die Anfragen über dein Google-Cloud-Projekt im Standort <b>eu</b>. Die Verarbeitung bleibt dann in der EU. Andere Standorte sind bewusst nicht wählbar.</li>
 <li><b>Verwenden für</b>:
  <ul>
   <li><b>Aus</b>: alles über die Gemini-API.</li>
   <li><b>Transkript, Zusammenfassung, Auswertung</b>: Texte über Vertex, die Telefon-KI weiter über den Schlüssel.</li>
   <li><b>Alles</b>: auch Telefon-KI und Stimmproben über Vertex. Der Gemini-Schlüssel wird dann nicht mehr gebraucht.</li>
  </ul></li>
 <li>Nötig sind die <b>Projekt-ID</b> und der Schlüssel eines Dienstkontos als Datei <code>data/vertex.json</code> auf dem Server (Rolle „Vertex AI User“).</li>
 <li>Es gibt <b>keinen stillen Rückfall</b>: Ist Vertex gewählt, aber nicht eingerichtet, schlagen die Anfragen fehl, und oben steht „Vertex unvollständig“.</li>
 <li><b>Verbindung testen</b> prüft jedes Textmodell der App im Standort eu. Nicht angebotene Modelle sind in der Auswahl gesperrt.</li>
</ul>
<div class="hint">Für die DSGVO zusätzlich nötig: die Auftragsverarbeitung (Cloud Data Processing Addendum) in der Google-Cloud-Konsole annehmen und die Verarbeitung im eigenen Verzeichnis dokumentieren.</div>`
},
{
 id: "kosten", group: "Einstellungen", title: "Kosten und Kostenbremse",
 keys: "kosten token budget kostenbremse limit anrufe stunde minuten tag robo spam euro",
 html: `
<p>Die Seite <b>Kosten</b> zeigt Tokens und Kosten je Anruf, je Monat und je Agent, das Budget und den Rest. Euro-Beträge nutzen den täglichen EZB-Kurs. Maßgeblich ist die Abrechnung bei Google.</p>
<h3>Kostenbremse</h3>
<p>Einstellungen → KI → <b>Kostenbremse</b> (je Agent im Tab „Grenzen und Optionen“ überschreibbar):</p>
<ul>
 <li>höchstens <i>n</i> KI-Anrufe je Rufnummer und Stunde,</li>
 <li>höchstens <i>n</i> KI-Minuten je Rufnummer und Tag,</li>
 <li>höchstens <i>n</i> KI-Minuten insgesamt pro Tag,</li>
 <li>je Agent die Höchstdauer eines Gesprächs.</li>
</ul>
<p>0 = aus. Ist eine Grenze erreicht, nimmt die KI nicht an. Die Fritzbox lässt andere Telefone weiter klingeln, in der Anrufliste steht „Kostenbremse“. <b>Ausgenommen</b> sind interne Nummern, Kontakte und Anrufe von den Fernabfrage-Nummern eines Benutzers. Externe Nummern, die Benutzer selbst eintragen (Büro, außer Haus), werden gebremst wie jede andere Nummer.</p>`
},
{
 id: "fritzbox", group: "Einstellungen", title: "Fritzbox einrichten",
 keys: "fritzbox ip-telefon 99613 99611 klingel-rufnummer wahlregel registrar tr-064 benutzer kennwort anderer anbieter rufnummer anmeldung asterisk 404 registered",
 html: `
<p>Asterisk meldet sich an der Fritzbox wie ein <b>IP-Telefon</b> an. Für ein vollständiges Setup brauchst du drei Dinge.</p>
<ol>
 <li><b>IP-Telefon</b> (Pflicht): Fritzbox → Heimnetz → Telefoniegeräte → Neues Gerät → Telefon → LAN/WLAN (IP-Telefon). Benutzername (nur Buchstaben und Ziffern) und Kennwort (ohne <code>;</code>) wählst du selbst und trägst sie in der App ein: <b>Einstellungen → Anbindungen → „Fritzbox: Anmeldung von Asterisk“</b>. Bei den Rufnummern wählst du die, auf denen die KI antworten soll. Klingel-Rufnummern 99… hakst du dort <b>nicht</b> an.</li>
 <li><b>Klingel-Rufnummer für „alle Telefone klingeln“</b> (empfohlen): eine Nummer 99 und die interne Nummer eines Telefons, z. B. <code>99611</code> für den Empfang **611 (Voreinstellung ist <code>99613</code>). Am einfachsten auf der Seite <b>Rufnummern → „Anruferanzeige an den Telefonen“</b> einschalten: die App legt sie in der Fritzbox an. Von Hand: Telefonie → Eigene Rufnummern → Neue Rufnummer → <b>Anderer Anbieter</b>, Rufnummer und Benutzername = die Nummer, ein beliebiges Kennwort, <b>Registrar = IP des Hosts</b> (dort, wo Asterisk läuft; nicht die Fritzbox selbst). Weise die Nummer allen Telefonen zu, die klingeln sollen, und trage sie unter Einstellungen → Klingeln der Telefone ein; Asterisk richtet sie dann von selbst ein. Die Nummer ist über die IP-Adresse abgesichert, das Kennwort wird nicht geprüft.</li>
 <li><b>TR-064</b> (für Rufnummern, Telefonbuch, Anrufliste): Heimnetz → Netzwerk → Netzwerkeinstellungen → Weitere Einstellungen → Heimnetzfreigaben → <b>„Zugriff für Anwendungen zulassen“</b>, dazu ein Fritzbox-Benutzer mit dem Recht „FRITZ!Box Einstellungen“. Eintragen unter Einstellungen → Anbindungen → „Fritzbox (Abfragen …)“.</li>
</ol>
<h3>Typische Fehler</h3>
<ul>
 <li><b>404 beim Anmelden</b>: Die Fritzbox kennt den Benutzer nicht. Das IP-Telefon ist nicht fertig angelegt oder der Name weicht ab (Groß- und Kleinschreibung zählt).</li>
 <li><b>„abgelehnt“ (401/403)</b>: Das Kennwort stimmt nicht.</li>
 <li><b>Eine Klingel-Rufnummer 99… meldet sich nicht</b>: Sie ist auf der Seite Rufnummern nicht eingeschaltet (Asterisk nimmt dann ihre Anmeldung nicht an), oder der Registrar in der Fritzbox ist falsch (muss die Adresse von Asterisk sein, nicht die der Fritzbox).</li>
</ul>
<p>Die Anmeldung bleibt nicht stehen: Asterisk versucht sie nach einer Ablehnung weiter.</p>
<p><b>Maßgeblich sind die Einstellungen in der App.</b> Die Datei für Asterisk (<code>data/asterisk/fritz.conf</code>) schreibt die App selbst daraus, beim Start und nach jedem Speichern. Wurde die Datei von Hand geändert, gilt beim nächsten Start wieder, was in den Einstellungen steht (die geänderte Datei bleibt als <code>fritz.conf.vorher</code> liegen).</p>`
},
{
 id: "faq", group: "Hilfe bei Problemen", title: "Häufige Fragen und Fehlersuche",
 keys: "fehler problem keine musik kein freizeichen nimmt nicht an klingelt nicht aufnahme fehlt ansage mehrfach mail kommt nicht langsam übersteuert",
 html: `
<h3>Die KI nimmt nicht an</h3>
<p>Es fehlt eine passende <a href="#/help/anrufregeln">Anrufregel</a>, der Agent ist ausgeschaltet, das IP-Telefon ist bei der Rufnummer nicht eingetragen, Asterisk ist nicht an der Fritzbox angemeldet, die <a href="#/help/kosten">Kostenbremse</a> hat gegriffen oder das Gemini-Guthaben ist leer.</p>
<h3>Anrufer hört weder Freizeichen noch Musik</h3>
<p>Meist ein Zeitproblem mit der Fritzbox. Hilft nur ein Mitschnitt; melde den Fall dem Betreiber. Prüfe außerdem, ob die Wartemusik eingeschaltet ist und mindestens ein Stück aktiv ist.</p>
<h3>Ich höre in der Aufnahme nur eine Seite</h3>
<p>Neuere Aufnahmen sind Mono, beide Seiten sind überall zu hören. Ältere Aufnahmen sind Stereo (Anrufer links, KI rechts).</p>
<h3>Ich höre mehrere Ansagen zu Beginn</h3>
<p>Der Agent hat eine Hinweis-Ansage („Sie sprechen mit einer KI“) und die Aufzeichnungsregel eine eigene („wird aufgezeichnet“). Du kannst beide zu einer zusammenlegen (Agent: Hinweis-Text; Regel: Ansage aus).</p>
<h3>Eine Mail ist nicht angekommen</h3>
<p>Meist war die Aufnahme zu groß. Die Grenze steht unter Benachrichtigungen („Größte Mail mit Aufnahme“). Prüfe mit „Testmail senden“, ob der Versand funktioniert, und den Spam-Ordner.</p>
<h3>Die KI klingt übersteuert oder zu leise</h3>
<p>Einstellungen → KI → <a href="#/help/ki-stimme">Stimme der KI</a> → Lautstärke ändern.</p>
<h3>Warum zeigt das Telefon „LieschenMueller“ als Anrufer?</h3>
<p>Der Anruf läuft über das IP-Telefon. Mit einer Klingel-Rufnummer 99… (für „alle Telefone klingeln“ bzw. je Telefon unter Rufnummern → „Anruferanzeige an den Telefonen“) zeigen die Telefone die echte Nummer.</p>
<h3>Gehen mehrere Anrufe gleichzeitig?</h3>
<p>Ja, jedes Gespräch ist ein eigener Kanal mit eigener KI-Sitzung. Grenzen setzen Kosten und Bandbreite.</p>`
},
{
 id: "auffaelligkeiten", group: "Hilfe bei Problemen", title: "Auffälligkeiten bei Anrufen (Selbstprüfung)",
 keys: "auffälligkeiten störung selbstprüfung einweg ton rtcp ki hört nichts klingeln ohne wirkung klingelt nicht ereignisprotokoll fritzbox neueinwahl 480 606 nicht registriert verpasste anrufe watchdog wächter quittieren störungsmeldungen",
 html: `
<p>Die Anlage prüft sich im Hintergrund selbst und meldet, was auf eine Störung hindeutet. Nur <b>Administratoren</b> sehen die Meldungen: oben als <b>⚠ Zahl</b> (offene Meldungen), im Menü (☰) unter <b>Auffälligkeiten</b> als Liste und im <b>Ablauf</b> des betroffenen Anrufs als eigener Hinweis.</p>
<h3>Was wird erkannt?</h3>
<ul>
 <li><b>Einweg-Ton / keine RTCP-Bestätigung:</b> angenommener oder von der KI geführter Anruf, bei dem die eingehende Leitung keine Rückmeldung der Gegenseite hatte (Laufzeit 0) und länger als 20 s lief, oder bei dem die KI weniger als 50 % der empfangenen Pakete zurückschickte (KI-Gespräch über 30 s). Normal sind gesendet ≈ empfangen.</li>
 <li><b>KI hört nichts:</b> KI-Gespräch über 30 s mit vielen empfangenen Paketen (ab 1000, etwa 20 s Ton), aber ohne ein erkanntes Wort des Anrufers, und die KI hat wegen Stille aufgelegt.</li>
 <li><b>Klingeln ohne Wirkung:</b> dasselbe Telefon klingelte bei 3 Anrufen in Folge bis zum Ende (mindestens 15 s, „180 Ringing“), ohne dass es je abgenommen wurde. Klingelt es wirklich (Lautstärke, Ruhezeit, Akku)?</li>
 <li><b>Klingeln endet sofort:</b> das Klingeln an einem Telefon endete nach weniger als 3 s ohne erkennbaren Grund (nicht besetzt, nicht abgelehnt, kein anderes Telefon nahm ab, der Anrufer legte nicht auf), z. B. ein Direktruf auf **612, der sofort endet.</li>
 <li><b>Ereignisprotokoll der Fritzbox</b> (alle 5 min, nur neue Zeilen): „Internettelefonie … nicht erfolgreich“ (480, 606 …), Internetverbindung getrennt (Neueinwahl), Anmeldung einer Rufnummer gescheitert.</li>
 <li><b>Rufnummer nicht angemeldet:</b> eine eigene Rufnummer der Fritzbox ist zweimal in Folge nicht „registered“/„connected“.</li>
 <li><b>Viele nicht zugestellte Anrufe:</b> in der Anrufliste der Fritzbox mindestens 10 verpasste oder abgewiesene Anrufe ohne klingelndes Telefon auf derselben Rufnummer innerhalb von 10 Minuten. Die Meldung ist neutral, z. B. „Rufnummer …1234: 35 Anrufe zwischen 12:46 und 12:53 von 2 Nummern, bei keinem hat ein Telefon geklingelt“.</li>
 <li><b>Klingel-Rufnummern / Asterisk-Anmeldung:</b> eine Klingel-Rufnummer 99… ist nicht bei Asterisk angemeldet oder Asterisk nicht bei der Fritzbox. Das meldet schon der Wächter (<code>ops/watchdog.py</code>) per Mail, die <a href="#/help/ausgehend">ausgehende Mitschrift</a> schaltet selbst in den Notbetrieb; hier steht es nur zur Übersicht, ohne zweite Mail.</li>
</ul>
<p>Jede Regel lässt sich unter Einstellungen → <b>Diagnose</b> → „Auffälligkeiten erkennen“ ein- und ausschalten; die Schwellen stehen daneben (Tooltip zeigt die Bedeutung).</p>
<h3>Quittieren</h3>
<p><b>Quittieren</b> markiert eine Meldung als gesehen, das ⚠ oben verschwindet, wenn nichts mehr offen ist. Tritt dasselbe wieder auf (gleiche Art, gleiches Telefon bzw. gleiche Nummer, gleicher Tag), wird die Meldung wieder offen und zählt hoch, eine zweite Mail gibt es dafür nicht. Meldungen älter als 90 Tage werden gelöscht.</p>
<h3>Mail</h3>
<p>Mails gehen nur an Empfänger, bei denen unter <a href="#/help/mail">Benachrichtigungen</a> <b>„Störungs-/Auffälligkeitsmeldungen“</b> angekreuzt ist (Standard aus, unabhängig von den Anruf-Mails). Die Meldungen werden gesammelt: höchstens eine Mail je 15 Minuten und 6 am Tag (einstellbar). Ohne solchen Empfänger gibt es keine Mail, nur die Anzeige hier.</p>
<div class="hint">Rufnummern stehen in den Meldungen nur gekürzt (letzte 4 Ziffern), Namen von Telefonen ungekürzt. Fehler beim Lesen der Fritzbox stören den Anrufbetrieb nicht; für Ereignisprotokoll und Anrufliste braucht der TR-064-Benutzer die Rechte „FRITZ!Box Einstellungen“ und „Anrufliste“.</div>`
},
{
 id: "datenschutz", group: "Hilfe bei Problemen", title: "Datenschutz und Sicherheit",
 keys: "datenschutz sicherheit dsgvo aufzeichnung einwilligung hinweis google gemini daten passwort konto rolle backup",
 html: `
<ul>
 <li><b>Wohin gehen die Daten?</b> Gespräche und Transkripte werden von Google (Gemini) verarbeitet: über die einfache Gemini-API oder, wenn gewählt, über <a href="#/help/vertex">Vertex AI in der EU</a>. Mails gehen an den Mailanbieter. Alles andere bleibt auf dem Server im Ordner <code>data</code>.</li>
 <li><b>Aufzeichnen</b> ist nur zulässig, wenn die Beteiligten informiert sind und zustimmen. Die Hinweis-Ansagen sind standardmäßig an. Wer sie abschaltet, trägt die Verantwortung.</li>
 <li><b>Die KI</b> gibt sich auf Nachfrage immer als KI zu erkennen und gibt keine Auskunft über Personen, die nicht im Prompt stehen.</li>
 <li><b>Anwesenheit über WLAN</b> ist freiwillig: nur die Person selbst schaltet sie ein (Meine Einstellungen). Es wird kein Bewegungsprofil gespeichert, nur der aktuelle Status. Der TR-064-Benutzer braucht dafür das Recht „FRITZ!Box Einstellungen“ und kann damit viel an der Fritzbox ändern – Zugangsdaten entsprechend schützen.</li>
 <li><b>Konten:</b> Nutze starke Passwörter (mindestens 10 Zeichen) und die Rolle „Eingeschränkt“ für Mitarbeiter.</li>
 <li><b>Zugangsdaten</b> (Gemini, Fritzbox, Mail) liegen in der Datenbank. Schütze den Ordner <code>data</code> und Sicherungen.</li>
 <li><b>Assistent (Chat):</b> Fragen und die gelesenen Daten (z. B. Ablauf und Transkript-Auszug eines Anrufs) gehen an Gemini wie bei den Zusammenfassungen. Er ändert nur nach „Übernehmen“ und nie Zugangsdaten oder Konten; siehe <a href="#/help/assistent">Assistent</a>.</li>
 <li><b>Sicherung:</b> Sichere den Ordner <code>data</code> regelmäßig.</li>
</ul>`
},
];
