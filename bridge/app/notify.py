"""E-Mail-Benachrichtigungen (z. B. ueber ein Outlook-/Microsoft-365-Konto per SMTP) und der taegliche Anrufbericht."""
import asyncio
import html
import json
import re
import logging
import os
import smtplib
import ssl
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from . import db
from .logmask import email as _m_email, text as _m_text

log = logging.getLogger("notify")
SENT = {}      # Empfaenger-ID -> Zeitstempel der letzten Mails (Flutschutz)
MAX_PER_HOUR = 30


def config():
    """Einstellungen aus der Oberflaeche (meta), Rueckfall auf die .env. Das Passwort steht in meta (smtp_password) oder in SMTP_PASSWORD."""
    g = lambda k, env, default="": db.get_meta(k, "") or os.environ.get(env, "") or default
    user = g("smtp_user", "SMTP_USER")
    return {"host": g("smtp_host", "SMTP_HOST", "smtp.office365.com"), "port": int(g("smtp_port", "SMTP_PORT", "587") or 587),
            "security": g("smtp_security", "SMTP_SECURITY", "starttls"), "user": user,
            "from": g("smtp_from", "SMTP_FROM", user), "password": db.get_meta("smtp_password", "") or os.environ.get("SMTP_PASSWORD", ""),
            "password_source": "ui" if db.get_meta("smtp_password", "") else ("env" if os.environ.get("SMTP_PASSWORD") else ""),
            "base_url": db.get_meta("base_url", "").rstrip("/"), "report_time": db.get_meta("report_time", "18:00") or "18:00"}


def configured():
    c = config()
    return bool(c["host"] and c["user"] and c["password"] and c["from"])


def send_mail(to, subject, text, html_body=None, attachments=None):
    """Blockierend (per to_thread aufrufen). Wirft RuntimeError mit lesbarer Meldung."""
    c = config()
    if not (c["host"] and c["user"] and c["password"]):
        raise RuntimeError("Mail nicht eingerichtet: Server, Benutzer und Passwort fehlen (Seite Benachrichtigungen)")
    to = [t.strip() for t in (to if isinstance(to, list) else [to]) if t and t.strip()]
    if not to:
        raise RuntimeError("Kein Empfaenger")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = c["from"], ", ".join(to), subject
    msg["Date"], msg["Message-ID"] = formatdate(localtime=True), make_msgid()
    msg.set_content(text, cte="quoted-printable")
    if html_body:
        msg.add_alternative(html_body, subtype="html", cte="quoted-printable")
    for fname, data, maintype, subtype in attachments or []:
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=fname)
    log.info("Mail: sende '%s' an %s ueber %s:%s (%s)", _m_text(subject[:60]), ", ".join(map(_m_email, to)), c["host"], c["port"], c["security"])
    try:
        ctx = ssl.create_default_context()
        if c["security"] == "ssl":
            s = smtplib.SMTP_SSL(c["host"], c["port"], timeout=25, context=ctx)
        else:
            s = smtplib.SMTP(c["host"], c["port"], timeout=25)
        with s:
            if c["security"] == "starttls":
                s.starttls(context=ctx)
            s.login(c["user"], c["password"])
            s.send_message(msg)
        log.info("Mail: an %s gesendet", ", ".join(map(_m_email, to)))
    except smtplib.SMTPAuthenticationError as e:
        log.warning("Mail: Anmeldung am Server abgelehnt (%s %s)", e.smtp_code, (e.smtp_error or b"")[:200])
        raise RuntimeError("Anmeldung am Mailserver abgelehnt (Benutzer/Passwort, App-Kennwort oder SMTP-Authentifizierung "
                           f"beim Microsoft-Konto nicht erlaubt): {e.smtp_code}") from None
    except (smtplib.SMTPException, OSError) as e:
        log.warning("Mail: Versand fehlgeschlagen: %s: %s", type(e).__name__, _m_text(e))
        raise RuntimeError(f"Mailversand fehlgeschlagen: {type(e).__name__}: {e}"[:300]) from None


# --- Empfaenger ---
OUT_HOOKS = []      # Plugins: fn(call, meta) -> [{'id','email','detail','on_call'}] fuer ausgehende Anrufe


# Ergebnisse eines eingehenden Anrufs, nach denen je Empfaenger gefiltert werden kann (Schluessel = calls.status)
NOTIFY_STATUSES = [("beendet", "Von der KI bearbeitet"), ("durchgestellt", "Von der KI durchgestellt"), ("angenommen", "Von einer Person angenommen"),
                   ("unbeantwortet", "Nicht angenommen"), ("abgelehnt", "Abgelehnt (z. B. Kostenbremse)"), ("abgebrochen", "Abgebrochen")]


def status_ok(r, call):
    """Hat der Empfaenger dieses Ergebnis gewaehlt? Leer = alle Ergebnisse."""
    wanted = [x for x in (r.get("on_status") or "").split(",") if x]
    return not wanted or call.get("status") in wanted


