"""Zusatzinformationen zu einem Gespraech (Zusammenfassung, Anliegen, Dringlichkeit, ...), ausgewertet aus dem Transkript."""
import json
import logging

from google import genai
from google.genai import types

from . import db, gclient, models
from .events import hub

log = logging.getLogger("analyze")

PROMPT = (
    "Du wertest das Transkript eines Telefonats aus. Antworte nur mit JSON nach dem Schema, auf Deutsch, knapp und sachlich. "
    "Erfinde nichts; wenn etwas nicht erkennbar ist, schreibe 'unklar'. Triff keine Aussagen ueber Gesundheit, Alkohol oder Drogen. "
    "'anrufer' ist die Person, die angerufen hat bzw. angerufen wurde; 'agent' ist die KI.\n\nTranskript:\n")

SCHEMA = {
    "type": "OBJECT",
    "required": ["zusammenfassung", "anliegen", "dringlichkeit", "stimmung", "sprache", "ergebnis", "betrugsverdacht"],
    "properties": {
        "zusammenfassung": {"type": "STRING", "description": "2-4 Saetze"},
        "anliegen": {"type": "STRING", "description": "Kategorie in 1-3 Woertern, z. B. Terminanfrage, Beschwerde, Notfall, Werbung, Auskunft"},
        "dringlichkeit": {"type": "STRING", "enum": ["niedrig", "mittel", "hoch"]},
        "stimmung": {"type": "STRING", "description": "Stimmung des Anrufers im Verlauf, kurz"},
        "sprache": {"type": "STRING"},
        "ergebnis": {"type": "STRING", "description": "Was ist am Ende passiert (z. B. Nachricht hinterlassen, durchgestellt, Auskunft gegeben)"},
        "betrugsverdacht": {"type": "STRING", "enum": ["nein", "moeglich", "wahrscheinlich"]},
        "naechster_schritt": {"type": "STRING", "description": "Was sollte jemand tun? Leer, wenn nichts."},
        "genannte_namen": {"type": "STRING", "description": "Namen, die im Gespraech fielen, kommagetrennt"},
    },
}


def wanted(call_id):
    """Ist fuer diesen Anruf die Auswertung eingeschaltet (irgendein beteiligter Agent mit meta_on)?"""
    r = db.q("SELECT agent_name, agent_chain FROM calls WHERE id=?", (call_id,))
    if not r:
        return False
    names = {n.strip() for n in ((r[0]["agent_chain"] or "") + " → " + (r[0]["agent_name"] or "")).split("→") if n.strip()}
    return any(a.get("meta_on") for a in db.list_agents() if a["name"] in names)


def wanted_detail(call_id):
    """Ist fuer diesen Anruf die ausfuehrliche Zusammenfassung (alle besprochenen Punkte) eingeschaltet (irgendein beteiligter Agent mit meta_detail)?"""
    r = db.q("SELECT agent_name, agent_chain FROM calls WHERE id=?", (call_id,))
    if not r:
        return False
    names = {n.strip() for n in ((r[0]["agent_chain"] or "") + " → " + (r[0]["agent_name"] or "")).split("→") if n.strip()}
    return any(a.get("meta_detail") for a in db.list_agents() if a["name"] in names)


DETAIL_PROMPT = (
    "Du erstellst aus dem Transkript eines ganzen Telefonats eine AUSFUEHRLICHE, KONKRETE Zusammenfassung auf Deutsch. Antworte nur mit JSON nach dem Schema. "
    "Fasse nach THEMEN zusammen (nicht Aeusserung fuer Aeusserung), in der Reihenfolge, in der die Themen vorkamen. "
    "Zu jedem Thema gehoert immer: 'inhalt' = was genau gesagt, gefragt und erklaert wurde, mit Namen, Zahlen, Mengen, Mitteln/Produkten, Zeitangaben und "
    "Beobachtungen; 'ergebnis' = die ANTWORT, Empfehlung, Entscheidung oder Erkenntnis am Ende dieses Themas. Wurde nichts geklaert, schreibe "
    "'kein Ergebnis' und sage kurz warum. VERMEIDE leere Formulierungen wie 'Austausch ueber', 'Diskussion ueber' oder 'es wurde besprochen' ohne Inhalt. "
    "'fazit' fasst in 3 bis 6 Saetzen zusammen, was insgesamt herauskam: Antworten, Entscheidungen, Empfehlungen, Stimmung und das Wichtigste fuer den, der das Gespraech nicht gehoert hat. "
    "'zusagen' enthaelt nur ausdrueckliche Vereinbarungen, Zusagen und Termine (wer, was, bis wann). 'offen' enthaelt nur ausdruecklich offen gebliebene Fragen oder Aufgaben "
    "mit dem Grund; nichts doppelt aus 'punkte'. Erfinde nichts, schreibe nichts hinein, was nicht gesagt wurde, und mache keine Aussagen ueber Gesundheit, Alkohol oder "
    "Drogen von Personen, die nicht ausdruecklich genannt wurden (Tiere duerfen beschrieben werden). Das Transkript kann Erkennungsfehler enthalten; bei Namen "
    "die haeufigste Schreibweise nehmen. 'Anrufer' ist die Person, die angerufen hat bzw. angerufen wurde; 'KI' ist der Assistent; 'Benutzer' und 'Gegenueber' sind die beiden Menschen eines vermittelten Gespraechs.\n\nTranskript:\n")

