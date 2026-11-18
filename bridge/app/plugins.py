"""Plugin-System.

Ein Plugin ist ein Python-Paket (Ordner mit __init__.py und optional plugin.json), das beim Start geladen wird aus
  - app/plugins_builtin/<name>/   (mit der App ausgeliefert, z. B. "campaigns")
  - <Datenordner>/plugins/<name>/ (eigene Plugins; Ordner /data/plugins im Container)
und in __init__.py eine Funktion register(api) bereitstellt. Plugins laufen im Prozess der App mit allen ihren Rechten:
nur vertrauenswuerdige Plugins installieren. Einzelheiten und ein Beispiel: docs/PLUGINS.md.

Eigene Plugins (Datenordner) werden erst importiert, wenn ein Administrator sie freigegeben hat (meta plugin_trusted:<name>,
in der Oberflaeche: Plugin einschalten, danach Bridge neu starten). Beim ersten Start mit dieser Regel werden die schon
vorhandenen Plugins freigegeben (sie liefen vorher auch), damit bestehende Installationen gleich bleiben.
"""
import asyncio
import importlib.util
import inspect
import json
import logging
import os
import re
import sys
import time

from . import db
from .events import hub

log = logging.getLogger("plugins")
API_VERSION = 1
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
PLUGINS = {}          # name -> Plugin
_core = None          # Dienste des Kerns (von main gesetzt)


class Plugin:
    def __init__(self, name, path, manifest, builtin):
        self.name, self.path, self.manifest, self.builtin = name, path, manifest, builtin
        self.hooks, self.tools, self.prompts, self.pages, self.tasks = {}, {}, [], [], []
        self.routers = []
        self.error = ""
        self.loaded = False
        self.restart = False          # Freigabe geaendert: wirkt erst nach einem Neustart der Bridge

    @property
    def title(self):
        return self.manifest.get("title") or self.name

    @property
    def trusted(self):
        """Eingebaute Plugins immer; eigene nur nach Freigabe durch einen Administrator."""
        return self.builtin or db.get_meta(f"plugin_trusted:{self.name}", "") == "1"

    @property
    def enabled(self):
        v = db.get_meta(f"plugin_enabled:{self.name}", "")
        if v == "":
            return bool(self.manifest.get("default_enabled", self.builtin))
        return v == "1"

    def fail(self, where, exc):
        self.error = f"{where}: {type(exc).__name__}: {exc}"[:300]
        log.exception("Plugin %s (%s)", self.name, where)

    def info(self):
        return {"name": self.name, "title": self.title, "version": self.manifest.get("version", ""),
                "description": self.manifest.get("description", ""), "author": self.manifest.get("author", ""),
                "builtin": self.builtin, "enabled": self.enabled and self.trusted, "loaded": self.loaded, "error": self.error,
                "trusted": self.trusted, "restart": self.restart,
                "pages": [{"id": p["id"], "title": p["title"], "menu": p["menu"], "js": p["js"], "icon": p.get("icon", "")} for p in self.pages]}