def recipients(called=None, event=None):
    out = []
    for r in db.q("SELECT * FROM mail_recipients WHERE enabled=1"):
        nums = [db._digits(n) for n in r["numbers"].split(",") if n.strip()]
        if called is not None and nums and db._digits(called) not in nums:
            continue
        out.append(r)
    return out


def _important(call, meta, notes):
    if any(k.startswith("Nachricht") for k in notes):
        return True
    if meta.get("dringlichkeit") == "hoch":
        return True
    mood = str(notes.get("Stimmung", "")) + " " + str(notes.get("Stimmung (Wortlaut)", ""))
    return any(m in mood for m in ("panisch", "aufgeregt", "aengstlich")) and any(f"({n}/5)" in mood for n in (4, 5))


def _detail_html(detail):
    """Besprochene Punkte als Karten (Thema, Inhalt, hervorgehobenes Ergebnis), danach Zusagen und Offenes als Tabellen/Listen."""
    from . import analyze
    d = analyze.normalize_detail(detail)
    head = lambda t: (f"<div style=\"margin-top:24px;padding-bottom:6px;border-bottom:2px solid #e5e7eb;font-size:12px;font-weight:700;letter-spacing:.06em;"
                      f"text-transform:uppercase;color:#374151\">{_e(t)}</div>")
    h = ""
    if d["punkte"]:
        h += head("Besprochene Punkte")
        for i, p in enumerate(d["punkte"], 1):
            none = p["ergebnis"].lower().startswith("kein ergebnis")
            res = ""
            if p["ergebnis"]:
                bg, bd, fg = ("#fffbeb", "#f59e0b", "#92400e") if none else ("#ecfdf5", "#10b981", "#065f46")
                res = (f"<div style=\"margin-top:10px;padding:9px 12px;background:{bg};border-left:4px solid {bd};border-radius:4px;font-size:13px;line-height:20px;color:{fg}\">"
                       f"<b>Ergebnis:</b> {_e(p['ergebnis'])}</div>")
            h += (f"<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"margin-top:12px;border:1px solid #e5e7eb;border-radius:8px\"><tr>"
                  f"<td style=\"padding:12px 14px\"><div style=\"font-size:14px;line-height:20px;font-weight:700;color:#111827\">"
                  f"<span style=\"color:#2563eb\">{i}.</span> {_e(p['thema'] or 'Punkt')}</div>"
                  + (f"<div style=\"margin-top:5px;font-size:13px;line-height:20px;color:#374151\">{_e(p['inhalt'])}</div>" if p["inhalt"] else "")
                  + res + "</td></tr></table>")
    if d["zusagen"]:
        h += head("Zusagen und Vereinbarungen")
        rows = "".join(
            f"<tr><td style=\"padding:8px 12px 8px 0;vertical-align:top;font-size:13px;font-weight:700;color:#111827;white-space:nowrap;width:1%;border-bottom:1px solid #f3f4f6\">{_e(z['wer'] or '–')}</td>"
            f"<td style=\"padding:8px 12px 8px 0;vertical-align:top;font-size:13px;line-height:19px;color:#374151;border-bottom:1px solid #f3f4f6\">{_e(z['was'])}</td>"
            f"<td style=\"padding:8px 0;vertical-align:top;font-size:12px;color:#6b7280;white-space:nowrap;border-bottom:1px solid #f3f4f6\">{_e(('bis ' + z['bis_wann']) if z['bis_wann'] else '')}</td></tr>"
            for z in d["zusagen"])
        h += f"<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"margin-top:6px\">{rows}</table>"
    if d["offen"]:
        h += head("Offen")
        items = "".join(f"<tr><td style=\"padding:8px 10px 8px 0;vertical-align:top;font-size:13px;color:#f59e0b;width:1%\">&#9679;</td>"
                        f"<td style=\"padding:8px 0;font-size:13px;line-height:19px;color:#374151;border-bottom:1px solid #f3f4f6\">{_e(o['punkt'])}"
                        + (f"<div style=\"margin-top:2px;font-size:12px;color:#6b7280\">{_e(o['grund'])}</div>" if o["grund"] else "") + "</td></tr>" for o in d["offen"])
        h += f"<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"margin-top:6px\">{items}</table>"
    return h


def _load(call_id):
    r = db.q("SELECT * FROM calls WHERE id=?", (call_id,))
    if not r:
        return None, {}, {}
    call = r[0]
    try:
        meta = json.loads(call.get("meta") or "{}")
    except ValueError:
        meta = {}
    try:
        notes = json.loads(call.get("notes") or "{}")
    except ValueError:
        notes = {}
    return call, meta, notes


def _who(call):
    c = db.find_contact(call["caller"])
    return f"{c['name']} ({call['caller']})" if c else (call["caller"] or "unbekannt")


def _dur(call):
    if not call.get("ended") or call["ended"] <= call["started"]:
        return "–"
    s = int(call["ended"] - call["started"])
    return f"{s // 60} min {s % 60:02d} s" if s >= 60 else f"{s} s"


def _fmt_ts(ts):
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


