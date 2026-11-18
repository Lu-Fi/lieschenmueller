"""Fritzbox ueber die Weboberflaeche (login_sid.lua, query.lua, data.lua): ankommende Rufnummern der DECT- und IP-Telefone lesen und setzen.

TR-064 kann die Rufnummern eines DECT-Telefons nicht schreiben; die Oberflaeche der Fritzbox kann es (Seite edit_dect_num). Dieses
Modul macht dasselbe wie ein Klick dort - mit denselben Zugangsdaten wie TR-064 (FRITZ_TR064_USER / FRITZ_TR064_PASSWORD, Host
FRITZ_HOST). Geprueft an FRITZ!OS 8.25.

Lesen (query.lua): telcfg:settings/Foncontrol/User/list(Name,Intern,Type,RingOnAllMSNs) und je Telefon .../User<N>/MSN/list(Number).
Erster Eintrag = ausgehende Nummer, Suffix '#' = nur ausgehend (klingelt nicht), weitere = ankommende; RingOnAllMSNs=1 = "alle".

Schreiben (data.lua, page=edit_dect_num): Formular laden, alle Felder unveraendert uebernehmen, nur die Haekchen aendern, speichern,
danach per query.lua gegenlesen. Stimmt der neue Stand nicht, wird der alte zurueckgeschrieben und ein Fehler gemeldet. Sieht das
Formular anders aus als erwartet, wird nichts geschrieben.

Ausgehende Rufnummer (dect_set_out / ip_set_out): dasselbe Formular, nur das Feld out_num aendert sich (die Auswahl enthaelt alle
Rufnummern der Box, auch die Klingel-Rufnummern 99NNN); gegengelesen, bei Abweichung die bisherige Nummer zurueck.

IP-Telefone (auch FRITZ!Apps): dasselbe Formular unter page=edit_ipfon_num mit ip_idx = TR-064-Client-Index (X_AVM-DE_GetClient3).
Es hat kein Kennwortfeld - Speichern aendert das Kennwort nicht (TR-064 SetClient3 schickt es mit und scheitert an Sonderzeichen,
Fehler 803). Gegengelesen wird hier ueber das Formular selbst (num_selection und Haekchen), unabhaengig von TR-064.

Kennwoerter und Sitzungskennungen (SID) kommen nie in Logs oder Fehlermeldungen."""
import hashlib
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from html.parser import HTMLParser

log = logging.getLogger("fritzweb")
TIMEOUT = (4, 12)                    # Verbindungsaufbau, Antwort (Speichern dauert an der Box ein paar Sekunden)
MAX_NUMS = 10                        # g_max_selectable_nums der Oberflaeche
NO_SID = "0000000000000000"
PAGE = "edit_dect_num"
BACK = "/fon_devices/fondevices_list.lua"
KINDS = {"dect": {"page": "edit_dect_num", "idx": "idx", "fonbook": True},
         "ip": {"page": "edit_ipfon_num", "idx": "ip_idx", "fonbook": False}}


class FritzWebError(RuntimeError):
    """Lesen/Schreiben ueber die Weboberflaeche ging nicht; die Meldung ist fuer die Oberflaeche gedacht (ohne Geheimnisse).
    wrote=False: an der Box wurde nichts geaendert (z. B. nicht erreichbar, Anmeldung, Formular unerwartet) - ein anderer Weg
    (TR-064) darf es dann versuchen. wrote=True: es wurde gespeichert, aber der Stand stimmte danach nicht."""

    def __init__(self, msg, wrote=False):
        super().__init__(msg)
        self.wrote = wrote


