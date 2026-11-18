# Plugins

Die App lässt sich über Plugins erweitern. Das Paket **Kampagnen** ist ein Plugin (`bridge/app/plugins_builtin/campaigns/`)
und dient als größeres Beispiel; ein kleines findest du in `examples/plugins/hello/`.

> **Sicherheit:** Plugins laufen im Prozess der App mit allen ihren Rechten (Anrufe, Kontakte, Zugangsdaten aus der Umgebung).
> Nur Plugins aus vertrauenswürdiger Quelle installieren.

## Installieren
1. Plugin-Ordner nach `data/plugins/<name>/` kopieren (im Container `/data/plugins/`; anderer Ort über `PLUGIN_DIR`).
2. Bridge neu starten: Das Plugin erscheint unter **Plugins** als „nicht freigegeben“; sein Code wird **noch nicht** ausgeführt.
3. In der Oberfläche unter **Plugins** einschalten. Das ist die **Freigabe** (meta `plugin_trusted:<name>`).
4. Bridge noch einmal neu starten: Erst jetzt wird das Plugin importiert und `register(api)` läuft.

Eigene Plugins werden nur nach dieser ausdrücklichen Freigabe durch einen Administrator importiert (sonst liefe `register()` beim Start mit allen Rechten, auch für ein untergeschobenes Plugin); `default_enabled` in `plugin.json` gilt erst nach der Freigabe. Ausschalten nimmt die Freigabe zurück (sofort ohne Wirkung, ab dem nächsten Start nicht mehr importiert). Eingebaute Plugins (`plugins_builtin/`, z. B. Kampagnen) sind immer freigegeben. **Umstellung:** Beim ersten Start mit dieser Regel gelten alle Plugins, die schon im Ordner liegen, als freigegeben (sie liefen vorher auch); ihr Ein/Aus-Zustand bleibt.

Der Ordnername ist der Plugin-Name (`[a-z][a-z0-9_]{1,30}`, also 2 bis 31 Zeichen). Ein- und Ausschalten wirkt sofort für Werkzeuge, Prompt, Ereignisse,
Web-Schnittstelle, Seiten und periodische Aufgaben eines geladenen Plugins; neuer oder geänderter Code und die erste Freigabe brauchen einen Neustart.

## Aufbau
```
<name>/
  plugin.json     Name, Titel, Version, Beschreibung, "api": 1, "default_enabled"
  __init__.py     def register(api): ...
  static/ui.js    optionale Seite (ES-Modul mit  export async function view(ctx))
```

## Die Schnittstelle `api` (Version 1)
| Aufruf | Zweck |
|---|---|
| `api.schema(sql)` | eigene Tabellen anlegen (`CREATE TABLE IF NOT EXISTS …`, Namen mit Plugin-Namen beginnen) |
| `api.db` | Datenbank des Kerns: `q(sql, args)`, `x(sql, args)`, `x_count(...)`, `get_meta`, `list_agents`, `list_contacts`, `list_staff` … |
| `api.get_setting(key)` / `api.set_setting(key, v)` | einfache Einstellungen des Plugins |
| `api.on(event, fn)` | Ereignisse: `startup`, `call_end(call, meta)`, `call_unreached(meta, grund)` (fn darf `async` sein) |
| `api.every(sekunden, fn)` | periodische Aufgabe, läuft nur bei eingeschaltetem Plugin |
| `api.tool(name, make_decl, handler)` | Werkzeug für die KI. `make_decl(call)` liefert eine `FunctionDeclaration` oder `None` (nicht anbieten); `handler(call, args)` gibt ein dict zurück |
| `api.prompt(fn)` | `fn(call)` liefert Text, der dem System-Prompt der KI angehängt wird |
| `api.call_data(call)` | eigener Speicher des Plugins je Anruf (dict) |
| `api.router()` | FastAPI-Router unter `/api/plugins/<name>/…` (nur Administratoren) |
| `api.page(titel, js="ui.js")` | Seite im Menü; `js` liegt in `static/` |
| `api.start_outbound(number, name, task, agent, owner, extra, label)` | startet einen ausgehenden KI-Anruf, gibt die Anruf-ID zurück; `extra` landet in `call.meta` |
| `api.dial_number(raw)` | Rufnummer prüfen/normalisieren (Wahlsperre), `ValueError` bei ungültig |
| `api.publish(event)` | Ereignis an die Oberfläche (Live-Anzeige) |
| `api.log` | Logger |

Das `call`-Objekt (Klasse `Call`) bietet u. a. `call.meta`, `call.event(sprecher, text)`, `call.note(schluessel, wert)`,
`call.result` (Ergebnis von `save_result`: `ergebnis`, `zusammenfassung`, `erledigt`) und `call.caller_spoke`.

Fehler in einem Plugin werden abgefangen, im Log und auf der Plugin-Seite angezeigt und stören die Telefonanlage nicht.

## Oberfläche
`static/ui.js` exportiert `view(ctx)`. `ctx` enthält `root` (Element der Seite), `$`, `api(url)` (GET → JSON), `post(url, body)`,
`esc`, `badge`, `fmt`, `getContacts()` und `openCall(id)`. Aufrufe aus HTML-Attributen (`onclick="…"`) brauchen globale Namen,
z. B. ein Objekt `window.P_<name>` (siehe das Kampagnen-Plugin).
