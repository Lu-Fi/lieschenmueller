"""Anmeldung mit und ohne "Angemeldet bleiben": Laufzeit der Sitzung und Art des Cookies."""
import time

from app import main, users
from tests.conftest import run


def test_remember_me_session_lifetime(bridge):
    async def scenario(b):
        uid = b.db.x("INSERT INTO users(username,pw_hash,role) VALUES('anna',?,'admin')", (users.hash_pw("geheim-123"),))
        r = await b.http.post("/api/login", json={"username": "anna", "password": "geheim-123"})
        assert r.status_code == 200
        ck = r.headers["set-cookie"].lower()
        assert "max-age" not in ck and "expires" not in ck                    # Sitzungs-Cookie
        r2 = await b.http.post("/api/login", json={"username": "anna", "password": "geheim-123", "remember": True})
        assert f"max-age={users.SESSION_MAX_REMEMBER}" in r2.headers["set-cookie"].lower()
        rows = {s["remember"]: s for s in b.db.q("SELECT * FROM sessions WHERE user_id=?", (uid,))}
        assert rows[0]["expires"] - rows[0]["created"] == users.SESSION_IDLE
        assert rows[1]["expires"] - rows[1]["created"] == users.SESSION_IDLE_REMEMBER
        tok = r2.cookies.get(main.SESSION_COOKIE)
        old = time.time() - 20 * 86400                                       # 20 Tage alt, zuletzt vor 2 Tagen aktiv
        b.db.x("UPDATE sessions SET created=?, last_seen=?, expires=? WHERE remember=1", (old, time.time() - 2 * 86400, time.time() + 86400))
        assert users.session_user(tok)["id"] == uid                          # 7-Tage-Grenze gilt hier nicht
        b.db.x("UPDATE sessions SET created=? WHERE remember=1", (time.time() - 91 * 86400,))
        assert users.session_user(tok) is None                               # spaetestens nach 90 Tagen
    run(bridge, scenario)