def _http(method, path, params=None, data=None):
    """Eine Anfrage an die Fritzbox; Rueckgabe (Status, Text). In den Tests ersetzt (Fake-Fritzbox)."""
    import requests
    host = os.environ.get("FRITZ_HOST", "192.168.178.1")
    try:
        r = requests.request(method, f"http://{host}{path}", params=params, data=data, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise FritzWebError(f"Weboberfläche der Fritzbox nicht erreichbar ({type(e).__name__})") from None
    return r.status_code, r.text


def _call(method, path, params=None, data=None):
    st, txt = _http(method, path, params=params, data=data)
    if st != 200:
        raise FritzWebError(f"Die Fritzbox antwortete mit HTTP {st} ({path})")
    return txt


# --- Anmeldung -------------------------------------------------------------------------------------------------------

def _response(challenge, pw):
    """Antwort auf die Challenge: PBKDF2 (2$it1$salt1$it2$salt2) oder die alte MD5-Variante."""
    if challenge.startswith("2$"):
        try:
            _, it1, s1, it2, s2 = challenge.split("$")
            h1 = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(s1), int(it1))
            h2 = hashlib.pbkdf2_hmac("sha256", h1, bytes.fromhex(s2), int(it2))
        except ValueError:
            raise FritzWebError("Anmeldung an der Fritzbox: unbekanntes Challenge-Format") from None
        return f"{s2}${h2.hex()}"
    return challenge + "-" + hashlib.md5((challenge + "-" + pw).encode("utf-16le")).hexdigest()


def _sid_info(txt):
    try:
        x = ET.fromstring(txt)
    except ET.ParseError:
        raise FritzWebError("Anmeldung an der Fritzbox: unerwartete Antwort von login_sid.lua") from None
    return (x.findtext("SID") or "").strip(), (x.findtext("Challenge") or "").strip(), (x.findtext("BlockTime") or "0").strip()


def _login():
    user, pw = os.environ.get("FRITZ_TR064_USER", ""), os.environ.get("FRITZ_TR064_PASSWORD", "")
    if not (user and pw):
        raise FritzWebError("FRITZ_TR064_USER / FRITZ_TR064_PASSWORD fehlen in den Einstellungen")
    _, ch, block = _sid_info(_call("GET", "/login_sid.lua", params={"version": "2"}))
    if block.isdigit() and int(block) > 0:
        raise FritzWebError(f"Die Fritzbox sperrt Anmeldungen noch {int(block)} s (zu viele Fehlversuche)")
    if not ch:
        raise FritzWebError("Anmeldung an der Fritzbox: keine Challenge erhalten")
    sid, _, block = _sid_info(_call("POST", "/login_sid.lua", params={"version": "2"},
                                    data={"username": user, "response": _response(ch, pw)}))
    if not re.fullmatch(r"[0-9a-f]{16}", sid or "") or sid == NO_SID:
        raise FritzWebError("Anmeldung an der Weboberfläche der Fritzbox fehlgeschlagen (Benutzer/Kennwort von TR-064 prüfen)")
    return sid


def _logout(sid):
    try:
        _http("POST", "/login_sid.lua", params={"version": "2"}, data={"logout": "1", "sid": sid})
    except Exception:
        pass


@contextmanager
def session():
    """Angemeldet an der Weboberflaeche; abgemeldet wird immer (auch bei Fehlern)."""
    sid = _login()
    try:
        yield sid
    finally:
        _logout(sid)


# --- Lesen -----------------------------------------------------------------------------------------------------------

def _query(sid, expr):
    txt = _call("GET", "/query.lua", params={"sid": sid, "u": expr})
    try:
        v = json.loads(txt)["u"]
    except (ValueError, KeyError, TypeError):
        raise FritzWebError("Die Fritzbox lieferte unerwartete Daten (query.lua)") from None
    if not isinstance(v, list):
        raise FritzWebError("Die Fritzbox lieferte unerwartete Daten (query.lua)")
    return v


def _intern(n):
    d = str(n or "").lstrip("*").strip()
    return d if d.isdigit() else ""


def _query_many(sid, exprs):
    """Mehrere Ausdruecke in EINER Anfrage an query.lua ({schluessel: ausdruck} -> {schluessel: liste})."""
    if not exprs:
        return {}
    txt = _call("GET", "/query.lua", params=dict({"sid": sid}, **exprs))
    try:
        d = json.loads(txt)
    except ValueError:
        raise FritzWebError("Die Fritzbox lieferte unerwartete Daten (query.lua)") from None
    if not isinstance(d, dict) or not all(isinstance(d.get(k), list) for k in exprs):
        raise FritzWebError("Die Fritzbox lieferte unerwartete Daten (query.lua)")
    return {k: d[k] for k in exprs}


