"""Weiterleiten durch die KI ohne und mit Rueckfrage (accept, decline -> KI nimmt wieder an, voicemail, keine Antwort),
Rueckfall nach fehlgeschlagenem Durchstellen, Klingeln -> Rueckfall zur KI. Verhalten festgeschrieben."""
import asyncio
import uuid

import pytest

from app import main
from tests.conftest import run

MSN = "0301111"
CALLER = "015112345678"
SCREEN = "wartender Anrufer"          # Teilstring im Prompt der Rueckfrage-KI


def setup_staff(db, ask_before=False):
    sid = db.save_staff({"name": "Petra Muster", "number": "**610", "msn": "", "status": "office"})
    if ask_before:
        db.x("UPDATE agents SET ask_before=1")
    return sid


async def start_ai(b, u):
    assert await b.internal("call", uuid=u, frm=CALLER, to="s", hdr="<sip:0309999@fritz.box>", chan="PJSIP/fritz-1") == "ok"
    sock = await b.audiosocket(u)
    await b.wait_ended(sock)
    return sock


CONNECT = [("say", "Guten Tag."), ("hear", "Ich moechte Petra sprechen."),
           ("tool", "connect_employee", {"name": "Petra"}), ("say", "Ich verbinde Sie.")]
CONNECT_ASK = [("say", "Guten Tag."), ("hear", "Ich moechte Petra sprechen."),
               ("tool", "connect_employee", {"name": "Petra"}),                   # ohne Anliegen: KI soll erst fragen
               ("hear", "Mein Name ist Huber, es geht um die Rechnung."),
               ("tool", "connect_employee", {"name": "Petra", "anliegen": "Herr Huber wegen der Rechnung"}),
               ("say", "Ich frage kurz nach, ob Petra Zeit hat.")]


def test_connect_without_screen_then_busy_returns_to_ai(bridge):
    u = str(uuid.uuid4())
    bridge.genai.script(CONNECT)
    bridge.genai.script([("say", "Petra ist leider gerade nicht erreichbar. Moechten Sie eine Nachricht hinterlassen?"),
                         ("hear", "Nein danke."), ("tool", "end_call")])

    async def scenario(b):
        setup_staff(b.db)
        await start_ai(b, u)
        assert b.genai.sessions[0].tool_results[0][1]["ok"] is True
        assert await b.internal("after", uuid=u) == "dial:**610:default"          # mit Wartemusik-Klasse beim Verbinden
        row = b.call_row(u)
        assert row["status"] == "durchgestellt"
        assert "Weiterleitung an **610" in b.segments(row["id"], "system")
        # Dialplan: niemand nimmt ab -> /internal/busy -> die KI nimmt den Anrufer wieder an
        assert await b.internal("busy", uuid=u, status="NOANSWER") == "ok"
        assert main.CALLS.field(u, "pending")["retry"] is True
        assert main.CALLS.field(u, "pending")["for_staff"][0]["note"] == "nimmt gerade nicht ab"
        sock = await b.audiosocket(u)
        await b.wait_ended(sock)
        s2 = b.genai.sessions[1]
        assert "nicht erreichbar" in s2.texts[0]                                  # Systemhinweis statt Begruessung
        assert "Petra Muster" in s2.texts[0] and "sage nicht 'die Person'" in s2.texts[0]      # gewuenschte Person beim Namen nennen
        assert "nicht von dir aus" in s2.prompt and "Nenne keine Namen" in s2.prompt              # sonst keine Namen verraten
        assert "Hinweis-Ansage" not in b.segments(row["id"], "system")[1:]       # keine zweite Hinweis-Ansage... (nur einmal)
        assert b.segments(row["id"], "system").count("Hinweis-Ansage") == 1
        assert await b.internal("after", uuid=u) == "hangup"
        assert b.call_row(u)["status"] == "beendet"
    run(bridge, scenario)


def test_busy_without_transfer_is_no_agent(bridge):
    async def scenario(b):
        assert await b.internal("busy", uuid=str(uuid.uuid4()), status="BUSY") == "no-agent"
    run(bridge, scenario)


