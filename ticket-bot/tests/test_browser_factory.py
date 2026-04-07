import builtins
import sys
import types

import pytest

from ticket_bot.browser.factory import create_engine
from ticket_bot.browser.playwright_engine import PlaywrightEngine


def test_create_nodriver_with_stub(monkeypatch):
    fake_module = types.ModuleType("ticket_bot.browser.nodriver_engine")

    class FakeNodriverEngine:
        pass

    fake_module.NodriverEngine = FakeNodriverEngine
    monkeypatch.setitem(sys.modules, "ticket_bot.browser.nodriver_engine", fake_module)

    engine = create_engine("nodriver")

    assert isinstance(engine, FakeNodriverEngine)


def test_create_nodriver_falls_back_to_playwright(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "ticket_bot.browser.nodriver_engine":
            raise SyntaxError("broken nodriver")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.delitem(sys.modules, "ticket_bot.browser.nodriver_engine", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    engine = create_engine("nodriver")

    assert isinstance(engine, PlaywrightEngine)


def test_create_playwright():
    engine = create_engine("playwright")
    assert isinstance(engine, PlaywrightEngine)


def test_create_case_insensitive():
    assert isinstance(create_engine("PLAYWRIGHT"), PlaywrightEngine)


def test_create_unknown():
    with pytest.raises(ValueError, match="Unsupported browser engine"):
        create_engine("selenium")