def _phones(sid):
    out, users = [], []
    for u in _query(sid, "telcfg:settings/Foncontrol/User/list(Name,Intern,Type,RingOnAllMSNs)"):
        if not isinstance(u, dict):
            continue
        m = re.fullmatch(r"User(\d+)", str(u.get("_node", "")))
        intern = _intern(u.get("Intern"))
        if not m or int(m.group(1)) == 0 or not intern or str(u.get("Type", "")) != "0":     # Type 0 = DECT
            continue
        users.append((int(m.group(1)), u, intern))
    # Rufnummern aller Telefone in einer Anfrage (statt einer je Telefon)
    msns = _query_many(sid, {f"m{idx}": f"telcfg:settings/Foncontrol/User{idx}/MSN/list(Number)" for idx, _, _ in users})
    for idx, u, intern in users:
        nums = [str(x.get("Number", "")).strip() for x in msns[f"m{idx}"] if isinstance(x, dict)]
        nums = [n for n in nums if n]
        out.append({"idx": idx, "name": str(u.get("Name", "")), "intern": "**" + intern,
                    "ring_all": str(u.get("RingOnAllMSNs", "")) == "1",
                    "out_num": nums[0].rstrip("#") if nums else "", "out_only": bool(nums) and nums[0].endswith("#"),
                    "incoming": [n for n in nums if not n.endswith("#")]})
    return out


_cache = {"t": 0.0, "v": None, "gen": 0}    # gen: zaehlt jedes cache_clear (Schreiben); aeltere Lesevorgaenge speichern nicht mehr
CACHE_TTL = 30                       # jede Anmeldung steht im Ereignisprotokoll der Fritzbox: die Seite nicht bei jedem Aufruf neu lesen


def cache_clear():
    _cache.update(t=0.0, v=None, gen=_cache["gen"] + 1)


def dect_phones(fresh=False):
    """DECT-Telefone mit ihren Rufnummern: [{idx, name, intern '**NNN', ring_all, out_num, incoming, out_only}].
    30 s zwischengespeichert; nach jedem Schreiben verworfen."""
    import copy
    import time
    if not fresh and _cache["v"] is not None and time.time() - _cache["t"] < CACHE_TTL:
        return copy.deepcopy(_cache["v"])
    gen = _cache["gen"]
    with session() as sid:
        v = _phones(sid)
    if _cache["gen"] == gen:             # waehrenddessen geschrieben (cache_clear): dieser Stand ist womoeglich alt, nicht speichern
        _cache.update(t=time.time(), v=v)
    return copy.deepcopy(v)


def _phone(sid, intern):
    want = "**" + _intern(intern)
    p = next((p for p in _phones(sid) if p["intern"] == want), None)
    if not p:
        raise FritzWebError(f"In der Fritzbox gibt es kein DECT-Telefon {want}")
    return p


# --- Formular edit_dect_num ------------------------------------------------------------------------------------------

class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.inputs, self.selects, self._sel = [], {}, None

    def handle_starttag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        if tag == "input":
            self.inputs.append(a)
        elif tag == "select":
            self._sel = a.get("name", "")
            self.selects.setdefault(self._sel, [])
        elif tag == "option" and self._sel is not None:
            self.selects[self._sel].append((a.get("value", ""), "selected" in a))

    def handle_endtag(self, tag):
        if tag == "select":
            self._sel = None


def _js_object(html, var):
    m = re.search(r"\bvar\s+" + re.escape(var) + r"\s*=\s*", html)
    if not m:
        return None
    try:
        return json.JSONDecoder().raw_decode(html, m.end())[0]
    except ValueError:
        return None