_STR = {"type": "STRING"}
DETAIL_SCHEMA = {
    "type": "OBJECT", "required": ["fazit", "punkte", "zusagen", "offen"],
    "properties": {
        "fazit": {"type": "STRING", "description": "3 bis 6 Saetze: was kam insgesamt heraus"},
        "punkte": {"type": "ARRAY", "items": {"type": "OBJECT", "required": ["thema", "inhalt", "ergebnis"], "properties": {
            "thema": {"type": "STRING", "description": "kurzer Titel des Themas"},
            "inhalt": {"type": "STRING", "description": "was konkret gesagt, gefragt, erklaert wurde (Namen, Zahlen, Mittel, Zeiten)"},
            "ergebnis": {"type": "STRING", "description": "Antwort, Empfehlung, Entscheidung oder Erkenntnis; sonst 'kein Ergebnis' mit Grund"}}}},
        "zusagen": {"type": "ARRAY", "items": {"type": "OBJECT", "required": ["wer", "was"], "properties": {
            "wer": _STR, "was": _STR, "bis_wann": _STR}}},
        "offen": {"type": "ARRAY", "items": {"type": "OBJECT", "required": ["punkt"], "properties": {"punkt": _STR, "grund": _STR}}},
    },
}


def normalize_detail(detail):
    """Ausfuehrliche Zusammenfassung in eine einheitliche Form bringen (auch die aeltere mit Listen aus Texten):
    {'fazit': str, 'punkte': [{thema, inhalt, ergebnis}], 'zusagen': [{wer, was, bis_wann}], 'offen': [{punkt, grund}]}."""
    d = detail or {}
    s = lambda v: str(v or "").strip()
    return {
        "fazit": s(d.get("fazit")),
        "punkte": [{"thema": s(p.get("thema")), "inhalt": s(p.get("inhalt")), "ergebnis": s(p.get("ergebnis"))} if isinstance(p, dict)
                   else {"thema": "", "inhalt": s(p), "ergebnis": ""} for p in (d.get("punkte") or []) if p],
        "zusagen": [{"wer": s(z.get("wer")), "was": s(z.get("was")), "bis_wann": s(z.get("bis_wann"))} if isinstance(z, dict)
                    else {"wer": "", "was": s(z), "bis_wann": ""} for z in (d.get("zusagen") or []) if z],
        "offen": [{"punkt": s(o.get("punkt")), "grund": s(o.get("grund"))} if isinstance(o, dict)
                  else {"punkt": s(o), "grund": ""} for o in (d.get("offen") or []) if o],
    }


def detail_text(detail):
    """Ausfuehrliche Zusammenfassung als gut lesbarer Klartext (fuer den Text-Teil der Mail)."""
    d = normalize_detail(detail)
    out = []
    if d["fazit"]:
        out.append("FAZIT\n" + d["fazit"])
    if d["punkte"]:
        blocks = []
        for i, p in enumerate(d["punkte"], 1):
            b = f"{i}. {p['thema'] or 'Punkt'}"
            if p["inhalt"]:
                b += f"\n   {p['inhalt']}"
            if p["ergebnis"]:
                b += f"\n   → Ergebnis: {p['ergebnis']}"
            blocks.append(b)
        out.append("BESPROCHENE PUNKTE\n" + "\n\n".join(blocks))
    if d["zusagen"]:
        out.append("ZUSAGEN UND VEREINBARUNGEN\n" + "\n".join(
            "- " + (f"{z['wer']}: " if z["wer"] else "") + z["was"] + (f" (bis {z['bis_wann']})" if z["bis_wann"] else "") for z in d["zusagen"]))
    if d["offen"]:
        out.append("OFFEN\n" + "\n".join("- " + o["punkt"] + (f" – {o['grund']}" if o["grund"] else "") for o in d["offen"]))
    return "\n\n".join(out)


