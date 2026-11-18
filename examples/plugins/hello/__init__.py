"""Beispiel-Plugin: zeigt die wichtigsten Teile der Plugin-Schnittstelle (siehe docs/PLUGINS.md)."""
from datetime import datetime

from google.genai import types


def register(api):
    # Eigene Tabelle (Namen mit dem Plugin-Namen beginnen lassen)
    api.schema("CREATE TABLE IF NOT EXISTS hello_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, call_id INTEGER);")

    # Ereignis: nach jedem Anruf mitzaehlen
    async def on_end(call, meta):
        api.db.x("INSERT INTO hello_log(ts, call_id) VALUES(strftime('%s','now'), ?)", (meta["call_id"],))
    api.on("call_end", on_end)

    # KI-Werkzeug (nur fuer eingehende Anrufe anbieten)
    def decl(call):
        if call.meta.get("direction") != "out":
            return types.FunctionDeclaration(name="tell_time", description="Nennt die aktuelle Uhrzeit.")
    async def handler(call, args):
        return {"uhrzeit": datetime.now().strftime("%H:%M")}
    api.tool("tell_time", decl, handler)

    # Eigene Web-Schnittstelle: GET /api/plugins/hello/stats (nur Administratoren)
    r = api.router()
    @r.get("/stats")
    def stats():
        return {"anrufe_gezaehlt": api.db.q("SELECT COUNT(*) AS n FROM hello_log")[0]["n"]}

    # Eigene Seite im Menue (static/ui.js)
    api.page("Hallo-Beispiel")