def parse_form(html, idx, kind="dect"):
    """Felder des Formulars edit_dect_num (kind 'dect') bzw. edit_ipfon_num (kind 'ip'):
    {kind, idx, name, fonbook, out_num, selection, nums: [{post_id, num, checked}], fields}. IP-Telefone haben kein Telefonbuch
    (fonbook None) und ip_idx statt idx. FritzWebError, wenn etwas Erwartetes fehlt (dann wird nichts geschrieben)."""
    k = KINDS[kind]
    bad = lambda what: FritzWebError(f"Das Formular der Fritzbox (Telefon bearbeiten) sieht anders aus als erwartet ({what}) – nichts geändert")  # noqa: E731
    if not isinstance(html, str) or "<input" not in html:
        raise bad("kein Formular")
    p = _FormParser()
    try:
        p.feed(html)
        p.close()
    except Exception:
        raise bad("nicht lesbar") from None
    by_name = {}
    for a in p.inputs:
        by_name.setdefault(a.get("name", ""), []).append(a)
    hid = [a for a in by_name.get(k["idx"], []) if a.get("type", "").lower() == "hidden"]
    if not hid or hid[0].get("value") != str(idx):
        raise bad("Telefon-Nummer " + k["idx"])
    name = [a for a in by_name.get("name", []) if (a.get("type") or "text").lower() == "text"]
    if not name:
        raise bad("Name")
    if kind == "ip" and any((a.get("type") or "").lower() == "password" for a in p.inputs):
        raise bad("Kennwortfeld")                   # erwartet ist keins: nichts mitsenden, was das Kennwort aendern koennte
    fonbook = None
    if k["fonbook"]:
        fb = p.selects.get("fonbook")
        if not fb:
            raise bad("Telefonbuch")
        fonbook = next((v for v, s in fb if s), None)
        if fonbook is None:                         # nicht raten: sonst stuende das Telefon danach am ersten Telefonbuch
            raise bad("Telefonbuch ohne Auswahl")
    outs = p.selects.get("out_num")
    if not outs:
        raise bad("ausgehende Rufnummer")
    m = re.search(r"\bvar\s+g_OutNum\s*=\s*\"([^\"]*)\"", html)
    out_num = m.group(1) if m else next((v for v, s in outs if s), "")
    if not out_num or out_num not in [v for v, _ in outs]:
        raise bad("ausgehende Rufnummer")
    radios = [a for a in by_name.get("num_selection", []) if a.get("type", "").lower() == "radio"]
    if {a.get("value") for a in radios} != {"all_nums", "sel_nums"}:
        raise bad("Auswahl alle/feste Rufnummern")
    sel = [a.get("value") for a in radios if "checked" in a]
    if len(sel) != 1:
        raise bad("Auswahl alle/feste Rufnummern")
    nums = []
    data = _js_object(html, "data")
    if isinstance(data, dict) and isinstance(data.get("allIds"), list):
        for e in data["allIds"]:
            if not isinstance(e, dict) or not re.fullmatch(r"num_\d+", str(e.get("post_id", ""))) or not str(e.get("num", "")).strip():
                raise bad("Rufnummernliste")
            nums.append({"post_id": e["post_id"], "num": str(e["num"]).strip(), "checked": e.get("checked") is True})
    else:                                           # Rueckfall: die Checkboxen selbst
        for a in p.inputs:
            if a.get("type", "").lower() == "checkbox" and re.fullmatch(r"num_\d+", a.get("name", "")) and a.get("value", "").strip():
                nums.append({"post_id": a["name"], "num": a["value"].strip(), "checked": "checked" in a})
    if not nums or len({n["post_id"] for n in nums}) != len(nums) or len({n["num"] for n in nums}) != len(nums):
        raise bad("Rufnummernliste")
    return {"kind": kind, "idx": idx, "name": name[0].get("value", ""), "fonbook": fonbook, "out_num": out_num, "selection": sel[0], "nums": nums,
            "out_opts": [v for v, _ in outs if v], "fields": _fields(p)}


SKIP_TYPES = {"button", "submit", "reset", "image", "file"}


def _fields(p):
    """Alle uebrigen Felder des Formulars so, wie ein Browser sie beim Speichern sendet (Text/versteckt, angehakte Checkboxen,
    gewaehlte Radio-Knoepfe, Auswahl der Listen) - ausser den Rufnummern (num_*, num_selection), die _save selbst setzt."""
    out = {}
    skip = lambda n: not n or re.fullmatch(r"num_\d+", n) or n in ("num_selection", "sid") or n.startswith("btn_")  # noqa: E731
    for a in p.inputs:
        n, t = a.get("name", ""), (a.get("type") or "text").lower()
        if skip(n) or t in SKIP_TYPES or "disabled" in a:
            continue
        if t in ("checkbox", "radio"):
            if "checked" in a:
                out[n] = a.get("value") or "on"
        else:
            out.setdefault(n, a.get("value", ""))
    for n, opts in p.selects.items():
        if skip(n) or not opts:
            continue
        out[n] = next((v for v, s in opts if s), opts[0][0])     # ohne "selected" sendet ein Browser die erste Option
    return out