def party_label(call):
    """Name der Gegenseite fuers Transkript: Kontaktname, sonst Rufnummer, sonst 'Anrufer' (bei ausgehenden Anrufen ist es die angerufene Nummer)."""
    number = (call.get("called") if call.get("direction") in ("out", "rec") else call.get("caller")) or ""
    c = db.find_contact(number) if number else None
    return c["name"] if c else (number.strip() or "Anrufer")


def _connected_name(call):
    """Name der Person, die die KI verbunden bzw. intern gefragt hat (steht als Systemzeile im Transkript: 'Verbinde mit X' / 'Rueckfrage bei X ...')."""
    if not call.get("id"):
        return ""
    r = db.q("SELECT text FROM segments WHERE call_id=? AND speaker='system' AND (text LIKE 'Verbinde mit %' OR text LIKE 'Rückfrage bei %') ORDER BY id DESC LIMIT 1", (call["id"],))
    m = re.match(r"^(?:Verbinde mit|Rückfrage bei) (.+?)(?: vor dem Verbinden)?\s*$", r[0]["text"]) if r else None
    return m.group(1).strip() if m else ""


def local_label(call):
    """Name der eigenen Seite im Transkript: die Person, die die KI verbunden bzw. gefragt hat; sonst gehoert die angerufene Nummer genau einem
    Mitarbeiter, dessen Name; teilen sich mehrere die Nummer, das Telefon, an dem abgenommen wurde; sonst 'Benutzer'. Bei ausgehenden Anrufen der
    Mitarbeiter, der angerufen hat."""
    if call.get("direction") in ("out", "rec"):
        sid = call.get("owner_staff_id")
        st = db.q("SELECT name FROM staff WHERE id=?", (sid,)) if sid else []
        return st[0]["name"] if st else "Benutzer"
    name = _connected_name(call)
    if name:
        return name
    owners = db.staffs_for_number([call.get("called") or ""])
    if len(owners) == 1:
        return owners[0]["name"]
    try:
        taken = json.loads(call.get("notes") or "{}").get("Angenommen von", "")
    except ValueError:
        taken = ""
    return taken or "Benutzer"


def _transcript_rows(call_id):
    """[(Rolle, Name, Text)] - aufeinanderfolgende Beitraege desselben Sprechers zusammengefasst (kleinere Mail)."""
    call = (db.q("SELECT id, caller, called, direction, notes, owner_staff_id FROM calls WHERE id=?", (call_id,)) or [{}])[0]
    party = party_label(call)
    lab = {"caller": party, "remote": party, "agent": "Assistent", "local": local_label(call), "mix": "Gespräch"}
    rows = db.q("SELECT speaker, text FROM segments WHERE call_id=? AND speaker IN ('caller','agent','local','remote','mix') ORDER BY id", (call_id,))
    merged = []
    for r in rows:
        if merged and merged[-1][0] == r["speaker"] and r["speaker"] != "mix":
            merged[-1][2] += " " + r["text"]
        else:
            merged.append([r["speaker"], lab[r["speaker"]], r["text"]])
    return merged


def _transcript(call_id, limit=60000):
    text = "\n".join(f"{name}: {t}" for _, name, t in _transcript_rows(call_id))
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + " …\n(gekürzt - vollständig im Webinterface)"
    return text


# --- HTML-Gestaltung (tabellenbasiert mit Inline-Styles: funktioniert in Outlook, Gmail und auf dem Handy) ---
FONT = "'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
STATUS_STYLE = {            # Status -> (Text, Hintergrund, Schrift)
    "beendet": ("Von der KI bearbeitet", "#e0f2fe", "#075985"),
    "aktiv": ("Läuft", "#dcfce7", "#166534"),
    "durchgestellt": ("Durchgestellt", "#ede9fe", "#5b21b6"),
    "angenommen": ("Angenommen", "#dcfce7", "#166534"),
    "unbeantwortet": ("Nicht angenommen", "#fee2e2", "#991b1b"),
    "abgelehnt": ("Abgelehnt", "#fee2e2", "#991b1b"),
    "nicht erreicht": ("Nicht erreicht", "#fef3c7", "#92400e"),
    "abgebrochen": ("Abgebrochen", "#f3f4f6", "#374151"),
}
URGENCY_STYLE = {"hoch": ("#fee2e2", "#991b1b"), "mittel": ("#fef3c7", "#92400e"), "niedrig": ("#dcfce7", "#166534")}


def _e(x):
    return html.escape(str(x if x is not None else ""))


def _badge(text, bg, fg):
    return (f"<span style=\"display:inline-block;padding:3px 10px;border-radius:999px;background:{bg};color:{fg};"
            f"font-size:12px;font-weight:600;line-height:18px;white-space:nowrap\">{_e(text)}</span>")


def _status_badge(status):
    t, bg, fg = STATUS_STYLE.get(status, (status, "#f3f4f6", "#374151"))
    return _badge(t, bg, fg)