async def screen_flow(b, u, screen_steps, hang_screen=False):
    """KI fragt nach -> sdial; die angerufene Person nimmt ab ([screen]) -> Rueckfrage-KI. Gibt das Ergebnis von screen-result zurueck."""
    await start_ai(b, u)
    after = await b.internal("after", uuid=u)
    assert after == "sdial:**610:25:default:"
    s = str(uuid.uuid4())
    assert await b.internal("screen-start", uuid=u, suuid=s, chan="PJSIP/fritz-2") == "ok"
    ss = await b.audiosocket(s)
    if hang_screen:
        await asyncio.sleep(0.5)
        await ss.hangup()
    await b.wait_ended(ss)
    return await b.internal("screen-result", uuid=u)


def test_screen_accept(bridge):
    u = str(uuid.uuid4())
    bridge.genai.script(CONNECT_ASK)
    bridge.genai.script([("say", "Hallo Petra, Herr Huber wartet wegen der Rechnung. Hast du kurz Zeit?"),
                         ("hear", "Ja, gerne."), ("tool", "accept")], match=SCREEN)

    async def scenario(b):
        setup_staff(b.db, ask_before=True)
        assert await screen_flow(b, u, None) == "accept"
        first = b.genai.sessions[0].tool_results
        assert "fehler" in first[0][1] and "Name und Anliegen" in first[0][1]["fehler"]
        prompt = b.genai.sessions[1].prompt
        assert "<<<ZITAT ANFANG>>> Herr Huber wegen der Rechnung <<<ZITAT ENDE>>>" in prompt
        cid = b.call_row(u)["id"]
        sys_ = b.segments(cid, "system")
        assert "Rückfrage bei Petra Muster vor dem Verbinden" in sys_
        assert "Person nimmt den Anruf an" in sys_
        assert b.segments(cid, "local") == ["Ja, gerne."]              # Antwort der Person steht als "Ich/Benutzer"
        assert main.CALLS.field(u, "defer_post")                                    # Auswertung/Mail erst nach dem ganzen Gespraech
    run(bridge, scenario)


def test_mail_after_screen_connected(bridge):
    """Nach angenommener Rueckfrage und Gespraech: beim Auflegen (rec-done) Auswertung und Mail fuer das ganze Gespraech."""
    u = str(uuid.uuid4())
    bridge.genai.script(CONNECT_ASK)
    bridge.genai.script([("say", "Hallo Petra, Zeit?"), ("hear", "Ja."), ("tool", "accept")], match=SCREEN)

    async def scenario(b):
        setup_staff(b.db, ask_before=True)
        assert await screen_flow(b, u, None) == "accept"
        cid = b.call_row(u)["id"]
        assert cid not in b.finished                                   # noch nicht: das Gespraech laeuft
        assert await b.internal("rec-done", uuid=u) == "ok"
        await b.settle(0.5)
        assert cid in b.finished
    run(bridge, scenario)


def test_screen_accept_requires_yes(bridge):
    u = str(uuid.uuid4())
    bridge.genai.script(CONNECT_ASK)
    bridge.genai.script([("say", "Hallo Petra, hast du Zeit?"), ("hear", "Hm, wer ist das?"), ("tool", "accept"),
                         ("say", "Soll ich durchstellen?"), ("hear", "Nein, nicht jetzt."), ("tool", "decline")], match=SCREEN)

    async def scenario(b):
        setup_staff(b.db, ask_before=True)
        assert await screen_flow(b, u, None) == "decline"
        res = b.genai.sessions[1].tool_results
        assert "fehler" in res[0][1]                                   # accept ohne Ja wird serverseitig abgelehnt
        assert "accept() abgelehnt: kein Ja gehört" in b.segments(b.call_row(u)["id"], "system")
    run(bridge, scenario)