def _form(sid, idx, kind="dect"):
    k = KINDS[kind]
    html = _call("POST", "/data.lua", data={"sid": sid, "xhr": "1", "lang": "de", "page": k["page"], k["idx"]: str(idx), "xhrId": "all"})
    return parse_form(html, idx, kind)


def _save(sid, form, selection, checked):
    # alle uebrigen Felder unveraendert mitsenden (sonst setzt die Box sie zurueck), dann die Steuerfelder und die Rufnummern
    kind = form.get("kind", "dect")
    k = KINDS[kind]
    d = dict(form.get("fields") or {})
    d.update({"sid": sid, "xhr": "1", "lang": "de", "page": k["page"], "btn_save": "", k["idx"]: str(form["idx"]),
              "name": form["name"], "out_num": form["out_num"], "num_selection": selection})
    d["back_to_page"] = (d.get("back_to_page") or BACK) if kind == "ip" else BACK
    if k["fonbook"]:
        d["fonbook"] = form["fonbook"]
    for n in form["nums"]:
        if n["num"] in checked:
            d[n["post_id"]] = n["num"]
    txt = _call("POST", "/data.lua", data=d)
    try:
        res = json.loads(txt)["data"]
        ok = res.get("btn_save") == "ok" or (kind == "ip" and "redirect" in res)
    except (ValueError, KeyError, TypeError, AttributeError):
        ok = False
    if not ok:
        if kind == "ip":                # Antwort der IP-Seite live noch nicht gesehen: das Gegenlesen entscheidet
            log.info("Fritzbox: unerwartete Antwort beim Speichern (edit_ipfon_num) - wird gegengelesen")
            return
        raise FritzWebError("Die Fritzbox hat die Rufnummern des Telefons nicht gespeichert")


def _matches(p, checked):
    return not p["ring_all"] and set(p["incoming"]) == set(checked)


def _write(sid, phone, form, checked):
    """Feste Auswahl `checked` speichern, gegenlesen; bei Abweichung alten Stand zurueckschreiben und Fehler werfen."""
    old_sel, old_checked = form["selection"], {n["num"] for n in form["nums"] if n["checked"]}
    cache_clear()
    _save(sid, form, "sel_nums", checked)
    try:
        now = _phone(sid, phone["intern"])
        good = _matches(now, checked)
    except FritzWebError:
        good = False
    if good:
        return now
    restored = True
    try:
        _save(sid, form, old_sel, old_checked)
    except FritzWebError:
        restored = False
    log.warning("Fritzbox: Rufnummern von %s nach dem Speichern nicht wie erwartet - %s", phone["intern"],
                "alter Stand zurueckgeschrieben" if restored else "Zurueckschreiben fehlgeschlagen")
    raise FritzWebError("Die Fritzbox zeigt nach dem Speichern nicht den erwarteten Stand – "
                        + ("der alte Stand wurde zurückgeschrieben." if restored else
                           "der alte Stand konnte NICHT zurückgeschrieben werden, bitte in der Fritzbox prüfen."), wrote=True)


def dect_assign(intern, number, on):
    """Rufnummer `number` beim DECT-Telefon `intern` als ankommende Nummer an-/abhaken. Rueckgabe: 'ok' (geaendert), 'same'
    (war schon so) oder 'all' (das Telefon klingelt bei allen Rufnummern - bewusst nicht umgestellt, siehe dect_select_numbers)."""
    with session() as sid:
        phone = _phone(sid, intern)
        form = _form(sid, phone["idx"])
        if phone["ring_all"] or form["selection"] != "sel_nums":
            return "all"
        known = {n["num"] for n in form["nums"]}
        if number not in known:
            raise FritzWebError(f"Die Rufnummer {number} kennt das Formular der Fritzbox (noch) nicht – nichts geändert")
        checked = {n["num"] for n in form["nums"] if n["checked"]}
        if (number in checked) == bool(on) and (number in phone["incoming"]) == bool(on):
            return "same"
        checked = (checked | {number}) if on else (checked - {number})
        if not checked:
            raise FritzWebError("Das Telefon hätte danach keine ankommende Rufnummer mehr – nichts geändert")
        if len(checked) > MAX_NUMS:
            raise FritzWebError(f"Das Telefon hat schon {MAX_NUMS} ankommende Rufnummern (mehr erlaubt die Fritzbox nicht)")
        _write(sid, phone, form, checked)
        return "ok"


