"""Wegwerf-Bridge fuer Screenshots und Vorfuehrungen: frische Demo-Datenbank (tools/demo_seed.py), Fake-Fritzbox, Fake-Asterisk,
kein Gemini, keine Mails. Nichts davon erreicht ein echtes Geraet oder einen Dienst.

Start: tools/demo_shots.py startet sie selbst im Test-Image lm-test, OHNE Netz (--network none); die Oberflaeche ist ueber einen
Unix-Socket (DEMO_UDS) und eine Weiterleitung nur auf 127.0.0.1 erreichbar. Von Hand:
    tools/demo_shots.py --serve        (dann http://127.0.0.1:18093/login, Anmeldung: tools/demo_seed.py, DEMO_LOGINS)
Die Datenbank liegt in einem temporaeren Ordner und wird bei jedem Start neu angelegt."""
import asyncio
import os
import sys
import tempfile

DATA = tempfile.mkdtemp(prefix="lmdemo-")
os.environ["DB_PATH"] = os.path.join(DATA, "lieschen.db")
os.environ.setdefault("AMI_SECRET", "demo-ami-secret")
os.environ.setdefault("TZ", "Europe/Berlin")
for k in ("GEMINI_API_KEY", "FRITZ_USER", "FRITZ_PASSWORD", "FRITZ_HOST", "FRITZ_TR064_USER", "FRITZ_TR064_PASSWORD",
          "SMTP_PASSWORD", "UI_PASSWORD"):
    os.environ.pop(k, None)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "bridge"), ROOT]

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from app import ami, astconf, config, db, fritz, fritzweb, gclient, main, notify, outrec, presence, users  # noqa: E402
from tools import demo_seed  # noqa: E402

REG_IP = "192.168.178.20"         # Adresse der Demo-Bridge aus Sicht der Fake-Fritzbox (privates Netz, erfunden)