class PluginAPI:
    """Was ein Plugin in register(api) benutzen kann."""
    version = API_VERSION

    def __init__(self, plugin):
        self._p = plugin
        self.name, self.dir = plugin.name, plugin.path
        self.log = logging.getLogger(f"plugin.{plugin.name}")
        self.db = db                                  # q(), x(), x_count(), get_meta(), list_agents(), list_contacts(), list_staff() ...

    # --- Zustand ---
    def enabled(self):
        return self._p.enabled

    def get_setting(self, key, default=""):
        return db.get_meta(f"plg:{self.name}:{key}", default)

    def set_setting(self, key, value):
        db.set_meta(f"plg:{self.name}:{key}", value)

    def schema(self, sql):
        """Eigene Tabellen anlegen (CREATE TABLE IF NOT EXISTS ...). Tabellennamen mit dem Plugin-Namen beginnen lassen."""
        with db._lock:
            db.conn().executescript(sql)
            db.conn().commit()

    # --- Ereignisse ---
    def on(self, event, fn):
        """event: 'startup' | 'call_end' (call, meta) | 'call_unreached' (meta, grund). fn darf async sein."""
        self._p.hooks.setdefault(event, []).append(fn)

    def every(self, seconds, fn):
        """fn (async oder sync) alle `seconds` Sekunden aufrufen, solange das Plugin eingeschaltet ist."""
        self._p.tasks.append((float(seconds), fn))

    # --- KI-Werkzeuge und Prompt ---
    def tool(self, name, make_decl, handler):
        """Werkzeug fuer die KI. make_decl(call) -> google.genai.types.FunctionDeclaration oder None (fuer diesen Anruf nicht anbieten);
        handler(call, args) (async) -> dict (Antwort an die KI)."""
        from .call import CORE_TOOLS
        if name in CORE_TOOLS:
            raise ValueError(f"Werkzeugname '{name}' ist ein eingebautes Werkzeug und nicht verwendbar")
        self._p.tools[name] = (make_decl, handler)

    def prompt(self, fn):
        """fn(call) -> Text oder None: wird dem System-Prompt der KI angehaengt (nur wenn eingeschaltet)."""
        self._p.prompts.append(fn)

    def call_data(self, call):
        """Eigener Speicher des Plugins je Anruf (dict)."""
        return call.pd.setdefault(self.name, {})

    # --- Web ---
    def router(self):
        """FastAPI-Router; Pfade liegen unter /api/plugins/<name>/ und sind nur fuer Administratoren."""
        from fastapi import APIRouter
        r = APIRouter()
        self._p.routers.append(r)
        return r

    def page(self, title, js="ui.js", menu=True, page_id=None, icon=""):
        """Eigene Seite im Menue. js: Datei in <Plugin>/static/, ein ES-Modul mit  export async function view(ctx). icon: Name eines Symbols der Oberflaeche (z. B. campaign, mail, staff), leer = Plugin-Symbol."""
        self._p.pages.append({"id": page_id or self.name, "title": title, "js": js, "menu": menu, "icon": icon})

    # --- Dienste des Kerns ---
    async def start_outbound(self, number, name="", task="", agent=None, owner="", extra=None, label="Ausgehend"):
        """Startet einen ausgehenden KI-Anruf; gibt die Anruf-ID zurueck. extra: weitere Felder fuer call.meta."""
        return await _core.start_outbound(number, name, task, agent, owner, extra, label)

    def dial_number(self, raw):
        """Rufnummer pruefen/normalisieren (Wahlsperre); ValueError bei ungueltiger oder gesperrter Nummer."""
        return _core.dial_number(raw)

    def publish(self, event):
        hub.publish(event)