def test_screen_decline_ai_takes_back_and_never_reconnects(bridge):
    u = str(uuid.uuid4())
    bridge.genai.script(CONNECT_ASK)
    bridge.genai.script([("say", "Hallo Petra, Herr Huber wartet. Zeit?"), ("hear", "Nein, gerade nicht."), ("tool", "decline"),
                         ("say", "In Ordnung, ich richte es aus.")], match=SCREEN)
    # zweiter KI-Abschnitt: der Anrufer will es noch einmal versuchen, dann legt die KI auf
    bridge.genai.script([("say", "Petra ist gerade nicht erreichbar."), ("hear", "Bitte versuchen Sie es noch einmal."),
                         ("tool", "connect_employee", {"name": "Petra", "anliegen": "nochmal"}), ("say", "Das geht leider nicht."),
                         ("tool", "end_call")])

    async def scenario(b):
        setup_staff(b.db, ask_before=True)
        assert await screen_flow(b, u, None) == "decline"
        # Dialplan: Dial endet ohne ANSWEREDTIME -> /internal/busy (DIALSTATUS=ANSWER)
        assert await b.internal("busy", uuid=u, status="ANSWER") == "ok"
        assert main.CALLS.field(u, "pending")["screen_tried"] == ["610"]
        assert main.CALLS.field(u, "pending")["for_staff"][0]["note"] == "ist gerade nicht erreichbar"
        sock = await b.audiosocket(u)
        await b.wait_ended(sock)
        again = b.genai.sessions[2].tool_results[0][1]
        assert "schon versucht" in again["fehler"]                      # dieselbe Person nicht noch einmal fragen
        # Fehlerklasse "altes AFTER": nach abgelehnter Rueckfrage darf der Dialplan NICHT erneut verbinden
        assert await b.internal("after", uuid=u) == "hangup"
        assert b.call_row(u)["status"] == "beendet"
        assert not main.CALLS.field(u, "defer_post")
    run(bridge, scenario)


def test_screen_voicemail(bridge):
    u = str(uuid.uuid4())
    bridge.genai.script(CONNECT_ASK)
    bridge.genai.script([("hear", "Der Teilnehmer ist nicht erreichbar, hinterlassen Sie eine Nachricht nach dem Signal."),
                         ("tool", "voicemail")], match=SCREEN)

    async def scenario(b):
        setup_staff(b.db, ask_before=True)
        assert await screen_flow(b, u, None) == "voicemail"
        assert "Mailbox/Anrufbeantworter erkannt" in b.segments(b.call_row(u)["id"], "system")
        assert await b.internal("busy", uuid=u, status="ANSWER") == "ok"
        assert main.CALLS.field(u, "pending")["for_staff"][0]["note"] == "ist gerade nicht erreichbar"
    run(bridge, scenario)


def test_screen_no_answer(bridge):
    u = str(uuid.uuid4())
    bridge.genai.script(CONNECT_ASK)
    bridge.genai.script([("say", "Hallo, hast du Zeit?")], match=SCREEN)

    async def scenario(b):
        setup_staff(b.db, ask_before=True)
        assert await screen_flow(b, u, None, hang_screen=True) == "none"
        sys_ = b.segments(b.call_row(u)["id"], "system")
        assert "Rückfrage: die Person hat aufgelegt" in sys_
        assert "Rückfrage: Petra Muster hat nicht geantwortet" in sys_
        assert await b.internal("busy", uuid=u, status="ANSWER") == "ok"
        assert main.CALLS.field(u, "pending")["for_staff"][0]["note"] == "nimmt gerade nicht ab"
    run(bridge, scenario)


def test_screen_start_without_screen_is_skip(bridge):
    async def scenario(b):
        assert await b.internal("screen-start", uuid=str(uuid.uuid4()), suuid=str(uuid.uuid4())) == "skip"
        assert await b.internal("screen-result", uuid=str(uuid.uuid4())) == "none"
    run(bridge, scenario)


def test_ring_no_answer_fallback_to_ai(bridge):
    u = str(uuid.uuid4())
    bridge.genai.script([("say", "Guten Tag, hier ist der Anrufbeantworter."), ("tool", "end_call")])

    async def scenario(b):
        b.db.x("UPDATE call_rules SET ring_s=20")
        b.db.save_staff({"name": "Petra Muster", "number": "**610", "msn": MSN, "status": "office"})
        assert await b.internal("call", uuid=u, frm=CALLER, to="s", hdr=f"<sip:{MSN}@fritz.box>", chan="PJSIP/fritz-1") == "fdial:20:**610"
        assert await b.internal("fallback", uuid=u) == "ok"                       # niemand nahm ab
        assert main.CALLS.field(u, "pending")["for_staff"][0]["note"] == "hat den Anruf nicht angenommen"
        assert await b.internal("fallback", uuid=u) == "no-agent"                 # nur einmal
        row = b.call_row(u)
        assert row["status"] == "aktiv" and row["agent_name"] == "Assistent"
        assert "Nicht abgenommen: der Anrufbeantworter (KI) übernimmt" in b.segments(row["id"], "system")
        sock = await b.audiosocket(u)
        await b.wait_ended(sock)
        assert b.call_row(u)["status"] == "beendet"
    run(bridge, scenario)