def _button(url, label):
    return (f"<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" style=\"margin:22px 0 4px\"><tr><td style=\"background:#2563eb;border-radius:8px\">"
            f"<a href=\"{_e(url)}\" style=\"display:inline-block;padding:11px 22px;color:#ffffff;font-family:{FONT};font-size:14px;"
            f"font-weight:600;text-decoration:none\">{_e(label)}</a></td></tr></table>")


def _layout(kicker, title, subtitle, content, accent="#2563eb", footer_link=""):
    """Rahmen aller Mails: Kopfband, Titel, Inhalt, Fusszeile."""
    stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    return (f"<!DOCTYPE html><html lang=\"de\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"></head>"
            f"<body style=\"margin:0;padding:0;background:#f3f4f6\"><span style=\"display:none;max-height:0;overflow:hidden;opacity:0\">{_e(subtitle)}</span>"
            f"<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"background:#f3f4f6;padding:24px 12px\"><tr><td align=\"center\">"
            f"<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"max-width:640px;background:#ffffff;border-radius:12px;overflow:hidden;"
            f"border:1px solid #e5e7eb;font-family:{FONT};color:#111827\">"
            f"<tr><td style=\"background:#111827;padding:14px 28px;font-size:14px;font-weight:600;color:#ffffff\">"
            f"<span style=\"font-size:16px;margin-right:6px\">&#9742;</span>Lieschen Müller"
            f"<span style=\"float:right;font-weight:400;color:#9ca3af;font-size:12px\">{_e(kicker)}</span></td></tr>"
            f"<tr><td style=\"height:4px;background:{accent};font-size:0;line-height:0\">&nbsp;</td></tr>"
            f"<tr><td style=\"padding:26px 28px 8px\"><div style=\"font-size:22px;line-height:28px;font-weight:700;color:#111827\">{title}</div>"
            f"<div style=\"margin-top:4px;font-size:14px;color:#6b7280\">{_e(subtitle)}</div></td></tr>"
            f"<tr><td style=\"padding:8px 28px 28px;font-size:14px;line-height:21px\">{content}</td></tr>"
            f"<tr><td style=\"background:#f9fafb;border-top:1px solid #e5e7eb;padding:14px 28px;font-size:12px;color:#9ca3af\">"
            f"Automatisch erzeugt von Lieschen Müller &middot; {stamp}{footer_link}</td></tr></table></td></tr></table></body></html>")


def _facts(rows):
    out = ""
    for k, v in rows:
        out += (f"<tr><td style=\"padding:7px 14px 7px 0;color:#6b7280;font-size:13px;vertical-align:top;white-space:nowrap;width:1%\">{_e(k)}</td>"
                f"<td style=\"padding:7px 0;font-size:14px;color:#111827;border-bottom:1px solid #f3f4f6\">{v}</td></tr>")
    return f"<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"margin-top:6px\">{out}</table>"


def _callout(label, text, bg="#f8fafc", border="#cbd5e1"):
    return (f"<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"margin-top:16px\"><tr>"
            f"<td style=\"background:{bg};border-left:4px solid {border};border-radius:6px;padding:12px 16px\">"
            f"<div style=\"font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#6b7280\">{_e(label)}</div>"
            f"<div style=\"margin-top:4px;font-size:14px;line-height:21px;color:#111827;white-space:pre-wrap\">{_e(text)}</div></td></tr></table>")


