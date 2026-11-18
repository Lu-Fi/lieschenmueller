# Architektur

## Bausteine
| Baustein | Aufgabe |
|---|---|
| **Fritzbox** | Telefonanlage; Asterisk ist dort ein IP-Telefon (SIP-Registrierung), die Fritzbox meldet zusätzlich Klingel-Rufnummern `99NNN` (Voreinstellung `99613`) bei Asterisk an; TR-064 liefert Telefonbuch, Rufnummern und interne Anschlüsse |
| **Asterisk** (Container `andrius/asterisk`) | SIP/RTP zur Fritzbox, Dialplan, `AudioSocket` zur Bridge, `MixMonitor` für Mitschriften, AMI (nur lokal) |
| **Bridge** (`bridge/app`, Python 3.12, FastAPI/uvicorn) | Anrufentscheidung, Gespräch mit Gemini Live, Weboberfläche und API, Datenbank, Scheduler, Mail |
| **Google Gemini** (`google-genai`) | Live-API (Sprache↔Sprache, Werkzeuge) für Gespräche; ein Textmodell (`gemini-3.1-flash-lite`) für Nachtranskription und Auswertung |
| **SQLite** `/data/lieschen.db` (WAL) | Konfiguration (auch Fritzbox-Zugang und Gemini-Schlüssel, siehe unten), Anrufe, Transkripte, Konten |

**Konfiguration:** In der `.env` stehen nur noch `UI_PASSWORD`, `WEB_PORT`, `AMI_SECRET` (von `asterisk/render.sh` erzeugt), `GEMINI_MODEL` und optionale Schalter
(`ALLOW_BASIC`, `SIP_ACL`, `MAX_CALL_SECONDS`, `DIAL_BLOCK` …). Fritzbox-Adresse, SIP-Anmeldung, TR-064, Gemini-Schlüssel und Aufbewahrung stellt der Administrator
unter *Einstellungen* ein (`config.py`, Tabelle `meta`, Schlüssel `cfg:*`; Werte aus der `.env` gelten nur als Voreinstellung).

**Eine Wahrheit:** Die Datenbank ist die Quelle, Dateien für Asterisk sind nur Ausgabe (`astconf.py`):

| Datei | erzeugt aus | wann |
|---|---|---|
| `data/asterisk/fritz.conf` (SIP-Zugang, IP-Sperre, Klingel-Rufnummern) | `cfg:fritz_user/-password/-host`, meta `ring_lines`/`ring_all` | Start der Bridge (`astconf.sync_fritz`) und Speichern der Zugangsdaten (`config_put` → `sip_apply`), danach PJSIP neu laden |
| `data/asterisk/moh.conf` (Wartemusik) | Bibliothek + meta | Start und jede Änderung (`hold.rebuild_classes`), danach `moh reload` |
| `asterisk/conf/*.conf` | Vorlagen + `.env` (`AMI_SECRET`, `WEB_PORT`) | `asterisk/render.sh` |

Weichen `fritz.conf` und Datenbank voneinander ab, gewinnt beim Start die Datenbank (die alte Datei bleibt als `fritz.conf.vorher`).
Einmalige Migrationen: *adopt* übernimmt Zugangsdaten aus einer vorhandenen `fritz.conf` nur, wenn Benutzer und Kennwort in der Datenbank leer sind;
*upgrade* bringt bei unvollständigen Einstellungen eine ältere Datei mit ihren eigenen Daten ins aktuelle Format. `render.py` legt `fritz.conf` nur an,
wenn sie fehlt (aus der Datenbank, sonst `.env`, sonst Platzhalter) und überschreibt nie eine vorhandene Datei.

Beide Container laufen mit **Host-Netzwerk**. Lokale Schnittstellen: AudioSocket `127.0.0.1:9092`, AMI `127.0.0.1:5038`, die `/internal/*`-Aufrufe
der Bridge sind nur von `127.0.0.1` erreichbar. Die Weboberfläche lauscht auf `WEB_PORT` (8080).