def test_ring_answered_and_hangup_unanswered(bridge):
    async def scenario(b):
        b.db.x("UPDATE call_rules SET ring_s=20")
        b.db.save_staff({"name": "Petra Muster", "number": "**610", "msn": MSN, "status": "office"})
        u = str(uuid.uuid4())
        await b.internal("call", uuid=u, frm=CALLER, to="s", hdr=f"<sip:{MSN}@fritz.box>", chan="PJSIP/fritz-1")
        assert await b.internal("answered", uuid=u) == "ok"                       # jemand hat abgenommen
        assert not main.CALLS.field(u, "ringing") and main.CALLS.field(u, "human")["rec"] is False
        assert await b.internal("rec-done", uuid=u) == "ok"
        assert not main.CALLS.field(u, "human")
        # KI sollte nach Wartezeit annehmen, aber der Anrufer legt vorher auf
        b.db.x("UPDATE call_rules SET ring_s=0")
        b.db.set_meta("ring_all", "")
        u2 = str(uuid.uuid4())
        b.db.x("UPDATE call_rules SET ring_s=15")
        assert await b.internal("call", uuid=u2, frm=CALLER, to="s", hdr="<sip:0309999@fritz.box>") == "wait:15"
        assert await b.internal("rec-done", uuid=u2) == "ok"
        row = b.call_row(u2)
        assert row["status"] == "unbeantwortet"
        assert "Nicht angenommen: der Anrufer hat vorher aufgelegt oder ein anderes Gerät hat abgenommen" in b.segments(row["id"], "system")
        assert not main.CALLS.field(u2, "pending")
    run(bridge, scenario)


def test_manual_ring_redirect(bridge):
    async def scenario(b):
        b.db.x("UPDATE call_rules SET ring_s=20")
        b.db.save_staff({"name": "Petra Muster", "number": "**610", "msn": MSN, "status": "office"})
        u = str(uuid.uuid4())
        await b.internal("call", uuid=u, frm=CALLER, to="s", hdr=f"<sip:{MSN}@fritz.box>", chan="PJSIP/fritz-7")
        cid = b.call_row(u)["id"]
        from app import users
        users.ensure_admin()
        main.app.dependency_overrides[main.user_auth] = lambda: main.PSEUDO_ADMIN
        try:
            r = await b.http.post(f"/api/calls/{cid}/ring", json={"action": "route", "to": "**620"})
            assert r.status_code == 200, r.text
        finally:
            main.app.dependency_overrides.clear()
        assert b.ami.calls[-1] == ("redirect", "PJSIP/fritz-7", "s", "direct", {"RES": "dial:**620"})
        assert not main.CALLS.field(u, "ringing") and not main.CALLS.field(u, "fallback")
        b.db.save_staff({"name": "Peter Pause", "number": "**620", "msn": "", "status": "office"})
        assert await b.internal("busy", uuid=u, status="BUSY") == "ok"           # besetzt -> zurueck zur KI
        fs = main.CALLS.field(u, "pending")["for_staff"]
        assert fs[0]["name"] == "der gewuenschte Gespraechspartner"              # manuell umgeleitet: kein Name, nach dem keiner fragte
    run(bridge, scenario)


def test_asked_name_is_empty_when_person_unknown():
    from app import call as call_mod
    mk = lambda fs: type("C", (), {"meta": {"for_staff": fs}})()
    assert call_mod.Call._asked_name(mk([{"name": "Petra Muster"}])) == "Petra Muster"
    assert call_mod.Call._asked_name(mk({"name": "Petra Muster"})) == "Petra Muster"
    assert call_mod.Call._asked_name(mk([{"name": "der gewuenschte Gespraechspartner"}])) == ""
    assert call_mod.Call._asked_name(mk([])) == ""


