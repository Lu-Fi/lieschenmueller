"""Alle Statustexte an einer Stelle. db, schedule und dialrules nehmen sie von hier, die Oberflaeche bekommt sie ueber GET /api/labels.

Gespeicherte Werte (office/away/off/dnd, closed, Aktionen) bleiben unveraendert; nur die Anzeige-Texte stehen hier."""

# Status eines Benutzers (staff.status)
STAFF_STATUS = {"office": "erreichbar", "away": "außer Haus", "off": "nicht erreichbar", "dnd": "nicht stören"}
# Farbe der Statusknoepfe/-punkte in der Oberflaeche (CSS-Klassen)
STAFF_STATUS_CLASS = {"office": "ok", "away": "warn", "off": "bad", "dnd": "dnd"}
# Woher der aktuelle Status kommt (staff.status_src)
STATUS_SRC = {"manual": "von Hand gesetzt", "presence": "automatisch gesetzt (WLAN-Anwesenheit)",
              "until": "automatisch gesetzt (Abwesenheit „bis“ abgelaufen)"}
# Art eines Eintrags (staff.kind): Person oder Sammelziel (z. B. Zentrale: nur Nummern, kein Status, kein Konto)
STAFF_KIND = {"person": "Person", "group": "Sammelziel"}
# Zeilen im Zeitplan (global und je Benutzer)
SCHEDULE_STATUS = {"office": "erreichbar", "away": "abwesend", "off": "nicht erreichbar", "closed": "außerhalb der Bürozeit"}
# Waehlregeln: Aktionen und Anrufer-Auswahl; HINTS = Zusatz in der Auswahlliste der Oberflaeche
DIALRULE_ACTIONS = {"office": "erreichbar", "away": "abwesend", "off": "nicht erreichbar",
                    "forward": "weiterleiten an", "ai": "direkt zum Anrufbeantworter"}
DIALRULE_ACTION_HINTS = {"office": "(Nummer im Büro)", "away": "(Handy/Vertretung)", "off": "(Vertretung/Anrufbeantworter)",
                         "forward": "Nummer …", "ai": ""}
DIALRULE_MODES = {"any": "alle Anrufer", "list": "bestimmte Nummern", "contacts": "Anrufer aus dem Telefonbuch",
                  "unknown": "unbekannte externe Anrufer", "internal": "interne Anrufer", "external": "externe Anrufer"}


def all_labels():
    return {"staff_status": STAFF_STATUS, "staff_status_class": STAFF_STATUS_CLASS, "status_src": STATUS_SRC, "staff_kind": STAFF_KIND,
            "schedule_status": SCHEDULE_STATUS, "dialrule_actions": DIALRULE_ACTIONS, "dialrule_action_hints": DIALRULE_ACTION_HINTS,
            "dialrule_modes": DIALRULE_MODES}