## Anrufablauf (eingehend)
1. Die Fritzbox lässt das Gerät klingeln → Asterisk nimmt im Kontext `from-fritz` an (ohne `Answer`).
2. Der Dialplan fragt die Bridge: `GET /internal/call?uuid=…&from=…&to=…&hdr=…&chan=…` (gewählte Nummer steht in `P-Called-Party-ID`).
3. Die Bridge entscheidet (Reihenfolge): **Fernabfrage-Nummer?** → KI; **Rufnummer einem Benutzer zugewiesen?** → „nicht stören“ → Wählregeln → Zeitplan →
   Status/„bis“ → Vertretung → Weiterleitung, sonst KI (nimmt der Agent der ersten passenden Anrufregel sofort an, Klingelzeit 0, klingelt nur, wem eine Wählregel es erlaubt); **Anrufregel** (erste passende nach Priorität: Bedingungen, Agent, Klingelzeit; Aufzeichnung aus den *Aufzeichnungsregeln*); Modus *Durchstellen* → Weiterleitung.
   Bevor die KI annimmt (auch beim Rückfall nach dem Klingeln), prüft die **Kostenbremse** (`costguard.py`) die Grenzen des Agenten bzw. der Einstellungen; ist eine erreicht, wird der Anruf wie `no-agent` behandelt.
4. Antwort an den Dialplan (Klartext, siehe *Protokoll Bridge ↔ Dialplan*): `ok` (→ `Answer` + `AudioSocket`), `wait:N` (klingeln lassen, dann annehmen), `dial:a+b` (durchstellen, mehrere
   Ziele gleichzeitig), `fdial:…` (durchstellen, bei Nichtannahme nach 25 s übernimmt die KI über `/internal/fallback`), `recx:…` (mit Aufnahme), `no-agent` (ablehnen, `Hangup(19)`).
5. **Gespräch:** `Call` (call.py) bekommt über AudioSocket 8-kHz-PCM, setzt es per FIR-Resampler auf 16 kHz um und streamt es zu Gemini Live;
   Antworten (24 kHz) werden auf 8 kHz gebracht und in 20-ms-Rahmen zurückgespielt. Werkzeuge (`end_call`, `save_result`, `connect_contact`, `note_message`,
   `note_mood`, Fernabfrage-Werkzeuge, Plugin-Werkzeuge …) laufen in der Bridge. Ein Watchdog beendet bei Stille oder `MAX_CALL_SECONDS`.
6. **Nach dem Gespräch:** `GET /internal/after` sagt dem Dialplan, ob weiterverbunden wird (`dial:`/`rec:`); schlägt das Ziel fehl, ruft der Dialplan
   `/internal/busy` und die KI nimmt den Anrufer zurück. Danach: Auswertung (Zusatzinfos), E-Mail-Benachrichtigung, Plugin-Ereignis `call_end`.
7. `h`-Erweiterung → `/internal/rec-done` (Aufräumen, Nachtranskription, Meldung unbeantworteter Anrufe).

**Rückfrage vor dem Verbinden:** Verlangt der Anrufer einen Benutzer und ist beim Agenten „Vor dem Verbinden nachfragen“ an, antwortet `/internal/after` mit `sdial:<Ziel>:<Klingelzeit>:<Musikklasse>`. Der Dialplan wählt das Ziel mit `Dial(…,m(<Klasse>)U(screen^<uuid>))`: Der Anrufer hört Wartemusik (MoH-Klasse, siehe *Wartemusik*), und sobald das Ziel abhebt, läuft `[screen]` auf dessen Kanal. Es ruft `/internal/screen-start` (die Bridge legt eine zweite KI-Sitzung „Rückfrage“ mit den Werkzeugen `accept`/`decline`/`voicemail` an) und verbindet den Kanal per AudioSocket mit ihr. `accept()` gilt nur, wenn das Transkript der Antwort nach der Frage ein Ja-Wort ohne Verneinung enthält (`screen_consent` in `call.py`); das Anliegen des Anrufers steht im Prompt als gekürztes, markiertes Zitat. Danach fragt der Dialplan `/internal/screen-result`: bei `accept` geht es weiter (Verbinden), sonst setzt er `GOSUB_RESULT=CONTINUE`. Dial endet dann mit `DIALSTATUS=ANSWER` ohne `ANSWEREDTIME`; der Dialplan ruft `/internal/busy` und die KI nimmt den Anrufer zurück.