def build_call_mail(call, meta, notes, detail="short", details=True, points=False):
    """detail="full": mit Transkript; details=False: ohne Zusammenfassung/Zusatzinfos (hinterlassene Nachrichten bleiben)."""
    c = config()
    imp = _important(call, meta, notes)
    if not details:
        meta, notes = {}, {k: v for k, v in notes.items() if k.startswith("Nachricht")}
    who = _who(call)
    out = call["direction"] == "out"
    subject = (f"Kampagnenanruf an {call['called']} – {call['status']}" if out else
               f"Gespräch mit {call['called']} – {call['status']}" if call["direction"] == "rec" else f"Anruf von {who} auf {call['called']} – {call['status']}")
    link = f"{c['base_url']}/#/calls/{call['id']}" if c["base_url"] else ""
    # Klartext
    lines = [("Zeit", _fmt_ts(call["started"])), ("Von", who), ("Auf Nummer", call["called"]),
             ("Behandelt von", call["agent_name"] or "–"), ("Status", call["status"]), ("Dauer", _dur(call))]
    with_points = bool(points and (meta.get("detail") or {}).get("punkte"))
    if meta.get("zusammenfassung"):
        if not (with_points and (meta["detail"] or {}).get("fazit")):          # das Fazit ersetzt die Kurzfassung
            lines.append(("Zusammenfassung", meta["zusammenfassung"]))
        for k, lab in (("anliegen", "Anliegen"), ("dringlichkeit", "Dringlichkeit"), ("naechster_schritt", "Nächster Schritt")):
            if meta.get(k):
                lines.append((lab, meta[k]))
    if with_points:
        from . import analyze
        lines.append(("", "\n" + analyze.detail_text(meta["detail"])))
    for k, v in notes.items():
        lines.append((k, v))
    trans = _transcript(call["id"]) if detail == "full" else ""
    if trans:
        lines.append(("Transkript", trans))
    text = "\n".join(f"{k}: {v}" if k else v for k, v in lines) + (f"\n\nIm Webinterface: {link}" if link else "")
    # HTML
    facts = [("Zeit", _e(_fmt_ts(call["started"]))), ("Dauer", _e(_dur(call))),
             ("Angerufen", _e(call["called"])), ("Behandelt von", _e(call["agent_name"] or "–")),
             ("Status", _status_badge(call["status"]))]
    body = _facts(facts)
    chips = ""
    if meta.get("anliegen"):
        chips += _badge(meta["anliegen"], "#e0e7ff", "#3730a3") + " "
    if meta.get("dringlichkeit"):
        bg, fg = URGENCY_STYLE.get(meta["dringlichkeit"], ("#f3f4f6", "#374151"))
        chips += _badge("Dringlichkeit: " + meta["dringlichkeit"], bg, fg) + " "
    mood = notes.get("Stimmung") or notes.get("Stimmung (Wortlaut)")
    if mood:
        chips += _badge("Stimmung: " + str(mood).split(" – ")[0], "#fef3c7", "#92400e")
    if chips:
        body += f"<div style=\"margin-top:16px\">{chips}</div>"
    if meta.get("zusammenfassung") and not (with_points and (meta["detail"] or {}).get("fazit")):
        body += _callout("Zusammenfassung", meta["zusammenfassung"])
    if with_points and (meta["detail"] or {}).get("fazit"):
        body += _callout("Fazit", meta["detail"]["fazit"], "#eff6ff", "#3b82f6")
    if meta.get("naechster_schritt"):
        body += _callout("Nächster Schritt", meta["naechster_schritt"], "#fffbeb", "#f59e0b")
    if with_points:
        body += _detail_html(meta["detail"])
    for k, v in notes.items():
        if k.startswith("Nachricht"):
            body += _callout(k, v, "#fef2f2", "#ef4444")
        elif k not in ("Stimmung", "Stimmung (Wortlaut)", "Stimme (Eindruck)"):
            body += _callout(k, v)
    if trans:
        col = {"caller": "#2563eb", "remote": "#d97706", "agent": "#059669", "local": "#7c3aed", "mix": "#6b7280"}
        rows = ""
        shown = 0
        for role, name, msg in _transcript_rows(call["id"]):
            shown += len(msg)
            if shown > 60000:
                rows += "<tr><td></td><td style=\"padding:6px 0;font-size:13px;color:#6b7280\">(gekürzt - vollständig im Webinterface)</td></tr>"
                break
            rows += (f"<tr><td style=\"padding:6px 12px 6px 0;vertical-align:top;font-size:12px;font-weight:700;color:{col.get(role, '#6b7280')};width:1%\">{_e(name)}</td>"
                     f"<td style=\"padding:6px 0;font-size:13px;line-height:19px;color:#374151;border-bottom:1px solid #f3f4f6\">{_e(msg)}</td></tr>")
        body += (f"<div style=\"margin-top:20px;font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#6b7280\">Transkript</div>"
                 f"<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"margin-top:6px\">{rows}</table>")
    if link:
        body += _button(link, "Im Webinterface öffnen")
    subtitle = f"{who} → {call['called']}"
    page = _layout("Anrufbenachrichtigung", "Wichtiger Anruf" if imp else ("Kampagnenanruf" if out else "Neuer Anruf"), subtitle, body,
                   accent="#ef4444" if imp else "#2563eb")
    return subject, text, page


async def ensure_summary(call_id):
    """Fehlt die Zusammenfassung (Zusatzinfos beim Agenten aus), wird sie fuer die Mail kurz nachgereicht (Textmodell, Bruchteile eines Cents)."""
    row = db.q("SELECT meta, direction FROM calls WHERE id=?", (call_id,))
    if not row or "zusammenfassung" in (row[0]["meta"] or "") or row[0]["direction"] != "in":
        return
    try:
        from . import analyze
        await asyncio.wait_for(analyze.run(call_id), 40)
    except Exception as e:
        log.warning("Zusammenfassung fuer Anruf %s nicht erstellt: %s", call_id, type(e).__name__)


def max_attach_mb():
    """Hoechste Groesse der Aufnahme als Mailanhang in MB (Einstellung mail_max_mb, Standard 10). Mailserver begrenzen die ganze Mail
    (Gmail/Outlook meist 20-25 MB inklusive Kodierung, die ca. ein Drittel dazurechnet)."""
    try:
        return max(1, min(int(db.get_meta("mail_max_mb", "10") or 10), 50))
    except ValueError:
        return 10


