#!/usr/bin/env python3
"""Screenshots der Weboberflaeche fuer README und Wiki (docs/img/) - nur mit erfundenen Demo-Daten.

Ablauf: Wegwerf-Bridge (tools/demo_server.py) im Test-Image lm-test OHNE Netz starten (--network none, Unix-Socket), eine
Weiterleitung nur auf 127.0.0.1 oeffnen, Firefox headless ueber Marionette (ohne geckodriver) steuern und nur den Seiteninhalt
aufnehmen (keine Adresszeile, keine Fensterleiste). Danach mit ImageMagick verkleinern/komprimieren.

    tools/demo_shots.py              alle Bilder nach docs/img/
    tools/demo_shots.py --only calls,detail
    tools/demo_shots.py --serve      nur die Demo-Bridge starten (http://127.0.0.1:18093/login), Strg+C beendet

Braucht: podman, Image lm-test (podman build -t lm-test bridge), firefox, ImageMagick (magick)."""
import argparse
import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tools.demo_seed import DEMO_LOGINS  # noqa: E402

PORT = 18093
BASE = f"http://127.0.0.1:{PORT}"
OUT = os.path.join(ROOT, "docs", "img")
WIDTH = 1280


# --- Demo-Bridge im Container ohne Netz, Weiterleitung 127.0.0.1:PORT -> Unix-Socket ---------------------------------
def start_bridge(tmp):
    sock_dir = os.path.join(tmp, "sock")
    os.makedirs(sock_dir)
    name = f"lm-demo-{os.getpid()}"
    cmd = ["podman", "run", "--rm", "--name", name, "--network", "none", "-v", f"{ROOT}:/work:ro,z", "-v", f"{sock_dir}:/sock:z",
           "-w", "/work", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "DEMO_UDS=/sock/web.sock", "lm-test", "python", "tools/demo_server.py"]
    proc = subprocess.Popen(cmd)
    path = os.path.join(sock_dir, "web.sock")
    for _ in range(300):
        if os.path.exists(path):
            break
        if proc.poll() is not None:
            raise SystemExit("Demo-Bridge beendet sich sofort (siehe Ausgabe oben)")
        time.sleep(0.2)
    else:
        raise SystemExit("Demo-Bridge startet nicht")
    return proc, name, path


