# Fritzbox-Anbindung

**Wofür diese Seite:** Technische Details zur SIP-Anbindung und zur Absicherung der Klingel-Rufnummer. Was die Fritzbox sonst leisten muss: [Voraussetzungen und Fritzbox](Voraussetzungen-und-Fritzbox.md); Einrichtung: [Klingel-Rufnummern und Anruferanzeige](Klingel-Rufnummern-und-Anruferanzeige.md).

Asterisk meldet sich an der Fritzbox wie ein Telefon an. Zwei Rufnummern spielen dabei eine Rolle.

## 1. Das IP-Telefon `LieschenMueller`
Nimmt Anrufe an, die die Fritzbox ihm zustellt (eingehende Rufnummern bei dem Gerät eintragen). Es ist **der Weg der KI ins Telefonnetz**, zeigt aber als Anrufer „LieschenMueller“.

## 2. Die Klingel-Rufnummer „Anderer Anbieter“ (`99613`)
Damit **alle Handsets klingeln und die echte Nummer des Anrufers zeigen**, legt man in der Fritzbox eine zusätzliche Rufnummer an:
- Anbieter: *Anderer Anbieter*, Rufnummer/Benutzername `99613` (allgemein `99` + interne Nummer),
- Kennwort: beliebig (die Fritzbox verlangt eines; Asterisk prüft es nicht, siehe unten),
- Registrar: **IP des Hosts**, auf dem Asterisk läuft,
- allen gewünschten Telefonen zuweisen.

**Absicherung:** Asterisk nimmt SIP-Anfragen (Anmeldung von `99613`, Anrufe) nur von der IP-Adresse der Fritzbox an (PJSIP `type=acl` in `data/asterisk/fritz.conf`, IP aus der Fritzbox-Adresse in den Einstellungen; ändert man die Adresse, ändert sich die Sperre mit). Die Endpoints `fritz` und `99613` werden nur über die IP erkannt (`identify_by=ip`). Ein anderes Gerät im LAN kann sich so nicht als `99613` anmelden und die Fritzbox verdrängen. Notschalter: `SIP_ACL=0` in der `.env` (dann gilt nur noch `identify_by=ip`; Bridge neu erstellen, sie schreibt `fritz.conf` beim Start neu).

Ein Kennwort für `99613` (PJSIP-Authentifizierung) ist bewusst **nicht** eingebaut: Die Anmeldung der Fritzbox läuft über den Endpoint `fritz` (erkannt an der IP), ein Kennwortzwang müsste diese Zuordnung ändern und würde bei falschem oder fehlendem Kennwort in der Fritzbox alle Anrufe über `99613` stumm schalten. Wer es trotzdem möchte, kann es nach einem Test an der eigenen Fritzbox nachrüsten (eigener `type=auth`-Abschnitt, `auth=` am Endpoint `99613`, `99613` aus `aors` des Endpoints `fritz` entfernen); die frühere Variable `RING_PASSWORD` wird nicht mehr erzeugt oder benutzt.

Ablauf eines Anrufs: Asterisk nimmt den Anruf an, lässt über `99613` alle Telefone klingeln (Klingelzeit = Anrufregel) und übernimmt mit der KI, wenn niemand abhebt. Hebt jemand ab, wird durchgestellt (und je Aufzeichnungsregel aufgezeichnet). Fehlt `99613`, klingelt die Fritzbox selbst, und die KI übernimmt als normales IP-Telefon.

## 3. Aufgezeichnet ausgehend telefonieren: Vorwahl
In der Fritzbox eine **Wahlregel** anlegen (Telefonie → Eigene Rufnummern → Wahlregeln; je nach FRITZ!OS-Version abweichend): Rufnummern, die mit der Vorwahl beginnen → Verbindung über die Klingel-Rufnummer `99613` (bzw. eine andere bei Asterisk eingeschaltete `99…`). Die Vorwahl steht in *Einstellungen → Telefonie → Aufzeichnung von Gesprächen* (**Standard `*77`**, z. B. auch `000`) und muss mit der Wahlregel übereinstimmen. Wer Vorwahl + Nummer wählt, wird über Asterisk verbunden, das Gespräch wird nach den Aufzeichnungsregeln aufgezeichnet. Ohne Vorwahl wählt man ganz normal. Einzelheiten: [Ausgehende Gespräche und Mitschrift](Ausgehende-Gespraeche-und-Mitschrift.md).

## 4. TR-064 und Weboberfläche der Fritzbox
Ein Fritzbox-Benutzer mit dem Recht „FRITZ!Box Einstellungen“. Damit gleicht die Oberfläche das Telefonbuch ab und verwaltet Rufnummern (*Rufnummern*, *Kontakte*); Rufnummern von DECT-/IP-Telefonen schreibt sie über die inoffizielle Weboberfläche der Fritzbox (getestet mit FRITZ!OS 8.25). Einzelheiten: [Voraussetzungen und Fritzbox](Voraussetzungen-und-Fritzbox.md).

## Fehlersuche
- Registrierung schlägt fehl: `FRITZ_USER` ist die Rufnummer statt des Gerätebenutzers; Kennwort mit `;`.
- `99613` angemeldet? `docker compose exec asterisk asterisk -rx 'pjsip show contacts'`. Der Wächter ([Betrieb](Betrieb-und-Ueberwachung.md)) prüft jede eingetragene Klingel-Rufnummer aus `data/asterisk/fritz.conf`; in der App zeigt *Rufnummern → Anruferanzeige an den Telefonen* den Stand.
- Nach einem Neustart von Asterisk meldet sich `99613` erst nach bis zu 5 Minuten wieder an.
- Im Asterisk-Log `did not pass ACL test`: Die Anfrage kam nicht von der Fritzbox-Adresse aus den Einstellungen (z. B. Name statt IP, der anders aufgelöst wird). Fritzbox-Adresse als IP eintragen oder vorübergehend `SIP_ACL=0`.
