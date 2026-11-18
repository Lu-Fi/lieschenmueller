"""Gemini-Zugang: einfache API (Standard) oder Vertex AI in fester Region (bridge/app/gclient.py)."""
import types

import pytest

from app import gclient
from tests.conftest import run


class FakeGenai:
    """Ersatz fuer das Modul google.genai: merkt sich die Argumente von Client()."""
    def __init__(self):
        self.calls = []

    def Client(self, *a, **k):        # noqa: N802
        self.calls.append((a, k))
        return types.SimpleNamespace(kw=k)


def _vertex(b, monkeypatch, tmp_path, scope, project="lieschenmueller", location="eu", key=True):
    f = tmp_path / "vertex.json"
    if key:
        f.write_text("{}")
    monkeypatch.setattr(gclient, "KEY_FILE", str(f))
    monkeypatch.setattr(gclient, "_credentials", lambda: "CREDS")
    b.db.set_meta("vertex_scope", scope)
    b.db.set_meta("vertex_project", project)
    b.db.set_meta("vertex_location", location)


def test_default_uses_plain_api_without_arguments(bridge):
    async def scenario(b):
        g = FakeGenai()
        gclient.client(g, "text")
        gclient.client(g, "live")
        assert g.calls == [((), {}), ((), {})]
    run(bridge, scenario)


def test_scope_text_routes_only_text_to_vertex(bridge, monkeypatch, tmp_path):
    async def scenario(b):
        _vertex(b, monkeypatch, tmp_path, "text")
        g = FakeGenai()
        c = gclient.client(g, "text")
        assert c.kw == {"vertexai": True, "project": "lieschenmueller", "location": "eu", "credentials": "CREDS"}
        assert gclient.client(g, "live").kw == {}                       # Telefon-KI bleibt bei der einfachen API
    run(bridge, scenario)


def test_scope_all_routes_live_too(bridge, monkeypatch, tmp_path):
    async def scenario(b):
        _vertex(b, monkeypatch, tmp_path, "all", location="europe-west1")      # gespeicherter anderer Standort wird ignoriert
        g = FakeGenai()
        assert gclient.client(g, "live").kw["location"] == "eu"
    run(bridge, scenario)


def test_vertex_without_setup_fails_instead_of_falling_back(bridge, monkeypatch, tmp_path):
    async def scenario(b):
        _vertex(b, monkeypatch, tmp_path, "all", key=False)             # Schluesseldatei fehlt
        with pytest.raises(RuntimeError):
            gclient.client(FakeGenai(), "text")
        _vertex(b, monkeypatch, tmp_path, "all", project="")           # Projekt-ID fehlt
        with pytest.raises(RuntimeError):
            gclient.client(FakeGenai(), "live")
    run(bridge, scenario)


def test_unknown_scope_value_counts_as_off(bridge):
    async def scenario(b):
        b.db.set_meta("vertex_scope", "irgendwas")
        assert gclient.settings()["scope"] == ""
    run(bridge, scenario)


def test_project_pattern_and_fixed_eu_location():
    assert gclient.PROJECT_RE.match("lieschenmueller") and not gclient.PROJECT_RE.match("Abc")
    assert gclient.LOCATION == "eu"


def test_live_model_is_limited_to_eu_models_with_vertex():
    from app import models
    assert models.live_model("gemini-3.8-live-extended-thinking", eu_only=False) == "gemini-3.8-live-extended-thinking"
    assert models.live_model("gemini-3.8-live-extended-thinking", eu_only=True) == "gemini-3.8-live"       # gibt es in der EU nicht
    assert models.live_model("gemini-3.1-flash-live-preview", eu_only=True) == "gemini-3.8-live"
    assert models.live_model("gemini-3.8-live", eu_only=True) == "gemini-3.8-live"
    assert models.live_model("", eu_only=True) == "gemini-3.8-live"


def test_pro_preview_is_not_selectable_anywhere():
    from app import models
    assert "gemini-3.1-pro-preview" not in [i for i, _ in models.DETAIL_CHOICES]
    assert "gemini-3.1-pro-preview" not in [i for i, _, _ in models.TRANSCRIBE_CHOICES]
    assert models.transcribe_model("gemini-3.1-pro-preview")[0] == "gemini-3.1-flash-lite"


def _put(body):
    from app import main

    async def js():
        return body
    return main.put_settings(types.SimpleNamespace(json=js))


def test_settings_save_vertex_works_and_validates(bridge, monkeypatch, tmp_path):
    from fastapi import HTTPException

    async def scenario(b):
        f = tmp_path / "vertex.json"
        f.write_text("{}")
        monkeypatch.setattr(gclient, "KEY_FILE", str(f))
        await _put({"vertex_scope": "all", "vertex_project": "lieschenmueller", "vertex_location": "eu"})
        assert gclient.settings() == {"scope": "all", "project": "lieschenmueller", "location": "eu"}
        for bad in ({"vertex_location": "global"}, {"vertex_location": "us"}, {"vertex_scope": "x"}, {"vertex_project": "Gross"}):
            with pytest.raises(HTTPException) as e:
                await _put(bad)
            assert e.value.status_code == 400
        monkeypatch.setattr(gclient, "KEY_FILE", str(tmp_path / "fehlt.json"))
        with pytest.raises(HTTPException):
            await _put({"vertex_scope": "text"})                      # ohne Schluesseldatei nicht einschaltbar
    run(bridge, scenario)


def test_ready_follows_selected_access(bridge, monkeypatch, tmp_path):
    """Statusanzeige "Zugang ok": mit Vertex fuer alles zaehlt Projekt + Schluesseldatei, nicht der API-Schluessel."""
    async def scenario(b):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        _vertex(b, monkeypatch, tmp_path, "all")
        assert gclient.ready("text") and gclient.ready("live")
        b.db.set_meta("vertex_scope", "text")
        assert gclient.ready("text") and not gclient.ready("live")          # Telefon-KI braucht dann den Schluessel
        monkeypatch.setenv("GEMINI_API_KEY", "x")
        assert gclient.ready("live")
        b.db.set_meta("vertex_project", "")
        assert not gclient.ready("text")
    run(bridge, scenario)
