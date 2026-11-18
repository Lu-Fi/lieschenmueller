"""Rufnummern und Mailadressen fuer Protokolle (docker logs) unkenntlich machen: Logs landen in Dateien, Backups und
Fehlerberichten, die Anrufliste in der Oberflaeche (mit Rechten) ist der Ort fuer die vollstaendigen Daten."""
import logging
import re

_NUM = re.compile(r"(?<![\w.])(\+|00|\*\*)?(\d[\d /-]{3,}\d)")
_MAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def number(n):
    """0171 2345678 -> 01…78 (Laenge und Anfang/Ende reichen zum Wiederfinden in der Anrufliste)."""
    d = re.sub(r"\D", "", str(n or ""))
    if len(d) <= 4:
        return str(n or "")             # interne Nummern (**610) sind kein Personenbezug
    return d[:2] + "…" + d[-2:]


def text(s):
    """Alle Rufnummern (ab 5 Ziffern) und Mailadressen in einem Text kuerzen."""
    s = _MAIL.sub(lambda m: f"{m.group(1)}…@{m.group(2)}", str(s))
    return _NUM.sub(lambda m: (m.group(1) or "") + number(m.group(2)), s)


def email(a):
    return _MAIL.sub(lambda m: f"{m.group(1)}…@{m.group(2)}", str(a or ""))


class AccessFilter(logging.Filter):
    """uvicorn-Zugriffslog: /internal/* gar nicht, bei /api/calls* ohne Query-String (Suche nach Namen/Nummern)."""

    def filter(self, record):
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path = args[2]
            if path.startswith("/internal/"):
                return False
            if path.startswith("/api/calls") and "?" in path:
                record.args = args[:2] + (path.split("?", 1)[0] + "?…",) + args[3:]
            return True
        return "/internal/" not in record.getMessage()
