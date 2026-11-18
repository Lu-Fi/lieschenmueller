#!/bin/sh
# Lieschen Mueller aktualisieren: data/ sichern (backup/), neue Version holen, Konfiguration neu erzeugen, neu bauen und starten.
# Dasselbe wie ./install.sh --update (Hilfe: ./install.sh --help).
exec sh "$(dirname "$0")/install.sh" --update "$@"