**Wartemusik** (`hold.py`): Die Stücke liegen in `data/sounds/moh/lib/` (Bibliothek, 8 kHz, dazu `.wav16` für G.722), `play/` enthält die Wiedergabe-Kopien mit eingestellter Lautstärke,
`tones/` die Freizeichen-Töne. Je Stück erzeugt die Bridge MoH-Klassen als Verzeichnisse mit Verweisen unter `data/sounds/moh/c/<klasse>/` (`m_<id>` Musik ab diesem Stück,
`r_<id>` zuerst Freizeichen, `default` alle eingeschalteten) und schreibt `data/asterisk/moh.conf` (Asterisk: `musiconhold.conf` → `#include "dyn/moh.conf"`); danach `moh reload` per AMI.
`asterisk/render.py` legt eine Klasse `default` mit Stille an, damit Asterisk auch vor der Bridge startet.

## Zustand je Anruf
Alles, was die Bridge sich zu einem laufenden Anruf merkt, steht in **einem** `CallRecord` je Anruf-UUID in der Registry `CALLS`
(`bridge/app/callstate.py`), nicht mehr in getrennten Wörterbüchern. Felder: `chan` (Asterisk-Kanal), `ringing` (Anzeige „klingelt“), `pending`
(Anmeldung der nächsten KI-Sitzung), `fallback` („niemand nimmt ab → KI“), `retarget` (Ziel für `/internal/busy`), `after` (Antwort für
`/internal/after`), `active` (laufende KI-Sitzung), `screen`/`screen_tried` (Rückfrage), `human` (Gespräch zwischen Menschen), `defer_post`
(Auswertung/Mail erst beim Auflegen). Die Rückfrage-KI hat einen eigenen Eintrag (`kind='screen'`).

| Phase | Bedeutung | Eintritt |
|---|---|---|
| `klingelt` | Telefone klingeln, noch keine KI | `/internal/call` → `fdial`/`recx`/`dial`/`wait`, KI-Sitzung endet mit `dial`/`rec` |
| `ki` | KI-Sitzung angemeldet oder laufend | `/internal/call` → `ok`, `/internal/fallback`, `/internal/busy`, AudioSocket |
| `rueckfrage` | KI fragt bei der angerufenen Person nach | KI-Sitzung endet mit `sdial` |
| `verbunden` | Gespräch zwischen Menschen | `/internal/answered`, `/internal/rec-start`, `screen-result` = `accept` |
| `beendet` | Asterisk hat aufgelegt | `/internal/rec-done` |

Unerwartete Übergänge werden nur protokolliert, nie verhindert. **Aufräumen an einer Stelle:** `CALLS.expire()` (Janitor, alle 15 s) mit den
Ablaufzeiten in `callstate.TTL` (z. B. `after` 120 s, `fallback` 300 s, `pending` 90 s → Anruf „abgebrochen“); `CALLS.finish()` beim Auflegen leert
alles außer einer noch laufenden KI-Sitzung. Eine neue KI-Sitzung löscht ein altes `after` (sonst würde nach einer abgelehnten Rückfrage erneut
verbunden). Leere Einträge verschwinden sofort.

## Protokoll Bridge ↔ Dialplan
Der Dialplan fragt per CURL (`/internal/*`) und bekommt eine Zeile Klartext: Schlüsselwort und Felder mit `:` getrennt, die er mit `${RES:n}` und
`${CUT(RES,:,n)}` zerlegt. **Einzige Stelle**, die diese Zeilen baut und liest, ist `bridge/app/protocol.py` (Klassen `Dial`, `FDial`, `RecX`,
`Rec`, `SDial`, `Wait`, Konstanten `OK`, `NO_AGENT`, `HANGUP`, Parser `parse()`; Rundlauf-Tests in `tests/test_protocol.py`).