@pytest.mark.parametrize("ask", ["Zentrale", "Clemens"])
def test_busy_names_the_connected_entry_when_numbers_are_shared(bridge, ask):
    """Zentrale mit derselben Nummer wie ein Mitarbeiter: bei keiner Antwort nennt die KI genau den Eintrag, mit dem verbunden wurde."""
    u = str(uuid.uuid4())
    bridge.genai.script([("say", "Guten Tag."), ("tool", "connect_employee", {"name": ask}), ("say", "Ich verbinde Sie.")])
    bridge.genai.script([("say", "Leider nicht erreichbar."), ("tool", "end_call")])

    async def scenario(b):
        b.db.save_staff({"name": "Clemens Muster", "number": "**610", "msn": "", "status": "office"})
        b.db.save_staff({"name": "Zentrale", "number": "**610", "msn": "", "status": "office"})
        await start_ai(b, u)
        assert await b.internal("after", uuid=u) == "dial:**610:default"
        assert await b.internal("busy", uuid=u, status="NOANSWER") == "ok"
        who = main.CALLS.field(u, "pending")["for_staff"][0]["name"]
        assert who == ("Zentrale" if ask == "Zentrale" else "Clemens Muster")
        sock = await b.audiosocket(u)
        await b.wait_ended(sock)
        if ask == "Zentrale":
            assert "Clemens" not in b.genai.sessions[1].texts[0]
    run(bridge, scenario)


def test_screen_pass_on_to_colleague(bridge):
    """Rueckfrage: Petra gibt den Anruf an die Zentrale weiter -> die KI des Anrufers verbindet dorthin (mit Rueckfrage bei der Zentrale)."""
    u = str(uuid.uuid4())
    bridge.genai.script(CONNECT_ASK)
    bridge.genai.script([("tool", "pass_on", {"name": "Zentrale"}),                       # vor einer Antwort: abgelehnt
                         ("say", "Hallo Petra, Herr Huber wartet wegen der Rechnung. Zeit?"),
                         ("hear", "Nein, gib ihn bitte an die Zentrale."), ("tool", "pass_on", {"name": "Zentrale"}),
                         ("say", "In Ordnung, ich gebe ihn weiter.")], match=SCREEN)
    bridge.genai.script([("say", "Ich verbinde Sie weiter."), ("tool", "connect_employee", {"name": "Zentrale", "anliegen": "Herr Huber wegen der Rechnung"}),
                         ("say", "Einen Moment.")])

    async def scenario(b):
        setup_staff(b.db, ask_before=True)
        b.db.save_staff({"name": "Zentrale", "number": "**620", "msn": "", "status": "office"})
        assert await screen_flow(b, u, None) == "pass"
        res = b.genai.sessions[1].tool_results
        assert "fehler" in res[0][1] and res[1][1]["ok"] is True
        assert "Moegliche Kollegen: Zentrale." in b.genai.sessions[1].prompt
        assert "Person gibt den Anruf weiter an Zentrale" in b.segments(b.call_row(u)["id"], "system")
        assert await b.internal("busy", uuid=u, status="ANSWER") == "ok"
        assert main.CALLS.field(u, "pending")["pass_to"] == "Zentrale"
        sock = await b.audiosocket(u)
        await b.wait_ended(sock)
        s3 = b.genai.sessions[2]
        assert "an Zentrale weiterzugeben" in s3.texts[0] and "Herr Huber wegen der Rechnung" in s3.texts[0]
        assert s3.tool_results[0][1]["ok"] is True
        assert await b.internal("after", uuid=u) == "sdial:**620:25:default:"            # die Zentrale wird gefragt
    run(bridge, scenario)


def test_screen_pass_on_needs_known_other_colleague(bridge):
    u = str(uuid.uuid4())
    bridge.genai.script(CONNECT_ASK)
    bridge.genai.script([("say", "Hallo Petra, Zeit?"), ("hear", "Gib ihn an Hugo."), ("tool", "pass_on", {"name": "Hugo"}),
                         ("tool", "pass_on", {"name": "Petra"}), ("say", "An wen genau?"), ("hear", "Nein, lass."), ("tool", "decline")], match=SCREEN)

    async def scenario(b):
        setup_staff(b.db, ask_before=True)
        assert await screen_flow(b, u, None) == "decline"
        res = b.genai.sessions[1].tool_results
        assert "Kein eindeutiger Kollege" in res[0][1]["fehler"] and "Kein eindeutiger Kollege" in res[1][1]["fehler"]   # nicht an sich selbst
    run(bridge, scenario)