def detail_lines(detail):
    """Ausfuehrliche Zusammenfassung als Textzeilen je Abschnitt: [(Ueberschrift, [Zeilen])]. Versteht auch die aeltere Form (Listen aus Texten)."""
    out = []
    d = detail or {}
    if d.get("fazit"):
        out.append(("Fazit", [str(d["fazit"])]))
    pk = []
    for p in d.get("punkte") or []:
        if isinstance(p, dict):
            t = f"{p.get('thema', '').strip()}: {p.get('inhalt', '').strip()}".strip(": ")
            if p.get("ergebnis"):
                t += f" → Ergebnis: {p['ergebnis'].strip()}"
            pk.append(t)
        elif p:
            pk.append(str(p))
    if pk:
        out.append(("Besprochene Punkte", pk))
    zs = []
    for z in d.get("zusagen") or []:
        if isinstance(z, dict):
            t = f"{z.get('wer', '').strip()}: {z.get('was', '').strip()}".strip(": ")
            if z.get("bis_wann"):
                t += f" (bis {z['bis_wann'].strip()})"
            zs.append(t)
        elif z:
            zs.append(str(z))
    if zs:
        out.append(("Zusagen und Vereinbarungen", zs))
    of = []
    for o in d.get("offen") or []:
        if isinstance(o, dict):
            t = (o.get("punkt") or "").strip()
            if o.get("grund"):
                t += f" – {o['grund'].strip()}"
            of.append(t)
        elif o:
            of.append(str(o))
    if of:
        out.append(("Offen", of))
    return out


async def run_detail(call_id):
    """Ausfuehrliche Zusammenfassung (Fazit, besprochene Punkte mit Ergebnissen, Zusagen, offene Punkte) aus dem ganzen Transkript, gespeichert in calls.meta['detail']."""
    segs = db.q("SELECT speaker, text FROM segments WHERE call_id=? AND speaker IN ('caller','agent','local','remote','mix') ORDER BY id", (call_id,))
    if len(segs) < 2:
        return None
    label = {"caller": "Anrufer", "agent": "KI", "local": "Benutzer", "remote": "Gegenueber", "mix": "Gespraech"}
    text = "\n".join(f"{label.get(s['speaker'], s['speaker'])}: {s['text']}" for s in segs)[:200000]
    client = gclient.client(genai, "text")
    model = db.get_meta("detail_model", "")
    if model not in [i for i, _ in models.DETAIL_CHOICES]:
        model = models.DETAIL["id"]        # gespeicherte, nicht mehr waehlbare Modelle (z. B. nur global verfuegbar) nicht verwenden
    price = models.text_price(model)
    cfg = types.GenerateContentConfig(response_mime_type="application/json", response_schema=DETAIL_SCHEMA, temperature=0.2)
    try:
        r = await client.aio.models.generate_content(model=model, contents=DETAIL_PROMPT + text, config=cfg)
    except Exception as e:
        if model == models.TRANSCRIBE["id"]:
            raise
        log.warning("Zusammenfassung mit %s fehlgeschlagen (%s) - Rueckfall auf %s", model, type(e).__name__, models.TRANSCRIBE["id"])
        model, price = models.TRANSCRIBE["id"], models.TRANSCRIBE["price"]
        r = await client.aio.models.generate_content(model=model, contents=DETAIL_PROMPT + text, config=cfg)
    data = json.loads(r.text or "{}")

    def clip(v, n=700):
        return str(v or "").strip()[:n]
    detail = {
        "fazit": clip(data.get("fazit"), 1500),
        "punkte": [{"thema": clip(p.get("thema"), 120), "inhalt": clip(p.get("inhalt")), "ergebnis": clip(p.get("ergebnis"))}
                   for p in (data.get("punkte") or []) if isinstance(p, dict) and (p.get("thema") or p.get("inhalt"))][:40],
        "zusagen": [{"wer": clip(z.get("wer"), 80), "was": clip(z.get("was")), "bis_wann": clip(z.get("bis_wann"), 80)}
                    for z in (data.get("zusagen") or []) if isinstance(z, dict) and z.get("was")][:30],
        "offen": [{"punkt": clip(o.get("punkt")), "grund": clip(o.get("grund"))}
                  for o in (data.get("offen") or []) if isinstance(o, dict) and o.get("punkt")][:30],
    }
    u = r.usage_metadata
    tin, tout = (getattr(u, "prompt_token_count", 0) or 0), (getattr(u, "candidates_token_count", 0) or 0) + (getattr(u, "thoughts_token_count", 0) or 0)
    cost = (tin * price["in_text"] + tout * price["out_text"]) / 1e6
    row = db.q("SELECT meta FROM calls WHERE id=?", (call_id,))
    try:
        meta = json.loads((row[0]["meta"] if row else "") or "{}")
    except ValueError:
        meta = {}
    meta["detail"] = detail
    db.x("UPDATE calls SET meta=?, tok_in_text=tok_in_text+?, tok_out_text=tok_out_text+?, cost_usd=cost_usd+? WHERE id=?",
         (json.dumps(meta, ensure_ascii=False), tin, tout, cost, call_id))
    hub.publish({"type": "meta", "call_id": call_id})
    return detail


