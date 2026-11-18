"""Plugin "Kampagnen": mehrere ausgehende Anrufe nacheinander (z. B. Termine vereinbaren), mit Terminpool, Wiederholungen,
Anrufzeiten und Anrufbeantworter-Erkennung. Ein Beispiel fuer die Plugin-Schnittstelle (siehe docs/PLUGINS.md)."""
import asyncio
import json
import re
import time
from datetime import date, datetime, timedelta

from fastapi import HTTPException, Request
from google.genai import types

from app import gcal

from . import logic


def register(api):
    logic.api = api
    api.schema(logic.SCHEMA)
    logic.ensure_columns()
    db = api.db
    q, x = logic.q, logic.x

    # ---------- KI-Werkzeuge und Prompt ----------
    def mine(call):
        return call.meta.get("plg_campaigns")

    def decl_machine(call):
        if mine(call):
            return types.FunctionDeclaration(
                name="answering_machine_detected",
                description="Rufe das sofort auf, wenn sich ein Anrufbeantworter oder eine Bandansage meldet statt einer Person.")

    def decl_slots(call):
        m = mine(call)
        if m and m["campaign"].get("pool_id"):
            return types.FunctionDeclaration(
                name="get_free_slots", description="Liefert die naechsten freien Termine aus dem Terminpool.",
                parameters=types.Schema(type="OBJECT", properties={
                    "anzahl": types.Schema(type="INTEGER", description="Wie viele Termine (1 bis 5, Standard 3)"),
                    "ab_uhrzeit": types.Schema(type="STRING", description="Nur Termine ab dieser Uhrzeit, z. B. '13:00' (wenn die Person spaeter moechte)"),
                    "ab_tag": types.Schema(type="STRING", description="Nur Termine ab diesem Datum YYYY-MM-DD (wenn ein anderer Tag gewuenscht ist)")}))

    def decl_book(call):
        m = mine(call)
        if m and m["campaign"].get("pool_id"):
            return types.FunctionDeclaration(
                name="book_slot", description="Bucht einen freien Termin fuer die angerufene Person. Nur nach ausdruecklicher Zustimmung.",
                parameters=types.Schema(type="OBJECT", required=["slot_id"], properties={
                    "slot_id": types.Schema(type="INTEGER", description="ID aus get_free_slots")}))

    async def h_machine(call, args):
        m, flags = mine(call), api.call_data(call)
        camp = m["campaign"]
        flags["machine"] = True
        call.event("system", "Anrufbeantworter erkannt")
        if camp["vm_mode"] == "message" and (camp.get("vm_text") or "").strip():
            flags["vm_left"] = True
            txt = camp["vm_text"].replace("{name}", m["target"].get("name", ""))
            return {"ok": True, "hinweis": "Warte kurz auf den Signalton. Sprich dann deutlich diese Nachricht (nur diese, "
                                           f"nicht mehr): {txt}  Danach rufe save_result auf (ergebnis 'Anrufbeantworter', "
                                           "erledigt=false) und anschliessend end_call."}
        return {"ok": True, "hinweis": "Sprich nichts. Rufe save_result auf (ergebnis 'Anrufbeantworter', erledigt=false) "
                                       "und danach sofort end_call."}

    async def h_slots(call, args):
        camp = mine(call)["campaign"]
        p = logic.pool(camp.get("pool_id"))
        if p and p["kind"] == "gcal":
            try:
                await asyncio.to_thread(logic.sync_pool, p)
            except gcal.CalendarError as e:
                call.event("system", f"Kalender: {e}")
                return {"termine": [], "hinweis": "Der Kalender ist gerade nicht erreichbar. Biete keine Termine an, sondern einen Rueckruf (erledigt=false)."}
        fl = api.call_data(call)
        seen = fl.setdefault("offered_ids", set())
        allf = logic.free_slots(camp.get("pool_id"), 6, camp.get("pause_min") or 0, limit=500)
        t_from = None
        try:
            if args.get("ab_tag"):
                t_from = time.mktime(time.strptime(str(args["ab_tag"])[:10], "%Y-%m-%d"))
            if args.get("ab_uhrzeit"):
                hh, mm = str(args["ab_uhrzeit"]).split(":")[:2]
                base = time.localtime(allf[0]["start_ts"] if allf and not t_from else (t_from or time.time()))
                t_from = max(t_from or 0, time.mktime((base.tm_year, base.tm_mon, base.tm_mday, int(hh), int(mm), 0, 0, 0, -1)))
        except (ValueError, IndexError, OverflowError):
            t_from = None
        cand = [s_ for s_ in allf if (t_from is None or s_["start_ts"] >= t_from) and (t_from is not None or s_["id"] not in seen)]
        n = max(1, min(int(args.get("anzahl") or 3), 5))
        slots, rest = cand[:n], cand[n:]
        seen.update(s_["id"] for s_ in slots)
        if not slots and allf:
            return {"termine": [], "weitere_termine_vorhanden": 0, "spaetester_termin_im_pool": logic.spoken_slot(allf[-1]["start_ts"])["gesprochen"],
                    "hinweis": "Du hast schon alle freien Termine genannt (bzw. es gibt keine spaeteren). Sage das ehrlich; "
                               "der spaeteste Termin ist 'spaetester_termin_im_pool'."}
        if not slots:
            return {"termine": [], "hinweis": "Zurzeit sind keine freien Termine vorhanden. Sage das und biete einen Rueckruf an (erledigt=false)."}
        x("UPDATE campaign_targets SET offered=1 WHERE id=?", (mine(call)["target"]["id"],))      # dieser Person wurden Termine angeboten
        last = allf[-1]
        return {"termine": [{"slot_id": s["id"], **logic.spoken_slot(s["start_ts"])} for s in slots],
                "weitere_termine_vorhanden": len(rest), "spaetester_termin_im_pool": logic.spoken_slot(last["start_ts"])["gesprochen"],
                "hinweis": "Sprich die Termine locker wie am Telefon, z. B. 'Montag um neun oder um halb elf'. Sage nicht bei jedem Termin 'Uhr', "
                           "nenne bei mehreren Terminen am selben Tag den Tag nur einmal und das Datum nur, wenn es nicht aus dem Wochentag klar ist. "
                           "Lies keine Zahlenformate wie 09:00 vor. Frage nach, welcher Termin passt."}

    async def h_book(call, args):
        m, flags = mine(call), api.call_data(call)
        camp, target = m["campaign"], m["target"]
        try:
            sid = int(args.get("slot_id"))
        except (TypeError, ValueError):
            return {"fehler": "slot_id fehlt"}
        if flags.get("booked"):
            return {"fehler": f"Es ist bereits ein Termin gebucht: {flags['booked']}"}
        label = logic.book(camp["pool_id"], sid, target["id"], call.meta["call_id"], camp.get("pause_min") or 0)
        if not label:
            return {"fehler": "Dieser Termin ist leider schon vergeben. Rufe get_free_slots erneut auf und biete einen anderen an."}
        p = logic.pool(camp["pool_id"])
        if p and p["kind"] == "gcal":
            try:
                await asyncio.to_thread(logic.book_in_calendar, p, camp, target, sid, call.meta["call_id"])
            except gcal.Conflict:
                await asyncio.to_thread(logic.release_slot, sid, True)
                return {"fehler": "Dieser Termin ist leider schon vergeben. Rufe get_free_slots erneut auf und biete einen anderen an."}
            except gcal.CalendarError as e:
                call.event("system", f"Kalender: {e}")
                await asyncio.to_thread(logic.release_slot, sid)
                return {"fehler": "Der Termin konnte im Kalender nicht eingetragen werden. Entschuldige dich und biete einen Rueckruf an (erledigt=false)."}
        flags["booked"] = label
        call.note("Termin gebucht", label)
        call.event("system", f"Termin gebucht: {label}" + (" (im Google Kalender eingetragen)" if p and p["kind"] == "gcal" else ""))
        d = logic.spoken_slot(logic.q("SELECT start_ts FROM slots WHERE id=?", (sid,))[0]["start_ts"])
        return {"ok": True, "termin": label, "gesprochen": d["gesprochen"],
                "hinweis": f"Bestaetige den Termin einmal knapp und natuerlich (z. B. 'Dann also {d['gesprochen']}, passt das?'), "
                           "mit Datum nur, wenn es hilft. Ohne Zahlenformate wie 09:00."}

    # ---------- Terminabsage durch eingehende Anrufer ----------
    def has_booked():
        return bool(q("SELECT 1 FROM slots WHERE target_id IS NOT NULL AND start_ts>? LIMIT 1", (time.time(),)))

    def decl_cancel(call):
        if not mine(call) and call.meta.get("direction") != "out" and has_booked():
            return types.FunctionDeclaration(
                name="cancel_appointment",
                description="Sagt einen Termin ab, den der Anrufer ueber eine Kampagne bekommen hat. Nur aufrufen, wenn der Anrufer "
                            "seinen Namen UND die Zeit seines Termins genannt hat und den Termin absagen moechte.",
                parameters=types.Schema(type="OBJECT", required=["name", "uhrzeit"], properties={
                    "name": types.Schema(type="STRING", description="Name des Anrufers, wie genannt"),
                    "uhrzeit": types.Schema(type="STRING", description="Uhrzeit des Termins HH:MM, z. B. 13:00"),
                    "datum": types.Schema(type="STRING", description="Datum des Termins YYYY-MM-DD, wenn bekannt (Wochentag selbst in ein Datum umrechnen)")}))

    async def h_cancel(call, args):
        fl = api.call_data(call)
        fl["cancel_tries"] = fl.get("cancel_tries", 0) + 1
        if fl["cancel_tries"] > 4:
            return {"fehler": "Zu viele Versuche. Nimm eine Nachricht fuer das Buero auf (note_message) und sage, dass sich jemand meldet."}
        nm = (args.get("name") or "").strip()
        tm = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", str(args.get("uhrzeit") or ""))
        if not nm or not tm:
            return {"fehler": "Name und Uhrzeit des Termins werden benoetigt. Frage freundlich danach."}
        want_t = (int(tm.group(1)), int(tm.group(2)))
        dts = str(args.get("datum") or "")[:10]
        rows = q("SELECT s.id, s.start_ts, t.id AS tid, t.name, t.number FROM slots s JOIN campaign_targets t ON t.id=s.target_id "
                 "WHERE s.start_ts>? ORDER BY s.start_ts", (time.time(),))
        def tmatch(r):
            d = datetime.fromtimestamp(r["start_ts"])
            return (d.hour, d.minute) == want_t and (not dts or d.strftime("%Y-%m-%d") == dts)
        rows = [r for r in rows if tmatch(r)]
        from app import db as coredb
        hits = coredb._name_hits(rows, nm)
        if not hits:
            return {"fehler": "Zu dieser Angabe gibt es keinen Termin. Nenne keine Details zu anderen Terminen oder Personen. "
                              "Bitte den Anrufer, Name und Zeit zu wiederholen; klappt es nicht, nimm eine Nachricht auf."}
        if len(hits) > 1:
            return {"fehler": "Die Angabe ist nicht eindeutig. Frage nach dem Datum bzw. dem vollen Namen, ohne andere Personen zu nennen."}
        h = hits[0]
        label = logic.slot_label(h["start_ts"])
        try:
            n = await asyncio.to_thread(logic.release_appointment, h["tid"])
        except gcal.CalendarError as e:
            call.event("system", f"Absage fehlgeschlagen (Kalender): {e}")
            return {"fehler": "Technisches Problem beim Absagen. Nimm eine Nachricht auf, damit das Buero den Termin absagt."}
        if not n:
            return {"fehler": "Der Termin ist nicht mehr gebucht."}
        x("UPDATE campaign_targets SET result=? WHERE id=?", ("Termin vom Anrufer abgesagt: " + label, h["tid"]))
        call.note("Termin abgesagt", f"{h['name'] or nm}: {label}")
        call.event("system", f"Termin abgesagt: {h['name'] or nm}, {label}")
        api.publish({"type": "campaign", "campaign_id": None})
        d = logic.spoken_slot(h["start_ts"])
        return {"ok": True, "gesprochen": d["gesprochen"],
                "hinweis": f"Bestaetige knapp, dass der Termin {d['gesprochen']} abgesagt ist. Biete keinen neuen Termin an, "
                           "ausser der Anrufer wuenscht das (dann Nachricht aufnehmen)."}

    api.tool("cancel_appointment", decl_cancel, h_cancel)

    api.tool("answering_machine_detected", decl_machine, h_machine)
    api.tool("get_free_slots", decl_slots, h_slots)
    api.tool("book_slot", decl_book, h_book)

    def prompt(call):
        m = mine(call)
        if not m:
            return None
        camp, t = m["campaign"], m["target"]
        s = ["KAMPAGNE: Du rufst im Rahmen der Kampagne '%s' an (Versuch %s von %s)." % (camp["name"], t.get("attempts", 1), camp["max_attempts"]),
             "ANRUFBEANTWORTER: Hoerst du statt einer Person eine Ansage (z. B. 'der Teilnehmer ist nicht erreichbar', Mailbox, "
             "Bandansage, Signalton), rufe SOFORT answering_machine_detected auf und tue danach genau, was die Antwort sagt. "
             "Rufe es nicht auf, wenn eine Person spricht."]
        if camp.get("pool_id"):
            s.append("TERMINE: Es gibt einen Pool freier Termine. Rufe get_free_slots auf, wenn du Termine anbieten willst, "
                     "und biete hoechstens zwei oder drei an, locker gesprochen wie am Telefon ('Montag um neun oder um halb elf', "
                     "nicht 'Montag, 19. Oktober um 09:00 Uhr'; 'Uhr' nicht bei jedem Termin). "
                     "get_free_slots liefert bei jedem Aufruf die naechsten noch nicht genannten Termine; will die Person spaeter oder einen anderen Tag, "
                     "rufe es erneut auf (ggf. mit ab_uhrzeit/ab_tag). Behaupte nie, ein Termin sei der letzte, ohne 'weitere_termine_vorhanden' "
                     "bzw. 'spaetester_termin_im_pool' zu pruefen. "
                     "Nenne IMMER Tag und Uhrzeit zusammen als konkreten Vorschlag ('Donnerstag um zehn oder um elf'); frage nie offen "
                     "'welcher Tag passt?' und nenne nie nur Uhrzeiten ohne Tag. Biete zuerst die naechsten freien Termine an. "
                     "Nur wenn die Person einem Termin ausdruecklich zustimmt, rufe book_slot(slot_id) auf. Ist er vergeben, "
                     "biete einen anderen an. Erfinde nie Termine. Passt keiner, rufe save_result mit erledigt=false auf, "
                     "wenn ein spaeterer Rueckruf sinnvoll ist, sonst mit erledigt=true.")
        s.append("GESPRAECHSBEGINN: Frage nie 'Passt es gerade?' o. ae. Ja/Nein-Fragen zum Einstieg. Nenne kurz den Grund deines Anrufs "
                 "und komm direkt zur Sache (bei Terminen: gleich einen konkreten Vorschlag). Ist eine Antwort unklar oder sehr kurz "
                 "('No', 'Ja'), nimm sie nicht als Ablehnung, sondern frage kurz nach.")
        s.append("save_result: Rufe es am Ende immer auf. erledigt=true, wenn die Angelegenheit abgeschlossen ist "
                 "(Termin vereinbart oder endgueltig abgelehnt), erledigt=false, wenn es spaeter erneut versucht werden soll "
                 "(z. B. 'rufen Sie morgen nochmal an').")
        return "\n".join(s)

    def prompt_in(call):
        if mine(call) or call.meta.get("direction") == "out" or not has_booked():
            return None
        d = datetime.now()
        wd = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"][d.weekday()]
        return ("TERMINABSAGE: Moechte ein Anrufer einen Termin absagen, den er telefonisch fuer eine Kampagne bekommen hat, frage nach seinem "
                "Namen und der Zeit des Termins und rufe cancel_appointment auf. Heute ist %s, der %s. Gib keine Auskuenfte "
                "ueber andere Termine oder Personen. Findet sich kein Termin, nimm eine Nachricht auf." % (wd, d.strftime("%d.%m.%Y")))

    def prompt_all(call):
        return prompt(call) or prompt_in(call)

    api.prompt(prompt_all)

    # ---------- Ereignisse ----------
    def on_end(call, meta):
        m = meta.get("plg_campaigns")
        if m:
            f = api.call_data(call)
            logic.finish(m, {"booked": f.get("booked"), "machine": f.get("machine"), "vm_left": f.get("vm_left"),
                             "result": call.result, "spoke": call.caller_spoke})

    def on_unreached(meta, reason):
        m = meta.get("plg_campaigns")
        if m:
            logic.finish(m, {"unreached": reason})

    def mail_hook(call, meta):
        c = q("SELECT c.notify_mail FROM campaign_targets t JOIN campaigns c ON c.id=t.campaign_id WHERE t.last_call_id=?", (call["id"],))
        mails = [a.strip() for a in ((c[0]["notify_mail"] if c else "") or "").split(",") if a.strip()]
        return [{"id": "camp:" + a, "email": a, "detail": "full", "on_call": "all"} for a in mails]

    from app import notify
    if mail_hook not in notify.OUT_HOOKS:
        notify.OUT_HOOKS.append(mail_hook)

    api.on("call_end", on_end)
    api.on("call_unreached", on_unreached)
    api.on("startup", logic.recover)
    api.every(20, logic.tick)
    api.page("Kampagnen", icon="campaign")

    # ---------- Web-Schnittstelle (unter /api/plugins/campaigns/) ----------
    r = api.router()

    def clean_campaign(d):
        out = {"name": (d.get("name") or "").strip(), "task": (d.get("task") or "").strip()}
        if not out["name"] or not out["task"]:
            raise HTTPException(400, "Name und Auftrag sind erforderlich")
        out["agent_id"] = int(d.get("agent_id") or 0) or None
        out["days"] = ",".join(str(i) for i in sorted({int(v) for v in str(d.get("days", "")).replace(" ", "").split(",")
                                                       if v.isdigit() and 1 <= int(v) <= 7}))
        for k, default in (("t_from", "09:00"), ("t_to", "17:00")):
            v = (d.get(k) or default).strip()
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
                raise HTTPException(400, "Uhrzeit im Format HH:MM")
            out[k] = v
        if out["t_from"] >= out["t_to"]:
            raise HTTPException(400, "Die Anrufzeit 'von' muss vor 'bis' liegen")
        out["max_attempts"] = max(1, min(int(d.get("max_attempts") or 3), 20))
        out["retry_min"] = max(5, min(int(d.get("retry_min") or 120), 7 * 24 * 60))
        out["vm_mode"] = d.get("vm_mode") if d.get("vm_mode") in ("retry", "message", "hangup") else "retry"
        out["vm_text"] = (d.get("vm_text") or "").strip()[:600]
        out["vm_stop"] = 1 if d.get("vm_stop") else 0
        out["pool_id"] = int(d.get("pool_id") or 0) or None
        out["owner"] = (d.get("owner") or "").strip()[:80]
        out["parallel"] = max(1, min(int(d.get("parallel") or 1), 3))
        out["pause_min"] = max(0, min(int(d.get("pause_min") or 0), 480))
        out["event_title"] = (d.get("event_title") or "").strip()[:120]
        mails = [m.strip() for m in re.split(r"[,;\s]+", d.get("notify_mail") or "") if m.strip()]
        if any(not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", m) for m in mails):
            raise HTTPException(400, "Ungültige E-Mail-Adresse bei der Benachrichtigung")
        out["notify_mail"] = ", ".join(mails)[:300]
        return out

    def pools_summary():
        rows = q("SELECT p.id, p.name, p.kind, p.cal_id, p.prefix, p.slot_min, p.horizon_days, COUNT(s.id) AS total, SUM(CASE WHEN s.target_id IS NOT NULL THEN 1 ELSE 0 END) AS booked, "
                 "SUM(CASE WHEN s.target_id IS NULL AND s.start_ts>? THEN 1 ELSE 0 END) AS free "
                 "FROM slot_pools p LEFT JOIN slots s ON s.pool_id=p.id GROUP BY p.id ORDER BY p.id", (time.time(),))
        return [{**p, "booked": p["booked"] or 0, "free": p["free"] or 0} for p in rows]

    @r.get("/campaigns")
    def campaigns_list():
        rows = q("SELECT * FROM campaigns ORDER BY id DESC")
        for c in rows:
            c["stats"] = logic.stats(c["id"])
            c["in_window"] = logic.in_window(c)
            c.update(logic.pool_info(c))
        return {"campaigns": rows, "pools": pools_summary(),
                "agents": [{"id": a["id"], "name": a["name"]} for a in db.list_agents(enabled_only=True) if a["mode"] == "assist"]}

    @r.post("/campaigns")
    async def campaign_create(request: Request):
        c = clean_campaign(await request.json())
        return {"id": x(f"INSERT INTO campaigns({','.join(c)},created) VALUES({','.join('?' * len(c))},?)", [*c.values(), time.time()])}

    @r.put("/campaigns/{cid}")
    async def campaign_update(cid: int, request: Request):
        c = clean_campaign(await request.json())
        if not q("SELECT id FROM campaigns WHERE id=?", (cid,)):
            raise HTTPException(404)
        x(f"UPDATE campaigns SET {','.join(k + '=?' for k in c)} WHERE id=?", [*c.values(), cid])
        return {"ok": True}

    @r.delete("/campaigns/{cid}")
    def campaign_delete(cid: int):
        for t in q("SELECT id FROM campaign_targets WHERE campaign_id=?", (cid,)):
            logic.release(t["id"])
        x("DELETE FROM campaign_targets WHERE campaign_id=?", (cid,))
        x("DELETE FROM campaigns WHERE id=?", (cid,))
        return {"ok": True}

    @r.post("/campaigns/{cid}/status")
    async def campaign_status(cid: int, request: Request):
        st = (await request.json()).get("status")
        if st not in ("active", "paused"):
            raise HTTPException(400, "active oder paused")
        if st == "active" and not q("SELECT id FROM campaign_targets WHERE campaign_id=? AND status IN ('pending','calling')", (cid,)):
            raise HTTPException(409, "Keine offenen Ziele. Bitte Rufnummern hinzufuegen.")
        x("UPDATE campaigns SET status=? WHERE id=?", (st, cid))
        return {"ok": True}

    @r.get("/campaigns/{cid}")
    def campaign_detail(cid: int):
        c = (q("SELECT * FROM campaigns WHERE id=?", (cid,)) or [None])[0]
        if not c:
            raise HTTPException(404)
        c["stats"] = logic.stats(cid)
        c["in_window"] = logic.in_window(c)
        c.update(logic.pool_info(c))
        targets = q("SELECT t.*, (SELECT start_ts FROM slots s WHERE s.target_id=t.id LIMIT 1) AS slot_ts "
                    "FROM campaign_targets t WHERE campaign_id=? ORDER BY id", (cid,))
        for t in targets:
            t["slot"] = logic.slot_label(t["slot_ts"]) if t["slot_ts"] else ""
        return {"campaign": c, "targets": targets}

    @r.post("/campaigns/{cid}/targets")
    async def add_targets(cid: int, request: Request):
        """Zeilen 'Name; Nummer' (oder nur Nummer) hinzufuegen."""
        if not q("SELECT id FROM campaigns WHERE id=?", (cid,)):
            raise HTTPException(404)
        d = await request.json()
        added, bad = 0, []
        existing = {db._digits(t["number"]) for t in q("SELECT number FROM campaign_targets WHERE campaign_id=?", (cid,))}
        for line in (d.get("lines") or "").splitlines():
            line = line.strip()
            if not line:
                continue
            name, _, num = line.rpartition(";") if ";" in line else ("", "", line)
            try:
                number = api.dial_number(num.strip())
            except ValueError:
                bad.append(line)
                continue
            if db._digits(number) in existing:
                continue
            existing.add(db._digits(number))
            x("INSERT INTO campaign_targets(campaign_id,name,number,updated) VALUES(?,?,?,?)", (cid, name.strip(), number, time.time()))
            added += 1
        x("UPDATE campaigns SET status='active' WHERE id=? AND status='done'", (cid,))
        return {"added": added, "invalid": bad}

    @r.delete("/targets/{tid}")
    def target_delete(tid: int):
        logic.release(tid)
        x("DELETE FROM campaign_targets WHERE id=?", (tid,))
        return {"ok": True}

    @r.post("/targets/{tid}/release-appointment")
    def target_release_appointment(tid: int):
        """Termin dieses Ziels freigeben (der Termin ist danach wieder frei, die Person wird nicht erneut angerufen)."""
        if not q("SELECT id FROM campaign_targets WHERE id=?", (tid,)):
            raise HTTPException(404)
        try:
            n = logic.release_appointment(tid)
        except gcal.CalendarError as e:
            raise HTTPException(502, str(e))
        if not n:
            raise HTTPException(409, "Für dieses Ziel ist kein Termin gebucht")
        return {"ok": True, "released": n}

    @r.post("/targets/{tid}/retry")
    def target_retry(tid: int):
        t = (q("SELECT * FROM campaign_targets WHERE id=?", (tid,)) or [None])[0]
        if not t:
            raise HTTPException(404)
        logic.release(tid)
        x("UPDATE campaign_targets SET status='pending', attempts=0, next_at=0, result='', summary='', updated=? WHERE id=?", (time.time(), tid))
        x("UPDATE campaigns SET status='active' WHERE id=? AND status='done'", (t["campaign_id"],))
        return {"ok": True}

    # --- Terminpools ---
    @r.get("/pools")
    def pools_list():
        return pools_summary()

    def clean_pool(d):
        name = (d.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "Name fehlt")
        kind = "gcal" if d.get("kind") == "gcal" else "manual"
        out = {"name": name, "kind": kind, "cal_id": (d.get("cal_id") or "").strip(), "prefix": (d.get("prefix") or "").strip(),
               "slot_min": max(5, min(int(d.get("slot_min") or 30), 480)), "horizon_days": max(1, min(int(d.get("horizon_days") or 60), 365))}
        if kind == "gcal" and not out["cal_id"]:
            out["cal_id"] = api.get_setting("gcal_default_cal", "")
        if kind == "gcal" and not (out["cal_id"] and out["prefix"]):
            raise HTTPException(400, "Kalender-Pool: Kalender-ID und Präfix der Platzhalter-Termine angeben")
        return out

    @r.post("/pools")
    async def pool_create(request: Request):
        p = clean_pool(await request.json())
        return {"id": x(f"INSERT INTO slot_pools({','.join(p)}) VALUES({','.join('?' * len(p))})", list(p.values()))}

    @r.put("/pools/{pid}")
    async def pool_update(pid: int, request: Request):
        p = clean_pool(await request.json())
        if not logic.pool(pid):
            raise HTTPException(404)
        x(f"UPDATE slot_pools SET {','.join(k + '=?' for k in p)} WHERE id=?", [*p.values(), pid])
        logic.SYNC.pop(pid, None)
        return {"ok": True}

    @r.post("/pools/{pid}/sync")
    def pool_sync(pid: int):
        """Kalender-Pool sofort mit dem Google Kalender abgleichen."""
        p = logic.pool(pid)
        if not p or p["kind"] != "gcal":
            raise HTTPException(400, "Nur für Kalender-Pools")
        try:
            return logic.sync_pool(p, force=True)
        except gcal.CalendarError as e:
            raise HTTPException(502, str(e))

    @r.delete("/pools/{pid}")
    def pool_delete(pid: int):
        if q("SELECT id FROM campaigns WHERE pool_id=? AND status IN ('active')", (pid,)):
            raise HTTPException(409, "Der Pool wird von einer laufenden Kampagne genutzt")
        x("UPDATE campaigns SET pool_id=NULL WHERE pool_id=?", (pid,))
        x("DELETE FROM slots WHERE pool_id=?", (pid,))
        x("DELETE FROM slot_pools WHERE id=?", (pid,))
        return {"ok": True}

    @r.get("/pools/{pid}/slots")
    def pool_slots(pid: int):
        p = logic.pool(pid)
        if p and p["kind"] == "gcal":
            try:
                logic.sync_pool(p)                 # aktueller Stand aus dem Kalender (kurz zwischengespeichert)
            except gcal.CalendarError:
                pass
        rows = q("SELECT s.*, t.name AS target_name, t.number AS target_number FROM slots s LEFT JOIN campaign_targets t ON t.id=s.target_id "
                 "WHERE pool_id=? AND s.start_ts>? ORDER BY start_ts LIMIT 500", (pid, time.time() - 86400))
        for s in rows:
            s["label"] = logic.slot_label(s["start_ts"])
        return rows

    @r.post("/pools/{pid}/generate")
    async def pool_generate(pid: int, request: Request):
        """Termine erzeugen: Zeitraum, Wochentage, Uhrzeit von-bis und Dauer."""
        d = await request.json()
        try:
            d1, d2 = date.fromisoformat(d["date_from"]), date.fromisoformat(d.get("date_to") or d["date_from"])
            h1, m1 = map(int, (d.get("t_from") or "09:00").split(":"))
            h2, m2 = map(int, (d.get("t_to") or "17:00").split(":"))
            step = max(5, min(int(d.get("minutes") or 30), 480))
        except (KeyError, ValueError):
            raise HTTPException(400, "Datum (JJJJ-MM-TT), Uhrzeit (HH:MM) und Dauer pruefen")
        if (d2 - d1).days > 120 or d2 < d1:
            raise HTTPException(400, "Zeitraum: hoechstens 120 Tage")
        days = {int(v) for v in d.get("days") or [1, 2, 3, 4, 5]}
        have = {s["start_ts"] for s in q("SELECT start_ts FROM slots WHERE pool_id=?", (pid,))}
        n, day = 0, d1
        while day <= d2:
            if day.isoweekday() in days:
                t = datetime(day.year, day.month, day.day, h1, m1)
                end = datetime(day.year, day.month, day.day, h2, m2)
                while t + timedelta(minutes=step) <= end:
                    ts = t.timestamp()
                    if ts not in have and ts > time.time():
                        x("INSERT INTO slots(pool_id,start_ts,end_ts) VALUES(?,?,?)", (pid, ts, (t + timedelta(minutes=step)).timestamp()))
                        n += 1
                    t += timedelta(minutes=step)
            day += timedelta(days=1)
        return {"added": n}

    @r.delete("/slots/{sid}")
    def slot_delete(sid: int):
        if not db.x_count("DELETE FROM slots WHERE id=? AND target_id IS NULL", (sid,)):
            raise HTTPException(409, "Gebuchte Termine zuerst freigeben")
        return {"ok": True}

    @r.post("/slots/{sid}/release")
    def slot_release(sid: int):
        t = (q("SELECT target_id FROM slots WHERE id=?", (sid,)) or [{}])[0].get("target_id")
        logic.release_slot(sid)
        if t:      # das Ziel dieses Termins gilt nicht mehr als gebucht
            x("UPDATE campaign_targets SET status='done', result='Termin freigegeben', updated=? WHERE id=? AND status='booked'", (time.time(), t))
        return {"ok": True}

    # --- Google Kalender (Dienstkonto) ---
    @r.get("/gcal")
    def gcal_status():
        return {"configured": gcal.configured(), "email": gcal.service_email(), "default_cal": api.get_setting("gcal_default_cal", "")}

    @r.put("/gcal")
    async def gcal_save(request: Request):
        """Nur der Standard-Kalender gehoert zum Plugin; der Dienstkonto-Schluessel steht in den Einstellungen."""
        d = await request.json()
        if (d.get("key") or "").strip():          # aeltere Seitenversion: Schluessel an die Einstellungen weiterreichen
            try:
                gcal.set_key(d["key"])
            except ValueError as e:
                raise HTTPException(400, str(e) if "Dienstkonto" in str(e) else "Der Schlüssel ist kein gültiges JSON")
        if "default_cal" in d:
            api.set_setting("gcal_default_cal", (d.get("default_cal") or "").strip()[:200])
        return gcal_status()

    @r.post("/gcal/test")
    async def gcal_test(request: Request):
        cal = ((await request.json()).get("calendar_id") or api.get_setting("gcal_default_cal", "")).strip()
        if not cal:
            raise HTTPException(400, "Kalender-ID angeben")
        try:
            title = await asyncio.to_thread(gcal.calendar_title, cal)
            evs = await asyncio.to_thread(gcal.events, cal, time.time(), time.time() + 30 * 86400)
        except gcal.CalendarError as e:
            raise HTTPException(502, str(e))
        return {"ok": True, "title": title, "events_30d": len(evs)}
