import asyncio
import json
import logging
import time
import urllib.request

from . import db

log = logging.getLogger("rate")
URL = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR"   # EZB-Referenzkurs
FALLBACK = 0.88


def get():
    """(Kurs USD->EUR, Datum des Kurses)"""
    try:
        return float(db.get_meta("usd_eur", "") or FALLBACK), db.get_meta("usd_eur_date", "")
    except ValueError:
        return FALLBACK, ""


def _fetch():
    req = urllib.request.Request(URL, headers={"User-Agent": "lieschen-mueller/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        d = json.loads(r.read())
    return float(d["rates"]["EUR"]), d.get("date", "")


async def refresher():
    """Beim Start und danach alle 12 h; bei Fehlern bleibt der letzte Kurs."""
    while True:
        try:
            rate, date = await asyncio.to_thread(_fetch)
            db.set_meta("usd_eur", rate)
            db.set_meta("usd_eur_date", date)
            log.info("Wechselkurs USD->EUR %.5f (%s)", rate, date)
        except Exception as e:
            log.warning("Wechselkurs nicht aktualisiert: %s %s", type(e).__name__, e)
        await asyncio.sleep(12 * 3600)
