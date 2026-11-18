"""Beide verabschieden sich: auflegen, das Modell darf nichts mehr sagen (Testanruf 17.11.: "This conversation has already concluded.")."""
import uuid

from tests.conftest import run

CALLER = "030123456"


def _call(bridge, steps):
    u = str(uuid.uuid4())
    bridge.genai.script(steps)

    async def scenario(b):
        assert await b.internal("call", uuid=u, frm=CALLER, to="s", hdr="<sip:99613@fritz.box>", chan="PJSIP/fritz-00000001") == "ok"
        sock = await b.audiosocket(u)
        await sock.audio(10)
        await b.wait_ended(sock)
        row = b.call_row(u)
        bridge.result = (b.segments(row["id"], "agent"), b.segments(row["id"], "system"))
    run(bridge, scenario)
    return bridge.result


def test_both_said_goodbye_no_more_output(bridge):
    agent, system = _call(bridge, [
        ("say", "Ich gebe es weiter. Auf Wiederhoeren."),
        ("hear", "Ja, auf"), ("hear", "Wiederhoeren."),
        ("say", "This conversation has already concluded."),
    ])
    assert agent == ["Ich gebe es weiter. Auf Wiederhoeren."]
    assert "Beide haben sich verabschiedet: Gespraech wird beendet" in system


def test_caller_continues_after_agent_goodbye(bridge):
    agent, system = _call(bridge, [
        ("say", "Auf Wiederhoeren."),
        ("hear", "Moment, eine Frage noch."),
        ("say", "Ja, gerne?"),
        ("tool", "end_call"),
    ])
    assert agent == ["Auf Wiederhoeren.", "Ja, gerne?"]
    assert not any("verabschiedet" in x for x in system)


def test_interrupted_goodbye_then_bis_dann_in_sentence(bridge):
    """Die KI verabschiedet sich, der Anrufer hat noch eine Frage, die naechste Antwort der KI wird unterbrochen: ein spaeteres
    "bis dann" mitten im Satz ist keine Verabschiedung mehr."""
    agent, system = _call(bridge, [
        ("say", "Auf Wiederhoeren."),
        ("hear", "Ach, noch eins."),
        ("cut", "Ja, gerne, was"),
        ("hear", "Schaffen Sie das bis dann noch"),
        ("wait", 1.2),
        ("say", "Ja, das schaffen wir."),
        ("tool", "end_call"),
    ])
    assert "Ja, das schaffen wir." in agent
    assert not any("verabschiedet" in x for x in system)


def test_goodbye_then_wait_a_question(bridge):
    agent, system = _call(bridge, [
        ("say", "Gern geschehen. Auf Wiederhoeren."),
        ("hear", "Tschuess, ach warten Sie, eine Frage"),
        ("wait", 1.2),
        ("say", "Ja, bitte?"),
        ("tool", "end_call"),
    ])
    assert agent == ["Gern geschehen. Auf Wiederhoeren.", "Ja, bitte?"]
    assert not any("verabschiedet" in x for x in system)


def test_goodbye_then_wait_in_fragments(bridge):
    """Fragmente: "Tschuess," allein waere eine Verabschiedung - "ach, warten Sie" kurz danach verhindert das Auflegen."""
    agent, system = _call(bridge, [
        ("say", "Auf Wiederhoeren."),
        ("hear", "Tschuess,"),
        ("hear", "ach, warten Sie"),
        ("wait", 1.2),
        ("say", "Ja?"),
        ("tool", "end_call"),
    ])
    assert agent == ["Auf Wiederhoeren.", "Ja?"]
    assert not any("verabschiedet" in x for x in system)


def test_bye_only():
    from app.call import bye_only
    for t in ("Ja, auf Wiederhoeren.", "Danke, Ihnen auch, tschuess!", "Tschüss Frau Meier", "Okay, bis dann."):
        assert bye_only(t), t
    for t in ("Tschuess, ach warten Sie", "Auf Wiederhoeren?", "Moment, eine Frage noch", "Ja",
              "Bis dann muss ich das Formular noch ausgefuellt haben und abgeben"):
        assert not bye_only(t), t
