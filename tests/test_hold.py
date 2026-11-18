"""Wartemusik: Lautstaerke-Skala."""


def test_moh_volume_is_db_scale_and_old_value_migrated(bridge):
    from app import hold
    b = bridge
    b.db.set_meta("moh_volume", "10")                      # Altwert (linear 10 % = -20 dB)
    assert 50 <= hold.moh_volume() <= 60                    # gleiche Lautheit in der neuen Skala
    assert abs(20 * __import__("math").log10(hold.moh_gain(hold.moh_volume())) + 20) < 0.5
    assert hold.moh_gain(100) == 1.0
    assert 20 * __import__("math").log10(hold.moh_gain(10)) < -40
    b.db.set_meta("moh_volume", "10")
    assert hold.moh_volume() == 10                          # schon migriert: kein zweites Umrechnen


def test_moh_live_db_endpoint_and_reset_on_answer(bridge):
    from tests.conftest import run

    async def scenario(b):
        want = ("action", "Setvar", {"Channel": "PJSIP/fritz-00000001", "Variable": "VOLUME(TX)", "Value": "0"})
        assert await b.internal("moh-db") == "0"
        b.db.set_meta("moh_live_db", "12")
        assert await b.internal("moh-db") == "-3.98"                # 12 dB = Teiler 3,98
        b.db.set_meta("moh_live_db", "3")
        assert await b.internal("moh-db") == "-1.41"                # kleine Werte wirken auch (nicht auf ganze Teiler gerundet)
        b.db.set_meta("moh_live_db", "12")
        await b.internal("answered", uuid="x", chan="PJSIP/fritz-00000001")
        assert want in b.ami.calls
        b.ami.calls.clear()
        b.db.set_meta("moh_live_db", "0")
        await b.internal("answered", uuid="x", chan="PJSIP/fritz-00000001")
        assert want not in b.ami.calls                       # ohne Zusatzdaempfung kein Eingriff in den Kanal
    run(bridge, scenario)
