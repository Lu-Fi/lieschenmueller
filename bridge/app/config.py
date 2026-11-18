"""Verbindungs-Einstellungen, die der Administrator in der Oberflaeche aendert (Tabelle meta, Schluessel cfg:<name>).
Gesetzte Werte haben Vorrang vor der .env; sie werden beim Start und beim Speichern in os.environ uebernommen, damit der
vorhandene Code (der os.environ liest) sie ohne Aenderung nutzt. Geheimnisse (Kennwoerter, Schluessel) werden nie ausgeliefert."""
import os

from . import db

# Name -> (Umgebungsvariable, geheim?)
KEYS = {
    "gemini_api_key": ("GEMINI_API_KEY", True),
    "fritz_host": ("FRITZ_HOST", False),
    "fritz_user": ("FRITZ_USER", False),
    "fritz_password": ("FRITZ_PASSWORD", True),
    "fritz_tr064_user": ("FRITZ_TR064_USER", False),
    "fritz_tr064_password": ("FRITZ_TR064_PASSWORD", True),
    "retention_days": ("RETENTION_DAYS", False),
}
ENV0 = {k: os.environ.get(env) for k, (env, _) in KEYS.items()}      # Werte aus der .env beim Start


def ui_value(k):
    return db.get_meta("cfg:" + k, "") or ""


def get(k, default=""):
    return ui_value(k) or ENV0.get(k) or default


def source(k):
    return "ui" if ui_value(k) else ("env" if ENV0.get(k) else "")


def apply():
    """Oberflaechenwerte in os.environ uebernehmen (leer = Wert aus der .env)."""
    for k, (env, _) in KEYS.items():
        v = ui_value(k) or ENV0.get(k)
        if v:
            os.environ[env] = v
        else:
            os.environ.pop(env, None)


def set_value(k, v):
    if k not in KEYS:
        raise KeyError(k)
    if v:
        db.set_meta("cfg:" + k, v)
    else:
        db.x("DELETE FROM meta WHERE key=?", ("cfg:" + k,))
    apply()


def public():
    """Fuer die Oberflaeche: Werte ohne Geheimnisse, bei Geheimnissen nur 'gesetzt' und die letzten vier Zeichen."""
    out = {}
    for k, (env, secret) in KEYS.items():
        v = get(k)
        out[k] = {"source": source(k), "set": bool(v)}
        if secret:
            out[k]["hint"] = ("…" + v[-4:]) if len(v) >= 8 else ("gesetzt" if v else "")
        else:
            out[k]["value"] = v
    return out


def retention_days():
    try:
        return max(0, int(get("retention_days", "0") or 0))
    except ValueError:
        return 0