def audio_attachment(call_id, max_mb=None):
    """Aufnahme eines Anrufs als Mailanhang: [(Dateiname, Bytes, maintype, subtype)] oder [] (zu gross, fehlt)."""
    max_mb = max_mb or max_attach_mb()
    row = db.q("SELECT recording FROM calls WHERE id=?", (call_id,))
    rec = row[0]["recording"] if row else ""
    if not rec or not os.path.exists(rec) or os.path.getsize(rec) > max_mb * 1024 * 1024:
        return []
    mp3f = rec.lower().endswith(".mp3")
    with open(rec, "rb") as f:
        return [(f"anruf-{call_id}.{'mp3' if mp3f else 'wav'}", f.read(), "audio", "mpeg" if mp3f else "wav")]


def _rec_size_mb(call_id):
    row = db.q("SELECT recording FROM calls WHERE id=?", (call_id,))
    rec = row[0]["recording"] if row else ""
    return os.path.getsize(rec) / 1048576 if rec and os.path.exists(rec) else 0.0


def _note_no_audio(text, body, note):
    """Hinweis, dass die Aufnahme nicht angehaengt ist, an Text und HTML der Mail haengen."""
    html = f'<p style="margin:16px 0 0;padding:10px 12px;background:#fef3c7;border:1px solid #f59e0b;border-radius:6px;font-size:13px;color:#92400e">{_e(note)}</p>'
    if body:
        body = body.replace("</body>", html + "</body>", 1) if "</body>" in body else body + html
    return text + "\n\n" + note, body


async def ensure_detail(call_id):
    """Empfaenger mit 'alle besprochenen Punkte': fehlt die ausfuehrliche Zusammenfassung, wird sie kurz nachgereicht."""
    try:
        from . import analyze
        await asyncio.wait_for(analyze.run_detail(call_id), 60)
    except Exception as e:
        log.warning("Ausfuehrliche Zusammenfassung fuer Anruf %s nicht erstellt: %s", call_id, type(e).__name__)


async def annotate_answered(call_id):
    """Bei eingehenden Anrufen in den Notizen festhalten, an welchem Telefon abgenommen wurde (Anrufliste der Fritzbox)."""
    try:
        if not (os.environ.get("FRITZ_TR064_USER") and os.environ.get("FRITZ_TR064_PASSWORD")):
            return
        call, meta, notes = _load(call_id)
        if not call or call["direction"] != "in" or notes.get("Angenommen von"):
            return
        from . import fritz
        for wait in (3, 6):                         # die Anrufliste der Fritzbox ist erst kurz nach dem Auflegen aktuell
            await asyncio.sleep(wait)
            dev = await asyncio.to_thread(fritz.answered_by, call["caller"], call["started"], call["ended"] or time.time())
            if dev:
                notes["Angenommen von"] = dev
                db.x("UPDATE calls SET notes=? WHERE id=?", (json.dumps(notes, ensure_ascii=False), call_id))
                db.add_segment(call_id, "system", f"Angenommen am Telefon: {dev}")
                return
    except Exception:
        log.exception("Anrufliste der Fritzbox: Abgenommen-von nicht ermittelt")


