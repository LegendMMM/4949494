from __future__ import annotations

from ticket_bot.config import AppConfig
from ticket_bot.web import app as web_app


def test_extract_takeover_settings_supports_generic_keys():
    settings = web_app._extract_takeover_settings(
        {
            "mode": "takeover",
            "takeover_config": {
                "debug_port": "9333",
                "page_url_substring": "vscinemas.com.tw",
            },
        }
    )

    assert settings["enabled"] is True
    assert settings["mode"] == "takeover"
    assert settings["debug_port"] == 9333
    assert settings["cdp_url"] == "http://127.0.0.1:9333"
    assert settings["page_url_substring"] == "vscinemas.com.tw"


def test_launch_handoff_browser_waits_for_port(monkeypatch):
    launched = {}
    state = {"calls": 0}

    def fake_detect():
        return r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"

    def fake_port_open(port):
        state["calls"] += 1
        return state["calls"] >= 3

    class FakePopen:
        def __init__(self, command, stdout=None, stderr=None):
            launched["command"] = command

    monkeypatch.setattr(web_app, "_detect_chrome_executable", fake_detect)
    monkeypatch.setattr(web_app, "_is_local_port_open", fake_port_open)
    monkeypatch.setattr(web_app, "_chrome_process_running", lambda: False)
    monkeypatch.setattr(web_app.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(web_app.time, "sleep", lambda *_: None)

    ok, message = web_app._launch_handoff_browser(9222)

    assert ok is True
    assert "接手 Chrome 已開啟" in message
    assert launched["command"][1] == "--remote-debugging-port=9222"
    assert launched["command"][2].startswith("--user-data-dir=")
    assert launched["command"][-1] == "https://www.vscinemas.com.tw/vsTicketing/ticketing/ticket.aspx"


def test_launch_handoff_browser_reports_missing_browser(monkeypatch):
    monkeypatch.setattr(web_app, "_detect_chrome_executable", lambda: "")
    monkeypatch.setattr(web_app, "_is_local_port_open", lambda port: False)
    monkeypatch.setattr(web_app, "_chrome_process_running", lambda: False)

    ok, message = web_app._launch_handoff_browser(9222)

    assert ok is False
    assert "找不到 Chrome/Edge" in message


def test_api_takeover_showtimes_returns_monkeypatched_results(monkeypatch):
    monkeypatch.setattr(web_app, "_is_local_port_open", lambda port: True)
    monkeypatch.setattr(
        web_app,
        "_fetch_takeover_showtimes",
        lambda *, debug_port, page_url_substring: [
            {
                "option_id": "0",
                "value": "16:40",
                "text": "16:40",
                "selected": True,
            },
            {
                "option_id": "1",
                "value": "18:20",
                "text": "18:20",
                "selected": False,
            },
        ],
    )

    app = web_app.create_app("config.yaml")
    client = app.test_client()

    response = client.post(
        "/api/takeover/showtimes",
        json={
            "debug_port": 9222,
            "page_url_substring": "vscinemas.com.tw",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["debug_port"] == 9222
    assert payload["page_url_substring"] == "vscinemas.com.tw"
    assert payload["showtimes"][0]["text"] == "16:40"
    assert payload["showtimes"][0]["selected"] is True


def test_api_start_rejects_missing_takeover_listener(monkeypatch):
    monkeypatch.setattr(web_app, "load_config", lambda path: AppConfig())
    monkeypatch.setattr(web_app, "_is_local_port_open", lambda port: False)

    app = web_app.create_app("config.yaml")
    client = app.test_client()

    response = client.post(
        "/api/start",
        json={
            "mode": "takeover",
            "takeover": True,
            "takeover_config": {"enabled": True, "debug_port": 9444},
        },
    )

    assert response.status_code == 400
    assert "接手 Chrome 尚未就緒" in response.get_json()["error"]


def test_api_start_applies_takeover_settings(monkeypatch):
    captured = {}

    class DummyTakeoverV3:
        def __init__(self, cfg, event, session=None):
            captured["cfg"] = cfg
            captured["event"] = event
            captured["session"] = session
            self._status_callback = None

        def set_status_callback(self, callback):
            self._status_callback = callback

        async def run(self):
            return True

        async def watch(self, interval=5.0):
            return True

        async def close(self):
            return None

        def request_stop(self):
            return None

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    monkeypatch.setattr(web_app, "load_config", lambda path: AppConfig())
    monkeypatch.setattr(web_app, "_is_local_port_open", lambda port: True)
    monkeypatch.setattr(web_app.threading, "Thread", ImmediateThread)
    monkeypatch.setattr("ticket_bot.vieshow_takeover_v3.VieShowTakeoverV3", DummyTakeoverV3)

    app = web_app.create_app("config.yaml")
    client = app.test_client()

    response = client.post(
        "/api/start",
        json={
            "mode": "takeover",
            "takeover": True,
            "takeover_config": {
                "enabled": True,
                "debug_port": 9444,
                "page_url_substring": "vscinemas.com.tw",
                "selected_showtime_option_id": "0",
                "selected_showtime_value": "16:40",
            },
            "movie_keyword": "Test Movie",
            "showtime_keyword": "19:30",
            "sale_time_date": "2026-04-11",
            "sale_time_time": "12:00",
            "ticket_count": 2,
            "ticket_type": "full",
            "seat_preference": "center",
        },
    )

    assert response.status_code == 200
    assert response.get_json()["mode"] == "takeover"
    assert captured["cfg"].browser.takeover_from_current_page is True
    assert captured["cfg"].browser.attach_cdp_url == "http://127.0.0.1:9444"
    assert captured["cfg"].browser.attach_page_url_substring == "vscinemas.com.tw"
    assert captured["cfg"].vieshow.takeover.enabled is True
    assert captured["cfg"].vieshow.takeover.debug_port == 9444
    assert captured["cfg"].vieshow.takeover.cdp_url == "http://127.0.0.1:9444"
    assert captured["cfg"].vieshow.takeover_mode is True
    assert captured["cfg"].vieshow.attach_cdp_url == "http://127.0.0.1:9444"
    assert captured["cfg"].vieshow.attach_page_url_substring == "vscinemas.com.tw"
    assert captured["cfg"].vieshow.showtime_keyword == "16:40"
    assert captured["event"].name == "Test Movie"
    assert captured["event"].sale_time == "2026-04-11 12:00:00"
    assert captured["event"].presale_code == "0"
    assert captured["event"].date_keyword == "16:40"
    assert captured["session"].name == "web-ui"


def test_api_launch_handoff_browser_ready(monkeypatch):
    monkeypatch.setattr(web_app, "_is_local_port_open", lambda port: True)

    app = web_app.create_app("config.yaml")
    client = app.test_client()

    response = client.post("/api/launch-handoff-browser", json={"attach_debug_port": 9222})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ready"
