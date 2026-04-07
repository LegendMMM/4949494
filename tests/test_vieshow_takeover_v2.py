from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from ticket_bot.config import AppConfig, EventConfig, SessionConfig
from ticket_bot.vieshow_takeover_v2.flow import VieShowTakeoverV2, read_showtime_options


def _make_bot() -> VieShowTakeoverV2:
    cfg = AppConfig()
    cfg.vieshow.takeover.enabled = True
    cfg.vieshow.takeover.cdp_url = "http://127.0.0.1:9222"
    cfg.vieshow.takeover.page_url_substring = "vscinemas.com.tw"
    event = EventConfig(
        name="VieShow",
        platform="vieshow",
        url="https://www.vscinemas.com.tw/vsTicketing/ticketing/ticket.aspx",
        ticket_count=2,
    )
    return VieShowTakeoverV2(cfg, event, session=SessionConfig())


@pytest.mark.asyncio
async def test_read_showtime_options_connects_and_returns(monkeypatch):
    calls: list[str] = []

    class FakeCDP:
        async def connect(self, cdp_url: str, page_url_substring: str):
            calls.append(f"connect:{cdp_url}:{page_url_substring}")
            return None

        async def evaluate(self, _expr: str):
            calls.append("evaluate")
            return [{"optionId": "0", "text": "16:40"}]

        async def close(self):
            calls.append("close")
            return None

    monkeypatch.setattr("ticket_bot.vieshow_takeover_v2.flow.CDPTakeoverEngine", FakeCDP)
    result = await read_showtime_options(cdp_url="http://127.0.0.1:9222", page_url_substring="ticket.aspx")

    assert result == [{"optionId": "0", "text": "16:40"}]
    assert calls == [
        "connect:http://127.0.0.1:9222:ticket.aspx",
        "evaluate",
        "close",
    ]


def test_pick_showtime_prefers_selected_option_id():
    bot = _make_bot()
    bot.event.presale_code = "2"
    options = [
        {"optionId": "0", "text": "14:00"},
        {"optionId": "1", "text": "15:20"},
        {"optionId": "2", "text": "16:40"},
    ]
    picked = bot._pick_showtime(options)
    assert picked is not None
    assert picked["text"] == "16:40"


@pytest.mark.asyncio
async def test_ensure_sale_window_waits_before_sale_time(monkeypatch):
    bot = _make_bot()
    future = datetime.now() + timedelta(seconds=10)
    bot.event.sale_time = future.strftime("%Y/%m/%d %H:%M:%S")
    sleeps: list[float] = []

    async def fake_sleep(value: float):
        sleeps.append(value)
        return None

    monkeypatch.setattr("ticket_bot.vieshow_takeover_v2.flow.asyncio.sleep", fake_sleep)
    ready = await bot._ensure_sale_window()
    assert ready is False
    assert sleeps


@pytest.mark.asyncio
async def test_run_state_machine_ticket_to_order_confirm(monkeypatch):
    bot = _make_bot()
    states = iter(
        [
            {"state": "ticket_showtime"},
            {"state": "booking_option"},
            {"state": "booking_rules"},
            {"state": "ticket_type"},
            {"state": "seat_selection"},
            {"state": "order_confirm"},
        ]
    )
    events: list[str] = []

    async def fake_wait_ready(timeout: float = 2.8):
        events.append(f"wait_ready:{timeout}")
        return None

    async def fake_read_state():
        return next(states)

    async def fake_ensure_sale_window():
        events.append("sale_window")
        return True

    async def fake_click_showtime():
        events.append("click_showtime")
        return True

    async def fake_handle_dialog():
        events.append("handle_dialog")
        return False

    async def fake_click_button(keywords, prefer_top=False):
        events.append(f"click_button:{keywords[0]}:{prefer_top}")
        return True

    async def fake_click_agree():
        events.append("click_agree")
        return True

    async def fake_select_ticket():
        events.append("select_ticket")
        return True

    async def fake_sleep(_value: float):
        return None

    monkeypatch.setattr(bot, "_wait_page_ready", fake_wait_ready)
    monkeypatch.setattr(bot, "_read_state", fake_read_state)
    monkeypatch.setattr(bot, "_ensure_sale_window", fake_ensure_sale_window)
    monkeypatch.setattr(bot, "_click_showtime", fake_click_showtime)
    monkeypatch.setattr(bot, "_handle_dialog_if_any", fake_handle_dialog)
    monkeypatch.setattr(bot, "_click_button", fake_click_button)
    monkeypatch.setattr(bot, "_click_first_unchecked_agree", fake_click_agree)
    monkeypatch.setattr(bot, "_select_full_ticket_count", fake_select_ticket)
    monkeypatch.setattr("ticket_bot.vieshow_takeover_v2.flow.asyncio.sleep", fake_sleep)

    result = await bot._run_state_machine()
    assert result is True
    assert "click_showtime" in events
    assert "click_agree" in events
    assert "select_ticket" in events