async def call_finished(call_id):
    """Nach einem Anruf: passende Empfaenger benachrichtigen (einmal je Anruf)."""
    try:
        if not db.x_count("UPDATE calls SET notified=1 WHERE id=? AND notified=0", (call_id,)):
            return
        await annotate_answered(call_id)
        if not configured():
            return
        call, meta, notes = _load(call_id)
        if not call:
            return
        if call["direction"] == "in":
            rcpts = [r for r in recipients(call["called"]) if r["on_call"] in ("all", "important")]
        elif call["direction"] == "out":        # ausgehende Anrufe nur ueber Plugins (z. B. Kampagnen mit Mailbenachrichtigung)
            rcpts = []
            for hook in OUT_HOOKS:
                try:
                    rcpts += hook(call, meta) or []
                except Exception:
                    log.exception("Mail-Hook")
        elif call["direction"] == "rec":        # Gespraech mit Mitschrift: nur an die beim Start gewaehlten Empfaenger
            toks = [x for x in (call.get("rec_mail") or "").split(",") if x]
            ids = {x for x in toks if "@" not in x}
            rcpts = [dict(r, on_call="all") for r in db.q("SELECT * FROM mail_recipients WHERE enabled=1") if str(r["id"]) in ids or r.get("on_rec")]
            keep_audio = call.get("rec_keep") != 0
            rcpts = [dict(r, inc_transcript=1) if str(r["id"]) in ids else r for r in rcpts]     # in der Regel/beim Start gewaehlt: immer mit Transkript
            rcpts += [{"id": 0, "email": x, "on_call": "all", "inc_transcript": 1, "inc_details": 1, "inc_audio": int(keep_audio)} for x in toks if "@" in x]
            if call.get("rec_stopped"):
                rcpts = []                       # Aufzeichnung wurde verworfen
        else:
            return
        if not rcpts:
            return
        if not meta.get("zusammenfassung") and call["direction"] != "rec":
            await ensure_summary(call_id)
            call, meta, notes = _load(call_id)
        imp = _important(call, meta, notes)
        for r in rcpts:
            if call["direction"] == "in" and not status_ok(r, dict(call, status=db.end_status(call["id"]) if call.get("status") == "transkription fehlgeschlagen" else call.get("status"))):
                continue                       # dieses Ergebnis (z. B. 'angenommen') hat der Empfaenger nicht gewaehlt; gilt nur fuer eingehende Anrufe
            if r["on_call"] == "important" and not imp:
                continue
            now = time.time()
            sk = r["id"] or r["email"]
            SENT[sk] = [t for t in SENT.get(sk, []) if now - t < 3600]
            if len(SENT[sk]) >= MAX_PER_HOUR:
                log.warning("Mail-Flutschutz: %s hat %d Mails in der letzten Stunde", _m_email(r["email"]), MAX_PER_HOUR)
                continue
            SENT[sk].append(now)
            pts = bool(r.get("inc_points"))
            if pts and not (meta.get("detail") or {}).get("punkte"):
                await ensure_detail(call_id)
                call, meta, notes = _load(call_id)
            subject, text, body = build_call_mail(call, meta, notes, "full" if r.get("inc_transcript") else "short", bool(r.get("inc_details", 1)), pts)
            att = audio_attachment(call_id) if r.get("inc_audio") else []
            subj = ("[WICHTIG] " if imp else "") + subject
            if r.get("inc_audio") and not att:
                size = _rec_size_mb(call_id)
                if size:       # Aufnahme vorhanden, aber ueber der Grenze: Mail ohne Audio, mit Hinweis
                    text, body = _note_no_audio(text, body, f"Die Aufnahme ({size:.1f} MB) ist größer als die erlaubte Mailgröße ({max_attach_mb()} MB) und wurde nicht angehängt. Du kannst sie in der Oberfläche abspielen.")
            try:
                try:
                    await asyncio.to_thread(send_mail, r["email"], subj, text, body, att)
                except Exception as e:
                    if not att:
                        raise
                    # Versand mit Anhang gescheitert (z. B. Mail zu gross fuer den Server): einmal ohne Audio versuchen
                    log.warning("Mail mit Aufnahme an %s fehlgeschlagen (%s) - neuer Versuch ohne Audio", _m_email(r["email"]), _m_text(e))
                    text2, body2 = _note_no_audio(text, body, "Die Aufnahme konnte nicht angehängt werden (Mail zu groß für den Server). Du kannst sie in der Oberfläche abspielen.")
                    await asyncio.to_thread(send_mail, r["email"], subj, text2, body2, [])
            except Exception as e:
                log.warning("Benachrichtigung an %s fehlgeschlagen: %s", _m_email(r["email"]), _m_text(e))
    except Exception:
        log.exception("Benachrichtigung")