async def run_detail_safe(call_id):
    try:
        await run_detail(call_id)
    except Exception as e:
        log.warning("Ausfuehrliche Zusammenfassung von Anruf %s fehlgeschlagen: %s", call_id, type(e).__name__)


async def run(call_id):
    """Wertet das Transkript aus und speichert das Ergebnis in calls.meta. Gibt das Ergebnis (dict) oder None zurueck."""
    segs = db.q("SELECT speaker, text FROM segments WHERE call_id=? AND speaker IN ('caller','agent','local','remote','mix') ORDER BY id", (call_id,))
    if len(segs) < 2:
        return None
    label = {"caller": "Anrufer", "agent": "KI", "local": "Benutzer", "remote": "Gegenueber", "mix": "Gespraech"}
    text = "\n".join(f"{label.get(s['speaker'], s['speaker'])}: {s['text']}" for s in segs)[:30000]
    client = gclient.client(genai, "text")
    r = await client.aio.models.generate_content(
        model=models.TRANSCRIBE["id"], contents=PROMPT + text,
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=SCHEMA))
    data = json.loads(r.text or "{}")
    u = r.usage_metadata
    p = models.TRANSCRIBE["price"]
    tin, tout = (getattr(u, "prompt_token_count", 0) or 0), (getattr(u, "candidates_token_count", 0) or 0) + (getattr(u, "thoughts_token_count", 0) or 0)
    cost = (tin * p["in_text"] + tout * p["out_text"]) / 1e6
    old = db.q("SELECT meta FROM calls WHERE id=?", (call_id,))
    try:
        old_detail = json.loads((old[0]["meta"] if old else "") or "{}").get("detail")
    except ValueError:
        old_detail = None
    if old_detail:                                  # die ausfuehrliche Zusammenfassung bleibt bei einer neuen Auswertung erhalten
        data["detail"] = old_detail
    db.x("UPDATE calls SET meta=?, tok_in_text=tok_in_text+?, tok_out_text=tok_out_text+?, cost_usd=cost_usd+? WHERE id=?",
         (json.dumps(data, ensure_ascii=False), tin, tout, cost, call_id))
    hub.publish({"type": "meta", "call_id": call_id})
    return data


async def run_safe(call_id):
    try:
        await run(call_id)
    except Exception as e:
        log.warning("Auswertung von Anruf %s fehlgeschlagen: %s", call_id, type(e).__name__)


MOOD_PROMPT = ("Bewerte ausschliesslich anhand des WORTLAUTS (nicht der Stimme) der folgenden Aussagen eines Anrufers, welche Stimmung er hat. "
               "Antworte nur mit JSON. 'stimmung' ist genau eine der erlaubten Werte, 'intensitaet' 1 (schwach) bis 5 (sehr stark). "
               "Triff keine Aussagen ueber Gesundheit, Alkohol oder Drogen. Ist nichts erkennbar, nimm 'ruhig' mit Staerke 1.\n\nAussagen:\n")


async def mood_from_text(text):
    """Stimmung aus dem Wortlaut (zusaetzliche Absicherung zum Tonfall). Gibt (stimmung, staerke, tokens_in, tokens_out, kosten_usd) zurueck."""
    from . import agents
    schema = {"type": "OBJECT", "required": ["stimmung", "intensitaet"], "properties": {
        "stimmung": {"type": "STRING", "enum": list(agents.MOODS)}, "intensitaet": {"type": "INTEGER"}}}
    client = gclient.client(genai, "text")
    r = await client.aio.models.generate_content(
        model=models.TRANSCRIBE["id"], contents=MOOD_PROMPT + text[-900:],
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema))
    data = json.loads(r.text or "{}")
    u = r.usage_metadata
    p = models.TRANSCRIBE["price"]
    tin, tout = (getattr(u, "prompt_token_count", 0) or 0), (getattr(u, "candidates_token_count", 0) or 0) + (getattr(u, "thoughts_token_count", 0) or 0)
    mood = str(data.get("stimmung") or "ruhig")
    lvl = max(1, min(5, int(data.get("intensitaet") or 1)))
    return mood, lvl, tin, tout, (tin * p["in_text"] + tout * p["out_text"]) / 1e6