def dect_select_numbers(intern, numbers):
    """Bewusst von "alle Rufnummern" (oder einer anderen Auswahl) auf genau `numbers` umstellen. Rueckgabe: neuer Stand des Telefons."""
    numbers = [str(n).strip() for n in dict.fromkeys(numbers or []) if str(n).strip()]
    if not numbers:
        raise FritzWebError("Mindestens eine Rufnummer wählen")
    if len(numbers) > MAX_NUMS:
        raise FritzWebError(f"Höchstens {MAX_NUMS} ankommende Rufnummern je Telefon")
    with session() as sid:
        phone = _phone(sid, intern)
        form = _form(sid, phone["idx"])
        unknown = [n for n in numbers if n not in {x["num"] for x in form["nums"]}]
        if unknown:
            raise FritzWebError("Diese Rufnummern kennt die Fritzbox nicht: " + ", ".join(unknown))
        return _write(sid, phone, form, set(numbers))


def box_numbers(intern):
    """Alle Rufnummern, die die Fritzbox im Formular dieses Telefons anbietet (fuer den Vorschlag beim Umstellen)."""
    with session() as sid:
        phone = _phone(sid, intern)
        return [n["num"] for n in _form(sid, phone["idx"])["nums"]]


def _out_check(form, number):
    number = str(number or "").strip()
    if not number:
        raise FritzWebError("Keine ausgehende Rufnummer angegeben")
    if number not in form["out_opts"]:
        raise FritzWebError(f"Die Rufnummer {number} bietet die Fritzbox bei diesem Telefon nicht als ausgehende Nummer an – nichts geändert")
    return number


def dect_set_out(intern, number):
    """Ausgehende Rufnummer des DECT-Telefons `intern` auf `number` setzen (Feld out_num des Formulars; Auswahl und Haekchen der
    ankommenden Nummern und alle uebrigen Felder bleiben). Gegenlesen ueber query.lua (erster MSN-Eintrag); stimmt es nicht, wird
    die bisherige Nummer zurueckgeschrieben und FritzWebError(wrote=True) geworfen. Rueckgabe: Stand des Telefons (wie dect_phones)
    plus out_was (die bisherige ausgehende Nummer)."""
    with session() as sid:
        phone = _phone(sid, intern)
        form = _form(sid, phone["idx"])
        number = _out_check(form, number)
        was = form["out_num"]
        if was == number and phone["out_num"] == number:
            return dict(phone, out_was=was)
        sel, checked = form["selection"], {n["num"] for n in form["nums"] if n["checked"]}
        cache_clear()
        _save(sid, dict(form, out_num=number), sel, checked)
        try:
            now = _phone(sid, intern)
            # bei "alle" listet query.lua nur die ausgehende Nummer: dann zaehlt nur "alle"
            good = now["out_num"] == number and now["ring_all"] == phone["ring_all"] and (
                phone["ring_all"] or set(now["incoming"]) == set(phone["incoming"]))
        except FritzWebError:
            now, good = None, False
        if good:
            return dict(now, out_was=was)
        try:
            _save(sid, form, sel, checked)
            back = _phone(sid, intern)
            restored = back["out_num"] == was
        except FritzWebError:
            restored = False
        log.warning("Fritzbox: ausgehende Nummer von %s nach dem Speichern nicht wie erwartet - %s", phone["intern"],
                    "alter Stand zurueckgeschrieben" if restored else "Zurueckschreiben fehlgeschlagen")
        raise FritzWebError("Die Fritzbox zeigt nach dem Speichern nicht die neue ausgehende Rufnummer – "
                            + ("die bisherige wurde zurückgeschrieben." if restored else
                               "die bisherige konnte NICHT zurückgeschrieben werden, bitte in der Fritzbox prüfen."), wrote=True)


