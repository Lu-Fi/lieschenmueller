# WLAN-Anwesenheit

**Wofür diese Seite:** Der Status eines Benutzers kann sich automatisch ändern, solange sein Handy im WLAN der Fritzbox ist: Aus „nicht erreichbar“ wird „nicht stören“ (die Person ist da, will aber in Ruhe arbeiten; Ausnahmen kommen durch).

## Ablauf
Einstellung je Benutzer (*Meine Einstellungen → Mein Status → Anwesenheit über WLAN*), ein oder mehrere Geräte (bis 5, per MAC-Adresse; „Dieses Gerät hinzufügen“ zeigt nur das Gerät, mit dem man die Seite gerade öffnet).
Die App fragt alle **60 Sekunden** die Fritzbox (TR-064, Hosts) je MAC-Adresse ab:
- Ist **eines** der Geräte im Netz und der Status **„nicht erreichbar“**, wird er auf **„nicht stören“** gestellt (als automatisch gesetzt markiert, ⚙).
- Ist **kein** Gerät mehr im Netz und der Status „nicht stören“, wird nach **10 Minuten** wieder „nicht erreichbar“ – **aber nur, wenn die Anwesenheit dieses „nicht stören“ selbst gesetzt hat.** Ein von Hand gesetztes „nicht stören“ bleibt stehen. Die Wartezeit fängt Handys ab, die im Ruhezustand kurz aus dem WLAN gehen.
- **„erreichbar“ und „außer Haus“ bleiben unberührt.** Ein von Hand gesetzter **Urlaub** („nicht erreichbar“ mit „abwesend bis“ in der Zukunft) wird nicht umgestellt.
- Ist die Fritzbox nicht lesbar, ändert sich nichts.
- Sammelziele haben keine Anwesenheit.

## Datenschutz
- **Einschalten kann nur die Person selbst**; ein Administrator kann die Geräte vorbelegen und die Anwesenheit ausschalten.
- Es wird **nur der Status** gespeichert. Wann ein Gerät zuletzt im Netz war, steht nur im Arbeitsspeicher, nicht in der Datenbank und nicht im Protokoll (kein Bewegungsprofil); nach einem Neustart beginnt die Wartezeit von vorn.
- Der TR-064-Benutzer braucht das weitreichende Recht „FRITZ!Box Einstellungen“ ([Voraussetzungen und Fritzbox](Voraussetzungen-und-Fritzbox.md)).

Zum Status allgemein: [Benutzer und Status](Benutzer-und-Status.md).
