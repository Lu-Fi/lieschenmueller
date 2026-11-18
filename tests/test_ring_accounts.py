"""Klingel-Rufnummern 99NNN per Haken: in der Fritzbox anlegen (nur wenn frei), in Asterisk ein-/austragen."""
import pytest

from app import astconf, fritz, main
from tests.conftest import run


class FC:
    def __init__(self, accounts):
        self.acc = dict(accounts)          # index -> (number, registrar)
        self.added = []

    def call_action(self, svc, action, **kw):
        if action == "GetMaxVoIPNumbers":
            return {"NewMaxVoIPNumbers": 8}
        if action == "X_AVM-DE_GetVoIPAccount":
            i = kw["NewVoIPAccountIndex"]
            if i not in self.acc:
                raise IndexError
            n, r = self.acc[i]
            return {"NewVoIPNumber": n, "NewVoIPRegistrar": r}
        if action == "X_AVM-DE_AddVoIPAccount":
            self.added.append(kw)
            self.acc[kw["NewVoIPAccountIndex"]] = (kw["NewVoIPNumber"], kw["NewVoIPRegistrar"])
            return {}
        raise AssertionError(action)


def test_create_ring_account(monkeypatch):
    fc = FC({0: ("5550163", "sip.example"), 1: ("99613", "192.168.178.20"), 3: ("555", "x")})
    monkeypatch.setattr(fritz, "_conn", lambda: fc)
    assert fritz.create_ring_account("99611") == 2                     # erster freier Platz
    a = fc.added[0]
    assert (a["NewVoIPNumber"], a["NewVoIPUsername"], a["NewVoIPRegistrar"]) == ("99611", "99611", "192.168.178.20")
    assert a["NewVoIPPassword"].isalnum() and a["NewVoIPOutboundProxy"] == ""
    with pytest.raises(RuntimeError, match="schon angelegt"):
        fritz.create_ring_account("99611")
    monkeypatch.setattr(fritz, "cached_phones", lambda: [{"number": "**611", "name": "Headset", "kind": "DECT"},
                                                         {"number": "**612", "name": "Fremd", "kind": "DECT"}, {"number": "**1", "name": "FON1", "kind": "analog"}])
    fc.acc[4] = ("99612", "10.0.0.9")
    rows = {r["ring"]: r for r in fritz.ring_overview()}
    assert rows["99611"]["exists"] and rows["99611"]["ours"]
    assert rows["99612"]["exists"] and not rows["99612"]["ours"] and rows["991"]["exists"] is False


def test_ring_line_api(bridge, monkeypatch):
    state = {"exists": False, "ours": False, "registrar": ""}
    created = []
    monkeypatch.setattr(fritz, "ring_overview", lambda: [dict(phone="**611", name="Headset", kind="DECT", ring="99611", own_ip="1.2.3.4", **state)])
    monkeypatch.setattr(fritz, "create_ring_account", lambda n: created.append(n) or 2)
    applied = []

    async def apply():
        applied.append(astconf.stored_ring_lines())
    monkeypatch.setattr(main, "ring_apply", apply)

    async def scenario(b):
        main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
        r = await b.http.put("/api/fritz/ring-lines/99611", json={"on": True})
        assert r.status_code == 200 and r.json()["created"] is True and created == ["99611"] and applied[-1] == ["99611"]
        state.update(exists=True, ours=True)
        r = await b.http.put("/api/fritz/ring-lines/99611", json={"on": True})
        assert r.json()["created"] is False and created == ["99611"]                     # vorhanden: nichts angelegt
        r = await b.http.put("/api/fritz/ring-lines/99611", json={"on": False})
        assert r.status_code == 200 and applied[-1] == []
        state.update(ours=False, registrar="10.0.0.9")
        r = await b.http.put("/api/fritz/ring-lines/99611", json={"on": True})
        assert r.status_code == 409 and "schon" in r.json()["detail"] and applied[-1] == []
        assert (await b.http.put("/api/fritz/ring-lines/99613", json={"on": False})).status_code == 409    # ring_all (Voreinstellung)
        assert (await b.http.put("/api/fritz/ring-lines/123", json={"on": False})).status_code == 400
        main.app.dependency_overrides.pop(main.auth, None)
    run(bridge, scenario)