def dect_set_all(intern):
    """Zurueck auf "alle Rufnummern" (ankommend). Gegenlesen; bei Abweichung alten Stand zurueckschreiben."""
    with session() as sid:
        phone = _phone(sid, intern)
        if phone["ring_all"]:
            return phone
        form = _form(sid, phone["idx"])
        old = {n["num"] for n in form["nums"] if n["checked"]}
        cache_clear()
        _save(sid, form, "all_nums", old)
        try:
            now = _phone(sid, intern)
        except FritzWebError:
            now = None
        if now and now["ring_all"]:
            return now
        try:
            _save(sid, form, form["selection"], old)
            restored = True
        except FritzWebError:
            restored = False
        raise FritzWebError("Die Fritzbox zeigt nach dem Speichern nicht „alle Rufnummern“ – "
                            + ("der alte Stand wurde zurückgeschrieben." if restored else "bitte in der Fritzbox prüfen."))



# --- IP-Telefone (edit_ipfon_num, ip_idx = TR-064-Client-Index) ------------------------------------------------------

def _ip_state(form):
    """Stand eines IP-Telefons aus seinem Formular: {idx, name, out_num, ring_all, incoming, numbers}."""
    return {"idx": form["idx"], "name": form["name"], "out_num": form["out_num"], "ring_all": form["selection"] == "all_nums",
            "incoming": [n["num"] for n in form["nums"] if n["checked"]], "numbers": [n["num"] for n in form["nums"]]}


def _ip_idx(client_idx):
    if not str(client_idx).isdigit():
        raise FritzWebError("Unbekanntes IP-Telefon (Client-Index fehlt)")
    return int(client_idx)


def ip_form(client_idx):
    """Formular edit_ipfon_num des IP-Telefons mit TR-064-Client-Index `client_idx` (nur lesend): Stand wie _ip_state."""
    idx = _ip_idx(client_idx)
    with session() as sid:
        return _ip_state(_form(sid, idx, "ip"))


def _ip_write(sid, form, selection, checked):
    """Speichern, ueber das Formular gegenlesen; bei Abweichung alten Stand zurueckschreiben (auch gegengelesen) und Fehler werfen."""
    def state():
        try:
            f = _form(sid, form["idx"], "ip")
        except FritzWebError:
            return None, None
        return f, (f["selection"], {n["num"] for n in f["nums"] if n["checked"]})

    def same(st, sel, chk):             # bei "alle" zaehlen die Haekchen nicht
        return st is not None and st[0] == sel and (sel == "all_nums" or st[1] == set(chk))
    old_sel, old_checked = form["selection"], {n["num"] for n in form["nums"] if n["checked"]}
    cache_clear()
    _save(sid, form, selection, checked)
    now, st = state()
    if same(st, selection, checked):
        return _ip_state(now)
    try:
        _save(sid, form, old_sel, old_checked)
        restored = same(state()[1], old_sel, old_checked)
    except FritzWebError:
        restored = False
    log.warning("Fritzbox: Rufnummern des IP-Telefons (Client %s) nach dem Speichern nicht wie erwartet - %s", form["idx"],
                "alter Stand zurueckgeschrieben" if restored else "Zurueckschreiben fehlgeschlagen")
    raise FritzWebError("Die Fritzbox zeigt nach dem Speichern nicht den erwarteten Stand – "
                        + ("der alte Stand wurde zurückgeschrieben." if restored else
                           "der alte Stand konnte NICHT zurückgeschrieben werden, bitte in der Fritzbox prüfen."), wrote=True)


