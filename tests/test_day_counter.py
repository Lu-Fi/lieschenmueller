"""Anrufliste: der wievielte Anruf einer Nummer an diesem Tag (day_n/day_total), Nummer in jeder Schreibweise, nur eingehend."""
import datetime
import uuid

from app import main
from tests.conftest import run


def add(db, caller, when, direction="in"):
    return db.x("INSERT INTO calls(uuid,started,ended,caller,called,status,direction) VALUES(?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), when.timestamp(), when.timestamp() + 30, caller, "0301111", "beendet", direction))


def test_day_counter_in_call_list(bridge):
    async def scenario(b):
        day = datetime.datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
        a1 = add(b.db, "01701234567", day)
        other = add(b.db, "030999888", day + datetime.timedelta(minutes=1))
        a2 = add(b.db, "+491701234567", day + datetime.timedelta(minutes=2))      # gleiche Nummer mit Landesvorwahl
        out = add(b.db, "01701234567", day + datetime.timedelta(minutes=3), "out")   # ausgehend zaehlt nicht
        a3 = add(b.db, "01701234567", day + datetime.timedelta(minutes=4))
        anon = add(b.db, "", day + datetime.timedelta(minutes=5))
        add(b.db, "", day + datetime.timedelta(minutes=6))
        y = add(b.db, "01701234567", day - datetime.timedelta(days=1))            # gestern: eigener Tag
        main.app.dependency_overrides[main.user_auth] = lambda: main.PSEUDO_ADMIN
        items = {c["id"]: c for c in (await b.http.get("/api/calls", params={"size": 50})).json()["items"]}
        main.app.dependency_overrides.pop(main.user_auth, None)
        assert [(items[i]["day_n"], items[i]["day_total"]) for i in (a1, a2, a3)] == [(1, 3), (2, 3), (3, 3)]
        assert (items[other]["day_n"], items[y]["day_n"], items[out]["day_n"], items[anon]["day_n"]) == (1, 1, 0, 0)
    run(bridge, scenario)