def test_ring_numbers_cached_and_reconnects(monkeypatch):
    """Die Nummernfelder warten auf ring_numbers: dauerhafte Verbindung, 60 s Zwischenspeicher, nach einem Fehler neu verbinden."""
    xml = "<List><Item><Number>99611</Number><Type>SIP</Type></Item><Item><Number>0309999</Number><Type>SIP</Type></Item></List>"
    calls, made = [], []

    class FC:
        def __init__(self, bad=False):
            self.bad = bad

        def call_action(self, svc, act, **kw):
            calls.append(act)
            if self.bad:
                raise OSError("weg")
            return {"NewNumberList": xml}

    conns = [FC(bad=True), FC()]
    monkeypatch.setattr(fritz, "_conn", lambda: made.append(1) or conns.pop(0))
    monkeypatch.setattr(fritz, "_pc", None)
    monkeypatch.setattr(fritz, "cached_phones", lambda: [{"number": "**611", "name": "Headset"}])
    fritz.ring_cache_clear()
    r = fritz.ring_numbers()
    assert r == [{"number": "99611", "phone": "**611", "phone_name": "Headset"}]
    assert len(made) == 2                                  # erste Verbindung kaputt -> einmal neu aufgebaut
    n = len(calls)
    r[0]["phone_name"] = "veraendert"
    assert fritz.ring_numbers()[0]["phone_name"] == "Headset" and len(calls) == n   # aus dem Speicher, unveraendert
    fritz.ring_cache_clear()
    fritz.ring_numbers()
    assert len(calls) == n + 1 and len(made) == 2          # dieselbe Verbindung weiter genutzt


def test_shared_conn_reconnect_only_on_connection_errors_and_locked(monkeypatch):
    """Neuaufbau der gemeinsamen TR-064-Verbindung nur bei Verbindungsfehlern; Parse-/Logikfehler gehen ohne Neuaufbau weiter.
    Gleichzeitige Aufrufe aus mehreren Threads benutzen die Verbindung nacheinander (_PC_LOCK)."""
    import threading
    import time
    import requests
    from fritzconnection.core.exceptions import FritzActionError, FritzConnectionException
    made, active, overlap = [], [0], []

    class FC:
        def __init__(self, err=None):
            self.err = err

        def call_action(self, svc, act, **kw):
            active[0] += 1
            overlap.append(active[0])
            time.sleep(0.02)
            active[0] -= 1
            if self.err:
                e, self.err = self.err, None
                raise e
            return {"NewNumberList": "<List/>"}

    def conn():
        made.append(1)
        return FC()
    monkeypatch.setattr(fritz, "_conn", conn)
    monkeypatch.setattr(fritz, "cached_phones", lambda: [])
    for err, again in ((requests.ConnectionError("weg"), True), (TimeoutError(), True), (FritzConnectionException("x"), True),
                       (KeyError("NewNumberList"), False), (FritzActionError("x"), False), (ValueError("xml"), False)):
        monkeypatch.setattr(fritz, "_pc", FC(err))
        monkeypatch.setattr(fritz, "_pc_key", tuple(fritz.os.environ.get(k, "") for k in
                                                    ("FRITZ_HOST", "FRITZ_TR064_USER", "FRITZ_TR064_PASSWORD")))
        made.clear()
        fritz.ring_cache_clear()
        if again:
            assert fritz.ring_numbers(fresh=True) == [] and len(made) == 1, err
        else:
            with pytest.raises(type(err)):
                fritz.ring_numbers(fresh=True)
            assert not made and fritz._pc is not None, err
    overlap.clear()
    ts = [threading.Thread(target=fritz.ring_numbers, kwargs={"fresh": True}) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(overlap) == 4 and max(overlap) == 1             # nie zwei Aufrufe gleichzeitig auf derselben Verbindung

