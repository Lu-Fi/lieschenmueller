"""Zugang zu Gemini: wahlweise die einfache Gemini-API (Schluessel in der .env/Oberflaeche) oder Vertex AI in einer festen Region
(Dienstkonto-Schluesseldatei, Projekt-ID, Region), damit Gespraechsdaten die gewaehlte Region nicht verlassen.

Einstellung vertex_scope: "" = aus (einfache API), "text" = Transkript, Zusammenfassung und Auswertung ueber Vertex, "all" = zusaetzlich die
Telefon-KI (Live) und die Stimmproben. Ist Vertex gewaehlt, aber nicht vollstaendig eingerichtet, gibt es einen Fehler: kein stiller Rueckfall
auf die einfache API (das waere an der gewaehlten Region vorbei)."""
import os
import re

from . import db

KEY_FILE = os.environ.get("VERTEX_KEY_FILE", os.path.join(os.path.dirname(os.environ.get("DB_PATH", "/data/lieschen.db")), "vertex.json"))
SCOPES = ("", "text", "all")
PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
LOCATION = "eu"                   # EU-Multiregion: die Verarbeitung bleibt in der EU; global, us und Einzelregionen sind bewusst nicht waehlbar (nicht EU-gebunden bzw. Modelle fehlen)
_CREDS = {}                       # (Pfad, mtime) -> Zugangsdaten


def settings():
    return {"scope": db.get_meta("vertex_scope", "") if db.get_meta("vertex_scope", "") in SCOPES else "",
            "project": db.get_meta("vertex_project", ""), "location": LOCATION}


def key_file_found():
    return os.path.isfile(KEY_FILE)


def use_vertex(kind):
    sc = settings()["scope"]
    return sc == "all" or (sc == "text" and kind == "text")


def ready(kind):
    """Zugang fuer "text" bzw. "live" eingerichtet: Vertex (Projekt-ID + Schluesseldatei) oder Gemini-API-Schluessel, je nach Auswahl."""
    if use_vertex(kind):
        return bool(settings()["project"]) and key_file_found()
    return bool(os.environ.get("GEMINI_API_KEY"))


def _credentials():
    from google.oauth2 import service_account
    key = (KEY_FILE, os.path.getmtime(KEY_FILE))
    if key not in _CREDS:
        _CREDS.clear()
        _CREDS[key] = service_account.Credentials.from_service_account_file(KEY_FILE, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return _CREDS[key]


def client(genai, kind="text"):
    """genai-Client fuer "text" (Transkript, Zusammenfassung, Tests) oder "live" (Telefon-KI, Stimmproben). genai ist das Modul des Aufrufers (Tests tauschen es aus)."""
    if not use_vertex(kind):
        return genai.Client()
    s = settings()
    if not s["project"] or not key_file_found():
        raise RuntimeError("Vertex AI ist gewählt, aber nicht eingerichtet (Projekt-ID oder Schlüsseldatei fehlt)")
    return genai.Client(vertexai=True, project=s["project"], location=s["location"], credentials=_credentials())
