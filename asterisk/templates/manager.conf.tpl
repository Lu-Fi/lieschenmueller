; AMI nur lokal; die Bridge loest damit ausgehende Anrufe aus.
[general]
enabled = yes
port = 5038
bindaddr = 127.0.0.1

[bridge]
secret = @AMI_SECRET@
deny = 0.0.0.0/0.0.0.0
permit = 127.0.0.1/255.255.255.255
read = call
; originate: ausgehende Anrufe; call: Redirect, Setvar, Hangup, StopMixMonitor;
; reporting: Status der Fritzbox-Anmeldung (PJSIPShowRegistrationsOutbound);
; command (CLI-Befehle) wird noch gebraucht fuer: mixmonitor start, module reload res_pjsip.so, pjsip send (un)register
; fritz-reg (die Aktionen MixMonitor/ModuleLoad/PJSIPRegister verlangen das ebenfalls weitreichende Recht "system"),
; core show channel (Ansage im Gespraech) und moh reload (Wartemusik, bridge/app/hold.py).
write = originate,call,reporting,command
