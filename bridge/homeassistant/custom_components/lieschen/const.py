"""Konstanten der Lieschen-Integration."""
DOMAIN = "lieschen"
CONF_SWITCHES = "switches"
EVENT_CALL = "lieschen_call"
SIGNAL_UPDATE = "lieschen_update_{}"
FINAL = ("beendet", "nicht_erreicht")
KI = "KI"
CONF_MODE = "switch_mode"
CONF_MIN_ON = "min_on"
MODE_PULSE = "taster"         # an fuer min_on Sekunden, dann aus (wie ein Taster; Alexa sieht das Einschalten sicher)
MODE_CALL = "gespraech"       # an, solange das Gespraech laeuft; aus = auflegen
DEFAULT_MIN_ON = 3