| Antwort | Felder (n = Position für `CUT(RES,:,n)`) | Wirkung im Dialplan |
|---|---|---|
| `ok` | – | KI nimmt an (`answer`: `Answer` + `AudioSocket`) |
| `wait:<s>` | 2 = Sekunden | `Ringing`, `Wait`, dann KI |
| `no-agent` | – | nicht annehmen (`Hangup(19)`, ausgehend `Hangup(21)`) |
| `hangup` | – | nur `/internal/after`: auflegen |
| `dial:<ziele>[:<moh>]` | 2 = Ziele, 3 = Musikklasse | durchstellen; ohne Annahme → `/internal/busy` |
| `fdial:<ring>:<ziele>[:<moh>[:<verz>:<cid>:<töne>]]` | 2 = Klingelzeit (leer = 25 s), 3 = Ziele, 4 = Musik, 5–7 = `ringprep` | Asterisk nimmt an und lässt klingeln; ohne Annahme → `/internal/fallback` (KI) |
| `recx:<flags>:<ziele>[:<ring>[:<moh>[:<verz>:<cid>:<töne>]]]` | 2 = Flags `o`/`q`/`f`, 3 = Ziele, 4 = Klingelzeit, 5 = Musik, 6–8 = `ringprep` | Aufnahme ab Annahme (o = ausgehend, q = ohne Hinweis, f = KI-Rückfall) |
| `rec:<ziele>` | Rest = Ziele | nach der KI durchstellen mit Mitschrift |
| `sdial:<ziele>:<ring>:<moh>:<flags>` | 2 = Ziele, 3 = Klingelzeit, 4 = Musik, 5 = `r`/`q` | Rückfrage vor dem Verbinden (`[screen]`) |
| Rückfrage | `screen-start`: `ok`/`skip`; `screen-result`: `accept`/`decline`/`voicemail`/`none` | nur `accept` verbindet |

Ziele: Nummern mit `+` (klingeln gleichzeitig), je nur Ziffern (3–20) oder `**N`. `ringprep`-Felder stehen nur da, wenn eines gesetzt ist.

**JSON statt Klartext?** Asterisk 23.5 im Image hat `func_json`; `JSON_DECODE` liest eine per `CURL` geholte JSON-Antwort im Dialplan
korrekt (lokal bewiesen: `tests/integration`, `test_json_decode_available_in_dialplan`). Umgestellt ist trotzdem **nicht**: Der Dialplan müsste
in einer Übergangsphase beide Formen verstehen (jede Verzweigung doppelt), dazu kommen die per AMI-`Redirect` gesetzten Variablen (`RES`) und
das Rendern bei jedem Teil-Deploy. Der Gewinn an Lesbarkeit liegt jetzt im zentralen Modul; eine spätere Umstellung ändert in der Bridge nur `protocol.py`.

**Klingelnde Anrufe lenken:** Die Bridge merkt sich den Asterisk-Kanal je Anruf und leitet ihn per AMI (`Setvar` + `Redirect` auf die Labels
`answer`/`direct`/`recdial`) um.

**Ausgehend:** `AMI Originate` wählt `PJSIP/<nr>@fritz`; nach dem Abheben läuft `[outbound-ai]` mit AudioSocket. **Mitschrift-Gespräche:** erst klingelt der eigene
Anschluss, dann wählt `[rec-bridge]` das Ziel; `MixMonitor` schreibt je Seite eine Spur (`-me`, `-peer`), die Bridge transkribiert fast live und am Ende vollständig.

