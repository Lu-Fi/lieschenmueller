"""Benutzerkonten, Sitzungen und Zugriffsrechte auf Anrufe."""
import base64
import hashlib
import hmac
import os
import re
import secrets
import time

from . import db

MIN_PW = 10
SESSION_IDLE = 12 * 3600          # Sitzung endet nach 12 h ohne Aktivitaet
SESSION_MAX = 7 * 86400           # und spaetestens nach 7 Tagen
SESSION_IDLE_REMEMBER = 30 * 86400   # "Angemeldet bleiben": 30 Tage ohne Aktivitaet
SESSION_MAX_REMEMBER = 90 * 86400    # und spaetestens nach 90 Tagen


def _limits(remember):
    return (SESSION_IDLE_REMEMBER, SESSION_MAX_REMEMBER) if remember else (SESSION_IDLE, SESSION_MAX)
_b64 = lambda b: base64.b64encode(b).decode()


def hash_pw(pw):
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${_b64(salt)}${_b64(h)}"


def verify_pw(pw, stored):
    try:
        _, n, r, p, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(h))
    except Exception:
        return False


def check_new_password(pw):
    if len(pw or "") < MIN_PW:
        raise ValueError(f"Das Passwort muss mindestens {MIN_PW} Zeichen lang sein")


def get(uid):
    r = db.q("SELECT * FROM users WHERE id=?", (uid,))
    return r[0] if r else None


def get_by_name(name):
    r = db.q("SELECT * FROM users WHERE username=?", ((name or "").strip(),))
    return r[0] if r else None


def public(u):
    return {"id": u["id"], "username": u["username"], "role": u["role"], "staff_id": u["staff_id"],
            "disabled": bool(u["disabled"]), "last_login": u["last_login"]}


def create(username, password, role="user", staff_id=None, check=True):
    username = (username or "").strip()
    # Nur ASCII: sonst sind Namen mit gleich aussehenden Buchstaben (z. B. kyrillisches "а" in "аdmin") moeglich
    if not re.fullmatch(r"[A-Za-z0-9._@-]{1,40}", username):
        raise ValueError("Benutzername: 1-40 Zeichen aus a-z, A-Z, 0-9 und . _ - @ (keine Umlaute oder Sonderbuchstaben)")
    if role not in ("admin", "user"):
        raise ValueError("Rolle: admin oder user")
    if check:
        check_new_password(password)
    if get_by_name(username):
        raise ValueError("Diesen Benutzernamen gibt es schon")
    return db.x("INSERT INTO users(username,pw_hash,role,staff_id,created) VALUES(?,?,?,?,?)",
                (username, hash_pw(password), role, staff_id or None, time.time()))


def authenticate(username, password):
    u = get_by_name(username)
    if not u:
        verify_pw(password or "", hash_pw("x"))      # gleiche Rechenzeit, damit Namen nicht erratbar sind
        return None
    if u["disabled"] or not verify_pw(password or "", u["pw_hash"]):
        return None
    db.x("UPDATE users SET last_login=? WHERE id=?", (time.time(), u["id"]))
    return u


def new_session(uid, remember=False):
    tok = secrets.token_urlsafe(32)
    now = time.time()
    db.x("INSERT INTO sessions(token_hash,user_id,created,expires,last_seen,remember) VALUES(?,?,?,?,?,?)",
         (hashlib.sha256(tok.encode()).hexdigest(), uid, now, now + _limits(remember)[0], now, 1 if remember else 0))
    return tok


def session_user(tok):
    if not tok:
        return None
    th = hashlib.sha256(tok.encode()).hexdigest()
    s = db.q("SELECT * FROM sessions WHERE token_hash=?", (th,))
    now = time.time()
    if not s:
        return None
    s = s[0]
    idle, most = _limits(s.get("remember"))
    if s["expires"] < now or s["created"] + most < now:
        db.x("DELETE FROM sessions WHERE token_hash=?", (th,))
        return None
    u = get(s["user_id"])
    if not u or u["disabled"]:
        return None
    if now - s["last_seen"] > 300:                   # gleitende Laufzeit, aber nicht bei jeder Anfrage schreiben
        db.x("UPDATE sessions SET last_seen=?, expires=? WHERE token_hash=?", (now, now + idle, th))
    return u


def drop_session(tok):
    if tok:
        db.x("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(tok.encode()).hexdigest(),))


def drop_user_sessions(uid, keep_tok=None):
    keep = hashlib.sha256(keep_tok.encode()).hexdigest() if keep_tok else ""
    db.x("DELETE FROM sessions WHERE user_id=? AND token_hash!=?", (uid, keep))


def ensure_admin():
    """Beim ersten Start: Admin 'admin' mit dem bisherigen UI_PASSWORT anlegen, damit niemand ausgesperrt wird."""
    if db.q("SELECT 1 FROM users LIMIT 1"):
        return
    pw = os.environ.get("UI_PASSWORD", "")
    if pw:
        create("admin", pw, "admin", check=False)


# ---------- Zugriff auf Anrufe: die zugewiesene Rufnummer entscheidet ----------
def level_for(user, call):
    """'full' oder None. Admins sehen alles, Benutzer nur Anrufe auf ihre zugewiesenen Rufnummern
    und die der Mitarbeiter/Sammelziele aus "sieht auch Anrufe von" (jeweils ab der Zuweisung)."""
    if user["role"] == "admin":
        return "full"
    d = db._digits(call.get("called"))
    if any(d == n and (call.get("started") or 0) >= since for n, since in db.visible_numbers(user.get("staff_id"))):
        return "full"
    if call.get("id") and db.q("SELECT 1 FROM call_shares WHERE call_id=? AND user_id=?", (call["id"], user["id"])):
        return "full"          # einzeln freigegeben
    if call.get("user_id") and call["user_id"] == user["id"]:
        return "full"          # selbst gestartetes Gespraech
    return None


def may_share(user, call):
    """Freigeben darf, wer den Anruf aus eigenem Recht sieht (Admin, eigene Rufnummer, selbst gestartet), nicht wer ihn nur freigegeben bekam."""
    if user["role"] == "admin" or (call.get("user_id") and call["user_id"] == user["id"]):
        return True
    d = db._digits(call.get("called"))
    return any(d == n and (call.get("started") or 0) >= since for n, since in db.visible_numbers(user.get("staff_id")))


def sql_scope(user):
    """SQL-Bedingung + Parameter fuer die Anrufe, die der Benutzer sehen darf."""
    nums = db.visible_numbers(user.get("staff_id"))
    shared = "id IN (SELECT call_id FROM call_shares WHERE user_id=?) OR user_id=?"       # freigegeben oder selbst gestartet
    if not nums:
        return f"({shared})", [user["id"], user["id"]]
    own = " OR ".join("(digits(called)=? AND started>=?)" for _ in nums)                   # erst ab der Zuweisung der Nummer
    return f"({own} OR {shared})", [*(v for n in nums for v in n), user["id"], user["id"]]


def mask(call, level="full"):
    """Kosten sind Sache der Administratoren."""
    c = dict(call)
    for k in ("cost_usd", "tok_in_text", "tok_in_audio", "tok_out_text", "tok_out_audio", "tok_think"):
        c[k] = 0
    c["access"] = level
    return c
