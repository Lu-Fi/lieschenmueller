from . import db


def _patterns(s):
    return [p.strip().lower() for p in (s or "").split(",") if p.strip()]


def mood_rules(a):
    """Stimmungen, bei denen dieser Agent uebernimmt, mit eigener Mindeststaerke je Stimmung: 'aufgeregt:3,panisch:4'
    (ohne Zahl gilt mood_min des Agenten)."""
    default = max(1, min(5, int(a.get("mood_min") or 4)))
    out = {}
    for p in _patterns(a.get("mood_trigger")):
        name, _, lvl = p.partition(":")
        try:
            n = int(lvl) if lvl.strip() else default
        except ValueError:
            n = default
        out[name.strip()] = max(1, min(5, n))
    return out


def _norm(s):
    """Rufnummer vereinheitlichen: +49…, 0049… und 0… werden zu 49… (E.164 ohne +). Interne Nummern (**610, 610), Kurznummern und Namen bleiben unveraendert."""
    s = (s or "").strip().lower()
    d = "".join(ch for ch in s if ch.isdigit() or ch == "+")
    if not d or "*" in s or not d.replace("+", "").isdigit() or len(d.replace("+", "")) < 6:
        return s
    if d.startswith("+"):
        return d[1:]
    if d.startswith("00"):
        return d[2:]
    if d.startswith("0"):
        return "49" + d[1:]
    return d


def _hit(patterns, values):
    """Treffer, wenn der Anrufer (oder die angerufene Nummer) gleich dem Muster ist oder damit endet. Verglichen wird in der Schreibweise
    der Fritzbox UND in vereinheitlichter Form, damit 0141…, +49141… und 0049141… zusammenpassen; kurze Endungen (z. B. 5550163) gehen weiter."""
    for p in patterns:
        np = _norm(p)
        for v in values:
            if not v:
                continue
            nv = _norm(v)
            if v == p or v.endswith(p) or nv == np or nv.endswith(np):
                return True
    return False


def _fits(a, caller, called_values, open_now):
    """Erfuellt der Anruf die Bedingungen des Agenten (Zeitfenster, angerufene Nummer, Anrufer)? Leere Felder = egal."""
    if (a.get("schedule") == "open" and not open_now) or (a.get("schedule") == "closed" and open_now):
        return False      # Zeitfenster: "open" nur in, "closed" nur ausserhalb der Geschaeftszeiten
    pc, pa = _patterns(a["match_called"]), _patterns(a["match_caller"])
    if pc and not _hit(pc, called_values):
        return False
    if pa and not _hit(pa, [caller]):
        return False
    return True


def rec_flags(caller, called_values, open_now=True, leader_id=0, direction="in"):
    """Aufzeichnungsregeln: ALLE passenden Regeln gelten gemeinsam. Eine Regel mit Agent gilt nur, wenn dieser Agent das Gespraech fuehrt
    (leader_id), eine ohne Agent immer. Ergebnis: Audio / Transkript / KI-Audio / Sprachbefehl (nur aus, wenn eine passende Regel ihn ausschaltet)."""
    caller = (caller or "").lower()
    cv = [c.lower() for c in called_values if c]
    out = {"record": 0, "transcribe": 0, "record_ai": 0, "stop_cmd": 1, "notice": 0, "notice_ai": 0, "rec_screen": 0, "mail": "", "rules": []}
    for r in db.list_rec_rules(enabled_only=True):
        if (r.get("direction") or "in") != direction:        # eingehende und ausgehende Gespraeche haben eigene Regeln
            continue
        if r["agent_id"] and r["agent_id"] != leader_id:
            continue
        if not _fits(r, caller, cv, open_now):
            continue
        out["record"] |= int(bool(r["audio"]))
        out["rec_screen"] |= int(bool(r.get("rec_screen")) and bool(r["audio"]))      # nur zusammen mit Audio
        out["transcribe"] |= int(bool(r["transcript"]))
        out["record_ai"] |= int(bool(r["ai_audio"]))
        if not r["stop_cmd"]:
            out["stop_cmd"] = 0
        nt = int(bool(r.get("notice", 1)))
        if r["ai_audio"]:                               # Ansage vor der KI-Aufnahme: nur Regeln, die KI-Audio verlangen
            out["notice_ai"] |= nt
        if r["audio"] or r["transcript"]:               # Hinweis an Menschen-Gespraeche: nur Regeln, die diese aufzeichnen
            out["notice"] |= nt
        out["mail"] = ",".join(dict.fromkeys([*[x for x in out["mail"].split(",") if x], *[x for x in (r.get("mail") or "").split(",") if x]]))
        out["rules"].append(r["name"])
    return out