def start_proxy(uds):
    async def pipe(r, w):
        try:
            while data := await r.read(65536):
                w.write(data)
                await w.drain()
        except Exception:
            pass
        finally:
            w.close()

    async def handle(cr, cw):
        try:
            ur, uw = await asyncio.open_unix_connection(uds)
        except OSError:
            cw.close()
            return
        await asyncio.gather(pipe(cr, uw), pipe(ur, cw))

    async def serve():
        srv = await asyncio.start_server(handle, "127.0.0.1", PORT)
        async with srv:
            await srv.serve_forever()
    threading.Thread(target=lambda: asyncio.run(serve()), daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", PORT), 1).close()
            return
        except OSError:
            time.sleep(0.1)


# --- Marionette (Firefox-Fernsteuerung, ohne geckodriver) -------------------------------------------------------------
class Marionette:
    def __init__(self, port):
        for _ in range(150):
            try:
                self.s = socket.create_connection(("127.0.0.1", port), 2)
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise SystemExit("Firefox-Marionette nicht erreichbar")
        self.s.settimeout(60)
        self.buf = b""
        self.id = 0
        self._read()                                      # Begruessung
        self.cmd("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {"acceptInsecureCerts": True}}})

    def _read(self):
        while b":" not in self.buf:
            self.buf += self.s.recv(65536)
        n, _, rest = self.buf.partition(b":")
        n = int(n)
        while len(rest) < n:
            rest += self.s.recv(1 << 20)
        self.buf = rest[n:]
        return json.loads(rest[:n])

    def cmd(self, name, params=None):
        self.id += 1
        data = json.dumps([0, self.id, name, params or {}]).encode()
        self.s.sendall(str(len(data)).encode() + b":" + data)
        while True:
            msg = self._read()
            if msg[0] == 1 and msg[1] == self.id:
                if msg[2]:
                    raise RuntimeError(f"{name}: {msg[2]}")
                return msg[3]

    def js(self, script, *args):
        return self.cmd("WebDriver:ExecuteScript", {"script": script, "args": list(args)}).get("value")

    def go(self, url):
        self.cmd("WebDriver:Navigate", {"url": url})

    def chrome_js(self, script):
        self.cmd("Marionette:SetContext", {"value": "chrome"})
        try:
            return self.js(script)
        finally:
            self.cmd("Marionette:SetContext", {"value": "content"})

    def dark(self, on):
        # 0 = dunkel, 1 = hell (prefers-color-scheme der Seiten)
        self.chrome_js(f'Services.prefs.setIntPref("layout.css.prefers-color-scheme.content-override", {0 if on else 1});')

    def size(self, w, h):
        self.cmd("WebDriver:SetWindowRect", {"width": w, "height": h})
        iw, ih = self.js("return [window.innerWidth, window.innerHeight]")
        self.cmd("WebDriver:SetWindowRect", {"width": w + (w - iw), "height": h + (h - ih)})

    def shot(self, path, full=False):
        b = self.cmd("WebDriver:TakeScreenshot", {"full": full}).get("value")
        with open(path, "wb") as f:
            f.write(base64.b64decode(b))


def start_firefox(tmp):
    prof = os.path.join(tmp, "profile")
    os.makedirs(prof)
    port = 28290
    with open(os.path.join(prof, "user.js"), "w") as f:
        for k, v in {"marionette.port": port, "browser.shell.checkDefaultBrowser": False, "datareporting.policy.dataSubmissionEnabled": False,
                     "toolkit.telemetry.reportingpolicy.firstRun": False, "browser.startup.homepage_override.mstone": "ignore",
                     "intl.accept_languages": "de-DE", "intl.locale.requested": "de-DE", "intl.regional_prefs.use_os_locales": True,
                     "layout.css.prefers-color-scheme.content-override": 1, "ui.prefersReducedMotion": 1,
                     "network.proxy.type": 0, "app.update.enabled": False, "browser.aboutwelcome.enabled": False,
                     "font.default.x-western": "sans-serif"}.items():
            f.write(f"user_pref({json.dumps(k)}, {json.dumps(v)});\n")
    env = dict(os.environ, MOZ_HEADLESS="1", TZ="Europe/Berlin", LANG="de_DE.UTF-8", LC_ALL="de_DE.UTF-8")
    proc = subprocess.Popen(["firefox", "--headless", "--marionette", "-remote-allow-system-access", "--no-remote", "--profile", prof, "about:blank"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, Marionette(port)


# --- Ansichten ----------------------------------------------------------------------------------------------------
def login(m, user):
    m.go(BASE + "/favicon.svg")                      # gleiche Herkunft, ohne Skript der Seite (die Anmeldeseite leitet weiter)
    m.js("""const [u, p] = arguments; return fetch('/api/logout', {method: 'POST', headers: {'content-type': 'application/json'}, body: '{}'})
            .then(() => fetch('/api/login', {method: 'POST', headers: {'content-type': 'application/json'},
                   body: JSON.stringify({username: u, password: p})})).then(r => r.status)""", user, DEMO_LOGINS[user])


def wait_idle(m, extra=0.8):
    for _ in range(50):
        if m.js("return document.readyState") == "complete" and not m.js("return !!document.querySelector('.spin')"):
            break
        time.sleep(0.2)
    time.sleep(extra)


TAB = "[...document.querySelectorAll('button,a')].find(e => e.textContent.trim() === %s).click()"


def view(m, hash_, after=None):
    m.go(BASE + "/#" + hash_)
    wait_idle(m)
    if after:
        m.js(after)
        wait_idle(m, 0.6)


def run_shots(m, only):
    shots = SHOTS if not only else [s for s in SHOTS if s[0] in only]
    who = None
    for name, user, hash_, after, height, dark in shots:
        if user != who:
            login(m, user)
            who = user
        m.dark(dark)
        m.size(WIDTH, height or 900)
        view(m, hash_, after)
        raw = os.path.join(OUT, name + ".raw.png")
        m.shot(raw, full=height is None)          # None = ganze Seite
        dst = os.path.join(OUT, name + ".png")
        subprocess.run(["magick", raw, "-strip", "-colors", "256", "-define", "png:compression-level=9", dst], check=True)
        os.remove(raw)
        print(f"{dst}: {os.path.getsize(dst) // 1024} KB")


# Ablauf aufklappen, Transkript ganz zeigen (sonst ein Rollbereich, ans Ende gerollt)
OPEN = ("document.querySelectorAll('details.metacard').forEach(d => d.open = /Ablauf/.test(d.querySelector('summary').textContent));"
        "document.querySelectorAll('.chat').forEach(c => {c.style.maxHeight = 'none'; c.scrollTop = 0})")
GROUP = "try {localStorage.setItem('lgroup', '1')} catch (e) {} location.reload()"
# (Dateiname, Benutzer, Ansicht, JS danach, Hoehe (None = ganze Seite), dunkel)
SHOTS = [
    ("anrufliste", "admin", "/calls", GROUP, 900, False),
    ("anruf-detail", "admin", "/calls/BIG", OPEN, None, False),
    ("benutzer", "admin", "/staff", None, 805, False),
    ("benutzer-editor", "admin", "/staff/PETRA", TAB % '"Erreichbarkeit"', None, False),
    ("regeln", "admin", "/rules", None, 720, False),
    ("rufnummern", "admin", "/numbers", None, None, False),
    ("einrichtung", "admin", "/setup", None, 1000, False),
    ("hilfe", "admin", "/help", None, 900, False),
    ("meine-einstellungen", "petra", "/account", TAB % '"Ausgehende Gespräche"', 720, False),
    ("anrufliste-dunkel", "admin", "/calls", GROUP, 900, True),
    ("anruf-detail-dunkel", "admin", "/calls/BIG", OPEN, 1300, True),
    ("benutzer-dunkel", "admin", "/staff", None, 805, True),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--keep", action="store_true", help="Demo-Bridge und Firefox nach den Bildern laufen lassen")
    a = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix="lmshots-")
    bridge, name, uds = start_bridge(tmp)
    ff = None
    try:
        start_proxy(uds)
        if a.serve:
            print(f"Demo-Bridge: {BASE}/login  (Strg+C beendet)")
            bridge.wait()
            return
        os.makedirs(OUT, exist_ok=True)
        ff, m = start_firefox(tmp)
        global SHOTS
        login(m, "admin")
        m.go(BASE + "/")
        wait_idle(m)
        calls = m.js("return fetch('/api/calls?size=50').then(r => r.json())")["items"]
        big = next(c["id"] for c in calls if c.get("meta") and "Heizungssteuerung" in c["meta"])
        staff = m.js("return fetch('/api/staff').then(r => r.json())")
        petra = next(s["id"] for s in staff if s["name"] == "Petra Muster")
        SHOTS = [(n, u, h.replace("BIG", str(big)).replace("PETRA", str(petra)), x, hh, d) for n, u, h, x, hh, d in SHOTS]
        run_shots(m, {x for x in a.only.split(",") if x})
        if a.keep:
            print(f"Demo-Bridge laeuft weiter: {BASE}/login  (Strg+C beendet)")
            bridge.wait()
    finally:
        if ff:
            ff.terminate()
        subprocess.run(["podman", "stop", "-t", "2", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        bridge.wait()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