def _load_dir(base, builtin):
    if not os.path.isdir(base):
        return
    for name in sorted(os.listdir(base)):
        path = os.path.join(base, name)
        init = os.path.join(path, "__init__.py")
        if not os.path.isfile(init) or name.startswith((".", "_")):
            continue
        if not NAME_RE.match(name):
            log.warning("Plugin-Ordner %r uebersprungen: Name muss [a-z][a-z0-9_]* sein", name)
            continue
        if name in PLUGINS:
            log.warning("Plugin %r doppelt (%s) - uebersprungen", name, path)
            continue
        manifest = {}
        try:
            mf = os.path.join(path, "plugin.json")
            if os.path.isfile(mf):
                manifest = json.load(open(mf, encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("Plugin %s: plugin.json unlesbar (%s)", name, e)
        p = Plugin(name, path, manifest, builtin)
        PLUGINS[name] = p
        if int(manifest.get("api", API_VERSION)) > API_VERSION:
            p.error = f"braucht Plugin-API {manifest.get('api')} (vorhanden: {API_VERSION})"
            continue
        if not p.trusted:            # nicht importieren (register() liefe sonst schon beim Start mit allen Rechten)
            log.info("Plugin %s nicht freigegeben - nicht geladen (in der Oberflaeche einschalten, dann Bridge neu starten)", name)
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"lmplugin_{name}", init, submodule_search_locations=[path])
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            mod.register(PluginAPI(p))
            p.loaded = True
        except Exception as e:
            p.fail("Laden", e)
    return


def load_all(core, data_dir):
    """Plugins laden. core: Objekt mit start_outbound(...) und dial_number(...)."""
    global _core
    _core = core
    _load_dir(os.path.join(os.path.dirname(__file__), "plugins_builtin"), True)
    own = os.environ.get("PLUGIN_DIR") or os.path.join(data_dir, "plugins")
    migrate_trust(own)
    _load_dir(own, False)
    log.info("Plugins: %s", ", ".join(f"{p.name}{'' if p.loaded else ' (Fehler)' if p.trusted else ' (nicht freigegeben)'}"
                                      for p in PLUGINS.values()) or "keine")


def migrate_trust(base):
    """Einmalig: Plugins, die vor der Freigabe-Regel im Ordner lagen (und damit geladen wurden), gelten als freigegeben."""
    if db.get_meta("plugins_trust_migrated", "") == "1":
        return
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            if NAME_RE.match(name) and os.path.isfile(os.path.join(base, name, "__init__.py")):
                db.set_meta(f"plugin_trusted:{name}", "1")
                log.info("Plugin %s: war schon vorhanden, gilt als freigegeben", name)
    db.set_meta("plugins_trust_migrated", "1")


def active():
    return [p for p in PLUGINS.values() if p.loaded and p.enabled]


# --- vom Kern aufgerufen ---
async def emit(event, *args):
    for p in active():
        for fn in p.hooks.get(event, []):
            try:
                r = fn(*args)
                if inspect.isawaitable(r):
                    await asyncio.wait_for(r, 30)
            except Exception as e:
                p.fail(f"Ereignis {event}", e)


def tool_decls(call):
    out = []
    for p in active():
        for name, (make_decl, _) in p.tools.items():
            try:
                d = make_decl(call)
            except Exception as e:
                p.fail(f"Werkzeug {name}", e)
                continue
            if d is not None:
                out.append(d)
    return out


async def run_tool(call, name, args):
    """None, wenn kein Plugin das Werkzeug kennt."""
    for p in active():
        t = p.tools.get(name)
        if t:
            try:
                return await t[1](call, args)
            except Exception as e:
                p.fail(f"Werkzeug {name}", e)
                return {"fehler": "Interner Fehler im Plugin"}
    return None


def prompt_parts(call):
    out = []
    for p in active():
        for fn in p.prompts:
            try:
                t = fn(call)
            except Exception as e:
                p.fail("Prompt", e)
                continue
            if t:
                out.append(t)
    return out


def start_tasks(track):
    """Periodische Aufgaben starten (einmal beim Start); sie laufen nur, solange das jeweilige Plugin eingeschaltet ist."""
    for p in PLUGINS.values():
        if not p.loaded:
            continue
        for seconds, fn in p.tasks:
            async def loop(p=p, seconds=seconds, fn=fn):
                while True:
                    await asyncio.sleep(seconds)
                    if not p.enabled:
                        continue
                    try:
                        r = fn()
                        if inspect.isawaitable(r):
                            await r
                    except Exception as e:
                        p.fail("Aufgabe", e)
            track(asyncio.create_task(loop()))


def mount(app, auth):
    """Routen der Plugins einbinden und die Verwaltungs-Schnittstelle bereitstellen."""
    from fastapi import Depends, HTTPException, Request
    from fastapi.responses import FileResponse

    def guard(name):
        def dep():
            if not (PLUGINS[name].loaded and PLUGINS[name].enabled):
                raise HTTPException(404, "Plugin ist ausgeschaltet oder nicht geladen")
        return dep

    for p in PLUGINS.values():
        for r in p.routers:
            app.include_router(r, prefix=f"/api/plugins/{p.name}", dependencies=[Depends(auth), Depends(guard(p.name))])

    @app.get("/api/plugins", dependencies=[Depends(auth)])
    def plugins_list():
        return [p.info() for p in PLUGINS.values()]

    @app.put("/api/plugins/{name}/enabled", dependencies=[Depends(auth)])
    async def plugin_enabled(name: str, request: Request):
        if name not in PLUGINS:
            raise HTTPException(404)
        on = bool((await request.json()).get("enabled"))
        p = PLUGINS[name]
        db.set_meta(f"plugin_enabled:{name}", "1" if on else "0")
        if not p.builtin and on != p.trusted:
            # Einschalten = Freigabe (wird beim naechsten Start importiert); Ausschalten nimmt die Freigabe zurueck
            # (sofort ohne Wirkung, ab dem naechsten Start auch nicht mehr importiert)
            db.set_meta(f"plugin_trusted:{name}", "1" if on else "0")
            log.info("Plugin %s %s", name, "freigegeben (wird nach einem Neustart der Bridge geladen)" if on else "nicht mehr freigegeben")
        p.restart = not p.builtin and on and not p.loaded
        return p.info()

    @app.get("/plugins/{name}/{path:path}", dependencies=[Depends(auth)])
    def plugin_static(name: str, path: str):
        p = PLUGINS.get(name)
        if not p or not p.loaded or not p.enabled:
            raise HTTPException(404)
        root = os.path.realpath(os.path.join(p.path, "static"))
        f = os.path.realpath(os.path.join(root, path))
        if not f.startswith(root + os.sep) or not os.path.isfile(f):
            raise HTTPException(404)
        return FileResponse(f, headers={"Cache-Control": "no-cache"})
