[transport-udp]
type=transport
protocol=udp
bind=0.0.0.0:5060

; Zugangsdaten der Fritzbox: dyn/fritz.conf (aus der .env erzeugt, spaeter von der Bridge aus den Einstellungen geschrieben)
#include "dyn/fritz.conf"

; --- Anzeige des Anrufers auf den Fritzbox-Telefonen ---
; Die Fritzbox meldet je Telefon eine zusaetzliche Rufnummer "Anderer Anbieter" 99<intern> (z. B. 99611 fuer **611) bei Asterisk an
; (Registrar = diese Maschine). Asterisk ruft darueber an; fuer die Fritzbox ist das ein externer Anruf, sie zeigt die Nummer des Anrufers.
; AOR und Endpoint dieser Klingel-Rufnummern stehen in dyn/fritz.conf (bridge/app/fritzconf.py, aus den Einstellungen: Tabelle
; "Klingel-Rufnummern" und die Nummer fuer "alle Telefone klingeln"); die Anmeldung kommt von der Fritzbox-IP und wird dem Endpoint
; "fritz" zugeordnet, dessen aors sie deshalb enthaelt. Bis 2026-11 stand 99613 hier fest; jetzt ist sie eine Nummer wie jede andere.