## Module der Bridge
| Datei | Inhalt |
|---|---|
| `main.py` | API, Anmeldung, `/internal/*`, Live-Ereignisse (WebSocket für Admins, SSE für alle), Sicherheits-Header, Hintergrundaufgaben (Janitor mit Aufräumen, Wiederherstellung) |
| `callstate.py` | Zustand je Anruf (`CallRecord`, Registry `CALLS`, Phasen, Ablaufzeiten) |
| `protocol.py` | Antworten an den Dialplan bauen und lesen (einzige Stelle) |
| `astconf.py` | Asterisk-Dateien aus den Einstellungen (`fritz.conf`), Migrationen adopt/upgrade |
| `flow.py` | Ablauf je Anruf (Tabelle `call_events`, Texte für die Zeitleiste) |
| `call.py` | Ein Gespräch: Audio, Gemini-Sitzung, Werkzeuge, Prompt, Aufnahme, Übergabe/Weiterleitung, Watchdog, Kosten |
| `db.py` | Schema, Migrationen, Zugriff; Benutzer-/Regel-/Erreichbarkeitslogik |
| `agents.py` | Auswahl über die Anrufregeln, Aufzeichnungsregeln (`rec_flags`), Codewort-/Stimmungs-Auslöser |
| `dialrules.py`, `schedule.py`, `hours.py` | Wählregeln, Zeitplan, Geschäftszeiten |
| `remote.py` | Fernabfrage (Erkennung, Anrufliste, Zugriffsprüfung) |
| `analyze.py` | Zusatzinfos zu Gesprächen (Textmodell, JSON-Schema) |
| `notify.py` | SMTP, Anruf-Mails, Tagesbericht |
| `rectrans.py`, `audio.py`, `voices.py` | Mitschrift/Transkription, Resampler, Ansagen (Live-TTS, zwischengespeichert) |
| `ami.py`, `fritz.py` | Asterisk-Manager (Originate, Redirect, AMI-Aktionen, CLI-Befehle), Fritzbox TR-064 (Telefonbuch, Rufnummern, interne Anschlüsse) |
| `config.py` | Verbindungs-Einstellungen aus der Oberfläche (meta `cfg:*`, Vorrang vor der `.env`) |
| `fritzconf.py` | `data/asterisk/fritz.conf` (SIP-Zugang, IP-Sperre `type=acl`, `identify_by=ip`, Endpoints der Klingel-Rufnummern), Umstellung alter Dateien |
| `hold.py` | Wartemusik: Bibliothek, Lautstärke, MoH-Klassen, `moh.conf` |
| `costguard.py` | Kostenbremse (Anrufe/Minuten je Rufnummer, KI-Minuten pro Tag) |
| `models.py`, `rate.py` | Gemini-Modelle und Preise, Wechselkurs USD→EUR |
| `mp3.py` | Aufnahmen als MP3 (lameenc/miniaudio) |
| `gcal.py`, `staffcal.py` | Google Kalender (Dienstkonto), Kalender der Benutzer |
| `numinfo.py` | Herkunft einer Rufnummer (Ort, Art) ohne Internet |
| `logmask.py` | Rufnummern/Mailadressen im Log kürzen, Zugriffslog-Filter |
| `events.py` | Ereignis-Verteiler für Live-Anzeige (WebSocket/SSE) |
| `users.py` | Konten, scrypt, Sitzungen, Sichtbarkeit nach Rufnummer |
| `plugins.py` | Plugin-Lader (eigene Plugins nur nach Freigabe) und Schnittstelle |
| `plugins_builtin/campaigns` | Kampagnen-Plugin |

## Datenmodell (SQLite)
`calls` (Anruf, Status, Kosten, `notes`, `meta`), `segments` (Transkript), `call_events` (Ablauf: `call_id`, `ts`, `kind`, `data` als JSON; `flow.py`), `agents` (KI-Persönlichkeiten), `call_rules` (Anrufregeln) und `rec_rules` (Aufzeichnungsregeln), `staff` (inkl. Zeitplan, Wählregeln, Fernabfrage),
`contacts`, `templates`, `users`/`sessions`, `meta` (Einstellungen, Schlüssel/Wert), `usage_extra` (Kosten ohne Anruf),
`mail_recipients`, sowie die Tabellen des Kampagnen-Plugins (`campaigns`, `campaign_targets`, `slot_pools`, `slots`).
Schemaänderungen laufen als einfache Migrationsliste (`MIGRATIONS` in `db.py`: Spalte anlegen, wenn sie fehlt).