def with_flags(agent, caller, called_values, open_now=True):
    """Agent (Persoenlichkeit) mit den Aufzeichnungs-Einstellungen aus den Regeln ergaenzen (Kopie)."""
    a = dict(agent)
    f = rec_flags(caller, called_values, open_now, a.get("id") or 0)
    a.update(record=f["record"], transcribe=f["transcribe"], record_ai=f["record_ai"], stop_cmd=f["stop_cmd"], rec_rules=f["rules"],
             rec_notice=f["notice_ai"], rec_notice_h=f["notice"])
    return a


def pick_agent(caller, called_values, open_now=True, modes=None, uuid=None):
    """Erste passende Anrufregel (nach Prioritaet) bestimmt den Agenten; Rueckgabe: der Agent mit Annahmezeit der Regel
    (answer_delay) und den Aufzeichnungs-Einstellungen aus den Aufzeichnungsregeln. Eine Regel mit "mehrfach angerufen"
    passt nur, wenn die Nummer vorher oft genug angerufen hat (uuid = dieser Anruf, zaehlt nicht mit)."""
    caller = (caller or "").lower()
    cv = [c.lower() for c in called_values if c]
    for r in db.list_call_rules(enabled_only=True):
        if not _fits(r, caller, cv, open_now):
            continue
        repeat = None
        if int(r.get("repeat_n") or 0) > 0:
            hist = db.recent_calls_from(caller, int(r.get("repeat_min") or 0), uuid)
            kind = r.get("repeat_kind") or ""
            n = sum(1 for h in hist if db.repeat_counts(h["outcome"], kind))
            if n < int(r["repeat_n"]):
                continue
            repeat = {"n": n, "min": int(r["repeat_min"]), "calls": hist, "kind": kind}
        ag = db.get_agent(r["agent_id"])
        if not ag or not ag["enabled"]:
            continue
        if modes and ag.get("mode") not in modes:         # z. B. Rueckfall der KI: nur Assistenten (kein Monitor/Durchstellen)
            continue
        ag = dict(ag)
        ag.update(answer_delay=int(r["ring_s"] or 0), rule_id=r["id"], rule_name=r["name"], priority=r["priority"])
        if repeat:
            ag["repeat"] = repeat
        return with_flags(ag, caller, called_values, open_now)
    return None


MOODS = ("ruhig", "freundlich", "unsicher", "aufgeregt", "aergerlich", "aengstlich", "panisch")


def check_triggers(current_id, state, caller="", called_values=(), open_now=True, text=None, mood=None):
    """Wechsel im laufenden Gespraech: Codewoerter und/oder Stimmung. Gibt den Agenten zurueck, der uebernehmen soll.

    state: je Anruf, {"kw": {Agent-ID: Menge gehoerter Codewoerter}}; text: neuer Anruferdialog (Codewoerter);
    mood: (Stimmung, Staerke 1-5) laut note_mood. Pro Agent gilt: Anrufer-/Nummernliste und Zeitfenster muessen passen;
    Codewoerter zaehlen je Wort einmal (keyword_count verschiedene); Stimmung nach mood_trigger/mood_min;
    trigger_mode 'any' = Codewoerter ODER Stimmung, 'all' = beides (soweit beim Agenten eingestellt)."""
    t = (text or "").lower()
    kw = state.setdefault("kw", {})
    caller = (caller or "").lower()
    cv = [c.lower() for c in called_values if c]
    for a in db.list_agents(enabled_only=True):
        if a["id"] == current_id or not _fits(a, caller, cv, open_now):
            continue
        pats = _patterns(a["keywords"])
        moods = mood_rules(a)
        if not pats and not moods:
            continue
        seen = kw.setdefault(a["id"], set())
        seen.update(k for k in pats if k in t)
        kw_ok = bool(pats) and len(seen) >= max(1, int(a.get("keyword_count") or 1))
        mood_ok = bool(moods) and bool(mood) and mood[0] in moods and mood[1] >= moods[mood[0]]
        if a.get("trigger_mode") == "all":
            ok = (kw_ok or not pats) and (mood_ok or not moods)
        else:
            ok = kw_ok or mood_ok
        if ok:
            return a
    return None

