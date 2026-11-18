"""Google Kalender ueber ein Dienstkonto (JSON-Schluessel in den Einstellungen): Termine lesen, eintragen, loeschen.
Genutzt fuer die Kalender der Mitarbeiter (Aufenthalt, Fernabfrage) und von Plugins (z. B. Kampagnen-Terminpools).
Jeder Kalender wird mit der Mailadresse des Dienstkontos geteilt."""
import json
import logging
import os
import time
import zoneinfo
from datetime import datetime, timezone

import requests

from . import db

log = logging.getLogger("gcal")
SCOPE = "https://www.googleapis.com/auth/calendar"
_cred = {"key": "", "obj": None}


class CalendarError(RuntimeError):
    """Lesbare Fehlermeldung fuer Oberflaeche und KI."""


class Conflict(CalendarError):
    """Der Termin ist inzwischen belegt."""


def base_url():
    return (db.get_meta("gcal_api_url", "") or "https://www.googleapis.com").rstrip("/")


def key_info():
    raw = db.get_meta("gcal_key", "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def configured():
    k = key_info()
    return bool(k and k.get("client_email") and k.get("private_key"))


def service_email():
    k = key_info()
    return (k or {}).get("client_email", "")


def _token():
    """OAuth2-Token des Dienstkontos (google-auth; wird kurz vor Ablauf erneuert)."""
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    raw = db.get_meta("gcal_key", "")
    info = key_info()
    if not info:
        raise CalendarError("Google Kalender ist nicht eingerichtet (Dienstkonto-Schlüssel fehlt)")
    if _cred["key"] != raw or _cred["obj"] is None:
        try:
            _cred["obj"] = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
        except Exception as e:
            raise CalendarError(f"Der Dienstkonto-Schlüssel ist ungültig ({type(e).__name__})") from None
        _cred["key"] = raw
    c = _cred["obj"]
    if not c.valid:
        try:
            c.refresh(Request())
        except Exception as e:
            log.warning("Google-Token nicht abrufbar: %s", e)
            raise CalendarError("Anmeldung bei Google fehlgeschlagen (Schlüssel gesperrt/abgelaufen oder keine Verbindung)") from None
    return c.token


def _call(method, path, params=None, body=None):
    url = f"{base_url()}{path}"
    try:
        r = requests.request(method, url, params=params, json=body, timeout=20, headers={"Authorization": f"Bearer {_token()}"})
    except requests.RequestException as e:
        raise CalendarError(f"Google Kalender nicht erreichbar ({type(e).__name__})") from None
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
        except ValueError:
            err = {}
        msg = err.get("message", "") if isinstance(err, dict) else str(err)
        log.warning("Google Kalender %s %s -> %s %s", method, path.split("?")[0], r.status_code, msg[:200])
        if r.status_code == 404:
            raise CalendarError("Kalender nicht gefunden: Kalender-ID falsch oder nicht mit dem Dienstkonto geteilt")
        if r.status_code == 403:
            raise CalendarError("Keine Berechtigung: Kalender mit dem Dienstkonto teilen (Änderungen an Terminen) und Kalender-API aktivieren"
                                + (f" ({msg[:100]})" if msg else ""))
        if r.status_code == 401:
            _cred["obj"] = None
            raise CalendarError("Anmeldung bei Google abgelehnt")
        raise CalendarError(f"Google Kalender meldet Fehler {r.status_code}: {msg[:150]}")
    return r.json() if r.content else {}


def _ts(d):
    """dateTime -> Epoche; Ganztagstermine (date) -> None."""
    s = d.get("dateTime")
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def events(cal_id, t_min, t_max):
    """Termine im Zeitraum (einzelne Vorkommen, nach Beginn sortiert). Ganztagstermine und abgesagte werden uebergangen."""
    out, token = [], None
    while True:
        params = {"timeMin": _iso(t_min), "timeMax": _iso(t_max), "singleEvents": "true", "orderBy": "startTime", "maxResults": 250}
        if token:
            params["pageToken"] = token
        d = _call("GET", f"/calendar/v3/calendars/{requests.utils.quote(cal_id, safe='')}/events", params)
        for e in d.get("items", []):
            if e.get("status") == "cancelled":
                continue
            s, en = _ts(e.get("start", {})), _ts(e.get("end", {}))
            if s is None or en is None:
                continue
            out.append({"id": e["id"], "summary": e.get("summary", ""), "start": s, "end": en, "location": e.get("location", ""),
                        "free": e.get("transparency") == "transparent"})
        token = d.get("nextPageToken")
        if not token:
            return out


def calendar_title(cal_id):
    return _call("GET", f"/calendar/v3/calendars/{requests.utils.quote(cal_id, safe='')}").get("summary", cal_id)


def insert(cal_id, summary, description, start, end, location=""):
    tz = os.environ.get("TZ", "Europe/Berlin")
    body = {"summary": summary, "description": description,
            "start": {"dateTime": _iso(start), "timeZone": tz}, "end": {"dateTime": _iso(end), "timeZone": tz}}
    if location:
        body["location"] = location
    return _call("POST", f"/calendar/v3/calendars/{requests.utils.quote(cal_id, safe='')}/events", body=body)["id"]


def delete(cal_id, event_id):
    try:
        _call("DELETE", f"/calendar/v3/calendars/{requests.utils.quote(cal_id, safe='')}/events/{requests.utils.quote(event_id, safe='')}")
    except CalendarError as e:
        log.warning("Termin %s nicht geloescht: %s", event_id, e)


def set_key(raw):
    """Dienstkonto-Schluessel speichern (JSON-Text) oder mit '' entfernen. Wirft ValueError bei ungueltigem Schluessel."""
    if not raw:
        db.x("DELETE FROM meta WHERE key='gcal_key'")
        return
    k = json.loads(raw)
    if not (k.get("client_email") and k.get("private_key") and k.get("type") == "service_account"):
        raise ValueError("Das ist kein Dienstkonto-Schlüssel (client_email/private_key fehlen)")
    db.set_meta("gcal_key", json.dumps(k))


def parse_local(s):
    """'2026-10-20T14:00' (lokale Zeit) -> Epoche."""
    tz = zoneinfo.ZoneInfo(os.environ.get("TZ", "Europe/Berlin"))
    return datetime.fromisoformat(s.strip()).replace(tzinfo=tz).timestamp()