## Sicherheit
- **Konten:** Formular-Anmeldung, Passwörter mit scrypt, Cookie `lm_session` (HttpOnly, SameSite=Strict), Rollen *admin*/*user*, nur-ASCII-Benutzernamen,
  Sperre nach Fehlversuchen. Basic-Auth (admin + `UI_PASSWORD`) als Reserve für Skripte nur mit `ALLOW_BASIC=1` (Standard aus).
- **CSRF:** Schreibende Aufrufe verlangen passenden `Origin` und JSON-Inhaltstyp; WebSocket prüft Origin und Anmeldung.
- **Sichtbarkeit:** Benutzer sehen nur Anrufe auf ihnen zugewiesene Rufnummern (auch im Live-Banner und per API).
- **Wählschutz:** Nur Ziffern/interne `**N`; Sperrpräfixe `DIAL_BLOCK` (Standard 0900, 0137, 0180, 0190, 118, 00); KI darf nur freigegebene Anrufer
  wählen lassen; Rückruf/Verbinden nur zu Kontakten, Benutzern oder Anrufern aus der eigenen Liste. Eingeschränkte Konten: eigenes Gerät nur intern oder vom Admin
  eingetragen (`staff.number_admin`), Durchstellen/Umleiten nur an interne Nummern, Benutzer und Kontakte.
- **SIP:** Asterisk nimmt SIP-Anfragen nur von der Fritzbox-IP an (`type=acl` in `fritz.conf`, Notschalter `SIP_ACL=0`), Endpoints nur über die IP erkannt (`identify_by=ip`).
- **Kosten:** Kostenbremse je Agent/global (`costguard.py`), Höchstdauer je Agent oder `MAX_CALL_SECONDS`.
- **AMI:** nur `127.0.0.1`; Rechte `originate,call,reporting,command` (`command` noch für einige CLI-Befehle nötig, siehe `manager.conf.tpl`).
- **HTTP-Header:** `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, schmale CSP ohne `script-src` (Inline-UI).
- **Fernabfrage:** Rufnummer *und* Codewörter; Codewörter im Transkript geschwärzt.
- **Aufnahmen:** Ankündigung vor Beginn; Rohspuren werden nach der Transkription gelöscht.
- **Geheimnisse:** `UI_PASSWORD` und `AMI_SECRET` stehen in der `.env`. Gemini-Schlüssel, Fritzbox-Kennwörter (SIP, TR-064), **Mail-Passwort** und **Google-Dienstkonto-Schlüssel** legt die Weboberfläche im Klartext in der Datenbank ab (`data/lieschen.db`, meta), das SIP-Kennwort zusätzlich in `data/asterisk/fritz.conf`: den Ordner `data/` und Sicherungen davon schützen.
- **Interne Aufrufe:** Die Anfragen des Dialplans an die Bridge (`/internal/*`) kommen nur von `127.0.0.1` und tragen einen aus `AMI_SECRET` abgeleiteten Schlüssel (`k=…`, von `asterisk/render.sh` eingesetzt). Ein Reverse-Proxy darf `/internal/` nicht weiterreichen.
- **Grenzen:** kein HTTPS im Lieferumfang; Plugins sind Vertrauenscode im selben Prozess (eigene erst nach Freigabe durch einen Admin); Bridge-Container läuft als root.

## Tests
Die Testmappe `tests/` (pytest) läuft vollständig in podman und braucht weder Netz noch Zugangsdaten:
```
tests/run.sh                  # Unit- und Szenariotests (Container ohne Netz)
tests/run.sh --asterisk       # zusätzlich: echte lokale Asterisk-Instanz (eigener Pod ohne Netz, keine Fritzbox)
tests/run.sh -- -k rueckfrage # Argumente nach -- gehen an pytest
```
`run.sh` baut das Image `lm-test` aus `bridge/Dockerfile` und darauf `lm-test-pytest` (`tests/Containerfile`, nur pytest dazu); mit `LM_SKIP_BUILD=1`
werden vorhandene Images benutzt. Das Repository wird nur lesend eingebunden, Datenbank und Datenordner sind temporär (je Test frisch).
- **Fakes** (`tests/fakes.py`): `FakeGenai` ersetzt den Gemini-Live-Client (skriptbare Sitzungen: KI spricht, Anrufer spricht, Werkzeugaufrufe wie
  `connect_employee`, `accept`/`decline`/`voicemail`, `end_call`, `save_answer`), `FakeAudioSocket` spielt Asterisk am AudioSocket (Anmeldung, Audio,
  Hangup-Frame), `AmiStub` ersetzt die AMI-Funktionen (wählt nie), Stimmen/TTS liefern Stille, Mails werden nur aufgezeichnet.
- **Szenarien** schreiben das Verhalten fest: KI-Anruf (Hinweis-Ansage, Begrüßung, Notiz, Ende), Rangfolge „nicht stören“ > Wählregel > Zeitplan > Status > Vertretung > KI,
  mehrere Ziele (`n1+n2`), Weiterleiten ohne/mit Rückfrage (Zusage, Ablehnung → KI nimmt wieder an und verbindet nicht erneut, Mailbox, keine Antwort),
  Kostenbremse, Aufzeichnung (Anhängen an die erste Aufnahme, Mono, `rec-done` blockiert nicht), Mail-Größenlimit, Antwort-Strings für den Dialplan.
- Unbehandelte Fehler in Hintergrundaufgaben lassen einen Test scheitern (`tests/conftest.py`, `run`).

## Robustheit
Beim Start werden hängende Anrufe als abgebrochen markiert und Aufnahmen nachgearbeitet; ein Aufräumer (Janitor) entfernt nie verbundene Anrufe und alte Zustände,
löscht abgelaufene Sitzungen, alte Rohaufnahmen (nach Aufbewahrungsfrist) und hält den Stimmproben-Cache klein;
der Hangup-Frame verhindert, dass Asterisk den Kanal beim Durchstellen abbricht; Fehler in Plugins werden abgefangen.
