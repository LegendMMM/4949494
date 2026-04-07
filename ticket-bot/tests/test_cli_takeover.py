from __future__ import annotations

from click.testing import CliRunner

from ticket_bot.cli import cli
from ticket_bot.config import AppConfig, EventConfig, SessionConfig


def test_takeover_command_applies_takeover_settings(monkeypatch):
    captured = {}

    class DummyBot:
        def __init__(self, cfg, event, session=None):
            captured["cfg"] = cfg
            captured["event"] = event
            captured["session"] = session

        async def run(self):
            return True

        async def close(self):
            return None

    cfg = AppConfig(
        events=[
            EventConfig(
                name="VieShow Demo",
                platform="vieshow",
                url="https://www.vscinemas.com.tw/vsTicketing/ticketing/ticket.aspx",
                ticket_count=2,
            )
        ],
        sessions=[SessionConfig(name="demo", user_data_dir="./profile")],
    )

    monkeypatch.setattr("ticket_bot.cli.load_config", lambda _path: cfg)
    monkeypatch.setattr("ticket_bot.platforms.vieshow.VieShowBot", DummyBot)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "--config",
            "config.yaml",
            "takeover",
            "--debug-port",
            "9444",
            "--page-url-substring",
            "ticketing",
            "--count",
            "3",
            "--ticket-type",
            "student",
            "--seat-preference",
            "F12,F13",
        ],
    )

    assert result.exit_code == 0
    assert "已完成接手流程" in result.output
    assert captured["cfg"].vieshow.takeover.enabled is True
    assert captured["cfg"].vieshow.takeover.debug_port == 9444
    assert captured["cfg"].vieshow.takeover.page_url_substring == "ticketing"
    assert captured["cfg"].vieshow.attach_cdp_url == "http://127.0.0.1:9444"
    assert captured["cfg"].browser.takeover_from_current_page is True
    assert captured["cfg"].vieshow.ticket_type == "student"
    assert captured["cfg"].vieshow.seat_preference == "F12,F13"
    assert captured["event"].ticket_count == 3
    assert captured["session"].name == "demo"