def test_screen_pass_on_to_same_number(bridge):
    """Zentrale mit derselben Nummer wie die gefragte Person: Weitergeben wird nicht als "schon versucht" abgelehnt."""
    u = str(uuid.uuid4())
    bridge.genai.script(CONNECT_ASK)
    bridge.genai.script([("say", "Hallo Petra, Zeit?"), ("hear", "Nein, gib ihn an die Zentrale."), ("tool", "pass_on", {"name": "Zentrale"}),
                         ("say", "In Ordnung.")], match=SCREEN)
    bridge.genai.script([("say", "Ich verbinde."), ("tool", "connect_employee", {"name": "Zentrale", "anliegen": "Rechnung"}), ("say", "Moment.")])

    async def scenario(b):
        setup_staff(b.db, ask_before=True)
        b.db.save_staff({"name": "Zentrale", "number": "**610", "msn": "", "status": "office"})
        assert await screen_flow(b, u, None) == "pass"
        assert await b.internal("busy", uuid=u, status="ANSWER") == "ok"
        assert main.CALLS.field(u, "pending")["screen_tried"] == []
        sock = await b.audiosocket(u)
        await b.wait_ended(sock)
        assert b.genai.sessions[2].tool_results[0][1]["ok"] is True
    run(bridge, scenario)



def test_screen_hello_on_pickup_is_no_consent(bridge):
    """Testanruf 16.11.: die Person meldet sich mit "Ja?" und unterbricht die Begruessung - das ist keine Zustimmung, solange die Frage
    (wer wartet, Anliegen) nicht vollstaendig gestellt ist."""
    u = str(uuid.uuid4())
    bridge.genai.script(CONNECT_ASK)
    bridge.genai.script([("cut", "Hallo Ludwig, hier"), ("hear", "Ja, hallo?"), ("tool", "accept"),
                         ("say", "Hier ist der Telefonassistent. Herr Huber wartet wegen der Rechnung. Hast du kurz Zeit?"),
                         ("hear", "Ja, stell durch."), ("tool", "accept")], match=SCREEN)

    async def scenario(b):
        setup_staff(b.db, ask_before=True)
        assert await screen_flow(b, u, None) == "accept"
        res = b.genai.sessions[1].tool_results
        assert "fehler" in res[0][1] and res[1][1]["ok"] is True          # erst nach der vollstaendigen Frage
    run(bridge, scenario)



def test_no_second_announcement_when_ai_already_said_it(bridge):
    """Testanruf 16.11.: "Ich frage kurz nach, ob Ludwig Zeit hat" kam doppelt (vor dem Werkzeugaufruf und nach dem Hinweis)."""
    u = str(uuid.uuid4())
    bridge.genai.script([("say", "Guten Tag."), ("hear", "Petra bitte, Huber wegen der Rechnung."),
                         ("say", "Danke, ich frage kurz nach, ob Petra Zeit hat."),
                         ("tool", "connect_employee", {"name": "Petra", "anliegen": "Huber wegen der Rechnung"}),
                         ("say", "<no speech>{pause}")])          # so antwortete das Modell auf "Sage nichts mehr" (Testanruf 16.11.)

    async def scenario(b):
        setup_staff(b.db, ask_before=True)
        await start_ai(b, u)
        assert not any("no speech" in t for t in b.segments(b.call_row(u)["id"], "agent"))
        res = b.genai.sessions[0].tool_results[0][1]
        assert res["ok"] is True and res["hinweis"] == "Du hast es dem Anrufer schon gesagt. Sage nichts mehr."
        assert "Ansage: Anrufer wird verbunden" not in b.segments(b.call_row(u)["id"], "system")
    run(bridge, scenario)



def test_screen_answer_kept_when_declined(bridge):
    """Abgelehnte Rueckfrage ohne aufgenommenes Gespraech: die Antwort des Benutzers bleibt im Transkript."""
    u = str(uuid.uuid4())
    bridge.genai.script(CONNECT_ASK)
    bridge.genai.script([("say", "Hallo Petra, Herr Huber wartet. Hast du Zeit?"), ("hear", "Nein, gerade nicht."), ("tool", "decline"),
                         ("say", "In Ordnung, ich richte es aus.")], match=SCREEN)
    bridge.genai.script([("say", "Petra ist gerade nicht erreichbar."), ("tool", "end_call")])

    async def scenario(b):
        setup_staff(b.db, ask_before=True)
        b.db.x("UPDATE calls SET rec_transcribe=1")
        assert await screen_flow(b, u, None) == "decline"
        assert await b.internal("busy", uuid=u, status="ANSWER") == "ok"
        sock = await b.audiosocket(u)
        await b.wait_ended(sock)
        from app import rectrans
        await rectrans.transcribe(b.call_row(u)["id"], u)
        assert "Nein, gerade nicht." in b.segments(b.call_row(u)["id"], "local")
    run(bridge, scenario)
