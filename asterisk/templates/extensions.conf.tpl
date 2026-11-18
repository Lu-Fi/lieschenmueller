; Antworten der Bridge auf /internal/* (ok, wait:, dial:, fdial:, recx:, rec:, sdial:, no-agent, hangup): Felder und Bedeutung
; stehen in bridge/app/protocol.py (einzige Stelle in der Bridge, die sie baut) und in docs/ARCHITEKTUR.md.
; Globale aus den Einstellungen (REC_PREFIX = Vorwahl fuer aufgezeichnete ausgehende Gespraeche, meta rec_prefix): schreibt die Bridge
; nach data/asterisk/globals.conf (astconf.write_globals, render.py legt sie an). #tryinclude: fehlt die Datei, laeuft der Dialplan trotzdem.
#tryinclude "dyn/globals.conf"

[from-fritz]
; h endet explizit: sonst sucht Asterisk h:2 und findet das Muster _. (Phantom-Anrufe)
exten => h,1,NoOp(aufgelegt)
 same => n,GotoIf($["${CALLUUID}" = ""]?end)
 same => n,Set(RES=${CURL(http://127.0.0.1:${BRIDGE_PORT}/internal/rec-done?k=@INTERNAL_KEY@&uuid=${CALLUUID})})
 same => n(end),Hangup()

; Jeder eingehende Anruf wird der Bridge gemeldet (Anrufer/Ziel/Header).
; Antwort: "ok" -> KI (AudioSocket), "dial:<nr>" -> direkt durchstellen.
exten => _.,1,NoOp(Anruf von ${CALLERID(num)} an ${EXTEN})
 same => n,Set(CALLUUID=${UUID()})
 ; Fehlersuche: beim Auflegen die Leitungswerte (RTCP) dieses Kanals an die Bridge melden ([lm-qos], Ablauf "Leitungswerte")
 same => n,Set(CHANNEL(hangup_handler_push)=lm-qos,s,1(${CALLUUID},a))
 same => n,Set(ORIG=${EXTEN})
 same => n,Set(CHANNEL(tonezone)=de)      ; Freizeichen/Tone wie in Deutschland (sonst US)
 ; Ausgehendes Gespraech eines Telefons ueber Asterisk (Wahlregel der Fritzbox: Vorwahl -> Rufnummer 99NNN): Absender ist die Rufnummer 99NNN
 same => n,GotoIf($["${REGEX("^99[0-9]{3}$" ${CALLERID(num)})}" = "1"]?outcall)
 same => n,Set(RES=${CURL(http://127.0.0.1:${BRIDGE_PORT}/internal/call?k=@INTERNAL_KEY@&uuid=${CALLUUID}&chan=${URIENCODE(${CHANNEL})}&from=${URIENCODE(${CALLERID(num)})}&to=${URIENCODE(${EXTEN})}&hdr=${URIENCODE(${PJSIP_HEADER(read,To)} ${PJSIP_HEADER(read,P-Called-Party-ID)})})})
 same => n,GotoIf($["${RES:0:5}" = "dial:"]?direct)
 same => n,GotoIf($["${RES:0:6}" = "fdial:"]?fdirect)
 same => n,GotoIf($["${RES:0:4}" = "rec:"]?recfirst)
 same => n,GotoIf($["${RES:0:5}" = "recx:"]?recx)
 same => n,GotoIf($["${RES:0:5}" = "wait:"]?waitans)
 same => n,GotoIf($["${RES}" != "ok"]?reject)
 ; Gespraech mit der KI: eine abgesenkte Wartemusik-Lautstaerke zuruecksetzen (auch nach Umleiten aus der Oberflaeche)
 same => n(answer),ExecIf($["${MDB}" != "0" & "${MDB}" != ""]?Set(VOLUME(TX)=0))
 same => n,Answer()
 same => n,AudioSocket(${CALLUUID},127.0.0.1:9092)
; Nach dem Gespraech: Bridge sagt, ob durchgestellt werden soll (ausgehende KI-Anrufe springen aus [outbound-ai] nach afterai)
 same => n,Set(RES=${CURL(http://127.0.0.1:${BRIDGE_PORT}/internal/after?k=@INTERNAL_KEY@&uuid=${CALLUUID})})
 same => n(afterai),GotoIf($["${RES:0:5}" = "dial:"]?direct)
 same => n,GotoIf($["${RES:0:6}" = "sdial:"]?sdirect)
 same => n,GotoIf($["${RES:0:4}" = "rec:"]?recdial)
 same => n,Hangup()
 ; Durchstellen mit Anrufbeantworter-Rueckfall: nimmt niemand ab (oder besetzt/nicht erreichbar), uebernimmt die KI
 ; RES = fdial:<Klingelzeit in s oder leer>:<Ziele>; die Klingelzeit kommt vom Agenten, der danach uebernimmt (Zeit bis zur Anrufannahme)
 ; Asterisk nimmt selbst an (die Fritzbox klingelt dann nicht mehr selbst) und fuehrt das Klingeln; Freizeichen fuer den Anrufer (Option r)
 same => n(fdirect),Answer()
 same => n,Gosub(build-targets,s,1(${CUT(RES,:,3)},ring))
 same => n,Set(CALLERID(name)=${CALLERID(num)})
 same => n,Gosub(ringprep,s,1(${CUT(RES,:,4)},${CUT(RES,:,5)},${CUT(RES,:,6)},${CUT(RES,:,7)}))
 same => n,Set(RNG=${CUT(RES,:,2)})
 ; Freizeichen fuer den Anrufer (Option r) oder, wenn eingestellt (Flag m), Wartemusik
 same => n,Dial(${TGT},${IF($["${RNG}" = ""]?25:${RNG})},${IF($["${CUT(RES,:,4)}" != ""]?m(${CUT(RES,:,4)}):r)}U(answered^${CALLUUID})b(lm-pre^s^1(${CALLUUID})))
 same => n,ExecIf($["${MDB}" != "0" & "${MDB}" != ""]?Set(VOLUME(TX)=0))
 same => n,GotoIf($["${DIALSTATUS}" = "ANSWER"]?fend)
 same => n,Set(RES=${CURL(http://127.0.0.1:${BRIDGE_PORT}/internal/fallback?k=@INTERNAL_KEY@&uuid=${CALLUUID}&status=${DIALSTATUS})})
 same => n,GotoIf($["${RES}" = "ok"]?answer)
 same => n(fend),Hangup()
 ; mehrere Ziele (n1+n2) klingeln gleichzeitig: das Unterprogramm baut daraus PJSIP/n1@fritz&PJSIP/n2@fritz
 ; Durchstellen: dial:<Ziele>[:<Wartemusik-Klasse>]; mit Klasse hoert der Anrufer Musik, solange das Ziel klingelt
 same => n(direct),ExecIf($["${MDB}" != "0" & "${MDB}" != ""]?Set(VOLUME(TX)=0))
 same => n,Set(DT=${RES:5})
 same => n,Gosub(build-targets,s,1(${CUT(DT,:,1)}))
 same => n,Set(DMOH=${CUT(DT,:,2)})
 same => n,Dial(${TGT},60,${IF($["${DMOH}" = ""]?:m(${DMOH}))}b(lm-pre^s^1(${CALLUUID})))
 same => n,GotoIf($["${DIALSTATUS}" = "ANSWER"]?dend)
 ; Ziel besetzt/nicht erreichbar/niemand nimmt ab: zurueck zur KI (sagt es dem Anrufer, bietet eine Nachricht an)
 same => n,Set(RES=${CURL(http://127.0.0.1:${BRIDGE_PORT}/internal/busy?k=@INTERNAL_KEY@&uuid=${CALLUUID}&status=${DIALSTATUS})})
 same => n,GotoIf($["${RES}" = "ok"]?answer)
 same => n(dend),Hangup()
 ; Durchstellen mit Rueckfrage: RES = sdial:<Ziele>:<Klingelzeit>:<Wartemusik-Klasse>. Der Anrufer hoert waehrend des Klingelns und der Rueckfrage Musik (Option m);
 ; wenn das Ziel abnimmt, fragt [screen] (zweite KI) die Person, ob sie den Anruf annehmen moechte. Bei Ablehnung/Mailbox/keiner Antwort
 ; endet die Rueckfrage mit GOSUB_RESULT=CONTINUE: Dial endet dann mit DIALSTATUS=ANSWER, aber ohne ANSWEREDTIME -> zurueck zur KI
 same => n(sdirect),Set(SF=${RES:6})
 same => n,Gosub(build-targets,s,1(${CUT(SF,:,1)}))
 same => n,Set(SRING=${CUT(SF,:,2)})
 same => n,Set(SMOH=${CUT(SF,:,3)})
 same => n,Set(SFL=${CUT(SF,:,4)})
 ; Aufzeichnung des verbundenen Gespraechs (Flags: r = aufnehmen, q = ohne Hinweis-Ansage): der Anrufer hoert den Hinweis vor dem Warten,
 ; die angerufene Person nach der Zusage (in [screen]); Option b nimmt erst auf, wenn beide verbunden sind
 same => n,GotoIf($["${REGEX("r" ${SFL})}" != "1"]?sdial)
 same => n,GotoIf($["${REGEX("q" ${SFL})}" = "1"]?srec)
 same => n,Playback(/sounds/rec_notice)
 same => n(srec),MixMonitor(/recordings/${CALLUUID}-mix.wav,br(/recordings/${CALLUUID}-peer.wav)t(/recordings/${CALLUUID}-me.wav))
 same => n(sdial),Dial(${TGT},${IF($["${SRING}" = ""]?25:${SRING})},${IF($["${SMOH}" = ""]?r:m(${SMOH}))}U(screen^${CALLUUID}^${SFL})b(lm-pre^s^1(${CALLUUID})))
 same => n,GotoIf($["${DIALSTATUS}" = "ANSWER" & "${ANSWEREDTIME}" != ""]?dend)
 same => n,Set(RES=${CURL(http://127.0.0.1:${BRIDGE_PORT}/internal/busy?k=@INTERNAL_KEY@&uuid=${CALLUUID}&status=${DIALSTATUS})})
 same => n,GotoIf($["${RES}" = "ok"]?answer)
 same => n,Hangup()
 ; Weiterleitung mit Mitschrift im laufenden Anruf: beide Seiten getrennt aufnehmen, der Angerufene hoert vorher einen festen Hinweis
 ; Anruf mit Mitschrift von Anfang an (Rufnummer ist einem Mitarbeiter zugeordnet): erst Hinweis an den Anrufer, dann aufnehmen
 same => n(recfirst),Answer()
 same => n,Playback(/sounds/rec_notice)
 same => n,Goto(recdial)
 same => n(recdial),ExecIf($["${MDB}" != "0" & "${MDB}" != ""]?Set(VOLUME(TX)=0))
 ; Aufnahme erst ab dem Abheben (Option b; der Start wird beim Abheben gemeldet, U), nicht schon das Freizeichen
 same => n,Set(RCHAN=${CHANNEL})
 same => n,MixMonitor(/recordings/${CALLUUID}-mix.wav,br(/recordings/${CALLUUID}-me.wav)t(/recordings/${CALLUUID}-peer.wav))
 same => n,Gosub(build-targets,s,1(${RES:4}))
 same => n,Dial(${TGT},60,A(/sounds/rec_notice)U(recdstart^${CALLUUID}^${RCHAN})b(lm-pre^s^1(${CALLUUID})))
 same => n,GotoIf($["${DIALSTATUS}" = "ANSWER"]?rdend)
 ; Ziel besetzt/nicht erreichbar/niemand nimmt ab: Aufnahme beenden und zurueck zur KI (wie beim Durchstellen ohne Mitschrift)
 same => n,StopMixMonitor()
 same => n,Set(RES=${CURL(http://127.0.0.1:${BRIDGE_PORT}/internal/busy?k=@INTERNAL_KEY@&uuid=${CALLUUID}&status=${DIALSTATUS})})
 same => n,GotoIf($["${RES}" = "ok"]?answer)
 same => n(rdend),Hangup()
 ; from = Absender 99NNN (welches Telefon): die Bridge erkennt daran den Benutzer ("Ausgehende Gespraeche mitschreiben", outrec.py)
 same => n(outcall),Set(RES=${CURL(http://127.0.0.1:${BRIDGE_PORT}/internal/outcall?k=@INTERNAL_KEY@&uuid=${CALLUUID}&chan=${URIENCODE(${CHANNEL})}&from=${URIENCODE(${CALLERID(num)})}&to=${URIENCODE(${EXTEN})})})
 same => n,GotoIf($["${RES:0:5}" = "recx:"]?recx)
 same => n,GotoIf($["${RES:0:5}" = "dial:"]?direct)
 same => n,GotoIf($["${RES}" = "no-agent"]?outrej)
 ; Die Bridge antwortet nicht (leer/Fehler): ohne Aufnahme ganz normal wählen, damit das Telefonieren nie am Ausfall hängt
 ; Vorwahl entfernen: die konfigurierte (Global REC_PREFIX aus dyn/globals.conf); ohne die Datei die Voreinstellung *77
 same => n,Set(RP=${IF($["${GLOBAL(LM_GLOBALS)}" = ""]?*77:${GLOBAL(REC_PREFIX)})})
 same => n,Set(FBN=${EXTEN})
 same => n,ExecIf($["${RP}" != "" & "${EXTEN:0:${LEN(${RP})}}" = "${RP}"]?Set(FBN=${EXTEN:${LEN(${RP})}}))
 same => n,GotoIf($["${REGEX("^[0-9*#+]+$" ${FBN})}" != "1"]?outrej)
 same => n,NoOp(Bridge nicht erreichbar: ${FBN} wird ohne Aufnahme gewaehlt)
 same => n,Dial(PJSIP/${FBN}@fritz,60,r)
 same => n,Hangup()
 same => n(outrej),Hangup(21)
 ; Anruf mit Aufnahme, von Asterisk gefuehrt: recx:<flags>:<Ziele>[:<Klingelzeit>]   flags: q = ohne Hinweis-Ansage, f = KI uebernimmt, wenn niemand abnimmt,
 ; o = ausgehend (Anrufer ist der Mitarbeiter: kein Hinweis an ihn, Spuren me/peer nicht vertauscht)
 ; Asterisk nimmt an (die Fritzbox klingelt dann nicht mehr selbst), waehlt die Ziele (wenn es zu einem Ziel **NNN eine bei Asterisk
 ; angemeldete Rufnummer 99NNN gibt, ueber diese: dann zeigt das Telefon die Nummer des Anrufers) und zeichnet ab Annahme auf
 same => n(recx),Set(RF=${CUT(RES,:,2)})
 same => n,Set(RNUM=${CUT(RES,:,3)})
 same => n,Answer()
 same => n,GotoIf($["${REGEX("[qo]" ${RF})}" = "1"]?recxgo)
 same => n,Playback(/sounds/rec_notice)
 same => n(recxgo),NoOp(Aufnahme startet erst, wenn abgenommen wird)
 ; r = was der Anrufer sagt: ausgehend ist das der Mitarbeiter (me), bei eingehenden Anrufen die Gegenseite (peer)
 same => n,Set(MM=${IF($["${REGEX("o" ${RF})}" = "1"]?r(/recordings/${CALLUUID}-me.wav)t(/recordings/${CALLUUID}-peer.wav):r(/recordings/${CALLUUID}-peer.wav)t(/recordings/${CALLUUID}-me.wav))})
 ; Option b: nur aufnehmen, solange der Kanal mit dem Gegenueber verbunden ist (nicht das Freizeichen); der Start wird beim Abnehmen gemeldet (U)
 same => n,MixMonitor(/recordings/${CALLUUID}-mix.wav,b${MM})
 same => n,Gosub(build-targets,s,1(${RNUM},ring))
 same => n,Set(CALLERID(name)=${CALLERID(num)})
 same => n,Set(RMC=${CUT(RES,:,5)})
 same => n,Gosub(ringprep,s,1(${RMC},${CUT(RES,:,6)},${CUT(RES,:,7)},${CUT(RES,:,8)}))
 same => n,Set(OPTS=${IF($["${RMC}" != ""]?m(${RMC}):r)}U(recxstart^${CALLUUID})${IF($["${REGEX("q" ${RF})}" = "1"]?:A(/sounds/rec_notice))})
 same => n,Set(RING=${CUT(RES,:,4)})
 same => n,Set(DT=60)
 same => n,GotoIf($["${REGEX("f" ${RF})}" != "1"]?dtdone)
 same => n,Set(DT=${IF($["${RING}" = ""]?30:${RING})})
 same => n(dtdone),NoOp(Klingelzeit ${DT} s)
 same => n,Dial(${TGT},${DT},${OPTS}b(lm-pre^s^1(${CALLUUID})))
 same => n,ExecIf($["${MDB}" != "0" & "${MDB}" != ""]?Set(VOLUME(TX)=0))
 same => n,GotoIf($["${DIALSTATUS}" = "ANSWER"]?recxend)
 same => n,GotoIf($["${REGEX("f" ${RF})}" != "1"]?recxend)
 same => n,Set(RES=${CURL(http://127.0.0.1:${BRIDGE_PORT}/internal/fallback?k=@INTERNAL_KEY@&uuid=${CALLUUID}&status=${DIALSTATUS})})
 ; Rueckfall zur KI: die Mitschrift der Mitarbeiter-Leitung beenden, sonst nimmt sie spaeter (KI verbindet weiter) Dritte ohne Hinweis auf
 same => n,GotoIf($["${RES}" != "ok"]?recxend)
 same => n,StopMixMonitor()
 same => n,Goto(answer)
 same => n(recxend),Hangup()
 ; Anrufbeantworter-Verhalten: erst klingeln lassen. Nimmt ein anderes Geraet ab (Fritzbox beendet unser Klingeln),
 ; endet der Anruf hier, der Aufraeumer der Bridge ist dann der h-Zweig.
 ; wait:<s>[:<Musik>[:<Verzoegerung>:<cid>:<Toene>]] - ohne Musik klingelt die Fritzbox selbst (Ringing); mit Musik nimmt Asterisk an und spielt sie (kein Telefon klingelt)
 same => n(waitans),GotoIf($["${CUT(RES,:,3)}" != ""]?wmoh)
 same => n,Ringing()
 same => n,Wait(${CUT(RES,:,2)})
 same => n,Goto(answer)
 same => n(wmoh),Answer()
 same => n,Gosub(ringprep,s,1(${CUT(RES,:,3)},${CUT(RES,:,4)},${CUT(RES,:,5)},${CUT(RES,:,6)}))
 same => n,Set(WS=${CUT(RES,:,2)})
 same => n,Set(WD=${IF($["${CUT(RES,:,4)}" = ""]?0:${CUT(RES,:,4)})})
 same => n,MusicOnHold(${CUT(RES,:,3)},${IF($[${WS} > ${WD}]?$[${WS} - ${WD}]:1)})
 same => n,ExecIf($["${MDB}" != "0" & "${MDB}" != ""]?Set(VOLUME(TX)=0))
 same => n,Goto(answer)
 ; Nicht annehmen (kein Answer): Ursache 19 = 480, die Fritzbox laesst die anderen Geraete weiter klingeln
 same => n(reject),Hangup(19)

; Ausgehende Anrufe, die die Bridge per AMI ausloest (Variable CALLUUID).
; Laeuft erst, wenn die Gegenseite abgehoben hat.
[outbound-ai]
exten => s,1,NoOp(ausgehend ${CALLUUID})
 same => n,AudioSocket(${CALLUUID},127.0.0.1:9092)
 same => n,Set(RES=${CURL(http://127.0.0.1:${BRIDGE_PORT}/internal/after?k=@INTERNAL_KEY@&uuid=${CALLUUID})})
 ; Durchstellen (dial:, sdial:, rec:) wie bei eingehenden Anrufen: mehrere Ziele, Wartemusik, Rueckfrage, Mitschrift, bei Besetzt zurueck zur KI
 same => n,GotoIf($["${RES:0:5}" = "dial:" | "${RES:0:6}" = "sdial:" | "${RES:0:4}" = "rec:"]?from-fritz,outai,afterai)
 same => n,Hangup()

; Gespraech mit Mitschrift: Die Bridge laesst per AMI zuerst das Geraet des Nutzers klingeln
; (Variablen CALLUUID, TARGET). Nach dem Abheben waehlt Asterisk das Ziel, nimmt beide Seiten
; getrennt auf (r = Nutzer, t = Gegenseite) und spielt dem Angerufenen eine feste Hinweis-Ansage.
; Die Aufnahmen landen in /recordings (Host: data/rec-in) und werden nach dem Gespraech transkribiert.
[rec-bridge]
exten => s,1,NoOp(Mitschrift ${CALLUUID} -> ${TARGET})
 same => n,Set(RES=${CURL(http://127.0.0.1:${BRIDGE_PORT}/internal/rec-start?k=@INTERNAL_KEY@&uuid=${CALLUUID}&chan=${URIENCODE(${CHANNEL})})})
 same => n,MixMonitor(/recordings/${CALLUUID}-mix.wav,r(/recordings/${CALLUUID}-me.wav)t(/recordings/${CALLUUID}-peer.wav))
 same => n,Dial(PJSIP/${TARGET}@fritz,60,${IF($["${NOTICE}" = "0"]?:A(/sounds/rec_notice))})
 same => n,Hangup()
exten => h,1,Set(RES=${CURL(http://127.0.0.1:${BRIDGE_PORT}/internal/rec-done?k=@INTERNAL_KEY@&uuid=${CALLUUID})})
 same => n,Hangup()

; Ziele "n1+n2+..." (aus der Bridge, nur geprueft Ziffern/**NNN) in eine Dial-Liste umwandeln -> Variable TGT
[build-targets]
exten => s,1,Set(EP=fritz)      ; ARG2 "ring": Ziele **NNN ueber die bei Asterisk angemeldete Rufnummer 99NNN, wenn es sie gibt
 same => n,Set(TGT=)
 same => n,Set(I=1)
 same => n,While($[${I} <= ${FIELDQTY(ARG1,+)}])
 same => n,Set(N=${CUT(ARG1,+,${I})})
 same => n,Set(T=${IF($["${ARG2}" = "ring" & "${PJSIP_ENDPOINT(99${N:2},context)}" != ""]?PJSIP/99${N:2}:PJSIP/${N}@${EP})})
 ; Telefon **NNN: die Klingel-Rufnummer 99NNN nur nehmen, wenn die Fritzbox sie gerade angemeldet hat (sonst klingelt dort nichts):
 ; dann direkt intern anrufen (das Telefon zeigt dabei den Geraetenamen statt der Nummer des Anrufers)
 same => n,GotoIf($["${T:0:8}" != "PJSIP/99" | "${N:0:2}" != "**"]?btadd)
 same => n,ExecIf($["${PJSIP_DIAL_CONTACTS(99${N:2})}" = ""]?Set(T=PJSIP/${N}@${EP}))
 same => n(btadd),Set(TGT=${TGT}&${T})
 same => n,Set(I=$[${I}+1])
 same => n,EndWhile()
 same => n,Set(TGT=${TGT:1})
 same => n,Return()

; Rufnummern, die die Fritzbox bei Asterisk anmeldet: Anrufe von dort werden nicht angenommen (Schleifenschutz)
[from-fritzring]
exten => _.,1,Hangup()

; Laeuft auf dem angerufenen Kanal, sobald abgenommen wird (Dial-Option U: Kontext^Argument): der Bridge den Beginn der Aufnahme melden
[recxstart]
exten => s,1,Set(RES2=${CURL(http://127.0.0.1:${BRIDGE_PORT}/internal/rec-start?k=@INTERNAL_KEY@&uuid=${ARG1}&vchan=${URIENCODE(${CALLERCH})})})
 same => n,Return()

; Durchstellen mit Mitschrift (rec:): Start der Aufnahme beim Abheben melden, mit dem Kanal des Anrufers (fuer "Aufnahme beenden"/"Auflegen")
[recdstart]
exten => s,1,Set(RES2=${CURL(http://127.0.0.1:${BRIDGE_PORT}/internal/rec-start?k=@INTERNAL_KEY@&uuid=${ARG1}&chan=${URIENCODE(${ARG2})})})
 same => n,Return()

; Dasselbe ohne Aufnahme: der Bridge melden, dass jemand abgenommen hat (das Anzeigen "klingelt" endet)
[answered]
exten => s,1,Set(RES2=${CURL(http://127.0.0.1:${BRIDGE_PORT}/internal/answered?k=@INTERNAL_KEY@&uuid=${ARG1}&chan=${URIENCODE(${CALLERCH})})})
 same => n,Return()

; Hinweis-Ansage in ein laufendes Gespraech sprechen: Local-Kanal, der per ChanSpy (Fluestern) in den Kanal spricht; die Bridge liefert den Kanal zur Nummer
[recspy]
exten => _X.,1,Answer()
 same => n,Set(TGT=${CURL(http://127.0.0.1:${BRIDGE_PORT}/internal/spy-target?k=@INTERNAL_KEY@&id=${EXTEN})})
 same => n,GotoIf($["${TGT}" = ""]?end)
 same => n,ChanSpy(${TGT},qw)
 same => n(end),Hangup()

; Rueckfrage auf der angerufenen Seite (Dial-Option U, laeuft auf dem Kanal der angerufenen Person, bevor beide verbunden werden):
; eine KI fragt, ob der Anruf angenommen werden soll. Ergebnis accept -> weiter (Verbinden); sonst GOSUB_RESULT=CONTINUE (Anrufer geht zurueck zur KI).
[screen]
exten => s,1,NoOp(Rueckfrage zu ${ARG1})
 same => n,Set(SUUID=${UUID()})
 same => n,Set(SRES=${CURL(http://127.0.0.1:${BRIDGE_PORT}/internal/screen-start?k=@INTERNAL_KEY@&uuid=${ARG1}&suuid=${SUUID}&chan=${URIENCODE(${CHANNEL})})})
 same => n,GotoIf($["${SRES}" = "skip"]?ok)
 same => n,GotoIf($["${SRES}" != "ok"]?nope)
 same => n,AudioSocket(${SUUID},127.0.0.1:9092)
 same => n,Set(SRES=${CURL(http://127.0.0.1:${BRIDGE_PORT}/internal/screen-result?k=@INTERNAL_KEY@&uuid=${ARG1})})
 same => n,GotoIf($["${SRES}" = "accept"]?ok)
 same => n(nope),Set(GOSUB_RESULT=CONTINUE)
 same => n,Return()
 ; Zusage (oder keine Rueckfrage): Aufzeichnung beginnt (ARG2 = Flags): der Bridge melden und der Person den Hinweis sagen
 same => n(ok),GotoIf($["${REGEX("r" ${ARG2})}" != "1"]?done)
 same => n,Set(SRES2=${CURL(http://127.0.0.1:${BRIDGE_PORT}/internal/rec-start?k=@INTERNAL_KEY@&uuid=${ARG1}&vchan=${URIENCODE(${CALLERCH})})})
 same => n,GotoIf($["${REGEX("q" ${ARG2})}" = "1"]?done)
 same => n,Playback(/sounds/rec_notice)
 same => n(done),Return()

; Vor dem Klingeln ueber eine Klingel-Rufnummer 99NNN, z. B. die fuer "alle Telefone klingeln" (Argumente: Wartemusik-Klasse, Verzoegerung in s, Schreibweise der Anrufernummer, Zahl der Freizeichen-Toene):
; die Fritzbox schaltet bei eingehenden Anrufen den Ton des ersten Gespraechs manchmal nicht durch, wenn der zweite Anruf mit derselben Nummer
; sofort folgt (plus = +49..., zz = 0049...); die Verzoegerung gibt der Fritzbox Zeit, mit Musik fuer den Anrufer.
[ringprep]
exten => s,1,Set(NUM=${CALLERID(num)})
 same => n,GotoIf($["${ARG3}" != "plus"]?c2)
 same => n,Set(CALLERID(num)=${IF($["${NUM:0:1}" = "0" & "${NUM:0:2}" != "00"]?+49${NUM:1}:${NUM})})
 same => n,Goto(dly)
 same => n(c2),GotoIf($["${ARG3}" != "zz"]?dly)
 same => n,Set(CALLERID(num)=${IF($["${NUM:0:1}" = "0" & "${NUM:0:2}" != "00"]?0049${NUM:1}:${NUM})})
 ; Verzoegerung: waehrend der ersten Sekunden spielt der Dialplan selbst die Freizeichen-Toene (ARG4 = Zahl der Toene, nur bei einer Klasse r_...);
 ; die Klasse r_... enthaelt nur noch den Rest der Toene nach der Verzoegerung und danach die Musik, so beginnt die Musik zur richtigen Zeit.
 same => n(dly),GotoIf($["${ARG2}" = ""]?done)
 same => n,GotoIf($[${ARG2} <= 0]?done)
 same => n,GotoIf($["${ARG4}" = ""]?plain)
 same => n,Set(TS=${IF($[${ARG2} < ${ARG4} * 5]?${ARG2}:$[${ARG4} * 5])})
 same => n,Playtones(ring)
 same => n,Wait(${TS})
 same => n,StopPlaytones()
 same => n,GotoIf($[${ARG2} <= ${TS}]?done)
 same => n,Wait($[${ARG2} - ${TS}])
 same => n,Goto(done)
 same => n(plain),Wait(${ARG2})
 ; Wartemusik zusaetzlich leiser: VOLUME(TX) ist ein Teiler (-10 = durch 10), die Bridge liefert ihn (Einstellung "Zusatzdaempfung", 0 = aus).
 ; Bei Annahme setzt die Bridge den Pegel zurueck (answered / rec-start), nach dem Dial setzt ihn der Dialplan zurueck.
 same => n(done),Set(MDB=0)
 same => n,GotoIf($["${ARG1}" = ""]?out)
 same => n,Set(__CALLERCH=${CHANNEL})
 same => n,Set(MDB=${CURL(http://127.0.0.1:${BRIDGE_PORT}/internal/moh-db?k=@INTERNAL_KEY@)})
 same => n,ExecIf($["${MDB}" != "0" & "${MDB}" != ""]?Set(VOLUME(TX)=${MDB}))
 same => n(out),Return()

; Fehlersuche (Leitungswerte): Dial-Option b laeuft auf jedem angerufenen Kanal vor dem Waehlen und haengt [lm-qos] an dessen Auflegen
[lm-pre]
exten => s,1,Set(CHANNEL(hangup_handler_push)=lm-qos,s,1(${ARG1},b))
 same => n,Return()

; Beim Auflegen: RTCP-Zusammenfassung des Kanals (Pakete, Verluste, Jitter) an die Bridge; nur PJSIP-Kanaele, nie mit Fehler.
; Angerufene Telefone (b) melden immer, auch ohne RTCP (nicht abgenommen): Grund des Endes (HANGUPCAUSE, SIP-Antwort) und Kanal
[lm-qos]
exten => s,1,GotoIf($["${CHANNEL(channeltype)}" != "PJSIP" | "${ARG1}" = ""]?end)
 same => n,Set(Q=${CHANNEL(rtcp,all)})
 same => n,GotoIf($["${ARG2}" = "b"]?send)
 same => n,GotoIf($["${Q}" = ""]?end)
 ; SIP-Antwort nur abfragen, wenn Asterisk fuer diesen Kanal einen Grund kennt (sonst WARNING von HANGUPCAUSE)
 same => n(send),Set(TECH=)
 same => n,Set(HK=${HANGUPCAUSE_KEYS()})
 same => n,GotoIf($["${REGEX("(^|,)${CHANNEL}(,|$)" ${HK})}" != "1"]?qsend)
 same => n,Set(TECH=${HANGUPCAUSE(${CHANNEL},tech)})
 ; Ziel des angerufenen Kanals: DIALEDPEERNUMBER ist hier die Dial-Angabe (**610@fritz bzw. 99610), auch bei Direktwahl ueber fritz
 same => n(qsend),Set(QR=${CURL(http://127.0.0.1:${BRIDGE_PORT}/internal/qos?k=@INTERNAL_KEY@&uuid=${ARG1}&leg=${ARG2}&q=${URIENCODE(${Q})}&cause=${HANGUPCAUSE}&tech=${URIENCODE(${TECH})}&chan=${URIENCODE(${CHANNEL})}&to=${URIENCODE(${CUT(DIALEDPEERNUMBER,@,1)})})})
 same => n(end),Return()