# --- Tagesbericht ---
def build_report(r, since, until, title="Tagesbericht"):
    nums = [db._digits(x) for x in r["numbers"].split(",") if x.strip()]
    sql, args = "SELECT * FROM calls WHERE direction='in' AND started>=? AND started<?", [since, until]
    if nums:
        sql += f" AND digits(called) IN ({','.join('?' * len(nums))})"
        args += nums
    calls = db.q(sql + " ORDER BY started", args)
    stat = {}
    for c in calls:
        stat[c["status"]] = stat.get(c["status"], 0) + 1
    day = datetime.fromtimestamp(since).strftime("%d.%m.%Y")
    scope = ", ".join(r["numbers"].split(",")) if nums else "alle Rufnummern"
    subject = f"{title} {day}: {len(calls)} Anrufe"
    items = []
    for c in calls:
        call, meta, notes = _load(c["id"])
        topic = meta.get("zusammenfassung") or next((v for k, v in notes.items() if k.startswith("Nachricht")), "") or ""
        items.append((c, topic, _important(c, meta, notes)))
    head = f"{title} {day} ({scope})\nAnrufe gesamt: {len(calls)}" + ("  (" + ", ".join(f"{n_} {s}" for s, n_ in sorted(stat.items())) + ")" if stat else "")
    text = head + "\n\n" + ("\n".join(
        f"{datetime.fromtimestamp(c['started']).strftime('%H:%M')}  {_who(c)} -> {c['called']}  [{c['status']}]"
        f"{' !' if imp else ''}{('  ' + t[:160]) if t else ''}" for c, t, imp in items) or "Keine Anrufe.")
    # Kennzahlen
    def tile(value, label, bg="#f8fafc", fg="#111827"):
        return (f"<td align=\"center\" style=\"background:{bg};border-radius:10px;padding:14px 6px;width:25%\">"
                f"<div style=\"font-size:26px;line-height:30px;font-weight:700;color:{fg}\">{value}</div>"
                f"<div style=\"margin-top:2px;font-size:12px;color:#6b7280\">{_e(label)}</div></td>")
    n_imp = sum(1 for _, _, i_ in items if i_)
    miss = stat.get("unbeantwortet", 0) + stat.get("abgelehnt", 0) + stat.get("nicht erreicht", 0)
    tiles = ("<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"6\" style=\"margin:4px -6px 0\"><tr>"
             + tile(len(calls), "Anrufe") + tile(stat.get("beendet", 0), "KI", "#e0f2fe", "#075985")
             + tile(stat.get("durchgestellt", 0) + stat.get("angenommen", 0), "durchgestellt", "#ede9fe", "#5b21b6")
             + tile(n_imp or miss, "wichtig" if n_imp else "verpasst", "#fee2e2" if (n_imp or miss) else "#f8fafc", "#991b1b" if (n_imp or miss) else "#111827")
             + "</tr></table>")
    SHORT = {"beendet": "KI", "unbeantwortet": "Verpasst", "durchgestellt": "Durchgestellt", "angenommen": "Angenommen", "nicht erreicht": "Nicht erreicht",
             "abgelehnt": "Abgelehnt", "abgebrochen": "Abgebrochen", "aktiv": "Läuft"}
    if items:
        rows = ""
        for c, t, imp in items:
            who = _who(c)
            nm, _, nr = who.partition(" (")
            _, bg, fg = STATUS_STYLE.get(c["status"], (c["status"], "#f3f4f6", "#374151"))
            line = "" if t else "border-bottom:1px solid #c4cad3;"
            tint = "background:#fff7ed;" if imp else ""
            sub = " &middot; ".join(x for x in (_e(nr.rstrip(")")), "an " + _e(c["called"])) if x)
            rows += (f"<tr style=\"{tint}\"><td style=\"padding:10px 10px 2px 8px;{line}font-size:13px;color:#6b7280;white-space:nowrap;vertical-align:top;width:1%\">"
                     f"{datetime.fromtimestamp(c['started']).strftime('%H:%M')}</td>"
                     f"<td style=\"padding:10px 10px 2px 0;{line}font-size:14px;vertical-align:top\"><b>{_e(nm)}</b>"
                     f"<span style=\"color:#9ca3af;font-size:12px\"> &nbsp;{sub}</span></td>"
                     f"<td align=\"right\" style=\"padding:10px 8px 2px 0;{line}vertical-align:top;white-space:nowrap;width:1%\">"
                     f"{_badge(SHORT.get(c['status'], c['status']), bg, fg)}{(' ' + _badge('wichtig', '#fee2e2', '#991b1b')) if imp else ''}</td></tr>")
            if t:
                rows += (f"<tr style=\"{tint}\"><td colspan=\"3\" style=\"padding:2px 8px 12px 8px;border-bottom:1px solid #c4cad3;font-size:13px;line-height:20px;color:#374151\">"
                         f"{_e(t[:300])}</td></tr>")
        table = (f"<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"margin-top:22px;border-top:2px solid #e5e7eb\">{rows}</table>")
    else:
        table = ("<div style=\"margin-top:22px;padding:28px;text-align:center;background:#f8fafc;border-radius:10px;color:#6b7280\">"
                 "Heute gab es keine Anrufe.</div>")
    c_ = config()
    link = f" &middot; <a href=\"{_e(c_['base_url'])}\" style=\"color:#6b7280\">Webinterface</a>" if c_["base_url"] else ""
    page = _layout("Tagesbericht", f"Tagesbericht {day}", f"{len(calls)} Anrufe auf {scope}", tiles + table, "#2563eb", link)
    return subject, text, page


async def send_report(r, since=None, until=None):
    now = datetime.now()
    since = since if since is not None else datetime(now.year, now.month, now.day).timestamp()
    until = until if until is not None else time.time() + 1
    nums = [db._digits(x) for x in r["numbers"].split(",") if x.strip()]
    sql, args = "SELECT id FROM calls WHERE direction='in' AND started>=? AND started<? AND meta NOT LIKE '%zusammenfassung%'", [since, until]
    if nums:
        sql += f" AND digits(called) IN ({','.join('?' * len(nums))})"
        args += nums
    for c in db.q(sql + " ORDER BY started DESC LIMIT 25", args):     # fehlende Zusammenfassungen nachreichen
        await ensure_summary(c["id"])
    subject, text, body = build_report(r, since, until)
    await asyncio.to_thread(send_mail, r["email"], subject, text, body)


async def scheduler():
    """Jede Minute pruefen, ob der Tagesbericht faellig ist (einmal pro Tag und Empfaenger)."""
    while True:
        await asyncio.sleep(60)
        try:
            if not configured():
                continue
            c = config()
            now = datetime.now()
            if now.strftime("%H:%M") < c["report_time"]:
                continue
            today = now.strftime("%Y-%m-%d")
            for r in db.q("SELECT * FROM mail_recipients WHERE enabled=1 AND daily=1"):
                if db.get_meta(f"report_last:{r['id']}", "") == today:
                    continue
                db.set_meta(f"report_last:{r['id']}", today)        # zuerst merken: kein Doppelversand bei Fehlern/Neustart
                try:
                    await send_report(r)
                except Exception as e:
                    log.warning("Tagesbericht an %s fehlgeschlagen: %s", _m_email(r["email"]), _m_text(e))
        except Exception:
            log.exception("Berichts-Scheduler")
