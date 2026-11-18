"""Rufnummern des Asterisk-Telefons aendern: Kennwort mit Zeichen, die TR-064 (SetClient3) nicht annimmt -> klare Meldung statt UPnPError 803."""
from app import fritz

ALLOWED = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-._"


class FC:
    def call_action(self, service, action, **kw):
        assert (service, action) == ("X_VoIP1", "GetInfoEx")
        return {"NewX_AVM-DE_ClientPasswordAllowedChars": ALLOWED, "NewX_AVM-DE_ClientPasswordMinChars": 8,
                "NewX_AVM-DE_ClientPasswordMaxChars": 64}


def test_write_block(monkeypatch):
    monkeypatch.setenv("FRITZ_PASSWORD", "Gutes-Kennwort_1.0")
    assert fritz.write_block(FC(), "LieschenMueller") == ""
    monkeypatch.setenv("FRITZ_PASSWORD", "mit#Raute!Zeichen")
    why = fritz.write_block(FC(), "LieschenMueller")
    assert "nicht annimmt" in why and "Telefoniegeräte → LieschenMueller" in why
    monkeypatch.setenv("FRITZ_PASSWORD", "kurz")
    assert fritz.write_block(FC()) != ""
    monkeypatch.setenv("FRITZ_PASSWORD", "")
    assert "fehlt" in fritz.write_block(FC())