def install_fakes():
    """Grenzen der Bridge ersetzen: Fritzbox (TR-064 und Weboberflaeche), Asterisk (AMI), Gemini, Mail."""
    fritz._numbers_cache.update(t=1e12, v=list(demo_seed.FRITZ_NUMBERS))
    fritz._phones_cache.update(t=1e12, v=list(demo_seed.PHONES))
    fritz.list_numbers = lambda: list(demo_seed.FRITZ_NUMBERS)
    fritz.cached_phones = lambda: list(demo_seed.PHONES)
    fritz.internal_phones = lambda: list(demo_seed.PHONES)
    names = {p["number"]: p["name"] for p in demo_seed.PHONES}
    fritz.ring_numbers = lambda fresh=False: [{"number": n, "phone": "**" + n[2:], "phone_name": names.get("**" + n[2:], "")}
                                              for n in demo_seed.RING_NUMBERS]

    def ring_overview():
        have = set(demo_seed.RING_NUMBERS)
        out = []
        for p in demo_seed.PHONES:
            if not p["number"].startswith("**6"):
                continue
            ring = "99" + p["number"][2:]
            row = dict(phone=p["number"], name=p["name"], kind=p["kind"], ring=ring, exists=ring in have, ours=ring in have,
                       registrar=REG_IP if ring in have else "", own_ip=REG_IP, assigned=None)
            if p["kind"] == "IP":
                row.update(assigned=False, client_idx=3, ip_all=True, ip_out=demo_seed.MSN_ZENTRALE)
            out.append(row)
        return out
    fritz.ring_overview = ring_overview
    fritz.ip_clients = lambda fc=None: {"**620": {"idx": 3, "name": "IP-Telefon Besprechung", "incoming": [], "all": True,
                                                  "out": demo_seed.MSN_ZENTRALE}}
    fritz.asterisk_numbers = lambda: {
        "phone": "LieschenMueller", "internal": "625", "outgoing": demo_seed.MSN_ZENTRALE, "all_calls": False,
        "numbers": [{"number": n["number"], "index": i, "on": n["number"] in (demo_seed.MSN_ZENTRALE, demo_seed.MSN_PETRA,
                                                                              demo_seed.MSN_CLEMENS)}
                    for i, n in enumerate(demo_seed.FRITZ_NUMBERS)],
        "write_block": "", "write_via": "web"}
    fritz.answered_by = lambda caller, t0, t1: ""
    fritz.write_block = lambda fc, phone="": ""
    fritz._conn = lambda: object()

    dect = [dict(idx=1, name="Mobilteil 1", intern="**610", ring_all=False, out_num=demo_seed.MSN_PETRA, out_only=False,
                 incoming=[demo_seed.MSN_ZENTRALE, demo_seed.MSN_PETRA, "99610", "99613"]),
            dict(idx=2, name="Mobilteil 2", intern="**611", ring_all=False, out_num=demo_seed.MSN_CLEMENS, out_only=False,
                 incoming=[demo_seed.MSN_CLEMENS, "99611", "99613"]),
            dict(idx=3, name="Empfang", intern="**612", ring_all=True, out_num=demo_seed.MSN_ZENTRALE, out_only=False,
                 incoming=[demo_seed.MSN_ZENTRALE]),
            dict(idx=4, name="Mobilteil Lager", intern="**613", ring_all=True, out_num=demo_seed.MSN_ZENTRALE, out_only=False,
                 incoming=[demo_seed.MSN_ZENTRALE])]
    fritzweb.dect_phones = lambda fresh=False: [dict(p) for p in dect]
    presence.hosts = lambda force=False: [
        {"mac": "02:00:00:00:00:01", "name": "Smartphone-Petra", "ip": "192.168.178.31", "active": True, "wlan": True},
        {"mac": "02:00:00:00:00:02", "name": "Notebook-Technik", "ip": "192.168.178.32", "active": False, "wlan": True}]
    presence.fritz_active = lambda mac: True

    contacts = "".join(f"  Contact:  {r}/sip:{r}@{REG_IP}:5060   abcd1234 Avail 12.3\n" for r in ("99610", "99611", "99613", "99620"))

    async def command(cmd, timeout_s=15):
        return contacts if "show contacts" in cmd else ""

    async def ok(*a, **k):
        return True, ""

    async def action(name, fields=None, list_end=None, timeout_s=10):
        return {"Response": "Success"}, []

    async def registration_status(name, timeout_s=10):
        return "Registered"

    async def bridged_peer(chan, timeout_s=10):
        return ""
    for n, f in dict(originate=ok, redirect=ok, play_into=ok, action=action, command=command,
                     registration_status=registration_status, bridged_peer=bridged_peer).items():
        setattr(ami, n, f)
    gclient.ready = lambda kind: True
    astconf.db_creds = lambda: ("LieschenMueller", "x", "192.168.178.1")

    async def none(*a, **k):
        return None
    main.warm_agent = none
    main.ring_apply = lambda: none()

    def no_mail(*a, **k):
        raise RuntimeError("In der Demo werden keine Mails gesendet")
    notify.send_mail = no_mail


async def seed():
    db.conn()
    config.apply()
    users.create("admin", demo_seed.DEMO_LOGINS["admin"], "admin")
    main.app.dependency_overrides[main.auth] = lambda: main.PSEUDO_ADMIN
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, client=("127.0.0.1", 40000)),
                                     base_url="http://127.0.0.1:8080") as http:
            ids = await demo_seed.seed_all(http, db)
    finally:
        main.app.dependency_overrides.clear()
    # Einstellungen, die sonst die Fritzbox/Asterisk-Seite liefern (nur Anzeige)
    for k, v in (("fritz_host", "192.168.178.1"), ("fritz_user", "LieschenMueller"), ("fritz_password", "DemoSip-0000-x"),
                 ("fritz_tr064_user", "lieschen"), ("fritz_tr064_password", "demo-tr064-0000"),
                 ("gemini_api_key", "demo-key-not-real-0000"), ("retention_days", "90")):
        config.set_value(k, v)
    return ids


async def run():
    install_fakes()
    ids = await seed()
    print("Demo-Daten:", ids, flush=True)
    import types as _t
    from app import plugins
    plugins.load_all(_t.SimpleNamespace(start_outbound=main.start_outbound, dial_number=ami.dial_number), DATA)
    plugins.mount(main.app, main.auth)
    uds = os.environ.get("DEMO_UDS", "")         # Unix-Socket: Container ohne Netz (--network none), tools/demo_shots.py leitet weiter
    cfg = uvicorn.Config(main.app, uds=uds, log_level="warning") if uds else uvicorn.Config(main.app, host="0.0.0.0", port=8080,
                                                                                          log_level="warning")
    await uvicorn.Server(cfg).serve()


if __name__ == "__main__":
    asyncio.run(run())