def ip_set_numbers(client_idx, numbers, ring_all=False):
    """Ankommende Rufnummern des IP-Telefons setzen: genau `numbers` (feste Auswahl) oder mit ring_all=True "alle Rufnummern".
    Gegenlesen ueber das Formular; bei Abweichung alter Stand zurueck. Rueckgabe: neuer Stand (wie ip_form)."""
    idx = _ip_idx(client_idx)
    if not ring_all:
        numbers = [str(n).strip() for n in dict.fromkeys(numbers or []) if str(n).strip()]
        if not numbers:
            raise FritzWebError("Mindestens eine Rufnummer wählen")
        if len(numbers) > MAX_NUMS:
            raise FritzWebError(f"Höchstens {MAX_NUMS} ankommende Rufnummern je Telefon")
    with session() as sid:
        form = _form(sid, idx, "ip")
        cur = {n["num"] for n in form["nums"] if n["checked"]}
        if ring_all:
            if form["selection"] == "all_nums":
                return _ip_state(form)
            return _ip_write(sid, form, "all_nums", cur)
        unknown = [n for n in numbers if n not in {x["num"] for x in form["nums"]}]
        if unknown:
            raise FritzWebError("Diese Rufnummern kennt die Fritzbox nicht: " + ", ".join(unknown))
        if form["selection"] == "sel_nums" and cur == set(numbers):
            return _ip_state(form)
        return _ip_write(sid, form, "sel_nums", set(numbers))


def ip_assign(client_idx, number, on):
    """Wie dect_assign, fuer das IP-Telefon mit TR-064-Client-Index `client_idx`: 'ok', 'same' oder 'all' (klingelt bei allen
    Rufnummern - bewusst nicht umgestellt)."""
    idx = _ip_idx(client_idx)
    with session() as sid:
        form = _form(sid, idx, "ip")
        if form["selection"] != "sel_nums":
            return "all"
        if number not in {n["num"] for n in form["nums"]}:
            raise FritzWebError(f"Die Rufnummer {number} kennt das Formular der Fritzbox (noch) nicht – nichts geändert")
        checked = {n["num"] for n in form["nums"] if n["checked"]}
        if (number in checked) == bool(on):
            return "same"
        checked = (checked | {number}) if on else (checked - {number})
        if not checked:
            raise FritzWebError("Das Telefon hätte danach keine ankommende Rufnummer mehr – nichts geändert")
        if len(checked) > MAX_NUMS:
            raise FritzWebError(f"Das Telefon hat schon {MAX_NUMS} ankommende Rufnummern (mehr erlaubt die Fritzbox nicht)")
        _ip_write(sid, form, "sel_nums", checked)
        return "ok"


def ip_set_out(client_idx, number):
    """Ausgehende Rufnummer des IP-Telefons (TR-064-Client-Index) auf `number` setzen; ankommende Nummern und uebrige Felder bleiben.
    Gegenlesen ueber das Formular; bei Abweichung die bisherige Nummer zurueck (auch gegengelesen) und FritzWebError(wrote=True).
    Rueckgabe: Stand wie ip_form plus out_was."""
    idx = _ip_idx(client_idx)
    with session() as sid:
        form = _form(sid, idx, "ip")
        number = _out_check(form, number)
        was = form["out_num"]
        if was == number:
            return dict(_ip_state(form), out_was=was)
        sel, checked = form["selection"], {n["num"] for n in form["nums"] if n["checked"]}

        def now():
            try:
                f = _form(sid, idx, "ip")
            except FritzWebError:
                return None
            return f if f["selection"] == sel and (sel == "all_nums" or {n["num"] for n in f["nums"] if n["checked"]} == checked) else None
        cache_clear()
        _save(sid, dict(form, out_num=number), sel, checked)
        f = now()
        if f and f["out_num"] == number:
            return dict(_ip_state(f), out_was=was)
        try:
            _save(sid, form, sel, checked)
            f = now()
            restored = bool(f and f["out_num"] == was)
        except FritzWebError:
            restored = False
        log.warning("Fritzbox: ausgehende Nummer des IP-Telefons (Client %s) nach dem Speichern nicht wie erwartet - %s", idx,
                    "alter Stand zurueckgeschrieben" if restored else "Zurueckschreiben fehlgeschlagen")
        raise FritzWebError("Die Fritzbox zeigt nach dem Speichern nicht die neue ausgehende Rufnummer – "
                            + ("die bisherige wurde zurückgeschrieben." if restored else
                               "die bisherige konnte NICHT zurückgeschrieben werden, bitte in der Fritzbox prüfen."), wrote=True)
