#!/bin/sh
# Rendert die Asterisk-Konfiguration aus ../.env (siehe render.py).
exec python3 "$(dirname "$0")/render.py"
